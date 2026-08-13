"""#2/#3 family 级 roofline 纯软件估算:家族分类 / 形状反推 / FLOPs·bytes / achieved。

数值基准来自 2026-08-07 runw 线上窗(Qwen2.5-0.5B int4-marlin,RTX 5060 Ti):
marlin 73.36% / gemv(lm_head) 23.93% / flash 2.19%,gemv launches=77,window 5s。
"""
from __future__ import annotations

import pytest

from pping_lang.collector.roofline_est import (
    FAM_CUTLASS,
    FAM_FLASH,
    FAM_GEMV,
    FAM_MARLIN,
    classify_family,
    estimate_families,
    projection_shapes,
)
from pping_lang.hardware import GPUPeak

# Qwen2.5-0.5B-Instruct 的 arch(_extract_arch 的产物)
ARCH = {
    "hidden_size": 896, "num_hidden_layers": 24, "intermediate_size": 4864,
    "num_attention_heads": 14, "num_key_value_heads": 2, "vocab_size": 151936,
    "tie_word_embeddings": False, "torch_dtype": "float16",
}
PEAK = GPUPeak(bf16_tflops=94.9, mem_bw_gbs=448.0)   # RTX 5060 Ti(read_gpu_peak 口径)

MARLIN_K = ("_ZN6marlin6MarlinILl1125899906910725ELl1125899906843648ELl1125899906910725E"
            "Ll1125899906910725ELi256ELi1ELi8ELi8ELb1ELi4ELi8ELb0EEEvPK4int4S3_PS1_S4_S3_"
            "PKfS3_S6_S3_PKiiiiiiPibbbi")
GEMV_K = ("_ZN8internal5gemvx6kernelIii6__halfS2_S2_fLb0ELb1ELb1ELb0ELi7ELb0E18"
          "cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEE"
          "NSt9enable_ifIXntT5_EvE4typeET11_")
FLASH_K = "_ZN5flash24flash_fwd_splitkv_kernelI23Flash_fwd_kernel_traitsILi128ELi64E..."


def _window(rows, **kw):
    d = {"available": True, "window_s": 5.0, "period_log2": 16,
         "sample_total": 4419452.0, "kernel_table": rows}
    d.update(kw)
    return d


def _row(name, pct, launches=0):
    return {"kernel": name, "time_pct": pct,
            "launch_cfg": {"launches": launches, "grid": [36, 1, 1]}}


# === 家族分类 ===

def test_classify_family():
    assert classify_family(MARLIN_K) == FAM_MARLIN
    assert classify_family(GEMV_K) == FAM_GEMV
    assert classify_family(FLASH_K) == FAM_FLASH
    assert classify_family("_ZN7cutlass6device8GemmUniversal...") == FAM_CUTLASS
    assert classify_family("_ZN2at6native29vectorized_elementwise_kernel...") is None
    assert classify_family("") is None
    # triton 融合 kernel 名里带 marlin/gemv 字样,子串匹配会误归(runw 线上实测)
    assert classify_family("triton_poi_fused_marlin_gemm_mul_silu_slice_1") is None


# === 形状反推 ===

def test_projection_shapes_qwen_0_5b():
    s = projection_shapes(ARCH)
    assert s["qkv"] == (896, (14 + 2 * 2) * 64)     # GQA:(H+2·Hkv)·head_dim = 1152
    assert s["o"] == (896, 896)
    assert s["gate_up"] == (896, 2 * 4864)
    assert s["down"] == (4864, 896)
    assert s["lm_head"] == (896, 151936)


# === 端到端估算(对齐线上窗数值) ===

def _est(rows=None):
    rows = rows if rows is not None else [
        _row(MARLIN_K, 73.36, launches=0),      # 图内 → launches=0
        _row(GEMV_K, 23.93, launches=77),       # lm_head 在图外 → 77 步
        _row(FLASH_K, 2.19, launches=0),
    ]
    return estimate_families(_window(rows), ARCH, PEAK,
                             mp_count=36, sm_clock_hz=2.572e9)


