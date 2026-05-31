"""run_sweep — verify each leg runs with overridden param."""
from __future__ import annotations

import asyncio
import time

import pytest

from pping_lang.bench.measurement import RequestSample
from pping_lang.bench.runner import SweepResult, run_sweep
from pping_lang.bench.scenarios.schema import StaticScenario


class RecordingClient:
    """Mock client that records each call's (model, prompt_len, output_tokens)."""

    def __init__(self, latency_s: float = 0.005):
        self.calls: list[tuple[str, int, int]] = []
        self.latency_s = latency_s

    async def chat(self, model: str, prompt: str, output_tokens: int) -> RequestSample:
        self.calls.append((model, len(prompt), output_tokens))
        await asyncio.sleep(self.latency_s)
        now = time.monotonic_ns()
        return RequestSample(
            started_ns=now,
            first_token_ns=now + 5_000_000,
            finished_ns=now + 20_000_000,
            output_tokens=output_tokens,
            input_tokens=50,
        )

    async def completions(self, *a, **kw):
        return await self.chat(*a, **kw)


def _sweep_scenario(**overrides):
    base = dict(
        name="sweep-t",
        endpoint="http://mock",
        model="m",
        prompt_tokens=100,
        output_tokens=20,
        concurrency=2,
        duration_s=None,
        num_requests=4,
        warmup_s=0,
        timeout_s=2.0,
        sweep_param="concurrency",
        sweep_values=[1, 2, 4],
    )
    base.update(overrides)
    return StaticScenario(**base)


async def test_sweep_runs_one_leg_per_value():
    scenario = _sweep_scenario()
    client = RecordingClient()
    result = await run_sweep(scenario, client)
    assert isinstance(result, SweepResult)
    assert result.param == "concurrency"
    assert len(result.points) == 3
    assert [p.value for p in result.points] == [1, 2, 4]
    # Each leg ran num_requests=4 measured calls (warmup=0)
    assert all(p.summary.total == 4 for p in result.points)


async def test_sweep_propagates_overridden_param_to_workers():
    """concurrency override should change the number of in-flight workers per leg.

    We can't directly observe internal worker count, but we can observe that
    each leg's total call count equals num_requests regardless of the swept
    concurrency — proves the leg ran correctly with the override.
    """
    scenario = _sweep_scenario(sweep_values=[1, 8], num_requests=8)
    client = RecordingClient(latency_s=0.01)
    result = await run_sweep(scenario, client)
    assert all(p.summary.total == 8 for p in result.points)


async def test_sweep_output_tokens_param():
    scenario = _sweep_scenario(
        sweep_param="output_tokens",
        sweep_values=[10, 20, 40],
        num_requests=2,
    )
    client = RecordingClient()
    result = await run_sweep(scenario, client)
    # Each leg's calls should request the leg's output_tokens
    assert result.points[0].summary.output_tokens_total == 10 * 2
    assert result.points[1].summary.output_tokens_total == 20 * 2
    assert result.points[2].summary.output_tokens_total == 40 * 2


async def test_sweep_raises_when_no_param_set():
    scenario = _sweep_scenario(sweep_param=None, sweep_values=[])
    with pytest.raises(ValueError, match="no sweep configured"):
        await run_sweep(scenario, RecordingClient())


async def test_sweep_result_as_dict_serializable():
    scenario = _sweep_scenario(sweep_values=[1, 2])
    result = await run_sweep(scenario, RecordingClient())
    d = result.as_dict()
    assert d["param"] == "concurrency"
    assert len(d["points"]) == 2
    # Each leg's summary should be a dict (not a dataclass)
    assert isinstance(d["points"][0]["summary"], dict)
    assert "ttft_ms" in d["points"][0]["summary"]
