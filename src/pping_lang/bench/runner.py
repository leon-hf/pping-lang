"""Static scenario runner — see design doc §7.3.

Keep `scenario.concurrency` workers in flight at all times. Bounded by either
`duration_s` (wall-clock) or `num_requests` (count). Warmup samples are
discarded but the worker tasks continue uninterrupted into the measured phase.

The runner is intentionally client-protocol-agnostic — it takes anything with
`async chat(model, prompt, output_tokens)` and `async completions(...)`. Tests
inject a mock; production uses `OpenAIStreamClient`.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pping_lang.bench.measurement import RequestSample, RunSummary, aggregate
from pping_lang.bench.prompts import load_prompts
from pping_lang.bench.scenarios.schema import StaticScenario


@dataclass(slots=True)
class SweepPoint:
    """One leg of a sweep: (param value, resulting summary)."""

    value: int | float
    summary: RunSummary


@dataclass(slots=True)
class SweepResult:
    param: str
    points: list[SweepPoint] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "param": self.param,
            "points": [
                {"value": p.value, "summary": p.summary.as_dict()}
                for p in self.points
            ],
        }

logger = logging.getLogger(__name__)


async def run_static(
    scenario: StaticScenario,
    client: Any,
    on_progress: Any = None,
    progress_interval_s: float = 10.0,
) -> RunSummary:
    """Execute one static scenario against `client`. Returns aggregated summary.

    `client` must implement async `chat(model, prompt, output_tokens)` and
    `completions(model, prompt, output_tokens)` returning `RequestSample`.

    `on_progress`(可选): 采集期每 `progress_interval_s` 秒回调一次运行中快照
    {elapsed_s, ok, errors, tps, ttft_p50_ms} —— 长压测窗把分数"长出来"的过程
    喂给上层直播,而非憋到最后只出总分。回调异常不影响压测。
    """
    scenario.validate()

    # Resolve prompt source → list of strings. `prompt_text` (fixed prompt)
    # wins over `prompt_source` (dataset/file/synthetic). Cycle through the
    # list so a 50-entry dataset can serve any num_requests; per-worker
    # selection is round-robin via a shared counter.
    if scenario.prompt_text:
        prompts: list[str] = [scenario.prompt_text]
    else:
        prompts = load_prompts(scenario.prompt_source, scenario.prompt_tokens)
    prompt_idx = 0  # rotated under counter_lock so two workers don't race
    n_prompts = len(prompts)

    # Shared mutable state across worker coroutines (single-threaded async loop,
    # explicit lock kept only for the counter decrement to be obvious).
    state = {
        "stop": False,
        "collect": False,
        "remaining": scenario.num_requests,  # None if duration-bounded
    }
    samples: list[RequestSample] = []
    counter_lock = asyncio.Lock()

    async def call_once() -> RequestSample:
        # Pick the next prompt round-robin. n_prompts==1 short-circuits.
        nonlocal prompt_idx
        if n_prompts == 1:
            this_prompt = prompts[0]
        else:
            async with counter_lock:
                this_prompt = prompts[prompt_idx % n_prompts]
                prompt_idx += 1
        try:
            if scenario.api == "chat":
                coro = client.chat(scenario.model, this_prompt, scenario.output_tokens)
            else:
                coro = client.completions(scenario.model, this_prompt, scenario.output_tokens)
            return await asyncio.wait_for(coro, timeout=scenario.timeout_s)
        except asyncio.TimeoutError:
            now = time.monotonic_ns()
            return RequestSample(
                started_ns=now, finished_ns=now, error="client_timeout",
            )

    async def worker() -> None:
        while not state["stop"]:
            # Bind the collect-or-warmup decision at iteration start. Otherwise a
            # request fired during warmup that completes AFTER the collect flag
            # flips would be counted — leaks warmup-tail samples into the
            # measured set (race window = single in-flight request per worker).
            is_collect_iter: bool = state["collect"]

            if is_collect_iter and state["remaining"] is not None:
                async with counter_lock:
                    if state["remaining"] <= 0:
                        return
                    state["remaining"] -= 1

            sample = await call_once()

            if is_collect_iter:
                samples.append(sample)

            if scenario.fail_fast and is_collect_iter and not sample.ok:
                state["stop"] = True
                return

    async def _reporter() -> None:
        while not state["collect"] and not state["stop"]:
            await asyncio.sleep(0.2)
        t0 = time.monotonic()
        while not state["stop"]:
            await asyncio.sleep(progress_interval_s)
            if state["stop"]:
                return
            oks = [s for s in samples if s.ok]
            elapsed = max(0.001, time.monotonic() - t0)
            toks = sum(s.output_tokens for s in oks)
            ttfts = sorted(s.ttft_ms for s in oks if s.ttft_ms is not None)
            try:
                on_progress({
                    "elapsed_s": int(round(elapsed)),
                    "ok": len(oks),
                    "errors": len(samples) - len(oks),
                    "tps": round(toks / elapsed, 1),
                    "ttft_p50_ms": round(ttfts[len(ttfts) // 2], 1) if ttfts else None,
                })
            except Exception:  # noqa: BLE001 — 直播回调绝不打断压测
                logger.debug("bench progress callback failed", exc_info=True)

    reporter = asyncio.create_task(_reporter()) if on_progress else None

    workers = [asyncio.create_task(worker()) for _ in range(scenario.concurrency)]

    try:
        if scenario.warmup_s > 0:
            logger.info("[bench] warmup %ds @ concurrency=%d",
                        scenario.warmup_s, scenario.concurrency)
            await asyncio.sleep(scenario.warmup_s)

        state["collect"] = True
        start_collect = time.monotonic()
        logger.info(
            "[bench] collecting (%s=%s) @ concurrency=%d",
            "duration_s" if scenario.duration_s is not None else "num_requests",
            scenario.duration_s if scenario.duration_s is not None else scenario.num_requests,
            scenario.concurrency,
        )

        if scenario.duration_s is not None:
            await asyncio.sleep(scenario.duration_s)
            state["stop"] = True

        # Wait for workers — either they exhaust num_requests, hit stop, or fail_fast
        await asyncio.gather(*workers, return_exceptions=True)
        duration_actual = time.monotonic() - start_collect

    finally:
        state["stop"] = True
        if reporter is not None and not reporter.done():
            reporter.cancel()
        # Defensive: any worker still pending gets the signal and exits next loop.
        # We don't cancel — let in-flight requests finish gracefully bounded by
        # `timeout_s`. Tests assert workers have completed before reading samples.
        for w in workers:
            if not w.done():
                w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if reporter is not None:
            await asyncio.gather(reporter, return_exceptions=True)

    return aggregate(samples, duration_actual)


async def run_sweep(
    scenario: StaticScenario,
    client: Any,
) -> SweepResult:
    """Iterate `scenario.sweep_param` over `sweep_values`, run_static each leg.

    The same vLLM endpoint sees the same client across legs — KV cache state
    carries over, which is realistic. Each leg has its own warmup as configured.
    """
    if not scenario.sweep_param or not scenario.sweep_values:
        raise ValueError("scenario has no sweep configured (sweep_param + sweep_values required)")

    # Cast values to int for token / concurrency params (the only sweepables in v0.1)
    legs: list[SweepPoint] = []
    for raw_value in scenario.sweep_values:
        value = int(raw_value)
        child = dataclasses.replace(
            scenario,
            sweep_param=None,
            sweep_values=[],
            **{scenario.sweep_param: value},
        )
        logger.info("[bench] sweep leg %s=%s", scenario.sweep_param, value)
        summary = await run_static(child, client)
        legs.append(SweepPoint(value=value, summary=summary))

    return SweepResult(param=scenario.sweep_param, points=legs)
