"""解析式操作点:从 token 计数 + 模型参数 + 硬件峰值算出 regime / MFU / AI。

为什么解析:vLLM 0.21 不发 perf_stats(flops/bytes),`vllm.perf.mfu_ratio` 是死的。
但 Roofline 操作点能从已有量解析换算(与 `/api/roofline` 的 analytical 路径同口径):
    AI(算术强度)  ≈ 2·tokens / dtype_bytes          (FLOPs/Byte;params 在 flops/bytes 里约掉)
    吞吐 TFLOP/s    ≈ 2·params·tokens / dt / 1e12
    脊点 ridge      = peak_compute_tflops / peak_mem_bw_tbs   (FLOPs/Byte)
    regime          = AI < ridge → memory_bound,否则 compute_bound
    MFU             = 实测吞吐 / 峰值算力

供诊断引擎每窗口算一次:得到 regime(传给 evaluate)+ MFU(覆盖死的 mfu 指标,喂 D1c/D3a)。
数据不足/缺参数时全返 None → 相关规则优雅不触发。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatingPoint:
    ai: float | None = None              # 算术强度中位数 (FLOPs/Byte)
    ridge: float | None = None           # 脊点 (FLOPs/Byte)
    achieved_tflops: float | None = None # 实测吞吐中位数 (TFLOP/s)
    mfu: float | None = None             # achieved / peak_compute (0-1)
    regime: str | None = None            # "memory_bound" | "compute_bound" | None


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
) -> OperatingPoint:
    """token_points = [(每 step 的 prompt+gen tokens, ts_ns), ...](按时间)。

    需相邻两点求 dt;<2 点或缺 params/peak → 返回全 None(优雅降级)。
    """
    if (
        params is None or not peak_compute_tflops or not peak_mem_bw_tbs
        or dtype_bytes <= 0 or len(token_points) < 2
    ):
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

    ridge = peak_compute_tflops / peak_mem_bw_tbs   # FLOPs/Byte
    regime = "memory_bound" if ai < ridge else "compute_bound"
    mfu = achieved / peak_compute_tflops
    return OperatingPoint(
        ai=ai, ridge=ridge, achieved_tflops=achieved, mfu=mfu, regime=regime,
    )
