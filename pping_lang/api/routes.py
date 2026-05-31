"""FastAPI app + 核心 API 端点 (Day 6/8)。

依赖通过闭包注入（避免 FastAPI Depends 的样板）。

Day 6: GET 端点（health, metrics, diagnoses, rules, instances）+ /  dashboard
Day 8: POST/PUT/DELETE/test 端点 — 规则 CRUD via RuleStore
Day 9: 规则热加载（store 改动让 engine 即时看到）— 见 RuleEngine
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from pping_lang.api.queries import (
    aggregate_metric,
    bucketed_quantiles,
    latest_per_metric,
    list_instances,
    open_conn,
    recent_diagnoses,
    recent_metric_points,
)
from pping_lang.api.schemas import BenchStartIn, RuleIn, RuleTestRequest
from pping_lang.bench import store as bench_store
from pping_lang.bench.client import OpenAIStreamClient
from pping_lang.bench.runner import run_static
from pping_lang.bench.scenarios.schema import SLO, StaticScenario
from pping_lang.hardware import GPUPeak
from pping_lang.metrics_catalog import ALLOWED_METRICS, M
from pping_lang.report.analysis import roofline_data
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
    gpu_name: str | None = None,
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

    # === GET /api/system — environment / model / GPU info for dashboard hero ===
    @app.get("/api/system")
    def system() -> dict[str, Any]:
        # vLLM version: real install first, then env override (demo)
        vllm_ver: str | None = None
        try:
            import vllm as _v
            vllm_ver = getattr(_v, "__version__", None)
        except Exception:
            vllm_ver = None
        if not vllm_ver:
            vllm_ver = os.environ.get("PPING_LANG_INFO_VLLM_VERSION") or None

        # Model name: from vllm_config.model_config.model, then env override
        model: str | None = None
        if vllm_config is not None:
            mc = getattr(vllm_config, "model_config", None)
            if mc is not None:
                model = getattr(mc, "model", None) or getattr(mc, "served_model_name", None)
        if not model:
            model = os.environ.get("PPING_LANG_INFO_MODEL") or None

        # GPU: NVML-detected first, then env override
        name = gpu_name or os.environ.get("PPING_LANG_INFO_GPU") or None
        count = nvml.num_gpus if (nvml and nvml.num_gpus) else None
        if count is None:
            env_count = os.environ.get("PPING_LANG_INFO_GPU_COUNT")
            if env_count and env_count.isdigit():
                count = int(env_count)

        peak: dict[str, float] | None = None
        if gpu_peak is not None:
            peak = {
                "bf16_tflops": gpu_peak.bf16_tflops,
                "mem_bw_gbs": gpu_peak.mem_bw_gbs,
            }
        else:
            env_tflops = os.environ.get("PPING_LANG_INFO_BF16_TFLOPS")
            env_bw = os.environ.get("PPING_LANG_INFO_MEM_BW_GBS")
            if env_tflops and env_bw:
                try:
                    peak = {"bf16_tflops": float(env_tflops), "mem_bw_gbs": float(env_bw)}
                except ValueError:
                    peak = None

        return {
            "vllm_version": vllm_ver,
            "model": model,
            "gpu_name": name,
            "gpu_count": count,
            "gpu_peak": peak,
            "instance_id": instance_id,
            "engine_index": engine_index,
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

    # === GET /api/kpis — curated KPI bundle for dashboard (one round-trip) ===
    @app.get("/api/kpis")
    def kpis(
        window: int = Query(60, ge=5, le=3600, description="Aggregation window (s)"),
    ) -> dict[str, Any]:
        since_ns = time.monotonic_ns() - int(window * 1e9)
        conn = open_conn(db_path)
        try:
            try:
                latest = latest_per_metric(conn, since_ns)
            except Exception:
                latest = {}

            def latest_val(name: str) -> float | None:
                row = latest.get(name)
                return row["value"] if row else None

            def agg(name: str, op: str) -> float | None:
                try:
                    return aggregate_metric(conn, name, since_ns, op)
                except Exception:
                    return None

            # TTFT (always per-iter list, p50/p99 over window)
            ttft_p50 = agg(M.VLLM_REQ_TTFT_MS, "p50")
            ttft_p99 = agg(M.VLLM_REQ_TTFT_MS, "p99")

            # TPOT preferred (per-finished-request), fall back to ITL (per-iter)
            tpot_p50 = agg(M.VLLM_REQ_TPOT_MS, "p50") or agg(M.VLLM_REQ_ITL_MS, "p50")
            tpot_p99 = agg(M.VLLM_REQ_TPOT_MS, "p99") or agg(M.VLLM_REQ_ITL_MS, "p99")

            # Output throughput: sum gen tokens / window seconds
            gen_sum = agg(M.VLLM_ITER_GEN_TOKENS, "sum")
            output_tps = (gen_sum / window) if gen_sum is not None else None

            # Preemption rate per minute
            preempt_sum = agg(M.VLLM_ITER_PREEMPTED_REQS, "sum")
            preempt_per_min = (
                preempt_sum * 60.0 / window if preempt_sum is not None else None
            )
        finally:
            conn.close()

        return {
            "window_seconds": window,
            "kpis": {
                "ttft_p50_ms": ttft_p50,
                "ttft_p99_ms": ttft_p99,
                "tpot_p50_ms": tpot_p50,
                "tpot_p99_ms": tpot_p99,
                "output_tps": output_tps,
                "kv_cache": latest_val(M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO),
                "running_reqs": latest_val(M.VLLM_SCHEDULER_RUNNING_REQS),
                "waiting_reqs": latest_val(M.VLLM_SCHEDULER_WAITING_REQS),
                "mfu": latest_val(M.VLLM_PERF_MFU_RATIO),
                "padding_ratio": latest_val(M.VLLM_CUDAGRAPH_PADDING_RATIO),
                "prefix_cache_hit": latest_val(M.VLLM_SCHEDULER_PREFIX_CACHE_HIT_RATIO),
                "preempt_per_min": preempt_per_min,
                "gpu_util": latest_val(M.GPU_UTIL_PCT),
            },
        }

    # === GET /api/latency_trends — TTFT / TPOT / E2E bucketed p50+p99 over time ===
    @app.get("/api/latency_trends")
    def latency_trends(
        seconds: int = Query(300, ge=30, le=86400),
        buckets: int = Query(30, ge=5, le=240),
    ) -> dict[str, Any]:
        now_ns = time.monotonic_ns()
        since_ns = now_ns - int(seconds * 1e9)
        conn = open_conn(db_path)
        try:
            ttft = bucketed_quantiles(conn, M.VLLM_REQ_TTFT_MS, since_ns, now_ns, buckets)
            # TPOT prefers per-finished-request, fall back to per-iter ITL when missing
            tpot = bucketed_quantiles(conn, M.VLLM_REQ_TPOT_MS, since_ns, now_ns, buckets)
            tpot_source = "tpot"
            if not tpot:
                tpot = bucketed_quantiles(
                    conn, M.VLLM_REQ_ITL_MS, since_ns, now_ns, buckets,
                )
                tpot_source = "itl"
            e2e = bucketed_quantiles(
                conn, M.VLLM_REQ_E2E_LATENCY_MS, since_ns, now_ns, buckets,
            )
        finally:
            conn.close()
        return {
            "seconds": seconds,
            "buckets": buckets,
            "ttft_ms": ttft,
            "tpot_ms": tpot,
            "tpot_source": tpot_source,
            "e2e_ms": e2e,
        }

    # === GET /api/roofline — live Roofline scatter (points + peak roofs) ===
    @app.get("/api/roofline")
    def roofline(
        seconds: int = Query(60, ge=5, le=3600),
    ) -> dict[str, Any]:
        since_ns = time.monotonic_ns() - int(seconds * 1e9)
        conn = open_conn(db_path)
        try:
            try:
                points = roofline_data(conn, since_ns)
            except Exception:
                points = []
        finally:
            conn.close()

        # Peak from gpu_peak param, or fall back to env (demo)
        peak: dict[str, float] | None = None
        if gpu_peak is not None:
            peak = {
                "compute_tflops": gpu_peak.bf16_tflops,
                "mem_bw_tbs": gpu_peak.mem_bw_gbs / 1000.0,
            }
        else:
            env_tflops = os.environ.get("PPING_LANG_INFO_BF16_TFLOPS")
            env_bw = os.environ.get("PPING_LANG_INFO_MEM_BW_GBS")
            if env_tflops and env_bw:
                try:
                    peak = {
                        "compute_tflops": float(env_tflops),
                        "mem_bw_tbs": float(env_bw) / 1000.0,
                    }
                except ValueError:
                    peak = None

        return {"seconds": seconds, "points": points, "peak": peak}

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

    # ─────────────────────────────────────────────────────────────────────
    #  BENCH API — see docs/bench-design-v0.1.md §10
    # ─────────────────────────────────────────────────────────────────────

    # In-memory registry of currently-running bench runs (asyncio Task references).
    # DB holds the canonical state; this dict is just for "is X live right now".
    _bench_runs: dict[str, dict[str, Any]] = {}

    # Initialize bench_runs table once. Re-init at every endpoint is also defensive
    # (in case of race with first vLLM step on a fresh DB).
    try:
        _init_conn = open_conn(db_path)
        try:
            bench_store.init_bench_table(_init_conn)
        finally:
            _init_conn.close()
    except Exception as e:
        logger.warning("[pping-lang] bench_runs table init deferred: %s", e)

    async def _execute_bench(
        run_id: str, scenario: StaticScenario,
    ) -> None:
        """Run a bench in-process; finalize DB on completion or failure."""
        try:
            async with OpenAIStreamClient(
                scenario.endpoint, timeout_s=scenario.timeout_s,
            ) as client:
                summary = await run_static(scenario, client)
            conn = open_conn(db_path)
            try:
                bench_store.init_bench_table(conn)
                slo = bench_store.evaluate_slo(summary, scenario)
                bench_store.mark_done(
                    conn, run_id, time.monotonic_ns(), summary, slo_status=slo,
                )
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.exception("[bench] run %s failed", run_id)
            try:
                conn = open_conn(db_path)
                try:
                    bench_store.mark_failed(
                        conn, run_id, time.monotonic_ns(),
                        f"{type(e).__name__}: {e}",
                    )
                finally:
                    conn.close()
            except Exception:
                logger.exception("[bench] failed to record failure for %s", run_id)
        finally:
            _bench_runs.pop(run_id, None)

    # === GET /api/bench/runs — list past + currently running ===
    @app.get("/api/bench/runs")
    def bench_list(
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        conn = open_conn(db_path)
        try:
            try:
                bench_store.init_bench_table(conn)
                runs = bench_store.list_runs(conn, limit=limit)
            except Exception:
                runs = []
        finally:
            conn.close()
        # Mark which runs are alive in this process right now
        for r in runs:
            r["live"] = r["run_id"] in _bench_runs
        # now_ns lets the UI compute "X seconds ago" against the server's
        # monotonic clock (started_at_ns uses time.monotonic_ns, not wall clock)
        return {"runs": runs, "now_ns": time.monotonic_ns()}

    # === GET /api/bench/runs/{run_id} — single run detail ===
    @app.get("/api/bench/runs/{run_id}")
    def bench_detail(run_id: str) -> dict[str, Any]:
        conn = open_conn(db_path)
        try:
            run = bench_store.get_run(conn, run_id)
        finally:
            conn.close()
        if run is None:
            raise HTTPException(404, f"bench run {run_id!r} not found")
        run["live"] = run_id in _bench_runs
        return run

    # === GET /api/bench/status — currently running snapshot ===
    @app.get("/api/bench/status")
    def bench_status() -> dict[str, Any]:
        return {
            "running": [
                {
                    "run_id": rid,
                    "scenario_name": meta["scenario_name"],
                    "started_at_ns": meta["started_at_ns"],
                }
                for rid, meta in _bench_runs.items()
            ],
        }

    # === POST /api/bench/start — kick off a new run, returns 202 ===
    @app.post("/api/bench/start", status_code=202)
    async def bench_start(body: BenchStartIn) -> dict[str, Any]:
        # v0.1 API does single static runs only — sweep stays CLI-only.
        if body.sweep:
            raise HTTPException(
                501,
                "sweep mode not yet supported via API in v0.1; use `python -m pping_lang.bench static --sweep ...`",
            )

        # num_requests wins over duration_s if both set
        duration_s = body.duration_s
        num_requests = body.num_requests
        if num_requests is not None:
            duration_s = None

        name = body.name or f"adhoc-{int(time.time())}"
        try:
            slo_obj = SLO.from_spec(body.slo) if body.slo else None
            scenario = StaticScenario(
                name=name,
                endpoint=body.endpoint,
                model=body.model,
                prompt_tokens=body.prompt_tokens,
                output_tokens=body.output_tokens,
                concurrency=body.concurrency,
                duration_s=duration_s,
                num_requests=num_requests,
                warmup_s=body.warmup_s,
                timeout_s=body.timeout_s,
                api=body.api,
                slo=slo_obj,
            )
            scenario.validate()
        except (ValueError, TypeError) as e:
            raise HTTPException(422, f"invalid scenario: {e}")

        # Persist initial 'running' row
        conn = open_conn(db_path)
        try:
            bench_store.init_bench_table(conn)
            run_id = bench_store.generate_run_id(conn, "static")
            started_at_ns = time.monotonic_ns()
            bench_store.insert_running(
                conn, run_id, scenario, "static", started_at_ns,
            )
        finally:
            conn.close()

        # Fire-and-forget asyncio task; finalization is in _execute_bench
        task = asyncio.create_task(_execute_bench(run_id, scenario))
        _bench_runs[run_id] = {
            "task": task,
            "scenario_name": scenario.name,
            "started_at_ns": started_at_ns,
        }

        return {
            "run_id": run_id,
            "status": "running",
            "started_at_ns": started_at_ns,
            "scenario_name": scenario.name,
        }

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
