"""bench/store.py — DuckDB CRUD + SLO evaluation."""
from __future__ import annotations

import duckdb
import pytest

from pping_lang.bench import store
from pping_lang.bench.measurement import LatencyStats, RunSummary
from pping_lang.bench.scenarios.schema import SLO, StaticScenario


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "bench.duckdb"
    c = duckdb.connect(str(db))
    store.init_bench_table(c)
    yield c
    c.close()


def _scenario(**overrides) -> StaticScenario:
    base = dict(
        name="t", endpoint="http://x:8000", model="m",
        concurrency=4, duration_s=30, num_requests=None,
    )
    base.update(overrides)
    return StaticScenario(**base)


def _summary(**overrides) -> RunSummary:
    s = RunSummary(
        total=10, ok=10, errors=0, duration_s=5.0,
        ttft_ms=LatencyStats(n=10, p50=100, p99=200),
        tpot_ms=LatencyStats(n=10, p50=20, p99=40),
        e2e_ms=LatencyStats(n=10, p50=1000, p99=2000),
        output_tokens_total=1000,
        input_tokens_total=5000,
        output_throughput_tps=200.0,
        input_throughput_tps=1000.0,
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ===== init / generate_run_id =====


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "bench.duckdb"
    c = duckdb.connect(str(db))
    store.init_bench_table(c)
    store.init_bench_table(c)  # second call must not raise
    rows = c.execute("SELECT COUNT(*) FROM bench_runs").fetchone()
    assert rows[0] == 0
    c.close()


def test_generate_run_id_format(conn):
    rid = store.generate_run_id(conn, "static")
    assert rid.startswith("static-")
    assert rid.endswith("-001")  # first run today


def test_generate_run_id_increments(conn):
    rid1 = store.generate_run_id(conn, "static")
    store.insert_running(conn, rid1, _scenario(), "static", started_at_ns=1)
    rid2 = store.generate_run_id(conn, "static")
    assert rid1 != rid2
    assert rid2.endswith("-002")


def test_generate_run_id_separates_by_type(conn):
    rid_static = store.generate_run_id(conn, "static")
    store.insert_running(conn, rid_static, _scenario(), "static", started_at_ns=1)
    rid_dyn = store.generate_run_id(conn, "dynamic")
    # Different type → starts fresh at 001
    assert rid_dyn.endswith("-001")
    assert rid_dyn.startswith("dynamic-")


# ===== insert / mark =====


def test_insert_then_get(conn):
    rid = "static-test-001"
    store.insert_running(conn, rid, _scenario(name="hello"), "static", 1000)
    row = store.get_run(conn, rid)
    assert row is not None
    assert row["run_id"] == rid
    assert row["scenario_name"] == "hello"
    assert row["status"] == "running"
    assert row["scenario"]["concurrency"] == 4


def test_mark_done_sets_status_and_metrics(conn):
    rid = "static-test-002"
    store.insert_running(conn, rid, _scenario(), "static", 1000)
    store.mark_done(conn, rid, finished_at_ns=2000, summary=_summary(),
                    slo_status="pass", suggestions=[{"title": "x"}])
    row = store.get_run(conn, rid)
    assert row["status"] == "done"
    assert row["finished_at_ns"] == 2000
    assert row["slo_status"] == "pass"
    assert row["suggestions"] == [{"title": "x"}]
    assert row["client_metrics"]["ttft_ms"]["p99"] == 200


def test_mark_failed(conn):
    rid = "static-test-003"
    store.insert_running(conn, rid, _scenario(), "static", 1000)
    store.mark_failed(conn, rid, finished_at_ns=1500,
                      error="connection refused at endpoint")
    row = store.get_run(conn, rid)
    assert row["status"] == "failed"
    assert "connection refused" in row["error"]


def test_get_run_missing(conn):
    assert store.get_run(conn, "nope") is None


def test_list_runs_ordered_desc(conn):
    for i, ts in enumerate([100, 300, 200]):
        rid = f"static-test-{i:03d}"
        store.insert_running(conn, rid, _scenario(name=f"r{i}"), "static",
                             started_at_ns=ts)
    runs = store.list_runs(conn)
    assert [r["started_at_ns"] for r in runs] == [300, 200, 100]


# ===== evaluate_slo =====


def test_slo_n_a_when_no_thresholds():
    assert store.evaluate_slo(_summary(), _scenario(slo=None)) == "n/a"
    assert store.evaluate_slo(_summary(), _scenario(slo=SLO())) == "n/a"
    assert store.evaluate_slo(_summary(), _scenario(slo=SLO.from_spec(""))) == "n/a"


def test_slo_pass_when_under_threshold():
    summary = _summary(ttft_ms=LatencyStats(n=10, p50=100, p99=200))
    sc = _scenario(slo=SLO.from_spec("ttft:p99<300ms"))
    assert store.evaluate_slo(summary, sc) == "pass"


def test_slo_fail_when_p99_exceeds():
    summary = _summary(ttft_ms=LatencyStats(n=10, p50=100, p99=500))
    sc = _scenario(slo=SLO.from_spec("ttft:p99<300ms"))
    assert store.evaluate_slo(summary, sc) == "fail"


def test_slo_fail_on_missing_actual():
    """If TTFT has no samples but SLO requires it, that's a fail (no proof)."""
    summary = _summary(ttft_ms=LatencyStats(n=0))
    sc = _scenario(slo=SLO.from_spec("ttft:p99<300ms"))
    assert store.evaluate_slo(summary, sc) == "fail"


def test_slo_error_rate_check():
    summary = _summary(total=100, ok=90, errors=10)  # 10% error
    sc = _scenario(slo=SLO.from_spec("error_rate<0.01"))    # 1% allowed
    assert store.evaluate_slo(summary, sc) == "fail"
    sc_ok = _scenario(slo=SLO.from_spec("error_rate<0.20")) # 20% allowed
    assert store.evaluate_slo(summary, sc_ok) == "pass"


def test_slo_multi_threshold_fails_if_any_violated():
    """All thresholds must pass; one fail → overall fail."""
    summary = _summary(
        ttft_ms=LatencyStats(n=10, p99=200),
        tpot_ms=LatencyStats(n=10, p99=80),   # exceeds
    )
    sc = _scenario(slo=SLO.from_spec("ttft:p99<300ms;tpot:p99<50ms"))
    assert store.evaluate_slo(summary, sc) == "fail"


def test_slo_scenario_json_round_trip(conn):
    """The scenario_json column should store the spec string so it survives DB round-trip."""
    sc = _scenario(slo=SLO.from_spec("ttft:p99<300ms;error_rate<0.01"))
    store.insert_running(conn, "static-test-rt", sc, "static", started_at_ns=1)
    row = store.get_run(conn, "static-test-rt")
    assert row is not None
    assert row["scenario"]["slo"]["spec"] == "ttft:p99<300ms;error_rate<0.01"
    assert len(row["scenario"]["slo"]["thresholds"]) == 2
