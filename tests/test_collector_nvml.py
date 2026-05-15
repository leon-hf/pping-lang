"""NvmlSampler 测试 — 用 fake nvml 模块注入 + 验证 7 项 GPU metric 写入。

故障路径（NVML 不可用）通过实际无 GPU 的测试环境隐式覆盖。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from pping_lang.collector.nvml import NvmlSampler, detect_first_gpu_name
from pping_lang.metrics_catalog import M
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis, MetricPoint


class _CollectingSink(Sink):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.flushed_metrics: list[MetricPoint] = []
        self.flushed_diags: list[Diagnosis] = []

    def _flush(self, metrics, diags):
        self.flushed_metrics.extend(metrics)
        self.flushed_diags.extend(diags)


class _FakeNvml:
    """Mock pynvml module — implements only what NvmlSampler uses."""

    NVML_TEMPERATURE_GPU = 0
    NVML_CLOCK_SM = 0
    NVML_CLOCK_MEM = 1

    def __init__(self, gpu_count: int = 1, gpu_name: str = "NVIDIA H100 80GB HBM3"):
        self._count = gpu_count
        self._name = gpu_name
        self.init_calls = 0
        self.shutdown_calls = 0
        # canned readings
        self.util_gpu = 75
        self.util_mem = 60
        self.mem_used = 40 * 1024**3   # 40 GB
        self.power_mw = 350_000        # 350 W
        self.temp = 65
        self.sm_clock = 1500
        self.mem_clock = 2000

    def nvmlInit(self):
        self.init_calls += 1

    def nvmlShutdown(self):
        self.shutdown_calls += 1

    def nvmlDeviceGetCount(self):
        return self._count

    def nvmlDeviceGetHandleByIndex(self, i):
        return ("handle", i)

    def nvmlDeviceGetName(self, h):
        return self._name

    def nvmlDeviceGetUtilizationRates(self, h):
        return SimpleNamespace(gpu=self.util_gpu, memory=self.util_mem)

    def nvmlDeviceGetMemoryInfo(self, h):
        return SimpleNamespace(used=self.mem_used, total=80 * 1024**3, free=40 * 1024**3)

    def nvmlDeviceGetPowerUsage(self, h):
        return self.power_mw

    def nvmlDeviceGetTemperature(self, h, sensor):
        return self.temp

    def nvmlDeviceGetClockInfo(self, h, clock):
        return self.sm_clock if clock == self.NVML_CLOCK_SM else self.mem_clock


def test_sampler_disabled_when_no_gpus():
    sink = _CollectingSink(flush_interval_s=10.0)
    sampler = NvmlSampler(sink, nvml_module=_FakeNvml(gpu_count=0))
    try:
        assert not sampler.enabled
        assert sampler.num_gpus == 0
        sampler.start()  # no-op
    finally:
        sampler.stop()
        sink.close()
    assert sink.flushed_metrics == []


def test_sampler_pushes_all_seven_gpu_metrics():
    sink = _CollectingSink(flush_interval_s=10.0)
    fake = _FakeNvml(gpu_count=1)
    sampler = NvmlSampler(sink, nvml_module=fake, interval_s=0.05)
    try:
        assert sampler.enabled
        sampler.start()
        time.sleep(0.15)  # let it sample 2-3 times
    finally:
        sampler.stop()
        sink.close()

    names = {m.name for m in sink.flushed_metrics}
    expected = {
        M.GPU_UTIL_PCT,
        M.GPU_MEM_UTIL_PCT,
        M.GPU_MEM_USED_BYTES,
        M.GPU_POWER_W,
        M.GPU_TEMP_C,
        M.GPU_SM_CLOCK_MHZ,
        M.GPU_MEM_CLOCK_MHZ,
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_sampler_value_scaling():
    """power 是 mW → 我们存 W；其他单位直接传。"""
    sink = _CollectingSink(flush_interval_s=10.0)
    fake = _FakeNvml(gpu_count=1)
    sampler = NvmlSampler(sink, nvml_module=fake, interval_s=0.05)
    try:
        sampler.start()
        time.sleep(0.15)
    finally:
        sampler.stop()
        sink.close()

    by_name = {m.name: m.value for m in sink.flushed_metrics}
    assert by_name[M.GPU_UTIL_PCT] == 75.0
    assert by_name[M.GPU_MEM_USED_BYTES] == float(40 * 1024**3)
    assert by_name[M.GPU_POWER_W] == 350.0  # 350_000 mW → 350 W
    assert by_name[M.GPU_TEMP_C] == 65.0


def test_sampler_multi_gpu():
    sink = _CollectingSink(flush_interval_s=10.0)
    fake = _FakeNvml(gpu_count=4)
    sampler = NvmlSampler(sink, nvml_module=fake, interval_s=0.05)
    try:
        assert sampler.num_gpus == 4
        sampler.start()
        time.sleep(0.10)
    finally:
        sampler.stop()
        sink.close()

    # Each GPU contributes its own gpu_idx
    util_metrics = [m for m in sink.flushed_metrics if m.name == M.GPU_UTIL_PCT]
    indices_seen = {m.gpu_idx for m in util_metrics}
    assert indices_seen == {0, 1, 2, 3}


def test_sampler_engine_index_propagated():
    sink = _CollectingSink(flush_interval_s=10.0)
    fake = _FakeNvml(gpu_count=1)
    sampler = NvmlSampler(sink, engine_index=2, nvml_module=fake, interval_s=0.05)
    try:
        sampler.start()
        time.sleep(0.10)
    finally:
        sampler.stop()
        sink.close()
    assert all(m.engine_idx == 2 for m in sink.flushed_metrics)


def test_sampler_shutdown_called_on_stop():
    sink = _CollectingSink(flush_interval_s=10.0)
    fake = _FakeNvml(gpu_count=1)
    sampler = NvmlSampler(sink, nvml_module=fake)
    sampler.start()
    sampler.stop()
    sink.close()
    assert fake.init_calls == 1
    assert fake.shutdown_calls == 1


def test_sampler_stop_idempotent():
    sink = _CollectingSink(flush_interval_s=10.0)
    fake = _FakeNvml(gpu_count=1)
    sampler = NvmlSampler(sink, nvml_module=fake)
    sampler.start()
    sampler.stop()
    sampler.stop()  # must not raise
    sink.close()


def test_detect_first_gpu_name_with_fake():
    fake = _FakeNvml(gpu_count=1, gpu_name="NVIDIA H100 80GB HBM3")
    name = detect_first_gpu_name(nvml_module=fake)
    assert name == "NVIDIA H100 80GB HBM3"


def test_detect_first_gpu_name_no_gpus():
    fake = _FakeNvml(gpu_count=0)
    assert detect_first_gpu_name(nvml_module=fake) is None


def test_sampler_continues_after_per_sample_exception():
    """单个 GPU 的采样异常不应该挂掉整个采样循环。"""
    sink = _CollectingSink(flush_interval_s=10.0)

    class _BrokenNvml(_FakeNvml):
        def nvmlDeviceGetUtilizationRates(self, h):
            # Always fails for this method
            raise RuntimeError("simulated nvml error")

    fake = _BrokenNvml(gpu_count=1)
    sampler = NvmlSampler(sink, nvml_module=fake, interval_s=0.05)
    try:
        sampler.start()
        time.sleep(0.15)  # multiple samples should be attempted
    finally:
        sampler.stop()
        sink.close()
    # Sampler still ran (didn't crash), just no metrics pushed for that GPU
    assert sink.flushed_metrics == []
    # Sampler still alive
    assert fake.shutdown_calls == 1
