"""RequestSample 派生指标 + percentile + aggregate."""
from __future__ import annotations

import math

import pytest

from pping_lang.bench.measurement import (
    RequestSample,
    aggregate,
    latency_stats,
)

# ===== RequestSample property derivation =====


def test_ok_requires_finished_and_no_error():
    s = RequestSample(started_ns=0)
    assert not s.ok
    s.finished_ns = 100
    assert s.ok
    s.error = "boom"
    assert not s.ok


def test_ttft_ms_returns_none_when_no_first_token():
    s = RequestSample(started_ns=0)
    assert s.ttft_ms is None


def test_ttft_ms_basic():
    s = RequestSample(started_ns=0, first_token_ns=150_000_000)  # 150ms
    assert s.ttft_ms == pytest.approx(150.0)


def test_e2e_ms_basic():
    s = RequestSample(started_ns=0, finished_ns=1_500_000_000)  # 1.5s
    assert s.e2e_ms == pytest.approx(1500.0)


def test_tpot_ms_basic():
    # first token at 100ms, last token at 1100ms, 11 output tokens
    # → 10 intervals over 1000ms → tpot = 100ms
    s = RequestSample(
        started_ns=0,
        first_token_ns=100_000_000,
        finished_ns=1_100_000_000,
        output_tokens=11,
    )
    assert s.tpot_ms == pytest.approx(100.0)


def test_tpot_none_when_too_few_tokens():
    s = RequestSample(
        started_ns=0,
        first_token_ns=100_000_000,
        finished_ns=200_000_000,
        output_tokens=1,
    )
    assert s.tpot_ms is None


# ===== percentile / latency_stats =====


def test_latency_stats_empty():
    out = latency_stats([])
    assert out.n == 0
    assert out.p50 is None
    assert out.p99 is None


def test_latency_stats_single():
    out = latency_stats([42.0])
    assert out.n == 1
    assert out.p50 == 42.0
    assert out.p99 == 42.0


def test_latency_stats_basic_percentiles():
    values = [float(x) for x in range(1, 101)]  # 1..100
    out = latency_stats(values)
    assert out.n == 100
    # linear interp percentile: q*(n-1) index
    # p50 → index 49.5 → 50.5
    assert out.p50 == pytest.approx(50.5)
    # p99 → index 98.01 → ~99.01
    assert out.p99 == pytest.approx(99.01)
    assert out.min == 1.0
    assert out.max == 100.0


def test_latency_stats_filters_nan():
    out = latency_stats([1.0, float("nan"), 2.0])
    assert out.n == 2  # nan dropped


# ===== aggregate =====


def _ok_sample(ttft_ns: int, finish_ns: int, out_tokens: int, in_tokens: int = 50):
    return RequestSample(
        started_ns=0,
        first_token_ns=ttft_ns,
        finished_ns=finish_ns,
        output_tokens=out_tokens,
        input_tokens=in_tokens,
    )


def test_aggregate_basic_throughput():
    samples = [
        _ok_sample(100_000_000, 1_000_000_000, 100),
        _ok_sample(150_000_000, 1_500_000_000, 100),
    ]
    summary = aggregate(samples, duration_s=10.0)
    assert summary.total == 2
    assert summary.ok == 2
    assert summary.errors == 0
    # output tps = 200 / 10
    assert summary.output_throughput_tps == pytest.approx(20.0)
    assert summary.input_throughput_tps == pytest.approx(10.0)


def test_aggregate_mixed_ok_and_errors():
    samples = [
        _ok_sample(100_000_000, 1_000_000_000, 100),
        RequestSample(started_ns=0, finished_ns=100, error="http_503: down"),
        RequestSample(started_ns=0, finished_ns=100, error="timeout"),
    ]
    summary = aggregate(samples, duration_s=5.0)
    assert summary.total == 3
    assert summary.ok == 1
    assert summary.errors == 2
    assert summary.error_rate == pytest.approx(2 / 3)
    # Only OK samples feed throughput / latency
    assert summary.ttft_ms.n == 1
    assert "http_503: down" in summary.error_samples


def test_aggregate_zero_duration_no_div_zero():
    summary = aggregate([], duration_s=0.0)
    assert summary.output_throughput_tps == 0.0
    assert summary.total == 0
    assert math.isnan(summary.error_rate) or summary.error_rate == 0.0
