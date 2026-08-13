"""Deep Profile(#1/#7):由常驻 launch 配置组装"该怎么改"面板数据。

数据链:EngineCore 的 launch 回调(launch_cfg,随 PC Sampling 窗写入共享 JSON)
→ API 进程读最近一窗 → 本模块按设备 SM 资源上限(现读,见 hardware.read_sm_limits)
算出**理论占用率 / 受限资源 / wave 量化**。全程不停服务、不需要深窗。

#2/#3(roofline 定位 / achieved TFLOPS·GB/s)走 roofline_est 的**纯软件估算**
(family 级,模型 arch × 形状反推 × PCS time_pct),随本模块一起输出;
#4 的实测 L2/DRAM 与 row 级 occupancy/tensor 实测仍是深窗指标,一律给 None
(UI 显示 "—")。

占用率估算口径(标准 roofline-of-occupancy 近似,不考虑 warp 分配粒度,
误差几个百分点,面板文案已标注"估算"):
  blocks/SM = min(线程上限, 寄存器上限, smem 上限, 硬件 block 上限) 各项分别除出来的最小值
  理论占用率 = blocks/SM × warps/block ÷ (max_threads/SM ÷ 32)
"""
from __future__ import annotations

import math
import time
from typing import Any

from pping_lang.collector.kernel_calibration import lookup as calib_lookup
from pping_lang.collector.roofline_est import estimate_families
from pping_lang.hardware import GPUPeak, SMLimits

_WARP = 32


def _blocks_per_sm(lim: SMLimits, block_threads: int, regs: int, smem_bytes: int) -> dict[str, int]:
    """各资源分别允许的常驻 block 数(任一 = 0 表示该资源直接卡死)。"""
    warps_per_block = max(1, math.ceil(block_threads / _WARP))
    by_threads = lim.max_threads_per_sm // block_threads if block_threads > 0 else 0
    by_regs = (
        lim.max_regs_per_sm // (regs * _WARP * warps_per_block)
        if regs > 0 else lim.max_blocks_per_sm  # regs 未取到(-1/0)→ 不当限制项
    )
    by_smem = (
        lim.max_smem_per_sm // smem_bytes
        if smem_bytes > 0 else lim.max_blocks_per_sm  # 无 smem 需求 → 不当限制项
    )
    by_hw = lim.max_blocks_per_sm
    return {"threads": by_threads, "registers": by_regs, "smem": by_smem, "hw": by_hw}


def _row(kernel: str, cls: str, cfg: dict[str, Any] | None, lim: SMLimits | None,
         calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kernel": kernel,
        "cls": cls,
        # 深窗指标(未开放)→ None,UI 显示 "—";有 ncu 离线标定表则填标定值(#4)
        "occupancy_pct": None,
        "tensor_pct": None,
        "l2_hit_pct": None,
        "dram_gbps": None,
        "metrics_source": None,
        "grid": None, "block": None,
        "regs_per_thread": None, "smem_static": None, "smem_dynamic": None,
        "occupancy_theoretical_pct": None,
        "limiter": None,
        "wave_quant": None,
    }
    # #4:ncu 离线标定查表(按 mangled 名精确 join;标定的是 decode M=1 口径)
    calib = calib_lookup(calibration, kernel)
    if calib:
        row["l2_hit_pct"] = calib.get("l2_hit_pct")
        row["dram_gbps"] = calib.get("dram_gbps")
        row["metrics_source"] = "calibrated"
    if not cfg:
        return row
    grid = cfg.get("grid") or [0, 0, 0]
    block = cfg.get("block") or [0, 0, 0]
    # 原始配置先透传(读不到 SM 上限时面板至少能看启动配置)
    row.update({
        "grid": grid, "block": block,
        "regs_per_thread": cfg.get("regs"),
        "smem_static": cfg.get("static_smem"),
        "smem_dynamic": cfg.get("dyn_smem"),
    })
    grid_blocks = int(grid[0]) * int(grid[1]) * int(grid[2])
    block_threads = int(block[0]) * int(block[1]) * int(block[2])
    if lim is None or grid_blocks <= 0 or block_threads <= 0:
        return row
    regs = int(cfg.get("regs") or 0)
    smem = int(cfg.get("static_smem") or 0) + int(cfg.get("dyn_smem") or 0)

    caps = _blocks_per_sm(lim, block_threads, regs, smem)
    blocks_sm = min(caps.values())
    warps_per_block = max(1, math.ceil(block_threads / _WARP))
    max_warps_sm = lim.max_threads_per_sm // _WARP
    theo_occ = min(100.0, 100.0 * blocks_sm * warps_per_block / max_warps_sm) if blocks_sm > 0 else 0.0

    # 受限资源:grid 打不满卡优先(它让一切资源上限失去意义);否则看谁把 blocks/SM
    # 压到低于"线程上限允许的值"(threads 项由 block 大小决定,算设计选择不算缺陷)。
    if grid_blocks < lim.mp_count:
        limiter = "grid"
    elif caps["registers"] < caps["threads"] and caps["registers"] <= caps["smem"]:
        limiter = "registers"
    elif caps["smem"] < caps["threads"] and caps["smem"] < caps["registers"]:
        limiter = "smem"
    else:
        limiter = "none"

    # wave 量化:一个 wave = 全卡同时能常驻的 block 数;最后一波的占比低 = 尾部空转
    wave_size = lim.mp_count * max(1, blocks_sm)
    waves = math.ceil(grid_blocks / wave_size)
    remainder = grid_blocks % wave_size
    last_wave_pct = 100.0 if remainder == 0 else 100.0 * remainder / wave_size

    row.update({
        "occupancy_theoretical_pct": round(theo_occ, 1),
        "limiter": limiter,
        "wave_quant": {"waves": waves, "last_wave_pct": round(last_wave_pct, 1)},
    })
    return row


def build_deep_profile(last_result: dict[str, Any] | None,
                       sm_limits: SMLimits | None,
                       arch: dict[str, Any] | None = None,
                       peak: GPUPeak | None = None,
                       sm_clock_hz: float | None = None,
                       calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    """由最近一窗 Deep Evidence 结果组装 deep profile 响应。fail-closed:无数据 → available=False。"""
    if not last_result or not last_result.get("available"):
        return {"available": False,
                "error": "还没有采样窗数据(等首个 PC Sampling 窗口写入)", "kernels": []}
    table = last_result.get("kernel_table") or []
    kernels = [
        _row(r.get("kernel", ""), r.get("cls", ""), r.get("launch_cfg"), sm_limits,
             calibration=calibration)
        for r in table
    ]
    # #2/#3 family 级 roofline 估算(纯软件,与行级 occupancy 估算互不依赖)
    roofline_est = estimate_families(
        last_result, arch, peak,
        mp_count=(sm_limits.mp_count if sm_limits else None),
        sm_clock_hz=sm_clock_hz,
    )
    return {
        "available": True,
        "collected_at": int(time.time() * 1000),  # 组装时刻;数据本体是最近一窗
        "pause_ms": 0,     # 常驻 launch 采集,不暂停服务
        "passes": 0,       # 无 kernel 重放(深窗指标未开放)
        "kernels": kernels,
        "roofline_est": roofline_est,
    }
