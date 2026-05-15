"""Performance regression tests — assert 热路径 stays under budget。

按 pre-impl-rfc §3.1 + design §8.3 预算 2-3× 给 buffer 防 CI 抖动。
失败说明热路径回归到预算外，需要排查（不是代码 bug 也是性能 bug）。
"""
from __future__ import annotations

from pping_lang.bench.microbench import (
    bench_collect_scheduler,
    bench_push_metric,
    bench_record_overhead,
)

# 实测在 Windows + Python 3.12（典型笔记本）：
#   push_metric: ~150ns mean
#   collect:     ~22μs mean
#   record:      ~12μs mean
# 这里用预算 2-3× 给 CI 共享 runner 的抖动留 buffer。


def test_push_metric_under_budget():
    """RFC §3.1: push_metric 必须 <5μs。CI 用 10μs 给 buffer。"""
    s = bench_push_metric(n=10_000)
    assert s["mean_ns"] < 10_000, (
        f"push_metric mean {s['mean_ns']}ns exceeds 10μs budget; "
        f"hot path regressed (target: 5μs from RFC §3.1)"
    )
    assert s["p99_ns"] < 20_000, (
        f"push_metric p99 {s['p99_ns']}ns exceeds 20μs"
    )


def test_collect_scheduler_under_budget():
    """完整 SchedulerStats 提取（含派生指标）应 <100μs。CI buffer 200μs。"""
    s = bench_collect_scheduler(n=2_000)
    assert s["mean_ns"] < 200_000, (
        f"collect_scheduler mean {s['mean_ns']}ns exceeds 200μs budget"
    )


def test_record_overhead_under_budget():
    """plugin.record() 类型工作：collect + push 心跳，应 <50μs。CI buffer 100μs。"""
    s = bench_record_overhead(n=2_000)
    assert s["mean_ns"] < 100_000, (
        f"record_overhead mean {s['mean_ns']}ns exceeds 100μs budget; "
        f"plugin hot path regressed (target: 50μs from design §8.3)"
    )
