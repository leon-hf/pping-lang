"""LocalSink 测试 — JSONL 落盘(metrics.jsonl / diagnoses.jsonl)写入与读回。

LocalSink 现在用 AppendLog 顺序追加 JSONL(替代进程内 DuckDB);读回经 JsonlStore。
"""
from __future__ import annotations

import json

from pping_lang.sink.local import LocalSink
from pping_lang.sink.metric_log import (
    DIAG_FILE,
    METRICS_FILE,
    JsonlStore,
    diag_path,
    metrics_path,
)
from pping_lang.types import Diagnosis, MetricPoint


def _read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_files_created_on_first_flush(tmp_path):
    sink = LocalSink(db_path=tmp_path / "local.duckdb", instance_id="test", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=45.0))
    sink.close()
    assert metrics_path(tmp_path).exists()
    rows = _read_lines(metrics_path(tmp_path))
    assert len(rows) == 1 and rows[0]["n"] == "gpu.utilization_pct"


def test_metric_round_trip_with_all_fields(tmp_path):
    sink = LocalSink(db_path=tmp_path / "local.duckdb", instance_id="inst-7", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(
        ts_ns=12345, engine_idx=2, gpu_idx=1,
        name="gpu.utilization_pct", value=87.5,
    ))
    sink.close()
    rows = _read_lines(metrics_path(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    assert r["t"] == 12345
    assert r["e"] == 2
    assert r["g"] == 1
    assert r["n"] == "gpu.utilization_pct"
    assert r["v"] == 87.5
    assert r["l"] is None
    # instance_id 不逐行存(嵌入模式每进程一个),由 JsonlStore 报告
    store = JsonlStore(tmp_path, "inst-7")
    assert store.list_instances() == ["inst-7"]


def test_metric_with_labels_serialized(tmp_path):
    sink = LocalSink(db_path=tmp_path / "local.duckdb", instance_id="i", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(
        ts_ns=1, name="vllm.req.finished_total", value=1.0,
        labels={"reason": "stop"},
    ))
    sink.close()
    rows = _read_lines(metrics_path(tmp_path))
    assert rows[0]["l"] == {"reason": "stop"}


def test_diagnosis_round_trip(tmp_path):
    sink = LocalSink(db_path=tmp_path / "local.duckdb", instance_id="i", flush_interval_s=10.0)
    sink.push_diagnosis(Diagnosis(
        ts_ns=99, rule_id="low-mfu", severity="critical",
        triggered_value=0.12, threshold=0.20, window_seconds=60,
        message="MFU 12%", suggestion="check padding",
        engine_idx=1, gpu_idx=0,
        context={"vllm.cudagraph.padding_ratio": 0.47},
    ))
    sink.close()
    rows = _read_lines(diag_path(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    assert r["rule_id"] == "low-mfu"
    assert r["severity"] == "critical"
    assert r["triggered_value"] == 0.12
    assert r["threshold"] == 0.20
    assert r["window_seconds"] == 60
    assert r["message"] == "MFU 12%"
    assert r["suggestion"] == "check padding"
    assert r["e"] == 1
    assert r["g"] == 0
    assert r["i"] == "i"
    assert r["context"] == {"vllm.cudagraph.padding_ratio": 0.47}


def test_close_idempotent(tmp_path):
    sink = LocalSink(db_path=tmp_path / "local.duckdb", instance_id="i", flush_interval_s=10.0)
    sink.close()
    sink.close()  # must not raise


def test_close_without_writes_creates_no_files(tmp_path):
    """No metrics → no JSONL files written (AppendLog opens lazily on first append)."""
    sink = LocalSink(db_path=tmp_path / "local.duckdb", instance_id="i", flush_interval_s=10.0)
    sink.close()
    assert not metrics_path(tmp_path).exists()
    assert not diag_path(tmp_path).exists()
    # store over an empty dir reads cleanly as empty
    store = JsonlStore(tmp_path, "i")
    assert store.recent_metric_points("gpu.utilization_pct", 0) == []


def test_batch_50_metrics(tmp_path):
    sink = LocalSink(db_path=tmp_path / "local.duckdb", instance_id="i", flush_interval_s=10.0)
    for i in range(50):
        sink.push_metric(MetricPoint(ts_ns=i, name="x.y_total", value=float(i)))
    sink.close()
    assert len(_read_lines(metrics_path(tmp_path))) == 50


def test_jsonl_file_names(tmp_path):
    assert metrics_path(tmp_path).name == METRICS_FILE
    assert diag_path(tmp_path).name == DIAG_FILE
