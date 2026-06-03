"""API 端点测试 — TestClient 直跑 FastAPI app（不起 uvicorn）。"""
from __future__ import annotations

import time

import duckdb
import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.metrics_catalog import M
from pping_lang.rules.store import RuleStore
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
        rule_store=RuleStore(),
        rule_engine=None,
        nvml=None,
    )
    yield TestClient(app), db, sink2
    sink2.close()


@pytest.fixture
def app_with_data(tmp_path):
    """DB + in-memory sink state both pre-populated for endpoint tests.

    Day 18 dual-path note: the realtime endpoints (kpis/snapshot/recent ≤60s,
    roofline) read from Sink.latest() / Sink.recent() — in-memory ring
    buffers — so we MUST push test data through the same sink instance the
    app receives. The DuckDB file gets populated as a side effect of bg flush
    so the long-window endpoints (latency_trends, diagnoses_history) work too.
    """
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
    sink.push_metric(MetricPoint(
        ts_ns=base, name=M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, value=0.45,
    ))
    sink.push_diagnosis(Diagnosis(
        ts_ns=base, rule_id="low-gpu-util", severity="warning",
        triggered_value=34.0, threshold=50.0, window_seconds=30,
        message="GPU 平均利用率 34% 持续低于 50% 已 30s",
        suggestion="检查 batch 是否退化为 1",
        engine_idx=0,
    ))
    # Force a flush so the DuckDB file has the data for long-window queries.
    # Sink stays open — app reads its in-memory state for short-window queries.
    sink._drain()
    app = build_app(
        db_path=str(db),
        instance_id="test-inst",
        engine_index=0,
        sink=sink,
        rule_store=RuleStore(),
    )
    yield TestClient(app), db, sink
    sink.close()


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
    # No rule_engine but rule_store has 10 defaults — health falls back to store count
    assert body["rules"]["num"] == 10
    assert body["rules"]["eval_count"] == 0
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


