"""run_static — verify warmup discard, num_requests/duration bounds, fail_fast."""
from __future__ import annotations

import asyncio
import time

import pytest

from pping_lang.bench.measurement import RequestSample
from pping_lang.bench.runner import run_static
from pping_lang.bench.scenarios.schema import StaticScenario


class MockClient:
    """Minimal client that records each call and returns synthetic samples.

    The `latency_s` controls how long each request "takes" (asyncio.sleep) so
    we can deterministically count requests-per-second under concurrency K.
    """

    def __init__(self, latency_s: float = 0.05, fail_every: int = 0):
        self.calls: list[str] = []
        self.latency_s = latency_s
        self.fail_every = fail_every  # 0 disables; N means every Nth call fails

    async def chat(self, model: str, prompt: str, output_tokens: int) -> RequestSample:
        n = len(self.calls)
        self.calls.append("chat")
        await asyncio.sleep(self.latency_s)
        now = time.monotonic_ns()
        if self.fail_every and (n + 1) % self.fail_every == 0:
            return RequestSample(
                started_ns=now, finished_ns=now, error="mock_failure"
            )
        return RequestSample(
            started_ns=now,
            first_token_ns=now + 10_000_000,
            finished_ns=now + 50_000_000,
            output_tokens=output_tokens,
            input_tokens=100,
        )

    async def completions(self, *a, **kw) -> RequestSample:
        return await self.chat(*a, **kw)


@pytest.fixture
def base_scenario():
    return StaticScenario(
        name="t",
        endpoint="http://mock",
        model="m",
        prompt_tokens=50,
        output_tokens=20,
        concurrency=4,
        duration_s=None,
        num_requests=20,
        warmup_s=0,
        timeout_s=2.0,
    )


async def test_runs_num_requests_exactly(base_scenario):
    client = MockClient(latency_s=0.01)
    summary = await run_static(base_scenario, client)
    assert summary.total == 20
    assert summary.ok == 20
    assert summary.errors == 0
    assert len(client.calls) == 20


async def test_duration_bounded(base_scenario):
    base_scenario.num_requests = None
    base_scenario.duration_s = 1  # 1 sec
    base_scenario.concurrency = 8
    base_scenario.warmup_s = 0
    client = MockClient(latency_s=0.05)
    t0 = time.monotonic()
    summary = await run_static(base_scenario, client)
    elapsed = time.monotonic() - t0
    # Should take ~1s plus a bit for in-flight completions
    assert 0.9 < elapsed < 2.5, f"elapsed {elapsed:.2f}s out of bounds"
    # 8 concurrency × ~20 req/s × 1s ≈ 160 requests; allow wide bound
    assert summary.total >= 50, f"too few requests: {summary.total}"


async def test_warmup_samples_discarded(base_scenario):
    base_scenario.warmup_s = 1
    base_scenario.num_requests = 5
    client = MockClient(latency_s=0.02)
    summary = await run_static(base_scenario, client)
    # Total measured == num_requests; warmup calls happened but were dropped
    assert summary.total == 5
    # Client should have been hit more than 5 times (warmup + measured)
    assert len(client.calls) > 5


async def test_fail_fast_stops_on_first_error(base_scenario):
    base_scenario.num_requests = 100
    base_scenario.fail_fast = True
    base_scenario.concurrency = 2
    client = MockClient(latency_s=0.02, fail_every=3)
    summary = await run_static(base_scenario, client)
    # fail_fast should stop early, well before 100
    assert summary.total < 100
    # And at least one error happened
    assert summary.errors >= 1


async def test_throughput_calculated(base_scenario):
    base_scenario.num_requests = 10
    client = MockClient(latency_s=0.01)
    summary = await run_static(base_scenario, client)
    # output_tokens per request = 20, total = 200
    assert summary.output_tokens_total == 200
    # tps depends on actual duration, but should be positive
    assert summary.output_throughput_tps > 0
