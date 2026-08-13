"""#2/#3 纯软件 roofline 估算(family 级)—— 不停服务、不占硬件计数器。

数据链(全程只读已有数据,零新增采集):
  kernel 名 → 家族分类(marlin-int4 / cublas-gemv / flash / cutlass)
  家族 + 模型 arch(routes._extract_arch 从 hf_config 抽)→ 每 decode step 的
      FLOPs/bytes 解析解(GEMM 形状由模型结构定死,decode M=1)
  AI = FLOPs/bytes → 对照 roof(read_gpu_peak 现读设备属性)判 memory/compute-bound → #2
  achieved = per-step bytes·steps/s ÷ (time_pct × busy 窗)→ 离 roof 多远 → #3

为什么 family 级而不是 row 级:marlin 同名模板聚合了 qkv/o/gate_up/down 四个投影
(同一 mangled 名 = kernel_table 同一行),row 级归因有歧义;family 级把同族行的
time_pct 加起来,绕开歧义。

诚实边界(全部进 notes,UI 必须标注"估算"):
  - M=1 decode 口径;prefill(M=序列长)不适用,prefill 重的窗数字会失真
  - CUDA graph 内的 kernel launches=0,steps/s 靠图外的 lm_head 行反推
    (vLLM decode 每步过一次 lm_head;载体:非量化=gemv,量化=cutlass fp16;
    lm_head 也被图捕获则无 steps/s → achieved 给 None)
  - busy 窗由 PCS 样本率反推:max = SM 数 × 时钟 ÷ 2^period;sample_total/max = 活跃占比。
    PC Sampling 只采活跃 warp 槽,故这是"SM 有活在干"的时间占比
  - GPTQ/AWQ 的 scales+zeros 字节(~3%)未计,AI 略偏高
  - achieved 依赖三层近似(busy 窗、steps/s、per-step bytes),标 confidence=low;
    AI/bound 只依赖形状与 roof,confidence=high
"""
from __future__ import annotations

from typing import Any

from pping_lang.hardware import GPUPeak

FAM_MARLIN = "marlin-int4"
FAM_GEMV = "cublas-gemv"
FAM_FLASH = "flash-attn"
FAM_CUTLASS = "cutlass-gemm"

_W_GPTQ_INT4 = 0.5   # marlin int4 权重字节/元素
_W_FP16 = 2.0        # fp16/bf16 权重与激活字节/元素


def classify_family(kernel_name: str) -> str | None:
    """mangled kernel 名 → 估算家族。认不出 → None(该行不参与估算)。

    按命名空间前缀判,不用子串:triton 融合 kernel(如
    triton_poi_fused_marlin_gemm_mul_silu_slice)名字里带 "marlin"/"gemv"
    字样的实测存在,子串匹配会误归(runw 线上撞过)。
    """
    n = kernel_name or ""
    low = n.lower()
    if n.startswith("_ZN6marlin"):                    # marlin::Marlin<...>
        return FAM_MARLIN
    if "gemvx" in low:                               # cublas internal::gemvx::kernel
        return FAM_GEMV
    if n.startswith("_ZN5flash") or "fmha" in low:
        return FAM_FLASH
    if n.startswith("_ZN7cutlass"):                  # cutlass::Kernel2<...>
        return FAM_CUTLASS
    return None


