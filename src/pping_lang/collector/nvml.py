"""NVML 采样线程 — 在 EngineCore 进程内按 GPU index 周期采样。

按 RFC §4.3 / §2.2 采集：utilization, memory, power, temp, sm_clock, mem_clock。

故障保护：NVML init 失败或 pynvml 未安装时降级到 no-op + warning，永不阻塞。
启动 / 关闭幂等。

依赖注入：构造时可传 `nvml_module` 替换 pynvml（测试用）。
"""
from __future__ import annotations

import logging
import os
from threading import Event, Thread
from typing import Any

from pping_lang.clock import wall_ns
from pping_lang.metrics_catalog import M
from pping_lang.sink.base import Sink
from pping_lang.types import MetricPoint

logger = logging.getLogger(__name__)

DEFAULT_NVML_INTERVAL_S = 0.1  # 100ms per RFC §4.3


def _parse_visible_gpus() -> list[int] | None:
    """解析 CUDA_VISIBLE_DEVICES，返回此进程能看到的 GPU 物理 index。

    - 未设置或为空：返回 None，表示"采全部"
    - 解析失败：返回 None + warning
    """
    spec = os.environ.get("CUDA_VISIBLE_DEVICES")
    if spec is None or spec == "":
        return None
    try:
        return [int(x) for x in spec.split(",") if x.strip()]
    except ValueError:
        logger.warning("[pping-lang] failed to parse CUDA_VISIBLE_DEVICES=%r", spec)
        return None


class NvmlSampler:
    """Periodic NVML sampler. Pushes per-GPU metrics into a Sink."""

    def __init__(
        self,
        sink: Sink,
        engine_index: int = 0,
        interval_s: float = DEFAULT_NVML_INTERVAL_S,
        nvml_module: Any | None = None,
    ) -> None:
        self._sink = sink
        self._engine_index = engine_index
        self._interval = interval_s
        self._stop = Event()
        self._thread: Thread | None = None
        self._gpu_handles: list[tuple[int, Any]] = []  # (gpu_idx, handle)
        self._enabled = False
        self._nvml: Any = None
        self._init(nvml_module)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def num_gpus(self) -> int:
        return len(self._gpu_handles)

    def _init(self, nvml_module: Any | None) -> None:
        try:
            nvml = nvml_module if nvml_module is not None else _import_pynvml()
            if nvml is None:
                logger.warning("[pping-lang] pynvml not installed, GPU metrics disabled")
                return
            nvml.nvmlInit()
            count = nvml.nvmlDeviceGetCount()
            visible = _parse_visible_gpus()
            indices = list(range(count)) if visible is None else [i for i in visible if i < count]
            for i in indices:
                self._gpu_handles.append((i, nvml.nvmlDeviceGetHandleByIndex(i)))
            self._nvml = nvml
            self._enabled = bool(self._gpu_handles)
            if self._enabled:
                logger.info(
                    "[pping-lang] NVML init: %d GPU(s), interval=%.0fms",
                    self.num_gpus, self._interval * 1000,
                )
            else:
                logger.warning("[pping-lang] NVML init: 0 GPU(s) visible, disabled")
        except Exception as e:
            logger.warning("[pping-lang] NVML unavailable, GPU metrics disabled: %s", e)
            self._enabled = False

    def start(self) -> None:
        if not self._enabled or self._thread is not None:
            return
        self._thread = Thread(target=self._run, daemon=True, name="NvmlSampler")
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=self._interval * 4)
        self._thread = None
        if self._enabled and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
        self._enabled = False

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            ts = wall_ns()
            for gpu_idx, handle in self._gpu_handles:
                try:
                    self._sample_one(ts, gpu_idx, handle)
                except Exception:
                    logger.exception(
                        "[pping-lang] NVML sample failed for GPU %d", gpu_idx,
                    )

    def _sample_one(self, ts: int, gpu_idx: int, handle: Any) -> None:
        nvml = self._nvml
        push = self._sink.push_metric
        ei = self._engine_index

        util = nvml.nvmlDeviceGetUtilizationRates(handle)
        push(MetricPoint(ts, M.GPU_UTIL_PCT, float(util.gpu), ei, gpu_idx))
        push(MetricPoint(ts, M.GPU_MEM_UTIL_PCT, float(util.memory), ei, gpu_idx))

        mem = nvml.nvmlDeviceGetMemoryInfo(handle)
        push(MetricPoint(ts, M.GPU_MEM_USED_BYTES, float(mem.used), ei, gpu_idx))
        # Capacity occupancy as a derived ratio so the dashboard doesn't have
        # to remember total VRAM. mem.total is fixed per-device — read at
        # every sample is cheap and means the metric is self-contained.
        if mem.total > 0:
            push(MetricPoint(
                ts, M.GPU_MEM_USED_PCT,
                100.0 * float(mem.used) / float(mem.total),
                ei, gpu_idx,
            ))

        power_mw = nvml.nvmlDeviceGetPowerUsage(handle)
        push(MetricPoint(ts, M.GPU_POWER_W, power_mw / 1000.0, ei, gpu_idx))

        temp = nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
        push(MetricPoint(ts, M.GPU_TEMP_C, float(temp), ei, gpu_idx))

        sm_clk = nvml.nvmlDeviceGetClockInfo(handle, nvml.NVML_CLOCK_SM)
        push(MetricPoint(ts, M.GPU_SM_CLOCK_MHZ, float(sm_clk), ei, gpu_idx))

        mem_clk = nvml.nvmlDeviceGetClockInfo(handle, nvml.NVML_CLOCK_MEM)
        push(MetricPoint(ts, M.GPU_MEM_CLOCK_MHZ, float(mem_clk), ei, gpu_idx))


def _import_pynvml() -> Any | None:
    try:
        import pynvml
        return pynvml
    except ImportError:
        return None


def detect_first_gpu_name(nvml_module: Any | None = None) -> str | None:
    """Lookup the first visible GPU's name via NVML. Returns None if unavailable.

    Used at plugin init to look up GPU peak performance for MFU calculation.
    """
    try:
        nvml = nvml_module if nvml_module is not None else _import_pynvml()
        if nvml is None:
            return None
        nvml.nvmlInit()
        if nvml.nvmlDeviceGetCount() == 0:
            return None
        h = nvml.nvmlDeviceGetHandleByIndex(0)
        name = nvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode()
        return str(name)
    except Exception as e:
        logger.warning("[pping-lang] could not detect GPU name: %s", e)
        return None
