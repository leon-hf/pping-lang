"""HTTP API: /api/bench/* — list / detail / status / start."""
from __future__ import annotations

import duckdb
import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.bench import store as bench_store
from pping_lang.bench.measurement import LatencyStats, RunSummary
from pping_lang.bench.scenarios.schema import StaticScenario
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "bench-api.duckdb"
    sink = LocalSink(db_path=db, instance_id="bench-test", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="bench-test", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    yield TestClient(app), str(db)
    sink.close()


def _seed(db_path: str, run_id: str, status: str = "done",
          slo_status: str = "n/a", scenario_name: str = "t"):
    conn = duckdb.connect(db_path)
    try:
        bench_store.init_bench_table(conn)
        scenario = StaticScenario(
            name=scenario_name, endpoint="http://x:8000", model="m",
            concurrency=4, duration_s=30, num_requests=None,
        )
        bench_store.insert_running(conn, run_id, scenario, "static", 1000)
        if status == "done":
            summary = RunSummary(
                total=10, ok=10, errors=0, duration_s=5.0,
                ttft_ms=LatencyStats(n=10, p50=100, p99=200),
                tpot_ms=LatencyStats(n=10, p50=20, p99=40),
                e2e_ms=LatencyStats(n=10, p50=1000, p99=2000),
                output_throughput_tps=200.0,
            )
            bench_store.mark_done(conn, run_id, 2000, summary, slo_status=slo_status)
        elif status == "failed":
            bench_store.mark_failed(conn, run_id, 2000, "boom")
    finally:
        conn.close()


def test_runs_list_empty(client):
    c, _ = client
    r = c.get("/api/bench/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["runs"] == []
    assert "now_ns" in body


def test_runs_list_after_seed(client):
    c, db = client
    _seed(db, "static-test-001", scenario_name="alpha")
    _seed(db, "static-test-002", scenario_name="beta")
    r = c.get("/api/bench/runs")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 2
    names = {x["scenario_name"] for x in runs}
    assert names == {"alpha", "beta"}
    # All seeded runs are done, none live
    assert all(not x["live"] for x in runs)


def test_run_detail_ok(client):
    c, db = client
    _seed(db, "static-test-001", slo_status="pass")
    r = c.get("/api/bench/runs/static-test-001")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "static-test-001"
    assert body["slo_status"] == "pass"
    assert body["client_metrics"]["ttft_ms"]["p99"] == 200


def test_run_detail_404(client):
    c, _ = client
    r = c.get("/api/bench/runs/nope")
    assert r.status_code == 404


def test_status_initially_empty(client):
    c, _ = client
    r = c.get("/api/bench/status")
    assert r.status_code == 200
    assert r.json() == {"running": []}


def test_start_rejects_bad_scenario(client):
    c, _ = client
    # Missing model
    r = c.post("/api/bench/start", json={"endpoint": "http://x:8000"})
    assert r.status_code == 422


def test_start_rejects_sweep_in_v01(client):
    c, _ = client
    r = c.post("/api/bench/start", json={
        "endpoint": "http://x:8000", "model": "m",
        "sweep": "concurrency=1,2,4",
    })
    assert r.status_code == 501


def test_start_accepted_creates_running_row(client):
    c, db = client
    # Point at an unreachable endpoint so the bench fails fast but does start
    r = c.post("/api/bench/start", json={
        "endpoint": "http://127.0.0.1:1", "model": "test-m",
        "num_requests": 1, "concurrency": 1, "warmup_s": 0, "timeout_s": 1,
    })
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "running"
    assert body["run_id"].startswith("static-")

    # The list should now contain this run
    list_r = c.get("/api/bench/runs")
    assert list_r.status_code == 200
    runs = list_r.json()["runs"]
    assert any(x["run_id"] == body["run_id"] for x in runs)
