"""Client-side latency measurement — see design doc §15.2.

`RequestSample`: one request's full lifecycle (start, ttft, last_token, errors).
`LatencyStats`: percentile bundle for a metric.
`Aggregator`: build summary from a stream of completed RequestSamples.

Time semantics: every ts is `time.monotonic_ns()` — single source, no NTP jitter.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RequestSample:
    """One streamed request's lifecycle. All times are monotonic ns."""

    started_ns: int = 0
    first_token_ns: int | None = None
    finished_ns: int | None = None
    output_tokens: int = 0
    input_tokens: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.finished_ns is not None

    @property
    def ttft_ms(self) -> float | None:
        if self.first_token_ns is None:
            return None
        return (self.first_token_ns - self.started_ns) / 1e6

    @property
    def e2e_ms(self) -> float | None:
        if self.finished_ns is None:
            return None
        return (self.finished_ns - self.started_ns) / 1e6

    @property
    def tpot_ms(self) -> float | None:
        """Mean inter-token latency after first token.

        Needs >= 2 output tokens (else generation phase has no duration to divide).
        """
        if self.first_token_ns is None or self.finished_ns is None:
            return None
        if self.output_tokens < 2:
            return None
        gen_ns = self.finished_ns - self.first_token_ns
        # output_tokens - 1 intervals between tokens
        return (gen_ns / (self.output_tokens - 1)) / 1e6


@dataclass(slots=True)
class LatencyStats:
    """Percentile bundle for one metric over a set of requests."""

    n: int = 0
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None
    mean: float | None = None
    min: float | None = None
    max: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "p50": self.p50,
            "p90": self.p90,
            "p95": self.p95,
            "p99": self.p99,
            "mean": self.mean,
            "min": self.min,
            "max": self.max,
        }


def latency_stats(values: list[float]) -> LatencyStats:
    """Compute percentiles + mean over a numeric list. Empty → all None."""
    values = [v for v in values if v is not None and not math.isnan(v)]
    n = len(values)
    if n == 0:
        return LatencyStats(n=0)
    s = sorted(values)
    return LatencyStats(
        n=n,
        p50=_percentile(s, 0.50),
        p90=_percentile(s, 0.90),
        p95=_percentile(s, 0.95),
        p99=_percentile(s, 0.99),
        mean=sum(values) / n,
        min=s[0],
        max=s[-1],
    )


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation percentile on a pre-sorted list. q in [0, 1]."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = q * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


@dataclass(slots=True)
class RunSummary:
    """Aggregated result of a bench run — sent to report / API / CLI."""

    total: int = 0
    ok: int = 0
    errors: int = 0
    duration_s: float = 0.0
    ttft_ms: LatencyStats = field(default_factory=LatencyStats)
    tpot_ms: LatencyStats = field(default_factory=LatencyStats)
    e2e_ms: LatencyStats = field(default_factory=LatencyStats)
    output_tokens_total: int = 0
    input_tokens_total: int = 0
    output_throughput_tps: float = 0.0
    input_throughput_tps: float = 0.0
    error_samples: list[str] = field(default_factory=list)  # truncated

    @property
    def error_rate(self) -> float:
        return self.errors / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "ok": self.ok,
            "errors": self.errors,
            "error_rate": self.error_rate,
            "duration_s": self.duration_s,
            "ttft_ms": self.ttft_ms.as_dict(),
            "tpot_ms": self.tpot_ms.as_dict(),
            "e2e_ms": self.e2e_ms.as_dict(),
            "output_tokens_total": self.output_tokens_total,
            "input_tokens_total": self.input_tokens_total,
            "output_throughput_tps": self.output_throughput_tps,
            "input_throughput_tps": self.input_throughput_tps,
            "error_samples": self.error_samples,
        }


def aggregate(samples: list[RequestSample], duration_s: float) -> RunSummary:
    """Roll a flat list of samples into a RunSummary."""
    total = len(samples)
    ok_samples = [s for s in samples if s.ok]
    errors = [s for s in samples if not s.ok]
    error_msgs: list[str] = []
    for e in errors[:20]:
        if e.error:
            error_msgs.append(e.error)

    ttft_vals = [s.ttft_ms for s in ok_samples if s.ttft_ms is not None]
    tpot_vals = [s.tpot_ms for s in ok_samples if s.tpot_ms is not None]
    e2e_vals = [s.e2e_ms for s in ok_samples if s.e2e_ms is not None]

    out_tokens = sum(s.output_tokens for s in ok_samples)
    in_tokens = sum(s.input_tokens for s in ok_samples)
    out_tps = (out_tokens / duration_s) if duration_s > 0 else 0.0
    in_tps = (in_tokens / duration_s) if duration_s > 0 else 0.0

    return RunSummary(
        total=total,
        ok=len(ok_samples),
        errors=len(errors),
        duration_s=duration_s,
        ttft_ms=latency_stats(ttft_vals),  # type: ignore[arg-type]
        tpot_ms=latency_stats(tpot_vals),  # type: ignore[arg-type]
        e2e_ms=latency_stats(e2e_vals),  # type: ignore[arg-type]
        output_tokens_total=out_tokens,
        input_tokens_total=in_tokens,
        output_throughput_tps=out_tps,
        input_throughput_tps=in_tps,
        error_samples=error_msgs,
    )
