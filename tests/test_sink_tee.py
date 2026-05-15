"""TeeSink — 多 Sink fan-out。"""
from __future__ import annotations

import time

from pping_lang.sink.base import Sink
from pping_lang.sink.tee import TeeSink
from pping_lang.types import Diagnosis, MetricPoint


class _CollectingSink(Sink):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.flushed_metrics: list[MetricPoint] = []
        self.flushed_diags: list[Diagnosis] = []

    def _flush(self, metrics, diags):
        self.flushed_metrics.extend(metrics)
        self.flushed_diags.extend(diags)


def _mp(name="x", value=1.0):
    return MetricPoint(ts_ns=1, name=name, value=value)


def _diag():
    return Diagnosis(
        ts_ns=1, rule_id="r", severity="warning",
        triggered_value=1.0, threshold=2.0, window_seconds=10,
        message="m", suggestion="s",
    )


def test_metrics_fan_out_to_all_children():
    a = _CollectingSink(flush_interval_s=10.0)
    b = _CollectingSink(flush_interval_s=10.0)
    tee = TeeSink(a, b)
    try:
        tee.push_metric(_mp())
        tee.push_metric(_mp())
    finally:
        tee.close()
    assert len(a.flushed_metrics) == 2
    assert len(b.flushed_metrics) == 2


def test_diagnosis_fan_out_to_all_children():
    a = _CollectingSink(flush_interval_s=10.0)
    b = _CollectingSink(flush_interval_s=10.0)
    tee = TeeSink(a, b)
    try:
        tee.push_diagnosis(_diag())
    finally:
        tee.close()
    assert len(a.flushed_diags) == 1
    assert len(b.flushed_diags) == 1


def test_tee_close_closes_all_children():
    a = _CollectingSink(flush_interval_s=10.0)
    b = _CollectingSink(flush_interval_s=10.0)
    tee = TeeSink(a, b)
    tee.close()
    assert a._closed
    assert b._closed


def test_dropped_counter_aggregates():
    a = _CollectingSink(queue_size=2, flush_interval_s=10.0)
    b = _CollectingSink(queue_size=2, flush_interval_s=10.0)
    tee = TeeSink(a, b)
    try:
        for _ in range(5):
            tee.push_metric(_mp())  # 3 dropped per child
    finally:
        tee.close()
    # Each child dropped 3 → total 6
    assert tee.dropped_metrics == a.dropped_metrics + b.dropped_metrics
    assert tee.dropped_metrics == 6


def test_queue_depth_aggregates():
    a = _CollectingSink(flush_interval_s=10.0)
    b = _CollectingSink(flush_interval_s=10.0)
    tee = TeeSink(a, b)
    try:
        tee.push_metric(_mp())
        tee.push_metric(_mp())
        # Each child has 2 metrics queued → total 4
        assert tee.queue_depth == 4
    finally:
        tee.close()


def test_single_child_works():
    a = _CollectingSink(flush_interval_s=10.0)
    tee = TeeSink(a)
    try:
        tee.push_metric(_mp())
    finally:
        tee.close()
    assert len(a.flushed_metrics) == 1
