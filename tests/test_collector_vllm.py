"""VllmStatsCollector 测试 — 字段映射 + 派生指标 + defensive 字段访问。"""
from __future__ import annotations

from types import SimpleNamespace

from pping_lang.collector.vllm_stats import VllmStatsCollector
from pping_lang.hardware import GPUPeak
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


def _by_name(metrics: list[MetricPoint], name: str) -> list[MetricPoint]:
    return [m for m in metrics if m.name == name]


def _value(metrics: list[MetricPoint], name: str) -> float:
    matches = _by_name(metrics, name)
    assert matches, f"no metric named {name!r}; available: {sorted({m.name for m in metrics})}"
    return matches[0].value


def _make_scheduler_stats(**overrides):
    base = dict(
        num_running_reqs=10,
        num_waiting_reqs=2,
        num_skipped_waiting_reqs=0,
        current_wave=3,
        kv_cache_usage=0.45,
        prefix_cache_stats=SimpleNamespace(queries=100, hits=67),
        kv_cache_eviction_events=[],
        spec_decoding_stats=None,
        cudagraph_stats=None,
        perf_stats=None,
        waiting_lora_adapters={},
        running_lora_adapters={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_iteration_stats(**overrides):
    base = dict(
        num_generation_tokens=128,
        prompt_token_stats=SimpleNamespace(total=512, local_cache_hit=64, external_kv_transfer=0),
        num_preempted_reqs=0,
        num_corrupted_reqs=0,
        time_to_first_tokens_iter=[0.123, 0.456],
        inter_token_latencies_iter=[0.05, 0.06, 0.07],
        finished_requests=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_scheduler_basic_fields():
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink, engine_index=0)
    c.collect(_make_scheduler_stats(), None)
    sink.close()
    m = sink.flushed_metrics
    assert _value(m, M.VLLM_SCHEDULER_RUNNING_REQS) == 10.0
    assert _value(m, M.VLLM_SCHEDULER_WAITING_REQS) == 2.0
    assert _value(m, M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO) == 0.45
    assert _value(m, M.VLLM_SCHEDULER_PREFIX_CACHE_HIT_RATIO) == 0.67


def test_prefix_cache_zero_queries_no_division():
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink)
    s = _make_scheduler_stats(
        prefix_cache_stats=SimpleNamespace(queries=0, hits=0),
    )
    c.collect(s, None)
    sink.close()
    # 应该跳过 hit_ratio，避免 zero division
    assert _by_name(sink.flushed_metrics, M.VLLM_SCHEDULER_PREFIX_CACHE_HIT_RATIO) == []


def test_cudagraph_padding_ratio_derived():
    """marquee 卖点：padding_ratio = (padded - unpadded) / padded"""
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink)
    s = _make_scheduler_stats(
        cudagraph_stats=SimpleNamespace(
            num_unpadded_tokens=1000,
            num_padded_tokens=2000,  # padding_ratio = 0.5 (50% wasted)
            num_paddings=8,
            runtime_mode="PIECEWISE",
        ),
    )
    c.collect(s, None)
    sink.close()
    m = sink.flushed_metrics
    assert _value(m, M.VLLM_CUDAGRAPH_UNPADDED_TOKENS) == 1000.0
    assert _value(m, M.VLLM_CUDAGRAPH_PADDED_TOKENS) == 2000.0
    assert _value(m, M.VLLM_CUDAGRAPH_PADDINGS) == 8.0
    assert _value(m, M.VLLM_CUDAGRAPH_PADDING_RATIO) == 0.5


def test_cudagraph_padded_zero_no_division():
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink)
    s = _make_scheduler_stats(
        cudagraph_stats=SimpleNamespace(
            num_unpadded_tokens=0, num_padded_tokens=0,
            num_paddings=0, runtime_mode="FULL",
        ),
    )
    c.collect(s, None)
    sink.close()
    # padded == 0 → 跳过 ratio
    assert _by_name(sink.flushed_metrics, M.VLLM_CUDAGRAPH_PADDING_RATIO) == []


