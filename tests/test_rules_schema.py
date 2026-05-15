"""Rule / Condition schema 验证。"""
from __future__ import annotations

import pytest

from pping_lang.metrics_catalog import M
from pping_lang.rules.schema import Condition, Rule, validate_rule


def _r(metric=M.GPU_UTIL_PCT, op="<", agg="avg", threshold=50.0, window=30):
    return Rule(
        id="t", name="test", severity="warning", category="x",
        condition=Condition(
            metric=metric, op=op, threshold=threshold,
            window_seconds=window, aggregation=agg,
        ),
        message="m", suggestion="s",
    )


def test_valid_rule_passes():
    validate_rule(_r())


def test_unknown_metric_rejected():
    with pytest.raises(ValueError, match="unknown metric"):
        validate_rule(_r(metric="totally.fake.metric"))


def test_invalid_op_rejected():
    with pytest.raises(ValueError, match="invalid op"):
        validate_rule(_r(op="!~"))  # type: ignore[arg-type]


def test_invalid_agg_rejected():
    with pytest.raises(ValueError, match="invalid aggregation"):
        validate_rule(_r(agg="median"))  # type: ignore[arg-type]


def test_zero_window_rejected():
    with pytest.raises(ValueError, match="window_seconds"):
        validate_rule(_r(window=0))


def test_rule_frozen():
    r = _r()
    with pytest.raises((AttributeError, Exception)):
        r.id = "other"  # type: ignore[misc]