def _fam(est, name):
    return next(f for f in est["families"] if f["family"] == name)


def test_window_level_fields():
    est = _est()
    assert est["available"] is True
    assert est["steps_per_s"] == pytest.approx(15.4)      # 77 / 5s
    # max = 36 × 2.572e9/2^16 × 5 ≈ 7.06M;busy = 4.42M/7.06M ≈ 62.6%
    assert est["busy_pct"] == pytest.approx(62.6, abs=0.2)
    assert est["peak"]["mem_bw_gbs"] == 448.0


def test_marlin_family_estimate():
    f = _fam(_est(), FAM_MARLIN)
    assert f["time_pct"] == pytest.approx(73.36)
    # per-step:24 层 × (qkv+o+gate_up+down),int4 权重
    assert f["flops_per_step"] == 715653120
    assert f["bytes_per_step"] == 179884032
    assert f["ai_flop_per_byte"] == pytest.approx(3.98, abs=0.01)
    assert f["ridge_ai"] == pytest.approx(211.8, abs=0.2)
    # achieved:77 步共 13.85GB ÷ (0.7336 × 3.128s) ≈ 6.0 GB/s → 离 448 GB/s 远
    assert f["achieved_gbps"] == pytest.approx(6.0, abs=0.1)
    assert f["pct_of_mem_peak"] == pytest.approx(1.35, abs=0.05)
    assert f["verdict"] == "memory-latency-bound"
    assert f["confidence"] == "low"      # achieved 叠了三层近似
    assert {m["proj"] for m in f["members"]} == {"qkv", "o", "gate_up", "down"}


def test_gemv_family_estimate():
    f = _fam(_est(), FAM_GEMV)
    assert f["bytes_per_step"] == 272574976    # lm_head fp16 权重 272MB + 激活
    assert f["ai_flop_per_byte"] == pytest.approx(1.0, abs=0.01)
    assert f["achieved_gbps"] == pytest.approx(28.0, abs=0.5)
    assert f["verdict"] in ("memory-bound", "memory-latency-bound")


def test_flash_family_has_no_shape_model():
    f = _fam(_est(), FAM_FLASH)
    assert f["time_pct"] == pytest.approx(2.19)
    assert f["ai_flop_per_byte"] is None and f["verdict"] is None
    assert f["confidence"] == "none"


# === 降级路径(fail-closed,一个都不能炸) ===

def test_no_window_fails_closed():
    est = estimate_families(None, ARCH, PEAK, 36, 2.572e9)
    assert est["available"] is False and est["families"] == []
    est = estimate_families({"available": False}, ARCH, PEAK, 36, 2.572e9)
    assert est["available"] is False


def test_no_arch_shapes_none_but_classification_works():
    """读不到 arch(非标准模型)→ 家族照分、time_pct 照给,形状/AI 为 None。"""
    est = estimate_families(_window([_row(MARLIN_K, 73.36)]), None, PEAK, 36, 2.572e9)
    f = _fam(est, FAM_MARLIN)
    assert f["time_pct"] == pytest.approx(73.36)
    assert f["ai_flop_per_byte"] is None and f["confidence"] == "none"


def test_no_peak_no_verdict():
    est = estimate_families(_window([_row(MARLIN_K, 73.36)]), ARCH, None, 36, 2.572e9)
    f = _fam(est, FAM_MARLIN)
    assert f["ai_flop_per_byte"] == pytest.approx(3.98, abs=0.01)   # AI 不依赖 peak
    assert f["ridge_ai"] is None and f["verdict"] is None


def test_no_clock_no_busy_no_achieved():
    """读不到 SM 时钟 → busy 窗 None → achieved None,但 AI 仍在。"""
    est = estimate_families(_window([_row(GEMV_K, 23.93, launches=77)]),
                            ARCH, PEAK, None, None)
    f = _fam(est, FAM_GEMV)
    assert est["busy_pct"] is None
    assert f["achieved_gbps"] is None and f["ai_flop_per_byte"] is not None


