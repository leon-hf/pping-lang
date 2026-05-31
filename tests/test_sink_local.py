"""LocalSink 测试 — DuckDB schema 创建、metric/diag 写入、JSON labels 处理。"""
from __future__ import annotations

import json

import duckdb
import pytest

from pping_lang.sink.local import LocalSink
from pping_lang.types import Diagnosis, MetricPoint


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.duckdb"


def test_schema_created_on_first_flush(db_path):
    sink = LocalSink(db_path=db_path, instance_id="test", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=45.0))
    sink.close()

    conn = duckdb.connect(str(db_path))
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    assert "metrics" in tables
    assert "diagnoses" in tables
    conn.close()


def test_metric_round_trip_with_all_fields(db_path):
    sink = LocalSink(db_path=db_path, instance_id="inst-7", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(
        ts_ns=12345, engine_idx=2, gpu_idx=1,
        name="gpu.utilization_pct", value=87.5,
    ))
    sink.close()

    conn = duckdb.connect(str(db_path))
    rows = conn.execute(
        "SELECT ts_ns, engine_idx, gpu_idx, instance_id, metric_name, value, labels FROM metrics"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    ts, engine_idx, gpu_idx, instance, name, value, labels = rows[0]
    assert ts == 12345
    assert engine_idx == 2
    assert gpu_idx == 1
    assert instance == "inst-7"
    assert name == "gpu.utilization_pct"
    assert value == 87.5
    assert labels is None


def test_metric_with_labels_serialized_as_json(db_path):
    sink = LocalSink(db_path=db_path, instance_id="i", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(
        ts_ns=1, name="vllm.req.finished_total", value=1.0,
        labels={"reason": "stop"},
    ))
    sink.close()

    conn = duckdb.connect(str(db_path))
    raw = conn.execute("SELECT labels FROM metrics").fetchone()[0]
    conn.close()
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    assert parsed == {"reason": "stop"}


def test_diagnosis_round_trip(db_path):
    sink = LocalSink(db_path=db_path, instance_id="i", flush_interval_s=10.0)
    sink.push_diagnosis(Diagnosis(
        ts_ns=99, rule_id="low-mfu", severity="critical",
        triggered_value=0.12, threshold=0.20, window_seconds=60,
        message="MFU 12%", suggestion="check padding",
        engine_idx=1, gpu_idx=0,
        context={"vllm.cudagraph.padding_ratio": 0.47},
    ))
    sink.close()

    conn = duckdb.connect(str(db_path))
    row = conn.execute(
        "SELECT rule_id, severity, triggered_value, threshold, window_seconds, "
        "message, suggestion, engine_idx, gpu_idx, instance_id, context "
        "FROM diagnoses"
    ).fetchone()
    conn.close()
    (rule_id, severity, tv, thr, win, msg, sug,
     engine_idx, gpu_idx, instance, ctx_raw) = row
    assert rule_id == "low-mfu"
    assert severity == "critical"
    assert tv == 0.12
    assert thr == 0.20
    assert win == 60
    assert msg == "MFU 12%"
    assert sug == "check padding"
    assert engine_idx == 1
    assert gpu_idx == 0
    assert instance == "i"
    parsed = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
    assert parsed == {"vllm.cudagraph.padding_ratio": 0.47}


def test_close_idempotent(db_path):
    sink = LocalSink(db_path=db_path, instance_id="i", flush_interval_s=10.0)
    sink.close()
    sink.close()  # must not raise


def test_close_without_writes_yields_empty_tables(db_path):
    """No metrics → DB still exists (schema bootstrapped eagerly to dodge
    write-write conflicts with RuleEngine on first flush), but tables empty."""
    sink = LocalSink(db_path=db_path, instance_id="i", flush_interval_s=10.0)
    sink.close()
    assert db_path.exists()
    conn = duckdb.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM diagnoses").fetchone()[0] == 0
    finally:
        conn.close()


def test_batch_insert_50_metrics(db_path):
    sink = LocalSink(db_path=db_path, instance_id="i", flush_interval_s=10.0)
    for i in range(50):
        sink.push_metric(MetricPoint(ts_ns=i, name="x.y_total", value=float(i)))
    sink.close()

    conn = duckdb.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    conn.close()
    assert count == 50


def test_instance_id_stamped_at_sink_boundary(db_path):
    """RFC §1.1: instance_id 在 Sink 出站边界打上，进程内 MetricPoint 不带。"""
    sink = LocalSink(db_path=db_path, instance_id="my-pod-3", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=1.0))
    sink.close()

    conn = duckdb.connect(str(db_path))
    instance = conn.execute("SELECT instance_id FROM metrics").fetchone()[0]
    conn.close()
    assert instance == "my-pod-3"
