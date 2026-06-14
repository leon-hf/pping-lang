"""Microbenchmarks — 测各热路径单次调用耗时。

运行：
    python -m pping_lang.bench.microbench

预算（pre-impl-rfc §3.1 + design §8.3）：
    push_metric: < 5μs        (热路径，O(1) deque.append)
    record/collect: < 50μs    (含完整 SchedulerStats 提取)
"""
from __future__ import annotations

import time
from collections.abc import Callable
from types import SimpleNamespace

from pping_lang.clock import wall_ns
from pping_lang.collector.vllm_stats import VllmStatsCollector
from pping_lang.hardware import GPUPeak
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis, MetricPoint


class _NoOpSink(Sink):
    """Discards everything — measures upstream code path only."""

    def _flush(self, metrics: list[MetricPoint], diags: list[Diagnosis]) -> None:
        pass


def _measure(fn: Callable[[], None], n: int, name: str) -> dict:
    """Throughput-style measurement (total time / N).

    Per-call timing on Windows has ~16ms resolution from monotonic_ns —
    most calls register as 0ns. Cumulative measurement avoids this.
    Returns mean ns/op + a sampled distribution for tail visibility.
    """
    # Warmup
    for _ in range(min(100, n // 10)):
        fn()

    # Throughput pass
    t0 = time.perf_counter_ns()
    for _ in range(n):
        fn()
    total_ns = time.perf_counter_ns() - t0
    mean_ns = total_ns // n

    # Sampled distribution: 100 batches of n/100 to capture tails
    batch = max(1, n // 100)
    batch_means = []
    for _ in range(100):
        bt0 = time.perf_counter_ns()
        for _ in range(batch):
            fn()
        batch_means.append((time.perf_counter_ns() - bt0) / batch)
    batch_means.sort()

    return {
        "name": name,
        "n": n,
        "mean_ns": int(mean_ns),
        "p50_ns": int(batch_means[50]),
        "p95_ns": int(batch_means[95]),
        "p99_ns": int(batch_means[99]),
        "max_ns": int(batch_means[-1]),
    }


def bench_push_metric(n: int = 50_000) -> dict:
    """Hot path: deque.append. RFC §3 budget: <5μs p95."""
    sink = _NoOpSink(flush_interval_s=10.0)
    p = MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=75.0)
    try:
        return _measure(lambda: sink.push_metric(p), n, "push_metric")
    finally:
        sink.close()


def bench_collect_scheduler(n: int = 5_000) -> dict:
    """Full vLLM stats extraction. Budget: <100μs p95."""
    sink = _NoOpSink(flush_interval_s=10.0)
    collector = VllmStatsCollector(
        sink, gpu_peak=GPUPeak(bf16_tflops=989.0, mem_bw_gbs=3350.0),
    )
    s = SimpleNamespace(
        num_running_reqs=8, num_waiting_reqs=2, num_skipped_waiting_reqs=0,
        current_wave=1, kv_cache_usage=0.45,
        prefix_cache_stats=SimpleNamespace(queries=100, hits=70),
        kv_cache_eviction_events=[],
        spec_decoding_stats=None,
        cudagraph_stats=SimpleNamespace(
            num_unpadded_tokens=300, num_padded_tokens=1000,
            num_paddings=8, runtime_mode="PIECEWISE",
        ),
        perf_stats=SimpleNamespace(
            num_flops_per_gpu=int(5e12),
            num_read_bytes_per_gpu=int(1e9),
            num_write_bytes_per_gpu=int(5e8),
            debug_stats=None,
        ),
        waiting_lora_adapters={},
        running_lora_adapters={},
    )
    try:
        return _measure(lambda: collector.collect(s, None), n, "collect_scheduler")
    finally:
        sink.close()


def bench_record_overhead(n: int = 5_000) -> dict:
    """End-to-end: collect + push overhead heartbeat (mimics plugin.record)."""
    sink = _NoOpSink(flush_interval_s=10.0)
    collector = VllmStatsCollector(
        sink, gpu_peak=GPUPeak(bf16_tflops=989.0, mem_bw_gbs=3350.0),
    )
    s = SimpleNamespace(
        num_running_reqs=8, num_waiting_reqs=2, kv_cache_usage=0.45,
        prefix_cache_stats=None, kv_cache_eviction_events=None,
        spec_decoding_stats=None, cudagraph_stats=None, perf_stats=None,
        waiting_lora_adapters=None, running_lora_adapters=None,
    )
    it = SimpleNamespace(
        num_generation_tokens=50,
        prompt_token_stats=None,
        num_preempted_reqs=0, num_corrupted_reqs=0,
        time_to_first_tokens_iter=[], inter_token_latencies_iter=[],
        finished_requests=[],
    )

    def one_record():
        collector.collect(s, it)
        sink.push_metric(MetricPoint(
            ts_ns=wall_ns(), name="pping_lang.overhead.record_us", value=1.0,
        ))

    try:
        return _measure(one_record, n, "record_overhead")
    finally:
        sink.close()


def main() -> None:
    print("=" * 72)
    print("pping-lang microbench — 热路径单次调用耗时")
    print("=" * 72)
    header = f"{'name':<22} {'n':>6} {'mean':>10} {'p50':>10} {'p95':>10} {'p99':>10} {'max':>10}"
    print(header)
    print("-" * len(header))
    for fn in [bench_push_metric, bench_collect_scheduler, bench_record_overhead]:
        s = fn()
        print(
            f"{s['name']:<22} {s['n']:>6} "
            f"{_fmt_ns(s['mean_ns']):>10} "
            f"{_fmt_ns(s['p50_ns']):>10} "
            f"{_fmt_ns(s['p95_ns']):>10} "
            f"{_fmt_ns(s['p99_ns']):>10} "
            f"{_fmt_ns(s['max_ns']):>10}"
        )
    print()
    print("预算（pre-impl-rfc §3.1 + design §8.3）：")
    print("  push_metric:   p95 < 5μs   (热路径 O(1))")
    print("  collect:       p95 < 100μs (完整 SchedulerStats 提取)")
    print("  record:        p95 < 50μs  (collect + heartbeat push)")


def _fmt_ns(ns: int) -> str:
    if ns < 1_000:
        return f"{ns}ns"
    if ns < 1_000_000:
        return f"{ns / 1000:.1f}μs"
    return f"{ns / 1_000_000:.2f}ms"


if __name__ == "__main__":
    main()
