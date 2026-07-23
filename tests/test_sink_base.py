"""Sink ABC 测试 — lifecycle、drop counter、flush 异常隔离。"""
from __future__ import annotations

import time

from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis, MetricPoint


class _CollectingSink(Sink):
    """In-memory sink for test introspection."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.flushed_metrics: list[MetricPoint] = []
        self.flushed_diags: list[Diagnosis] = []

    def _flush(self, metrics, diags):
        self.flushed_metrics.extend(metrics)
        self.flushed_diags.extend(diags)


class _FailingSink(Sink):
    def _flush(self, metrics, diags):
        raise RuntimeError("simulated failure")


def _mp(value: float = 1.0, ts: int = 0):
    return MetricPoint(ts_ns=ts, name="gpu.utilization_pct", value=value)


def _diag(rule: str = "r"):
    return Diagnosis(
        ts_ns=0, rule_id=rule, severity="warning",
        triggered_value=1.0, threshold=2.0, window_seconds=10,
        message="m", suggestion="s",
    )


def test_push_metric_then_bg_flush():
    sink = _CollectingSink(flush_interval_s=0.05)
    try:
        for i in range(5):
            sink.push_metric(_mp(value=float(i)))
        time.sleep(0.15)  # let bg thread flush at least once
    finally:
        sink.close()
    assert len(sink.flushed_metrics) == 5


def test_push_diagnosis_separate_queue():
    sink = _CollectingSink(flush_interval_s=0.05)
    try:
        sink.push_diagnosis(_diag("a"))
        sink.push_diagnosis(_diag("b"))
        time.sleep(0.15)
    finally:
        sink.close()
    assert len(sink.flushed_diags) == 2
    assert {d.rule_id for d in sink.flushed_diags} == {"a", "b"}


def test_overflow_drops_oldest_and_increments_counter():
    sink = _CollectingSink(queue_size=3, flush_interval_s=10.0)
    try:
        for i in range(5):  # capacity 3 → 2 should be dropped
            sink.push_metric(_mp(value=float(i)))
    finally:
        sink.close()
    assert sink.dropped_metrics == 2
    assert len(sink.flushed_metrics) == 3
    values = sorted(m.value for m in sink.flushed_metrics)
    assert values == [2.0, 3.0, 4.0]  # oldest 0,1 evicted by deque maxlen


def test_persist_decimation_thins_inflow_keeps_live_full_rate():
    """重压下(队列满)持久化队列改自适应降采样,而非尾丢;实时环仍全速。"""
    sink = _CollectingSink(queue_size=2048, flush_interval_s=1000.0)
    # 停掉后台 flush 线程,让队列无法被抽干 —— 隔离出降采样逻辑
    sink._stop.set()
    sink._wake.set()
    sink._thread.join(timeout=2)
    try:
        # 直接灌到高水位(绕过 push 的降采样计数),建立背压前置条件
        for i in range(sink._persist_hi):
            sink._metric_q.append(_mp(ts=i))
        assert sink.queue_depth == sink._persist_hi
        assert sink.downsampled_metrics == 0  # 还没经过 push 的降采样路径

        # 高水位之上猛灌一波 → 必然降采样,且队列不超上限
        for i in range(2000):
            sink.push_metric(_mp(value=float(i), ts=10_000 + i))

        assert sink.downsampled_metrics > 0          # 入流被有意稀释
        assert sink.queue_depth <= 2048              # 队列有界
        # 实时读路径全速：latest 反映最后一次 push(降采样只作用于持久化队列)
        assert sink.latest("gpu.utilization_pct") == (1999.0, 11_999)
    finally:
        sink.close()


def test_small_queue_keeps_plain_tail_drop_no_decimation():
    """小队列(< 降采样阈值)维持原尾丢语义,不触发降采样。"""
    sink = _CollectingSink(queue_size=8, flush_interval_s=1000.0)
    sink._stop.set()
    sink._wake.set()
    sink._thread.join(timeout=2)
    try:
        for i in range(40):
            sink.push_metric(_mp(value=float(i), ts=i))
        assert sink.downsampled_metrics == 0   # 小队列不降采样
        assert sink.dropped_metrics > 0        # 纯尾丢
    finally:
        sink.close()


def test_diag_overflow_counter_independent():
    sink = _CollectingSink(diag_queue_size=2, flush_interval_s=10.0)
    try:
        for _ in range(5):
            sink.push_diagnosis(_diag())
    finally:
        sink.close()
    assert sink.dropped_diags == 3
    assert sink.dropped_metrics == 0


def test_close_idempotent():
    sink = _CollectingSink(flush_interval_s=0.05)
    sink.close()
    sink.close()  # must not raise / hang


def test_flush_exception_isolated_and_counted():
    sink = _FailingSink(flush_interval_s=0.05)
    try:
        sink.push_metric(_mp())
        time.sleep(0.15)
    finally:
        sink.close()
    # _flush raised but Sink is still alive; counter incremented
    assert sink.flush_errors >= 1


def test_close_performs_final_flush():
    sink = _CollectingSink(flush_interval_s=10.0)  # bg won't fire in test window
    sink.push_metric(_mp(value=42.0))
    sink.close()
    assert len(sink.flushed_metrics) == 1
    assert sink.flushed_metrics[0].value == 42.0


def test_no_flush_call_when_buffers_empty():
    """Empty drain should not invoke _flush — saves a roundtrip per tick."""
    sink = _CollectingSink(flush_interval_s=0.05)
    try:
        time.sleep(0.15)
    finally:
        sink.close()
    assert len(sink.flushed_metrics) == 0
    assert sink.flush_errors == 0


def test_queue_depth_property():
    sink = _CollectingSink(flush_interval_s=10.0)
    try:
        assert sink.queue_depth == 0
        sink.push_metric(_mp())
        sink.push_metric(_mp())
        sink.push_diagnosis(_diag())
        assert sink.queue_depth == 3
    finally:
        sink.close()
