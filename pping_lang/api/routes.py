"""FastAPI app + 核心 API 端点 (Day 6)。

依赖通过闭包注入（避免 FastAPI Depends 的样板）。Day 8/9/10 在此扩 POST/PUT/DELETE。
"""
from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from pping_lang.api.queries import (
    latest_per_metric,
    list_instances,
    open_conn,
    recent_diagnoses,
    recent_metric_points,
)
from pping_lang.metrics_catalog import ALLOWED_METRICS

_UI_INDEX = Path(__file__).parent.parent / "ui" / "index.html"

if TYPE_CHECKING:
    from pping_lang.collector.nvml import NvmlSampler
    from pping_lang.rules.engine import RuleEngine
    from pping_lang.rules.schema import Rule
    from pping_lang.sink.base import Sink

logger = logging.getLogger(__name__)


def build_app(
    *,
    db_path: str,
    instance_id: str,
    engine_index: int,
    sink: Sink,
    rules: list[Rule],
    rule_engine: RuleEngine | None = None,
    nvml: NvmlSampler | None = None,
    version: str = "0.0.1.dev0",
) -> FastAPI:
    """Construct the FastAPI app with deps wired via closure."""
    app = FastAPI(
        title="pping-lang",
        version=version,
        description="vLLM 性能诊断插件 — HTTP API",
    )
    # CORS: open for v0.1 local-dev. Sidecar/Centralized will tighten in v0.2.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Cache UI HTML at build time — ~10KB, fine to keep in memory
    try:
        ui_html = _UI_INDEX.read_text(encoding="utf-8")
    except FileNotFoundError:
        ui_html = "<h1>pping-lang UI missing</h1>"
        logger.warning("[pping-lang] UI file not found at %s", _UI_INDEX)

    # === GET / — dashboard ===
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(ui_html)

    # === GET /api/health ===
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": version,
            "instance_id": instance_id,
            "engine_index": engine_index,
            "sink": {
                "dropped_metrics": sink.dropped_metrics,
                "dropped_diags": sink.dropped_diags,
                "flush_errors": sink.flush_errors,
                "queue_depth": sink.queue_depth,
            },
            "nvml": {
                "enabled": nvml.enabled if nvml else False,
                "num_gpus": nvml.num_gpus if nvml else 0,
            },
            "rules": {
                "num": rule_engine.num_rules if rule_engine else 0,
                "eval_count": rule_engine.eval_count if rule_engine else 0,
                "fire_count": rule_engine.fire_count if rule_engine else 0,
            },
        }

    # === GET /api/metrics/available ===
    @app.get("/api/metrics/available")
    def metrics_available() -> dict[str, list[str]]:
        return {"metrics": sorted(ALLOWED_METRICS)}

    # === GET /api/metrics/recent ===
    @app.get("/api/metrics/recent")
    def metrics_recent(
        name: str = Query(..., description="Metric name (must be in catalog)"),
        seconds: int = Query(60, ge=1, le=86400),
        limit: int = Query(1000, ge=1, le=10000),
    ) -> dict[str, Any]:
        if name not in ALLOWED_METRICS:
            raise HTTPException(422, f"unknown metric {name!r}")
        since_ns = time.monotonic_ns() - int(seconds * 1e9)
        conn = open_conn(db_path)
        try:
            try:
                points = recent_metric_points(conn, name, since_ns, limit)
            except Exception:
                # Tables may not exist yet (no flushes happened)
                points = []
        finally:
            conn.close()
        return {"name": name, "seconds": seconds, "points": points}

    # === GET /api/metrics/snapshot ===
    @app.get("/api/metrics/snapshot")
    def metrics_snapshot(
        seconds: int = Query(30, ge=1, le=3600),
    ) -> dict[str, Any]:
        """Latest value per metric within the window."""
        since_ns = time.monotonic_ns() - int(seconds * 1e9)
        conn = open_conn(db_path)
        try:
            try:
                latest = latest_per_metric(conn, since_ns)
            except Exception:
                latest = {}
        finally:
            conn.close()
        return {"window_seconds": seconds, "metrics": latest}

    # === GET /api/diagnoses ===
    @app.get("/api/diagnoses")
    def diagnoses(
        seconds: int = Query(300, ge=1, le=86400),
        limit: int = Query(200, ge=1, le=2000),
    ) -> dict[str, Any]:
        since_ns = time.monotonic_ns() - int(seconds * 1e9)
        conn = open_conn(db_path)
        try:
            try:
                diags = recent_diagnoses(conn, since_ns, limit)
            except Exception:
                diags = []
        finally:
            conn.close()
        return {"window_seconds": seconds, "diagnoses": diags}

    # === GET /api/diagnoses/history ===
    @app.get("/api/diagnoses/history")
    def diagnoses_history(
        limit: int = Query(500, ge=1, le=5000),
    ) -> dict[str, Any]:
        conn = open_conn(db_path)
        try:
            try:
                diags = recent_diagnoses(conn, since_ns=0, limit=limit)
            except Exception:
                diags = []
        finally:
            conn.close()
        return {"diagnoses": diags}

    # === GET /api/rules ===
    @app.get("/api/rules")
    def rules_list() -> dict[str, Any]:
        return {"rules": [_rule_to_dict(r) for r in rules]}

    # === GET /api/rules/{rule_id} ===
    @app.get("/api/rules/{rule_id}")
    def rule_get(rule_id: str) -> dict[str, Any]:
        for r in rules:
            if r.id == rule_id:
                return _rule_to_dict(r)
        raise HTTPException(404, f"rule {rule_id!r} not found")

    # === GET /api/instances ===
    @app.get("/api/instances")
    def instances() -> dict[str, list[str]]:
        conn = open_conn(db_path)
        try:
            try:
                ids = list_instances(conn)
            except Exception:
                ids = []
        finally:
            conn.close()
        return {"instances": ids}

    return app


def _rule_to_dict(r: Rule) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "severity": r.severity,
        "category": r.category,
        "enabled": r.enabled,
        "condition": dataclasses.asdict(r.condition),
        "message": r.message,
        "suggestion": r.suggestion,
    }
