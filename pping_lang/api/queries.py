"""DuckDB 查询辅助 — API handler 共用。

每次调用 open_conn 新开连接（DuckDB 文件级锁，多连接共存于同进程 OK）。
"""
from __future__ import annotations

import json
from typing import Any

import duckdb


def open_conn(db_path: str) -> Any:
    """Open a DuckDB connection. Caller is responsible for close()."""
    return duckdb.connect(db_path)


def latest_per_metric(conn: Any, since_ns: int) -> dict[str, dict[str, Any]]:
    """Latest value per metric_name within the window.

    Returns: {metric_name: {value, ts_ns, engine_idx, gpu_idx}}
    """
    sql = """
        SELECT metric_name, value, ts_ns, engine_idx, gpu_idx
        FROM metrics
        WHERE ts_ns >= ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY metric_name ORDER BY ts_ns DESC) = 1
    """
    rows = conn.execute(sql, [since_ns]).fetchall()
    return {
        name: {"value": value, "ts_ns": ts, "engine_idx": ei, "gpu_idx": gi}
        for name, value, ts, ei, gi in rows
    }


def recent_metric_points(
    conn: Any, name: str, since_ns: int, limit: int = 1000
) -> list[dict[str, Any]]:
    """Return up to `limit` recent points for `name` since `since_ns`, oldest first."""
    sql = """
        SELECT ts_ns, value, engine_idx, gpu_idx
        FROM metrics
        WHERE metric_name = ? AND ts_ns >= ?
        ORDER BY ts_ns DESC
        LIMIT ?
    """
    rows = conn.execute(sql, [name, since_ns, limit]).fetchall()
    # Reverse for chronological output
    return [
        {"ts_ns": ts, "value": v, "engine_idx": ei, "gpu_idx": gi}
        for ts, v, ei, gi in reversed(rows)
    ]


def recent_diagnoses(
    conn: Any, since_ns: int, limit: int = 200
) -> list[dict[str, Any]]:
    """Return recent diagnoses since `since_ns`, newest first."""
    sql = """
        SELECT ts_ns, rule_id, severity, triggered_value, threshold,
               window_seconds, message, suggestion, engine_idx, gpu_idx,
               instance_id, context
        FROM diagnoses
        WHERE ts_ns >= ?
        ORDER BY ts_ns DESC
        LIMIT ?
    """
    rows = conn.execute(sql, [since_ns, limit]).fetchall()
    out = []
    for r in rows:
        ctx_raw = r[11]
        try:
            ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
        except (json.JSONDecodeError, TypeError):
            ctx = None
        out.append({
            "ts_ns": r[0],
            "rule_id": r[1],
            "severity": r[2],
            "triggered_value": r[3],
            "threshold": r[4],
            "window_seconds": r[5],
            "message": r[6],
            "suggestion": r[7],
            "engine_idx": r[8],
            "gpu_idx": r[9],
            "instance_id": r[10],
            "context": ctx,
        })
    return out


def list_instances(conn: Any) -> list[str]:
    """Distinct instance_ids that have written metrics."""
    rows = conn.execute(
        "SELECT DISTINCT instance_id FROM metrics ORDER BY instance_id"
    ).fetchall()
    return [r[0] for r in rows]
