"""DuckDB persistence for bench runs — see design doc §12.

Single table `bench_runs` keyed by `run_id`. Per-request metrics/diagnoses are
NOT stored here — they live in the existing `metrics` / `diagnoses` tables
written by pping-lang's StatLogger. Joining metrics to a bench run is done at
query time via the `started_at_ns / finished_at_ns` window.

We deliberately keep the run record self-contained as JSON (no normalization
of nested LatencyStats / SLO / suggestions) — the table is for run-level
indexing and listing, not for analytical querying of inner fields.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import asdict
from typing import Any

from pping_lang.bench.measurement import RunSummary
from pping_lang.bench.scenarios.schema import SLO, StaticScenario, Threshold

logger = logging.getLogger(__name__)


BENCH_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS bench_runs (
    run_id              VARCHAR PRIMARY KEY,
    scenario_name       VARCHAR,
    scenario_type       VARCHAR,
    started_at_ns       BIGINT,
    finished_at_ns      BIGINT,
    status              VARCHAR,
    scenario_json       JSON,
    client_metrics_json JSON,
    slo_status          VARCHAR,
    suggestions_json    JSON,
    error               VARCHAR
);
"""

BENCH_RUNS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_bench_runs_started "
    "ON bench_runs(started_at_ns DESC);"
)


def init_bench_table(conn: Any) -> None:
    """Idempotent: create table + index. Called by store consumers at startup."""
    conn.execute(BENCH_RUNS_DDL)
    conn.execute(BENCH_RUNS_INDEX_DDL)


def generate_run_id(conn: Any, scenario_type: str) -> str:
    """Compose `{type}-YYYY-MM-DD-NNN` per design doc §5.1.

    NNN counts same-day same-type runs already in the table. Race-safe enough
    for embedded mode (single bench process) — for centralized v0.2 we'd switch
    to a sequence + INSERT-OR-RETRY.
    """
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    prefix = f"{scenario_type}-{today}-"
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM bench_runs WHERE run_id LIKE ?",
            [prefix + "%"],
        ).fetchone()
        n = int(row[0]) if row and row[0] is not None else 0
    except Exception:
        # Table missing — init first
        init_bench_table(conn)
        n = 0
    return f"{prefix}{n + 1:03d}"


def insert_running(
    conn: Any,
    run_id: str,
    scenario: StaticScenario,
    scenario_type: str,
    started_at_ns: int,
) -> None:
    """Initial row: status='running'. Caller updates on completion / failure."""
    conn.execute(
        """
        INSERT INTO bench_runs (
            run_id, scenario_name, scenario_type, started_at_ns, finished_at_ns,
            status, scenario_json, client_metrics_json, slo_status,
            suggestions_json, error
        ) VALUES (?, ?, ?, ?, NULL, 'running', ?, NULL, NULL, NULL, NULL)
        """,
        [
            run_id,
            scenario.name,
            scenario_type,
            started_at_ns,
            json.dumps(_scenario_to_dict(scenario)),
        ],
    )


def mark_done(
    conn: Any,
    run_id: str,
    finished_at_ns: int,
    summary: RunSummary,
    slo_status: str = "n/a",
    suggestions: list[dict[str, Any]] | None = None,
) -> None:
    conn.execute(
        """
        UPDATE bench_runs SET
            finished_at_ns = ?,
            status = 'done',
            client_metrics_json = ?,
            slo_status = ?,
            suggestions_json = ?
        WHERE run_id = ?
        """,
        [
            finished_at_ns,
            json.dumps(summary.as_dict()),
            slo_status,
            json.dumps(suggestions or []),
            run_id,
        ],
    )


def mark_failed(
    conn: Any,
    run_id: str,
    finished_at_ns: int,
    error: str,
) -> None:
    conn.execute(
        """
        UPDATE bench_runs SET
            finished_at_ns = ?,
            status = 'failed',
            error = ?
        WHERE run_id = ?
        """,
        [finished_at_ns, error[:2000], run_id],
    )


def get_run(conn: Any, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT run_id, scenario_name, scenario_type, started_at_ns, finished_at_ns,
               status, scenario_json, client_metrics_json, slo_status,
               suggestions_json, error
        FROM bench_runs WHERE run_id = ?
        """,
        [run_id],
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_runs(conn: Any, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT run_id, scenario_name, scenario_type, started_at_ns, finished_at_ns,
               status, scenario_json, client_metrics_json, slo_status,
               suggestions_json, error
        FROM bench_runs
        ORDER BY started_at_ns DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: tuple) -> dict[str, Any]:
    return {
        "run_id": row[0],
        "scenario_name": row[1],
        "scenario_type": row[2],
        "started_at_ns": row[3],
        "finished_at_ns": row[4],
        "status": row[5],
        "scenario": _parse_json(row[6]),
        "client_metrics": _parse_json(row[7]),
        "slo_status": row[8],
        "suggestions": _parse_json(row[9]) or [],
        "error": row[10],
    }


def _parse_json(s: Any) -> Any:
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _scenario_to_dict(scenario: StaticScenario) -> dict[str, Any]:
    """dataclass → dict, dropping non-serializable bits.

    `slo` is replaced with its spec string + structured thresholds so the row
    stays human-grep-able (`SELECT scenario_json ->> 'slo_spec' FROM bench_runs`).
    """
    d = asdict(scenario)
    slo: SLO | None = scenario.slo
    if slo is None or slo.is_empty():
        d["slo"] = None
    else:
        d["slo"] = {
            "spec": slo.as_spec(),
            "thresholds": [
                {
                    "metric": t.metric,
                    "percentile": t.percentile,
                    "op": t.op,
                    "value": t.value,
                }
                for t in slo.thresholds
            ],
        }
    return d


def evaluate_slo(summary: RunSummary, scenario: StaticScenario) -> str:
    """Apply scenario.slo to a finished summary. Returns 'pass' | 'fail' | 'n/a'.

    Semantics:
    - empty / missing SLO     → 'n/a'
    - any threshold fails     → 'fail'
    - any threshold has no data → 'fail' (no proof of passing)
    - otherwise               → 'pass'
    """
    slo = scenario.slo
    if slo is None or slo.is_empty():
        return "n/a"

    any_failed = False
    any_no_data = False
    for t in slo.thresholds:
        result = t.check(summary)
        if result is None:
            any_no_data = True
            logger.info("[bench] SLO no data for %s", t.as_spec())
        elif result is False:
            any_failed = True
            logger.info("[bench] SLO fail on %s", t.as_spec())

    if any_failed or any_no_data:
        return "fail"
    return "pass"


# Re-export for backward compat at the store import surface
__all__ = [
    "BENCH_RUNS_DDL",
    "init_bench_table",
    "generate_run_id",
    "insert_running",
    "mark_done",
    "mark_failed",
    "get_run",
    "list_runs",
    "evaluate_slo",
    "Threshold",
]
