"""数据模型测试 — MetricPoint / Diagnosis frozen + slots 行为验证。"""
from __future__ import annotations

import pytest

from pping_lang.types import Diagnosis, MetricPoint


def test_metric_point_immutable():
    p = MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=45.0)
    with pytest.raises((AttributeError, Exception)):
        p.value = 99.0  # type: ignore[misc]


def test_metric_point_defaults():
    p = MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=45.0)
    assert p.engine_idx == 0
    assert p.gpu_idx == -1
    assert p.labels is None


def test_metric_point_with_labels():
    p = MetricPoint(
        ts_ns=1,
        name="vllm.req.finished_total",
        value=1.0,
        labels={"reason": "stop"},
    )
    assert p.labels == {"reason": "stop"}


def test_metric_point_slots_no_dict():
    """slots=True 应该禁掉 __dict__，节省内存。"""
    p = MetricPoint(ts_ns=1, name="x", value=1.0)
    assert not hasattr(p, "__dict__")


def test_diagnosis_basic():
    d = Diagnosis(
        ts_ns=1,
        rule_id="r1",
        severity="warning",
        triggered_value=34.0,
        threshold=50.0,
        window_seconds=30,
        message="GPU 利用率 34% 持续低于 50% 已 30s",
        suggestion="检查 batch 是否退化",
    )
    assert d.severity == "warning"
    assert d.engine_idx == 0
    assert d.context is None


def test_diagnosis_with_context():
    d = Diagnosis(
        ts_ns=1,
        rule_id="low-mfu",
        severity="critical",
        triggered_value=0.12,
        threshold=0.20,
        window_seconds=60,
        message="MFU 12%",
        suggestion="检查 padding ratio",
        context={"vllm.cudagraph.padding_ratio": 0.47},
    )
    assert d.context == {"vllm.cudagraph.padding_ratio": 0.47}


def test_diagnosis_immutable():
    d = Diagnosis(
        ts_ns=1, rule_id="r", severity="info",
        triggered_value=1.0, threshold=2.0, window_seconds=10,
        message="m", suggestion="s",
    )
    with pytest.raises((AttributeError, Exception)):
        d.severity = "critical"  # type: ignore[misc]
