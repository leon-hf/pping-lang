"""Repeated bench aggregation for M1 noise reduction."""
from __future__ import annotations

import statistics

from pping_lang.autopilot.objective import Scorecard


_FIELDS = ("output_tps", "ttft_p99_ms", "tpot_p99_ms", "e2e_p99_ms", "error_rate")


def _series(samples: list[Scorecard], field: str) -> list[float]:
    return [float(getattr(s, field)) for s in samples]


def aggregate_scorecards(samples: list[Scorecard]) -> Scorecard:
    """Aggregate repeated bench samples into one scorecard plus stats in run_meta."""
    if not samples:
        raise ValueError("aggregate_scorecards requires at least one sample")
    if len(samples) == 1:
        sc = samples[0]
        sc.run_meta.setdefault("bench_repeats", 1)
        return sc
    vals = {f: _series(samples, f) for f in _FIELDS}
    meta = dict(samples[-1].run_meta)
    meta["bench_repeats"] = len(samples)
    meta["repeat_samples"] = [s.to_dict() for s in samples]
    meta["repeat_stats"] = {
        f: {
            "mean": sum(v) / len(v),
            "median": statistics.median(v),
            "min": min(v),
            "max": max(v),
            "stdev": statistics.stdev(v) if len(v) > 1 else 0.0,
        }
        for f, v in vals.items()
    }
    return Scorecard(
        output_tps=round(meta["repeat_stats"]["output_tps"]["mean"], 1),
        ttft_p99_ms=round(meta["repeat_stats"]["ttft_p99_ms"]["mean"], 0),
        tpot_p99_ms=round(meta["repeat_stats"]["tpot_p99_ms"]["mean"], 1),
        e2e_p99_ms=round(meta["repeat_stats"]["e2e_p99_ms"]["mean"], 0),
        error_rate=meta["repeat_stats"]["error_rate"]["mean"],
        run_meta=meta,
    )