def projection_shapes(arch: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """标准 decoder-only(Llama/Qwen 族)每层的 GEMM 投影集合 + lm_head,(K, N) 形式。

    decode 时 vLLM 把 q/k/v 并成一个 qkv_proj、gate/up 并成 gate_up。
    """
    h = arch["hidden_size"]
    i = arch["intermediate_size"]
    H = arch["num_attention_heads"]
    Hkv = arch["num_key_value_heads"]
    V = arch["vocab_size"]
    head_dim = h // H
    return {
        "qkv": (h, (H + 2 * Hkv) * head_dim),
        "o": (h, h),
        "gate_up": (h, 2 * i),
        "down": (i, h),
        "lm_head": (h, V),
    }


def _gemm_flops_bytes(m: int, n: int, k: int, w_bytes: float) -> tuple[float, float]:
    """一次 GEMM 的 FLOPs(2·M·N·K)与 DRAM 字节(权重 + 进/出激活,激活 fp16)。"""
    flops = 2.0 * m * n * k
    bts = n * k * w_bytes + m * k * _W_FP16 + m * n * _W_FP16
    return flops, bts


def _family_step(fam: str, arch: dict[str, Any],
                 cutlass_lm_head: bool = False) -> dict[str, Any] | None:
    """该家族每 decode step(M=1)的 FLOPs/bytes 解析解。无模型(如 attention)→ None。

    cutlass_lm_head:同窗存在 marlin 行 = 量化部署,层 GEMM 归 marlin,cutlass fp16
    那只剩 lm_head 一个角色(vLLM 不量化 lm_head;runw 7B-AWQ 实测:
    grid 2376 ≈ vocab/64,launches=decode 步数)。非量化部署(no marlin)时
    cutlass 是层 GEMM(qkv/o/gate_up/down)。
    """
    shapes = projection_shapes(arch)
    L = arch["num_hidden_layers"]
    if fam == FAM_MARLIN:
        flops = bts = 0.0
        members = []
        for name in ("qkv", "o", "gate_up", "down"):
            k, n = shapes[name]
            f, b = _gemm_flops_bytes(1, n, k, _W_GPTQ_INT4)
            flops += f * L
            bts += b * L
            members.append({"proj": name, "n": n, "k": k, "per_layer": True})
        return {"flops": flops, "bytes": bts, "members": members,
                "weight_bytes_per_elem": _W_GPTQ_INT4}
    if fam == FAM_GEMV:
        # decode 每步一次 lm_head;cublas 在 M=1 时选 gemv(fp16 权重直读)
        k, n = shapes["lm_head"]
        f, b = _gemm_flops_bytes(1, n, k, _W_FP16)
        return {"flops": f, "bytes": b,
                "members": [{"proj": "lm_head", "n": n, "k": k, "per_layer": False}],
                "weight_bytes_per_elem": _W_FP16}
    if fam == FAM_CUTLASS:
        if cutlass_lm_head:
            k, n = shapes["lm_head"]
            f, b = _gemm_flops_bytes(1, n, k, _W_FP16)
            return {"flops": f, "bytes": b,
                    "members": [{"proj": "lm_head", "n": n, "k": k, "per_layer": False}],
                    "weight_bytes_per_elem": _W_FP16}
        # 非量化 fp16 部署:层 GEMM 走 cutlass
        flops = bts = 0.0
        members = []
        for name in ("qkv", "o", "gate_up", "down"):
            k, n = shapes[name]
            f, b = _gemm_flops_bytes(1, n, k, _W_FP16)
            flops += f * L
            bts += b * L
            members.append({"proj": name, "n": n, "k": k, "per_layer": True})
        return {"flops": flops, "bytes": bts, "members": members,
                "weight_bytes_per_elem": _W_FP16}
    return None  # flash-attn:FLOPs 依赖序列长度,v1 不估


def _verdict(ai: float | None, ridge: float | None, pct_mem: float | None) -> str | None:
    """判型码(UI 负责翻译成当前语言)。memory-bound 再分"带宽喂饱"与
    "latency-bound(没喂饱)"两档,后者是 decode 低并发的常态,
    也是"该加大 batch"的直接证据。"""
    if ai is None or ridge is None or ridge <= 0:
        return None
    if ai >= ridge:
        return "compute-bound"
    if pct_mem is not None and pct_mem < 50.0:
        return "memory-latency-bound"
    return "memory-bound"


def estimate_families(
    last_result: dict[str, Any] | None,
    arch: dict[str, Any] | None,
    peak: GPUPeak | None,
    mp_count: int | None,
    sm_clock_hz: float | None,
) -> dict[str, Any]:
    """由最近一窗 Deep Evidence 结果组装 family 级 roofline 估算。fail-closed。"""
    if not last_result or not last_result.get("available"):
        return {"available": False, "families": [],
                "error": "还没有采样窗数据(等首个 PC Sampling 窗口写入)"}

    window_s = float(last_result.get("window_s") or 0)
    period_log2 = int(last_result.get("period_log2") or 16)
    sample_total = float(last_result.get("sample_total") or 0)
    rows = last_result.get("kernel_table") or []

    # family 聚合:time_pct 求和;顺带取 lm_head 行的 launches —— 它在 CUDA graph 外,
    # launches = 本窗 decode 步数,是 steps/s 的唯一在线来源。lm_head 的载体随部署变:
    # 非量化 → cublas gemv(M=1);量化(marlin 在)→ fp16 cutlass(vLLM 不量化 lm_head)
    fam_pct: dict[str, float] = {}
    fam_launches: dict[str, int] = {}
    for r in rows:
        fam = classify_family(r.get("kernel", ""))
        if fam is None:
            continue
        fam_pct[fam] = fam_pct.get(fam, 0.0) + float(r.get("time_pct") or 0.0)
        lc = r.get("launch_cfg") or {}
        fam_launches[fam] = fam_launches.get(fam, 0) + int(lc.get("launches") or 0)

    has_marlin = FAM_MARLIN in fam_pct
    lm_head_launches = fam_launches.get(FAM_GEMV, 0)
    if lm_head_launches == 0 and has_marlin:
        lm_head_launches = fam_launches.get(FAM_CUTLASS, 0)
    steps_per_s = lm_head_launches / window_s if (lm_head_launches > 0 and window_s > 0) else None

    # busy 窗:PCS 只采活跃 warp 槽 → 样本数/理论满采样数 = SM 活跃时间占比
    busy_s: float | None = None
    busy_pct: float | None = None
    if mp_count and sm_clock_hz and window_s > 0 and sample_total > 0:
        max_samples = mp_count * (sm_clock_hz / (2 ** period_log2)) * window_s
        if max_samples > 0:
            busy_pct = min(100.0, 100.0 * sample_total / max_samples)
            busy_s = window_s * min(1.0, sample_total / max_samples)

    ridge: float | None = None  # FLOP/B:ridge point,AI 低于它 = memory-bound
    if peak is not None and peak.mem_bw_gbs > 0:
        ridge = peak.bf16_tflops * 1000.0 / peak.mem_bw_gbs

    families: list[dict[str, Any]] = []
    for fam, pct in sorted(fam_pct.items(), key=lambda kv: -kv[1]):
        entry: dict[str, Any] = {
            "family": fam,
            "time_pct": round(pct, 2),
            "flops_per_step": None, "bytes_per_step": None,
            "ai_flop_per_byte": None, "ridge_ai": (round(ridge, 1) if ridge else None),
            "achieved_tflops": None, "achieved_gbps": None,
            "pct_of_mem_peak": None, "pct_of_compute_peak": None,
            "verdict": None, "confidence": "none", "members": [],
        }
        step = _family_step(fam, arch, cutlass_lm_head=has_marlin) if arch else None
        if step is not None:
            flops, bts = step["flops"], step["bytes"]
            ai = flops / bts if bts > 0 else None
            entry.update({
                "flops_per_step": round(flops),
                "bytes_per_step": round(bts),
                "ai_flop_per_byte": (round(ai, 2) if ai else None),
                "members": step["members"],
                "confidence": "high",   # AI/bound 只看形状与 roof
            })
            # achieved:窗内总工作量(per-step × steps)÷ 该族占用时间 → 运行时的速率。
            # 三层近似叠加(busy 窗/steps/s/per-step bytes)→ confidence=low
            time_s = (pct / 100.0) * busy_s if busy_s else None
            if steps_per_s and time_s and time_s > 0 and window_s > 0:
                steps_total = steps_per_s * window_s
                gbps = bts * steps_total / time_s / 1e9
                tflops = flops * steps_total / time_s / 1e12
                entry.update({
                    "achieved_gbps": round(gbps, 2),
                    "achieved_tflops": round(tflops, 3),
                    "confidence": "low",
                })
                if peak is not None:
                    if peak.mem_bw_gbs > 0:
                        entry["pct_of_mem_peak"] = round(100.0 * gbps / peak.mem_bw_gbs, 2)
                    if peak.bf16_tflops > 0:
                        entry["pct_of_compute_peak"] = round(
                            100.0 * tflops / peak.bf16_tflops, 2)
            entry["verdict"] = _verdict(ai, ridge, entry["pct_of_mem_peak"])
        families.append(entry)

    return {
        "available": True,
        "window_s": window_s,
        "steps_per_s": (round(steps_per_s, 2) if steps_per_s else None),
        "busy_pct": (round(busy_pct, 1) if busy_pct is not None else None),
        "peak": ({"bf16_tflops": peak.bf16_tflops, "mem_bw_gbs": peak.mem_bw_gbs}
                 if peak else None),
        "families": families,
        "notes": [
            "decode M=1 口径;prefill 重的窗数字会失真",
            "marlin 同名模板聚合 qkv/o/gate_up/down,只能 family 级归因",
            "steps/s 由 lm_head 的 launches 反推(它在 CUDA graph 外;非量化=gemv,量化=cutlass),图内 kernel launches=0",
            "busy 窗由 PC Sampling 样本率反推(样本只打在活跃 warp 槽)",
            "GPTQ scales/zeros 字节(~3%)未计,AI 略偏高",
            "achieved_* 叠了三层近似(busy 窗/steps/s/per-step bytes),confidence=low",
        ],
        "error": None,
    }