def test_perf_mfu_and_mem_bw_derived():
    """MFU = flops / (peak_flops * dt)；mem_bw_util = (r+w) / (peak_bw * dt)"""
    sink = _CollectingSink(flush_interval_s=10.0)
    # H100 SXM peak: 989 TFLOPS BF16, 3350 GB/s
    peak = GPUPeak(bf16_tflops=989.0, mem_bw_gbs=3350.0)
    c = VllmStatsCollector(sink, gpu_peak=peak)

    # 第一次调用：建立 _last_step_ts_ns，无 derived
    s = _make_scheduler_stats(
        perf_stats=SimpleNamespace(
            num_flops_per_gpu=int(989e12 * 0.01),  # 看起来像 1% 的 1s 工作量
            num_read_bytes_per_gpu=int(3350e9 * 0.01),
            num_write_bytes_per_gpu=0,
            debug_stats=None,
        ),
    )
    c.collect(s, None)
    # 第二次调用之间 sleep 让 dt 不为零（>16ms 避开 Windows 时钟分辨率边界）
    import time as _t
    _t.sleep(0.05)
    c.collect(s, None)
    sink.close()

    m = sink.flushed_metrics
    # raw counters from both collections
    assert len(_by_name(m, M.VLLM_PERF_FLOPS_PER_GPU)) == 2
    # mfu_ratio derived only on second call
    mfu = _by_name(m, M.VLLM_PERF_MFU_RATIO)
    assert len(mfu) == 1
    # 不验证精确值（dt 受 sleep 抖动影响）；验证非负且合理范围
    assert mfu[0].value > 0
    assert mfu[0].value < 100.0  # 上限合理性


def test_perf_no_peak_no_derived():
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink, gpu_peak=None)
    s = _make_scheduler_stats(
        perf_stats=SimpleNamespace(
            num_flops_per_gpu=1000, num_read_bytes_per_gpu=500,
            num_write_bytes_per_gpu=500, debug_stats=None,
        ),
    )
    c.collect(s, None)
    c.collect(s, None)
    sink.close()
    # 无 peak → 不算 MFU/带宽
    assert _by_name(sink.flushed_metrics, M.VLLM_PERF_MFU_RATIO) == []
    assert _by_name(sink.flushed_metrics, M.VLLM_PERF_MEM_BW_UTIL_RATIO) == []


def test_iteration_basic_fields():
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink)
    c.collect(None, _make_iteration_stats())
    sink.close()
    m = sink.flushed_metrics
    assert _value(m, M.VLLM_ITER_GEN_TOKENS) == 128.0
    assert _value(m, M.VLLM_ITER_PROMPT_TOKENS) == 512.0
    assert _value(m, M.VLLM_ITER_PROMPT_CACHE_HIT_TOKENS) == 64.0


def test_iteration_ttft_itl_unit_conversion_to_ms():
    """vLLM 给秒，我们存毫秒。"""
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink)
    it = _make_iteration_stats(
        time_to_first_tokens_iter=[0.5, 1.5],
        inter_token_latencies_iter=[0.025],
    )
    c.collect(None, it)
    sink.close()
    m = sink.flushed_metrics
    ttfts = sorted(p.value for p in _by_name(m, M.VLLM_REQ_TTFT_MS))
    assert ttfts == [500.0, 1500.0]
    itls = [p.value for p in _by_name(m, M.VLLM_REQ_ITL_MS)]
    assert itls == [25.0]


def test_finished_requests_per_request_metrics():
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink)
    fr = SimpleNamespace(
        e2e_latency=2.3,        # → 2300 ms
        queued_time=0.1,        # → 100 ms
        prefill_time=0.5,       # → 500 ms
        inference_time=2.0,     # → 2000 ms
        decode_time=1.5,        # → 1500 ms
        mean_time_per_output_token=0.012,  # → 12 ms
        num_prompt_tokens=128,
        num_generation_tokens=64,
        num_cached_tokens=16,
    )
    it = _make_iteration_stats(finished_requests=[fr])
    c.collect(None, it)
    sink.close()
    m = sink.flushed_metrics
    assert _value(m, M.VLLM_REQ_E2E_LATENCY_MS) == 2300.0
    assert _value(m, M.VLLM_REQ_QUEUED_MS) == 100.0
    assert _value(m, M.VLLM_REQ_TPOT_MS) == 12.0
    assert _value(m, M.VLLM_REQ_PROMPT_TOKENS) == 128.0
    assert _value(m, M.VLLM_REQ_GEN_TOKENS) == 64.0


def test_missing_optional_fields_silently_skipped():
    """Defensive：vLLM 老版本可能字段缺失，应跳过而非崩。"""
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink)
    # SchedulerStats with NO optional sub-objects
    minimal = SimpleNamespace(
        num_running_reqs=1,
        num_waiting_reqs=0,
        kv_cache_usage=0.0,
    )
    c.collect(minimal, None)
    sink.close()
    # Should have basic fields, no crashes
    m = sink.flushed_metrics
    assert _value(m, M.VLLM_SCHEDULER_RUNNING_REQS) == 1.0
    # Optional groups absent → no metrics
    assert _by_name(m, M.VLLM_SPEC_ACCEPTED_TOKENS) == []
    assert _by_name(m, M.VLLM_CUDAGRAPH_PADDING_RATIO) == []


def test_engine_index_propagated_to_metrics():
    sink = _CollectingSink(flush_interval_s=10.0)
    c = VllmStatsCollector(sink, engine_index=7)
    c.collect(_make_scheduler_stats(), None)
    sink.close()
    assert all(m.engine_idx == 7 for m in sink.flushed_metrics)
