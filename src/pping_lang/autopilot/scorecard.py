"""记分牌 —— 跑标准 bench → 组装 scorecard(§6.2)。

scorecard = client-side bench(`bench/runner.run_static` + `OpenAIStreamClient`)+ 候选当时的
诊断快照。`scorecard_view()` 把 sla_ok / objective_score / primary_delta_pct 内联进记录,供
round 记录与 UI 直接用(§6.2 / §9.4)。

标准 bench 固定档(保证候选间可比,§6.2):concurrency=8 / duration_s=30 / warmup_s=10 /
prompt_source=builtin:mixed-short / prompt_tokens=500 / output_tokens=128;warmup 段不计分;
error_rate>0.5 = 不可接受。
"""
from __future__ import annotations

from pping_lang.autopilot.objective import (
    ObjectiveSpec,
    Scorecard,
    objective_score,
    primary_delta_pct,
    sla_ok,
)

BENCH_SPEC = {"concurrency": 8, "duration_s": 30, "warmup_s": 20, "timeout_s": 60,
              "prompt_source": "builtin:mixed-short", "prompt_tokens": 500,
              "output_tokens": 128}


class BenchError(RuntimeError):
    """bench 超时 / 0 成功样本 → 判负回滚。"""


def bench_scorecard(endpoint: str, model: str, spec: dict,
                    run_meta: dict | None = None) -> Scorecard:
    """对一个真实 OpenAI 兼容端点跑标准 bench → Scorecard(复用 bench/runner + client)。"""
    import asyncio

    from pping_lang.bench.client import OpenAIStreamClient
    from pping_lang.bench.runner import run_static
    from pping_lang.bench.scenarios.schema import StaticScenario

    timeout = float(spec.get("timeout_s", 60))
    scen = StaticScenario(
        name="autopilot", endpoint=endpoint, model=model,
        concurrency=int(spec["concurrency"]), duration_s=int(spec["duration_s"]),
        warmup_s=int(spec.get("warmup_s", 10)), output_tokens=int(spec.get("output_tokens", 128)),
        prompt_tokens=int(spec.get("prompt_tokens", 500)),
        prompt_source=spec.get("prompt_source", "synthetic"), timeout_s=timeout)

    async def _run():
        # The client pool must not become the hidden concurrency cap. The
        # default pool allows ~128 active connections; D/KV probes often need
        # higher concurrency to fill vLLM's running queue.
        async with OpenAIStreamClient(endpoint, timeout_s=timeout,
                                      max_keepalive=max(64, scen.concurrency)) as c:
            return await run_static(scen, c)

    rs = asyncio.run(_run())
    if rs.ok == 0:
        raise BenchError(f"bench 0 个成功样本(共 {rs.total})→ 候选不可用,判负")
    return Scorecard(
        output_tps=round(rs.output_throughput_tps, 1),
        ttft_p99_ms=round(rs.ttft_ms.p99 or 0.0, 0),
        tpot_p99_ms=round(rs.tpot_ms.p99 or 0.0, 1),
        e2e_p99_ms=round(rs.e2e_ms.p99 or 0.0, 0),
        error_rate=rs.error_rate,
        run_meta={**(run_meta or {}), "concurrency": scen.concurrency,
                  "duration_s": scen.duration_s, "warmup_s": scen.warmup_s,
                  "prompt_source": scen.prompt_source, "prompt_tokens": scen.prompt_tokens,
                  "output_tokens": scen.output_tokens, "ok": rs.ok, "total": rs.total,
                  "model": model, "sim": False},
    )


def scorecard_view(sc: Scorecard, obj: ObjectiveSpec, *,
                   best_sc: Scorecard | None = None,
                   diagnosis: dict | None = None) -> dict:
    """§6.2 记分牌视图:bench 字段 + sla_ok + objective_score + primary_delta_pct + 诊断快照。"""
    score = objective_score(sc, obj)
    d = sc.to_dict()
    d["sla_ok"] = sla_ok(sc, obj)
    d["objective_score"] = None if score == float("-inf") else score
    if best_sc is not None:
        d["primary_delta_pct"] = primary_delta_pct(sc, best_sc, obj)
    if diagnosis is not None:
        d["diagnosis"] = diagnosis
    return d
