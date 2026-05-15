"""LocalSink — Embedded mode: DuckDB write in same process.

Schema lives here (single source of truth). Both metrics and diagnoses
include instance_id, stamped at this boundary per RFC §1.1.

Connection lifecycle: lazy-init on first _flush, closed on Sink.close().
DuckDB connections are not safe for concurrent writers; our bg flush
thread is the only writer, and close() serializes after thread.join() —
single-thread access throughout.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb

from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis, MetricPoint

logger = logging.getLogger(__name__)

# DDL — see design §9.1 + RFC §1
SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS metrics (
        ts_ns BIGINT NOT NULL,
        engine_idx INTEGER NOT NULL,
        gpu_idx INTEGER NOT NULL,
        instance_id VARCHAR NOT NULL,
        metric_name VARCHAR NOT NULL,
        value DOUBLE NOT NULL,
        labels JSON
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts_ns)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name)",
    """
    CREATE TABLE IF NOT EXISTS diagnoses (
        ts_ns BIGINT NOT NULL,
        engine_idx INTEGER NOT NULL,
        gpu_idx INTEGER NOT NULL,
        instance_id VARCHAR NOT NULL,
        rule_id VARCHAR NOT NULL,
        severity VARCHAR NOT NULL,
        triggered_value DOUBLE NOT NULL,
        threshold DOUBLE NOT NULL,
        window_seconds INTEGER NOT NULL,
        message VARCHAR NOT NULL,
        suggestion VARCHAR NOT NULL,
        context JSON
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_diagnoses_ts ON diagnoses(ts_ns)",
]

INSERT_METRIC = (
    "INSERT INTO metrics "
    "(ts_ns, engine_idx, gpu_idx, instance_id, metric_name, value, labels) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

INSERT_DIAG = (
    "INSERT INTO diagnoses "
    "(ts_ns, engine_idx, gpu_idx, instance_id, rule_id, severity, "
    "triggered_value, threshold, window_seconds, message, suggestion, context) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class LocalSink(Sink):
    """DuckDB-backed sink for Embedded mode."""

    def __init__(
        self,
        db_path: str | Path,
        instance_id: str,
        **base_kwargs,
    ) -> None:
        super().__init__(**base_kwargs)
        self._db_path = str(db_path)
        self._instance_id = instance_id
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _ensure_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(self._db_path)
            for stmt in SCHEMA_STATEMENTS:
                self._conn.execute(stmt)
        return self._conn

    def _flush(
        self,
        metrics: list[MetricPoint],
        diags: list[Diagnosis],
    ) -> None:
        conn = self._ensure_conn()
        if metrics:
            conn.executemany(
                INSERT_METRIC,
                [
                    (
                        m.ts_ns,
                        m.engine_idx,
                        m.gpu_idx,
                        self._instance_id,
                        m.name,
                        m.value,
                        json.dumps(dict(m.labels)) if m.labels else None,
                    )
                    for m in metrics
                ],
            )
        if diags:
            conn.executemany(
                INSERT_DIAG,
                [
                    (
                        d.ts_ns,
                        d.engine_idx,
                        d.gpu_idx,
                        self._instance_id,
                        d.rule_id,
                        d.severity,
                        d.triggered_value,
                        d.threshold,
                        d.window_seconds,
                        d.message,
                        d.suggestion,
                        json.dumps(dict(d.context)) if d.context else None,
                    )
                    for d in diags
                ],
            )

    def close(self) -> None:
        super().close()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