def test_no_gemv_launches_no_steps():
    """lm_head 也被 CUDA graph 捕获(launches=0)→ steps/s None → achieved None。"""
    est = estimate_families(_window([_row(MARLIN_K, 73.36, launches=0),
                                     _row(GEMV_K, 23.93, launches=0)]),
                            ARCH, PEAK, 36, 2.572e9)
    assert est["steps_per_s"] is None
    assert _fam(est, FAM_MARLIN)["achieved_gbps"] is None


# === 量化部署形态(marlin 在 → cutlass 是 lm_head,不是层 GEMM)===
# 数值基准:2026-08-08 runw 线上窗,Qwen2.5-7B-Instruct-AWQ:
# marlin 70.74% launches=0(图内)/ cutlass fp16 21.27% launches=74 grid=[8,297,1]
# (2376 block ≈ vocab 151936/64 → lm_head 实锤)

ARCH_7B = {
    "hidden_size": 3584, "num_hidden_layers": 28, "intermediate_size": 18944,
    "num_attention_heads": 28, "num_key_value_heads": 4, "vocab_size": 151936,
    "tie_word_embeddings": False, "torch_dtype": "float16",
}
CUTLASS_K = ("_ZN7cutlass7Kernel2I64cutlass_80_tensorop_f16_s16816gemm_relu_f16_"
             "64x64_32x6_tn_align8EEvNT_6ParamsE")


def test_cutlass_is_lm_head_when_marlin_present():
    est = estimate_families(
        _window([_row(MARLIN_K, 70.74, launches=0),
                 _row(CUTLASS_K, 21.27, launches=74)],
                sample_total=3900000.0),   # busy ≈ 55.2%
        ARCH_7B, PEAK, mp_count=36, sm_clock_hz=2.572e9)
    f = _fam(est, FAM_CUTLASS)
    # lm_head fp16:N=vocab=151936, K=hidden=3584;bytes ≈ N·K·2
    assert [m["proj"] for m in f["members"]] == ["lm_head"]
    assert f["bytes_per_step"] == 151936 * 3584 * 2 + 3584 * 2 + 151936 * 2
    assert f["ai_flop_per_byte"] == pytest.approx(1.0, abs=0.01)
    # steps/s 从 cutlass(lm_head 载体)的 launches 反推:74 / 5s
    assert est["steps_per_s"] == pytest.approx(14.8)
    # achieved:74 步 × 1.089GB ÷ (0.2127 × 5 × 0.552) ≈ 137 GB/s ≈ 30% roof
    assert f["achieved_gbps"] == pytest.approx(137.0, abs=3.0)
    assert f["pct_of_mem_peak"] == pytest.approx(30.7, abs=1.0)
    assert f["verdict"] == "memory-latency-bound"
    m = _fam(est, FAM_MARLIN)
    assert m["achieved_gbps"] == pytest.approx(124.0, abs=3.0)   # 3.26GB/步 × 74 ÷ 1.95s


def test_cutlass_is_layer_proj_when_no_marlin():
    """非量化 fp16 部署(无 marlin 行)→ cutlass = 层 GEMM;lm_head 无载体 → steps None。"""
    est = estimate_families(_window([_row(CUTLASS_K, 60.0, launches=0)]),
                            ARCH_7B, PEAK, 36, 2.572e9)
    f = _fam(est, FAM_CUTLASS)
    assert {m["proj"] for m in f["members"]} == {"qkv", "o", "gate_up", "down"}
    # fp16 层 GEMM decode:AI ≈ 1(权重 2 字节/元素)
    assert f["ai_flop_per_byte"] == pytest.approx(1.0, abs=0.02)
    assert est["steps_per_s"] is None and f["achieved_gbps"] is None
