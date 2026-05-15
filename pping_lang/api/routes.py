"""FastAPI app + 核心 API 端点 (Day 6/8)。

依赖通过闭包注入（避免 FastAPI Depends 的样板）。

Day 6: GET 端点（health, metrics, diagnoses, rules, instances）+ /  dashboard
Day 8: POST/PUT/DELETE/test 端点 — 规则 CRUD via RuleStore
Day 9: 规则热加载（store 改动让 engine 即时看到）— 见 RuleEngine
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from pping_lang.api.queries import (
    latest_per_metric,
    list_instances,
    open_conn,
    recent_diagnoses,
    recent_metric_points,
)
from pping_lang.api.schemas import RuleIn, RuleTestRequest
from pping_lang.hardware import GPUPeak
from pping_lang.metrics_catalog import ALLOWED_METRICS
from pping_lang.report.generator import generate_report
from pping_lang.rules.engine import evaluate_condition_against_db
from pping_lang.rules.schema import Condition, Rule

_UI_INDEX = Path(__file__).parent.parent / "ui" / "index.html"

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pping_lang.collector.nvml import NvmlSampler
    from pping_lang.rules.engine import RuleEngine
    from pping_lang.rules.store import RuleStore
    from pping_lang.sink.base import Sink


def build_app(
    *,
    db_path: str,
    instance_id: str,
    engine_index: int,
    sink: Sink,
    rule_store: RuleStore,
    rule_engine: RuleEngine | None = None,
    nvml: NvmlSampler | None = None,
    version: str = "0.0.1.dev0",
    vllm_config: Any = None,
    gpu_peak: GPUPeak | None = None,
) -> FastAPI:
    """Construct the FastAPI app with deps wired via closure."""
    app = FastAPI(
        title="pping-lang",
        version=version,
        description="vLLM 性能诊断插件 — HTTP API",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
                "num": rule_engine.num_rules if rule_engine else len(rule_store.list()),
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
                points = []
        finally:
            conn.close()
        return {"name": name, "seconds": seconds, "points": points}

    # === GET /api/metrics/snapshot ===
    @app.get("/api/metrics/snapshot")
    def metrics_snapshot(
        seconds: int = Query(30, ge=1, le=3600),
    ) -> dict[str, Any]:
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
        return {
            "rules": [_rule_to_dict(r, rule_store.is_default(r.id))
                      for r in rule_store.list()]
        }

    # === GET /api/rules/{rule_id} ===
    @app.get("/api/rules/{rule_id}")
    def rule_get(rule_id: str) -> dict[str, Any]:
        r = rule_store.get(rule_id)
        if r is None:
            raise HTTPException(404, f"rule {rule_id!r} not found")
        return _rule_to_dict(r, rule_store.is_default(rule_id))

    # === POST /api/rules — create new rule ===
    @app.post("/api/rules", status_code=201)
    def rule_create(rule_in: RuleIn) -> dict[str, Any]:
        if rule_store.get(rule_in.id) is not None:
            raise HTTPException(409, f"rule {rule_in.id!r} exists; use PUT to update")
        rule = _rule_in_to_rule(rule_in)
        try:
            rule_store.upsert(rule)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _rule_to_dict(rule, rule_store.is_default(rule.id))

    # === PUT /api/rules/{rule_id} — update existing rule ===
    @app.put("/api/rules/{rule_id}")
    def rule_update(rule_id: str, rule_in: RuleIn) -> dict[str, Any]:
        if rule_in.id != rule_id:
            raise HTTPException(
                422,
                f"rule_id mismatch: path={rule_id!r}, body.id={rule_in.id!r}",
            )
        rule = _rule_in_to_rule(rule_in)
        try:
            rule_store.upsert(rule)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _rule_to_dict(rule, rule_store.is_default(rule.id))

    # === DELETE /api/rules/{rule_id} ===
    @app.delete("/api/rules/{rule_id}", status_code=204)
    def rule_delete(rule_id: str) -> Response:
        try:
            rule_store.delete(rule_id)
        except KeyError:
            raise HTTPException(404, f"rule {rule_id!r} not found")
        return Response(status_code=204)

    # === POST /api/rules/{rule_id}/test — preview without firing ===
    @app.post("/api/rules/{rule_id}/test")
    def rule_test(
        rule_id: str,
        body: RuleTestRequest | None = None,
    ) -> dict[str, Any]:
        # Use override if provided, else look up by id
        if body and body.override is not None:
            rule = _rule_in_to_rule(body.override)
        else:
            rule = rule_store.get(rule_id)
            if rule is None:
                raise HTTPException(404, f"rule {rule_id!r} not found")
        conn = open_conn(db_path)
        try:
            try:
                fired, value = evaluate_condition_against_db(
                    conn, rule.condition, time.monotonic_ns(),
                )
            except Exception as e:
                logger.exception("test eval failed for %s", rule_id)
                raise HTTPException(500, f"eval failed: {e}")
        finally:
            conn.close()
        return {
            "rule_id": rule.id,
            "would_fire": fired,
            "value": value,
            "threshold": rule.condition.threshold,
            "data_available": value is not None,
            "metric": rule.condition.metric,
            "window_seconds": rule.condition.window_seconds,
            "aggregation": rule.condition.aggregation,
        }

    # === GET /api/report — 生成单 HTML 报告 ===
    @app.get("/api/report", response_class=HTMLResponse)
    def report(
        seconds: int = Query(86400, ge=60, le=2592000),  # 1 min .. 30 days
        plotly_mode: str = Query("cdn", pattern="^(cdn|inline)$"),
    ) -> HTMLResponse:
        try:
            html = generate_report(
                db_path=db_path,
                instance_id=instance_id,
                seconds=seconds,
                version=version,
                vllm_config=vllm_config,
                gpu_peak=gpu_peak,
                plotly_mode=plotly_mode,
            )
        except Exception as e:
            logger.exception("report generation failed")
            raise HTTPException(500, f"report generation failed: {e}")
        # Set filename for download via Content-Disposition
        return HTMLResponse(
            html,
            headers={
                "Content-Disposition":
                    f'inline; filename="pping-lang-report-{instance_id}-{seconds}s.html"',
            },
        )

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


def _rule_to_dict(r: Rule, is_default: bool = False) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "severity": r.severity,
        "category": r.category,
        "enabled": r.enabled,
        "is_default": is_default,
        "condition": {
            "metric": r.condition.metric,
            "op": r.condition.op,
            "threshold": r.condition.threshold,
            "window_seconds": r.condition.window_seconds,
            "aggregation": r.condition.aggregation,
        },
        "message": r.message,
        "suggestion": r.suggestion,
    }


def _rule_in_to_rule(rin: RuleIn) -> Rule:
    return Rule(
        id=rin.id,
        name=rin.name,
        severity=rin.severity,
        category=rin.category,
        condition=Condition(
            metric=rin.condition.metric,
            op=rin.condition.op,
            threshold=rin.condition.threshold,
            window_seconds=rin.condition.window_seconds,
            aggregation=rin.condition.aggregation,
        ),
        message=rin.message,
        suggestion=rin.suggestion,
        enabled=rin.enabled,
    )
