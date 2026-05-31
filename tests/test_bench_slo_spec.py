"""SLO.from_spec parser — constraint-string syntax (llm-optimizer-inspired)."""
from __future__ import annotations

import pytest

from pping_lang.bench.measurement import LatencyStats, RunSummary
from pping_lang.bench.scenarios.schema import SLO, Threshold


def _summary(**kw) -> RunSummary:
    base = dict(
        total=10, ok=10, errors=0, duration_s=1.0,
        ttft_ms=LatencyStats(n=10, p50=100, p99=200),
        tpot_ms=LatencyStats(n=10, p50=20, p99=40),
        e2e_ms=LatencyStats(n=10, p50=1000, p99=2000),
    )
    base.update(kw)
    return RunSummary(**base)


# ===== parsing happy paths =====


def test_parse_ttft_p99_ms():
    slo = SLO.from_spec("ttft:p99<500ms")
    assert len(slo.thresholds) == 1
    t = slo.thresholds[0]
    assert t.metric == "ttft"
    assert t.percentile == "p99"
    assert t.op == "<"
    assert t.value == 500.0


def test_parse_seconds_unit_converts_to_ms():
    slo = SLO.from_spec("e2e:p95<2s")
    assert slo.thresholds[0].value == 2000.0


def test_parse_error_rate_no_percentile_no_unit():
    slo = SLO.from_spec("error_rate<0.01")
    t = slo.thresholds[0]
    assert t.metric == "error_rate"
    assert t.percentile is None
    assert t.value == 0.01


def test_parse_multiple_constraints():
    slo = SLO.from_spec("ttft:p99<500ms;tpot:p99<50ms;error_rate<0.01")
    assert len(slo.thresholds) == 3
    assert [t.metric for t in slo.thresholds] == ["ttft", "tpot", "error_rate"]


def test_parse_handles_whitespace():
    slo = SLO.from_spec("  ttft:p99 < 500ms  ;  tpot:p99 < 50ms  ")
    assert len(slo.thresholds) == 2


def test_parse_all_ops():
    for op in ("<", "<=", ">", ">="):
        slo = SLO.from_spec(f"ttft:p99{op}100ms")
        assert slo.thresholds[0].op == op


def test_parse_all_percentiles():
    for pct in ("mean", "p50", "p90", "p95", "p99"):
        slo = SLO.from_spec(f"ttft:{pct}<100ms")
        assert slo.thresholds[0].percentile == pct


def test_parse_empty_string_yields_empty_slo():
    assert SLO.from_spec("").is_empty()
    assert SLO.from_spec("   ").is_empty()
    assert SLO.from_spec(None).is_empty()


# ===== parsing error paths =====


def test_parse_rejects_unknown_metric():
    with pytest.raises(ValueError, match="unknown SLO metric"):
        SLO.from_spec("throughput:p99<100ms")


def test_parse_rejects_missing_percentile_for_latency():
    with pytest.raises(ValueError, match="requires percentile"):
        SLO.from_spec("ttft<500ms")


def test_parse_rejects_missing_unit_for_latency():
    with pytest.raises(ValueError, match="needs unit"):
        SLO.from_spec("ttft:p99<500")


def test_parse_rejects_unit_for_error_rate():
    with pytest.raises(ValueError, match="must be unitless"):
        SLO.from_spec("error_rate<0.01s")


def test_parse_rejects_percentile_for_error_rate():
    with pytest.raises(ValueError, match="cannot take percentile"):
        SLO.from_spec("error_rate:p99<0.01")


def test_parse_rejects_error_rate_out_of_range():
    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        SLO.from_spec("error_rate<5")


def test_parse_rejects_unknown_percentile():
    with pytest.raises(ValueError, match="unknown percentile"):
        SLO.from_spec("ttft:p77<100ms")


def test_parse_rejects_garbage():
    with pytest.raises(ValueError, match="invalid SLO constraint"):
        SLO.from_spec("not a constraint at all")


# ===== round-trip =====


def test_round_trip_canonical_form():
    """Parse → as_spec should produce a canonical string. Re-parsing yields same."""
    original = "ttft:p99<500ms;error_rate<0.01"
    slo = SLO.from_spec(original)
    canon = slo.as_spec()
    assert canon == original
    again = SLO.from_spec(canon)
    assert again.thresholds == slo.thresholds


# ===== evaluation =====


def test_check_threshold_pass():
    t = Threshold(metric="ttft", percentile="p99", op="<", value=300)
    assert t.check(_summary()) is True  # 200 < 300


def test_check_threshold_fail():
    t = Threshold(metric="ttft", percentile="p99", op="<", value=100)
    assert t.check(_summary()) is False  # 200 NOT < 100


def test_check_threshold_no_data_returns_none():
    t = Threshold(metric="ttft", percentile="p99", op="<", value=100)
    s = _summary(ttft_ms=LatencyStats(n=0))
    assert t.check(s) is None


def test_check_error_rate():
    s = _summary(total=100, ok=90, errors=10)
    t_fail = Threshold(metric="error_rate", percentile=None, op="<", value=0.05)
    t_pass = Threshold(metric="error_rate", percentile=None, op="<", value=0.5)
    assert t_fail.check(s) is False
    assert t_pass.check(s) is True