def test_kernels_endpoint_with_data(tmp_path):
    """/api/kernels 打包 kernel 分解;class_shares 按占比降序。"""
    db = tmp_path / "k.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    base = time.monotonic_ns()
    for name, val in [
        (M.KERNEL_SHARE_ATTENTION_PCT, 42.0),
        (M.KERNEL_SHARE_GEMM_PCT, 31.0),
        (M.KERNEL_SHARE_OTHER_PCT, 27.0),
        (M.KERNEL_GPU_BUSY_PCT, 88.0),
        (M.KERNEL_LAUNCH_COUNT_PER_S, 4128.0),
        (M.KERNEL_MEAN_DUR_US, 12.5),
        (M.KERNEL_IN_GRAPH_PCT, 89.0),
        (M.PPING_LANG_CUPTI_CB_MS, 0.06),
    ]:
        sink.push_metric(MetricPoint(ts_ns=base, name=name, value=val))
    app = build_app(
        db_path=str(db), instance_id="x", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    client = TestClient(app)
    try:
        r = client.get("/api/kernels")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        # 降序:attention(42) > gemm(31) > other(27)
        shares = data["class_shares"]
        assert [c["cls"] for c in shares] == ["attention", "gemm", "other"]
        assert data["gpu_busy_pct"] == 88.0
        assert data["launch_count_per_s"] == 4128.0
        assert data["in_graph_pct"] == 89.0
        assert data["overhead_cb_ms"] == 0.06
    finally:
        sink.close()


def test_kernels_endpoint_top_kernels_from_collector(tmp_path):
    """/api/kernels 透出 collector 的原始 per-kernel 明细(未洗的 mangled 名)。"""
    db = tmp_path / "kd.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)

    class _FakeCupti:
        last_snapshot_ts = time.monotonic_ns()
        last_window_ns = int(1.0 * 1e9)

        def top_kernels(self):
            return [
                {"name": "flash_fwd_kernel", "cls": "attention", "count": 120,
                 "total_ms": 5.2, "mean_us": 43.0, "pct": 42.0, "in_graph_pct": 100.0},
                {"name": "cutlass_80_tensorop_s16816gemm_f16", "cls": "gemm", "count": 96,
                 "total_ms": 3.8, "mean_us": 39.6, "pct": 31.0, "in_graph_pct": 100.0},
            ]

    app = build_app(
        db_path=str(db), instance_id="x", engine_index=0,
        sink=sink, rule_store=RuleStore(), cupti=_FakeCupti(),
    )
    client = TestClient(app)
    try:
        data = client.get("/api/kernels").json()
        assert data["enabled"] is True  # 有原始明细即算启用
        tk = data["top_kernels"]
        assert tk[0]["name"] == "flash_fwd_kernel"
        assert tk[1]["name"] == "cutlass_80_tensorop_s16816gemm_f16"
        assert tk[0]["count"] == 120
        # 数据时刻字段:刚 stamp 的 ts → age 很小;窗宽 1s
        assert data["snapshot_age_s"] is not None and data["snapshot_age_s"] < 5.0
        assert abs(data["rollup_window_s"] - 1.0) < 0.01
    finally:
        sink.close()


def test_kernel_findings_logic():
    """诊断结论:从 kernel 指标派生人话结论(GEMM-bound / 单kernel / launch-bound / 碎片化)。"""
    from pping_lang.api.routes import _kernel_findings
    class_shares = [{"cls": "gemm", "pct": 87.0}, {"cls": "attention", "pct": 6.0}]
    top_kernels = [{"name": "cutlass_x", "cls": "gemm", "pct": 50.0}] + [
        {"name": f"gemm_{i}", "cls": "gemm", "pct": 1.0} for i in range(10)
    ]
    findings = _kernel_findings(
        class_shares=class_shares, top_kernels=top_kernels,
        sync_share=27.0, memcpy_share=0.1, in_graph=76.0,
        launch_rate=47000.0, overhead_cb_ms=80.0, window_s=2.0,
    )
    titles = " | ".join(f["title"] for f in findings)
    assert "bound" in titles  # GEMM-bound
    assert "单 kernel" in titles
    assert "Launch-bound" in titles
    assert "碎片化" in titles
    assert "采集开销" in titles  # 80ms/2s = 4% ≥ 3%
    assert all(f["level"] in ("info", "warning", "critical") for f in findings)


def test_kernel_findings_quiet_when_healthy():
    """没有明显瓶颈时不硬凑结论。"""
    from pping_lang.api.routes import _kernel_findings
    findings = _kernel_findings(
        class_shares=[{"cls": "gemm", "pct": 35.0}, {"cls": "attention", "pct": 30.0}],
        top_kernels=[{"name": "k", "cls": "gemm", "pct": 20.0}],
        sync_share=3.0, memcpy_share=1.0, in_graph=90.0,
        launch_rate=2000.0, overhead_cb_ms=2.0, window_s=2.0,
    )
    assert findings == []


def test_kernels_flamegraph_endpoint(tmp_path):
    """/api/kernels/flamegraph 透出 collector 的 Python→kernel 火焰图树。"""
    db = tmp_path / "fg.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)

    class _FakeCupti:
        def flamegraph(self):
            return {"name": "root", "kind": "root", "value": 100, "children": [
                {"name": "step", "kind": "python", "value": 100, "children": [
                    {"name": "flash_fwd", "kind": "kernel", "value": 100, "children": []},
                ]},
            ]}

    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore(), cupti=_FakeCupti())
    try:
        data = TestClient(app).get("/api/kernels/flamegraph").json()
        assert data["available"] is True
        assert data["tree"]["name"] == "root"
        assert data["tree"]["children"][0]["name"] == "step"
    finally:
        sink.close()


def test_kernels_flamegraph_unavailable_without_collector(empty_app):
    client, _, _ = empty_app
    data = client.get("/api/kernels/flamegraph").json()
    assert data["available"] is False
    assert data["tree"] is None


def test_kernels_timeline_endpoint(tmp_path):
    """/api/kernels/timeline 透出 collector 的执行时间线。"""
    db = tmp_path / "tl.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)

    class _FakeCupti:
        def timeline(self, max_events=800):
            return {"span_ns": 450, "streams": [7], "count": 1,
                    "events": [{"start": 50, "dur": 100, "cls": "attention",
                                "kind": "kernel", "stream": 7, "in_graph": 1, "name": "flash_fwd"}]}

    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore(), cupti=_FakeCupti())
    try:
        data = TestClient(app).get("/api/kernels/timeline").json()
        assert data["available"] is True
        assert data["timeline"]["count"] == 1
        assert data["timeline"]["events"][0]["name"] == "flash_fwd"
    finally:
        sink.close()


def test_kernels_timeline_unavailable_without_collector(empty_app):
    client, _, _ = empty_app
    data = client.get("/api/kernels/timeline").json()
    assert data["available"] is False and data["timeline"] is None


def test_kernels_endpoint_disabled_when_no_data(empty_app):
    """无 kernel 数据(CUPTI 未启用 / 无 GPU)→ enabled=False,不 500。"""
    client, _, _ = empty_app
    r = client.get("/api/kernels")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is False
    assert data["class_shares"] == []
    assert data["gpu_busy_pct"] is None


def test_diagnoses_empty_when_no_db_tables(tmp_path):
    """API should not 500 when DuckDB file/tables don't exist yet."""
    db = tmp_path / "empty.duckdb"
    duckdb.connect(str(db)).close()  # create file but no tables
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="x", engine_index=0,
        sink=sink, rule_store=RuleStore(),
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
