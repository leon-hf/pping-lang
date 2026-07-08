"""操作点:算出 regime / MFU / MBU / AI。两条来源,**实测优先、解析兜底**。

① 实测(perf_stats 在):AI = flops/(read+write 字节)，字节**含 KV-cache 读写**(vLLM perf.py
   的 AttentionMetrics 计了 KV）。吞吐/带宽用真字节。与 `/api/roofline` 的 measured 路径同口径。
② 解析(perf_stats 死，vLLM 0.21 默认不发）：从 token 计数 + 模型参数 + 硬件峰值换算：
       AI  ≈ 2·tokens / dtype_bytes          (FLOPs/Byte；只算权重读，**不含 KV** —— 见 NOTE)
       吞吐 ≈ 2·params·tokens / dt  (TFLOP/s)
   共用：ridge = peak_compute / peak_mem_bw；regime = AI < ridge → memory_bound 否则 compute_bound。

NOTE（为什么解析只给 MFU 不给 MBU）：FLOPs 在解析下是**全知**的（2·params·tokens），所以 MFU 可靠；
但字节**不全知**——解析只算权重读，漏了 KV-cache 读，而 decode 的访存正是 KV 主导。所以：
  - 解析 AI 偏高 → 长上下文 decode 可能被误判成 compute_bound（故 perf_stats 在时优先用实测 AI）；
  - 解析 MBU 会系统性低估带宽 → **不发**（设 None）。带宽信号宁可退回 NVML mem_util（反映真实访存流量）
    也不用权重-only 的解析 MBU。这条不对称是物理决定的，不是偷懒。

供诊断引擎每窗口算一次：regime（传给 evaluate）+ MFU（喂 D1c/D3a）+ MBU（喂 D2a/D3a，仅实测）。
数据不足/缺参数时相关字段返 None → 规则优雅不触发。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatingPoint:
    ai: float | None = None              # 算术强度中位数 (FLOPs/Byte)
    ridge: float | None = None           # 脊点 (FLOPs/Byte)
    achieved_tflops: float | None = None # 实测吞吐中位数 (TFLOP/s)
    mfu: float | None = None             # achieved / peak_compute (0-1)
    mbu: float | None = None             # achieved_bw / peak_mem_bw (0-1)；仅实测路径给值
    regime: str | None = None            # "memory_bound" | "compute_bound" | None
    source: str | None = None            # "measured" | "analytical" | None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def compute_operating_point(
    token_points: list[tuple[float, int]],
    params: float | None,
    dtype_bytes: int,
    peak_compute_tflops: float | None,
    peak_mem_bw_tbs: float | None,
    perf_points: list[tuple[float, float, float, int]] | None = None,
) -> OperatingPoint:
    """实测优先、解析兜底。

    token_points = [(每 step 的 prompt+gen tokens, ts_ns), ...]。
    perf_points  = [(flops, read_bytes, write_bytes, ts_ns), ...]（perf_stats 在时给）。
    缺峰值 → 全 None。需相邻两点求 dt；<2 点 → 该路降级。
    """
    if not peak_compute_tflops or not peak_mem_bw_tbs:
        return OperatingPoint()
    ridge = peak_compute_tflops / peak_mem_bw_tbs   # FLOPs/Byte

    measured = _from_perf(perf_points, peak_compute_tflops, peak_mem_bw_tbs, ridge)
    if measured is not None:
        return measured
    return _from_tokens(
        token_points, params, dtype_bytes, peak_compute_tflops, ridge,
    )


def _from_perf(
    perf_points: list[tuple[float, float, float, int]] | None,
    peak_c: float, peak_bw: float, ridge: float,
) -> OperatingPoint | None:
    """实测路径：vLLM perf_stats 的真 flops/bytes（含 KV）。同 /api/roofline 口径。"""
    if not perf_points or len(perf_points) < 2:
        return None
    pts = sorted(perf_points, key=lambda p: p[3])
    ais: list[float] = []
    tputs: list[float] = []
    bws: list[float] = []
    last_ts: int | None = None
    for flops, read_b, write_b, ts in pts:
        total_b = read_b + write_b
        if last_ts is None:
            last_ts = ts
            continue
        dt_s = (ts - last_ts) / 1e9
        last_ts = ts
        if dt_s <= 0 or flops <= 0 or total_b <= 0:
            continue
        ais.append(flops / total_b)
        tputs.append(flops / dt_s / 1e12)
        bws.append(total_b / dt_s / 1e12)   # TB/s
    ai = _median(ais)
    achieved = _median(tputs)
    bw = _median(bws)
    if ai is None or achieved is None:
        return None
    return OperatingPoint(
        ai=ai, ridge=ridge, achieved_tflops=achieved,
        mfu=achieved / peak_c,
        mbu=(bw / peak_bw) if bw is not None else None,
        regime="memory_bound" if ai < ridge else "compute_bound",
        source="measured",
    )


def _from_tokens(
    token_points: list[tuple[float, int]],
    params: float | None, dtype_bytes: int,
    peak_c: float, ridge: float,
) -> OperatingPoint:
    """解析路径：token 计数 + 模型参数换算。只给 MFU，不给 MBU（见模块 NOTE）。"""
    if params is None or dtype_bytes <= 0 or len(token_points) < 2:
        return OperatingPoint()
    pts = sorted(token_points, key=lambda p: p[1])
    ais: list[float] = []
    tputs: list[float] = []
    last_ts: int | None = None
    for tokens, ts in pts:
        if last_ts is None:
            last_ts = ts
            continue
        dt_s = (ts - last_ts) / 1e9
        last_ts = ts
        if dt_s <= 0 or tokens <= 0:
            continue
        ais.append(2.0 * tokens / dtype_bytes)
        tputs.append(2.0 * params * tokens / dt_s / 1e12)
    ai = _median(ais)
    achieved = _median(tputs)
    if ai is None or achieved is None:
        return OperatingPoint()
    return OperatingPoint(
        ai=ai, ridge=ridge, achieved_tflops=achieved,
        mfu=achieved / peak_c,
        mbu=None,                                   # 解析不给 MBU（权重-only 会低估带宽）
        regime="memory_bound" if ai < ridge else "compute_bound",
        source="analytical",
    )
