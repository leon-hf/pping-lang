"""解析操作点单测:decode→访存受限/MFU 低,prefill→计算受限,数据不足→全 None。"""
from __future__ import annotations

from pping_lang.rules.operating_point import compute_operating_point

# 一台小卡:0.5B 参数, bf16(2B), peak 95 TFLOPS / 0.448 TB/s → ridge ≈ 212 FLOPs/Byte
PARAMS = 0.5e9
DTYPE = 2
PEAK_C = 95.0
PEAK_BW = 0.448
MS = 10**7  # 10ms in ns


def test_decode_is_memory_bound_low_mfu():
    # 每 step 1 token(decode),dt=10ms
    pts = [(1.0, 10**9 + i * MS) for i in range(4)]
    op = compute_operating_point(pts, PARAMS, DTYPE, PEAK_C, PEAK_BW)
    assert op.ai == 1.0                       # 2*1/2
    assert op.regime == "memory_bound"        # 1 << 212
    assert op.mfu is not None and 0 < op.mfu < 0.2   # decode MFU 结构性低


def test_prefill_is_compute_bound():
    # 每 step 2000 token(prefill 批),AI = 2*2000/2 = 2000 > 212
    pts = [(2000.0, 10**9 + i * MS) for i in range(3)]
    op = compute_operating_point(pts, PARAMS, DTYPE, PEAK_C, PEAK_BW)
    assert op.ai == 2000.0
    assert op.regime == "compute_bound"


def test_ridge_computed():
    pts = [(1.0, 10**9), (1.0, 10**9 + MS)]
    op = compute_operating_point(pts, PARAMS, DTYPE, PEAK_C, PEAK_BW)
    assert abs(op.ridge - (PEAK_C / PEAK_BW)) < 1e-6


def test_insufficient_points_all_none():
    op = compute_operating_point([(1.0, 10**9)], PARAMS, DTYPE, PEAK_C, PEAK_BW)
    assert op.regime is None and op.mfu is None and op.ai is None


def test_missing_params_or_peak_all_none():
    pts = [(1.0, 10**9 + i * MS) for i in range(3)]
    assert compute_operating_point(pts, None, DTYPE, PEAK_C, PEAK_BW).regime is None
    assert compute_operating_point(pts, PARAMS, DTYPE, None, PEAK_BW).regime is None
    assert compute_operating_point(pts, PARAMS, DTYPE, PEAK_C, None).regime is None


def test_zero_dt_skipped():
    # 同一 ts 的点 dt=0 被跳过 → 不足两个有效点 → None
    op = compute_operating_point([(1.0, 10**9), (1.0, 10**9)], PARAMS, DTYPE, PEAK_C, PEAK_BW)
    assert op.regime is None
