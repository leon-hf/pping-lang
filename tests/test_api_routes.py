"""API 端点测试 — TestClient 直跑 FastAPI app（不起 uvicorn）。"""
from __future__ import annotations

import time

import duckdb
import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.metrics_catalog import M
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.sink.local import LocalSink
from pping_lang.types import Diagnosis, MetricPoint


@pytest.fixture
def empty_app(tmp_path):
    """App with empty (just-created) DB and no live components."""
    db = tmp_path / "api.duckdb"
    sink = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    # Create schema by pushing one throwaway metric, then close
    sink.push_metric(MetricPoint(ts_ns=1, name=M.GPU_UTIL_PCT, value=0.0))
    sink.close()
    # Reopen sink for the app's reference (so health endpoint can read counters)
    sink2 = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db),
        instance_id="test-inst",
        engine_index=3,
        sink=sink2,
        rules=DEFAULT_RULES,
        rule_engine=None,
        nvml=None,
    )
    yield TestClient(app), db, sink2
    sink2.close()


@pytest.fixture
def app_with_data(tmp_path):
    """DB pre-populated with metrics + diagnoses for richer endpoint tests."""
    db = tmp_path / "api.duckdb"
    sink = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    base = time.monotonic_ns()
    # Push 50 GPU util points over the last 10s
    for i in range(50):
        sink.push_metric(MetricPoint(
            ts_ns=base - int((50 - i) * 0.2 * 1e9),
            name=M.GPU_UTIL_PCT, value=30.0 + i * 0.5,
            engine_idx=0, gpu_idx=0,
        ))
    # A different metric
    sink.push_metric(MetricPoint(
        ts_ns=base, name=M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, value=0.45,
    ))
    # A diagnosis
    sink.push_diagnosis(Diagnosis(
        ts_ns=base, rule_id="low-gpu-util", severity="warning",
        triggered_value=34.0, threshold=50.0, window_seconds=30,
        message="GPU 平均利用率 34% 持续低于 50% 已 30s",
        suggestion="检查 batch 是否退化为 1",
        engine_idx=0,
    ))
    sink.close()
    sink2 = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db),
        instance_id="test-inst",
        engine_index=0,
        sink=sink2,
        rules=DEFAULT_RULES,
    )
    yield TestClient(app), db, sink2
    sink2.close()


def test_health_returns_ok(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["instance_id"] == "test-inst"
    assert body["engine_index"] == 3
    assert "version" in body
    assert "sink" in body
    assert "rules" in body
    assert "nvml" in body


def test_health_when_no_rule_engine(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/health")
    body = r.json()
    assert body["rules"]["num"] == 0
    assert body["nvml"]["enabled"] is False


def test_metrics_available_returns_catalog(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/metrics/available")
    assert r.status_code == 200
    body = r.json()
    metrics = body["metrics"]
    assert M.GPU_UTIL_PCT in metrics
    assert M.VLLM_CUDAGRAPH_PADDING_RATIO in metrics
    assert len(metrics) >= 35
    assert metrics == sorted(metrics)  # sorted output


def test_metrics_recent_returns_points(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/metrics/recent", params={
        "name": M.GPU_UTIL_PCT, "seconds": 60,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == M.GPU_UTIL_PCT
    assert len(body["points"]) > 0
    # chronologically ordered (oldest first)
    ts_values = [p["ts_ns"] for p in body["points"]]
    assert ts_values == sorted(ts_values)


def test_metrics_recent_unknown_metric_422(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/metrics/recent", params={"name": "fake.metric"})
    assert r.status_code == 422


def test_metrics_recent_invalid_seconds(empty_app):
    client, _, _ = empty_app
    # seconds < 1 should be rejected
    r = client.get("/api/metrics/recent", params={
        "name": M.GPU_UTIL_PCT, "seconds": 0,
    })
    assert r.status_code == 422


def test_metrics_snapshot(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/metrics/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert M.GPU_UTIL_PCT in body["metrics"]
    point = body["metrics"][M.GPU_UTIL_PCT]
    assert "value" in point
    assert "ts_ns" in point


def test_metrics_snapshot_empty_when_no_data(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/metrics/snapshot", params={"seconds": 1})
    body = r.json()
    # very recent window — likely empty
    assert isinstance(body["metrics"], dict)


def test_diagnoses_list(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/diagnoses")
    assert r.status_code == 200
    body = r.json()
    assert len(body["diagnoses"]) >= 1
    d = body["diagnoses"][0]
    assert d["rule_id"] == "low-gpu-util"
    assert d["severity"] == "warning"
    assert d["triggered_value"] == 34.0
    assert "message" in d
    assert "suggestion" in d


def test_diagnoses_history(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/diagnoses/history")
    body = r.json()
    assert len(body["diagnoses"]) >= 1


def test_rules_list_returns_defaults(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/rules")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rules"]) == 10
    ids = {r["id"] for r in body["rules"]}
    assert "low-gpu-util" in ids
    assert "high-cudagraph-padding" in ids
    assert "low-mfu" in ids


def test_rules_list_serialization_format(empty_app):
    client, _, _ = empty_app
    body = client.get("/api/rules").json()
    rule = body["rules"][0]
    # condition is a nested dict
    assert "condition" in rule
    assert "metric" in rule["condition"]
    assert "op" in rule["condition"]
    assert "threshold" in rule["condition"]
    assert "window_seconds" in rule["condition"]
    assert "aggregation" in rule["condition"]


def test_rule_get_by_id(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/rules/low-gpu-util")
    assert r.status_code == 200
    assert r.json()["id"] == "low-gpu-util"


def test_rule_get_unknown_404(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/rules/does-not-exist")
    assert r.status_code == 404


def test_instances_list(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/instances")
    body = r.json()
    assert "test-inst" in body["instances"]


def test_diagnoses_empty_when_no_db_tables(tmp_path):
    """API should not 500 when DuckDB file/tables don't exist yet."""
    db = tmp_path / "empty.duckdb"
    duckdb.connect(str(db)).close()  # create file but no tables
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="x", engine_index=0,
        sink=sink, rules=DEFAULT_RULES,
    )
    client = TestClient(app)
    try:
        r = client.get("/api/diagnoses")
        assert r.status_code == 200
        assert r.json()["diagnoses"] == []
        r = client.get("/api/metrics/snapshot")
        assert r.status_code == 200
        assert r.json()["metrics"] == {}
    finally:
        sink.close()
