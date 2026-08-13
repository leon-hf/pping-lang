"""Deep Profile 组装(#1/#7):launch 配置 → 理论占用率 / 受限资源 / wave 量化。"""
from __future__ import annotations

from pping_lang.collector.deep_profile import build_deep_profile
from pping_lang.hardware import SMLimits

# 近似 sm_120(RTX 5060 Ti)的每 SM 资源上限
LIM = SMLimits(mp_count=36, max_threads_per_sm=1536, max_regs_per_sm=65536,
               max_smem_per_sm=102400, max_blocks_per_sm=32)


def _result(rows):
    return {"available": True, "kernel_table": rows}


def test_no_data_fails_closed():
    r = build_deep_profile(None, LIM)
    assert r["available"] is False and r["kernels"] == []
    r = build_deep_profile({"available": False}, LIM)
    assert r["available"] is False


def test_normal_kernel_full_occupancy_and_wave_tail():
    """grid=64 block=128 regs=32:不受资源限(100% 理论),但 64 block 铺不满一个 wave。"""
    r = build_deep_profile(_result([{
        "kernel": "k", "cls": "elementwise",
        "launch_cfg": {"launches": 3, "grid": [64, 1, 1], "block": [128, 1, 1],
                       "dyn_smem": 0, "regs": 32, "static_smem": 0,
                       "local_mem": 0, "max_threads_per_block": 1024},
    }]), LIM)
    assert r["available"] is True and r["pause_ms"] == 0
    k = r["kernels"][0]
    # by_threads = 1536/128 = 12 → 12×4 warp / 48 warp = 100%
    assert k["occupancy_theoretical_pct"] == 100.0
    assert k["limiter"] == "none"
    # wave_size = 36×12 = 432;64 block → 1 个 wave,最后一波 64/432 ≈ 14.8%
    assert k["wave_quant"]["waves"] == 1
    assert abs(k["wave_quant"]["last_wave_pct"] - 14.8) < 0.1
    # 深窗指标未开放 → None
    assert k["occupancy_pct"] is None and k["tensor_pct"] is None
    assert k["l2_hit_pct"] is None and k["dram_gbps"] is None


def test_register_limited():
    """regs=255 block=256:by_regs = 65536/(255×32×8) = 1 → 寄存器受限。"""
    r = build_deep_profile(_result([{
        "kernel": "k", "cls": "gemm",
        "launch_cfg": {"grid": [512, 1, 1], "block": [256, 1, 1], "dyn_smem": 0,
                       "regs": 255, "static_smem": 0},
    }]), LIM)
    k = r["kernels"][0]
    assert k["limiter"] == "registers"
    # 1 block × 8 warp / 48 ≈ 16.7%
    assert abs(k["occupancy_theoretical_pct"] - 16.7) < 0.1


def test_smem_limited():
    """dyn_smem=64KB:by_smem = 102400/65536 = 1 → smem 受限。"""
    r = build_deep_profile(_result([{
        "kernel": "k", "cls": "attention",
        "launch_cfg": {"grid": [512, 1, 1], "block": [128, 1, 1], "dyn_smem": 65536,
                       "regs": 32, "static_smem": 0},
    }]), LIM)
    assert r["kernels"][0]["limiter"] == "smem"


def test_grid_too_small():
    """grid=8 < 36 个 SM → grid 受限(打不满卡)优先于资源项。"""
    r = build_deep_profile(_result([{
        "kernel": "k", "cls": "gemm",
        "launch_cfg": {"grid": [8, 1, 1], "block": [256, 1, 1], "dyn_smem": 0,
                       "regs": 255, "static_smem": 0},
    }]), LIM)
    assert r["kernels"][0]["limiter"] == "grid"


def test_row_without_launch_cfg_degrades():
    """无 launch_cfg(老 .so/被关)→ 配置字段全 None,limiter=None(UI 显示 —),不炸。"""
    r = build_deep_profile(_result([{"kernel": "k", "cls": "gemm", "launch_cfg": None}]), LIM)
    k = r["kernels"][0]
    assert k["limiter"] is None and k["grid"] is None
    assert k["occupancy_theoretical_pct"] is None and k["wave_quant"] is None


def test_no_sm_limits_degrades():
    """读不到设备上限(无 GPU 环境)→ 原始配置仍透传,占用率/受限资源不算。"""
    r = build_deep_profile(_result([{
        "kernel": "k", "cls": "gemm",
        "launch_cfg": {"grid": [64, 1, 1], "block": [128, 1, 1], "dyn_smem": 0,
                       "regs": 32, "static_smem": 0},
    }]), None)
    k = r["kernels"][0]
    assert k["grid"] == [64, 1, 1] and k["regs_per_thread"] == 32
    assert k["occupancy_theoretical_pct"] is None and k["limiter"] is None
