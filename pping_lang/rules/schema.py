"""规则 schema — Condition / Rule dataclasses。

规则 condition.metric 必须命中 ALLOWED_METRICS（见 metrics_catalog），加载时校验。
复合条件 (all/any) 推到 v0.2，v0.1 仅单一 metric + 阈值 + 窗口。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pping_lang.metrics_catalog import ALLOWED_METRICS

Severity = Literal["info", "warning", "critical"]
Op = Literal["<", "<=", ">", ">=", "==", "!="]
Aggregation = Literal["avg", "p50", "p95", "p99", "max", "min", "count"]


@dataclass(frozen=True)
class Condition:
    metric: str           # must ∈ ALLOWED_METRICS (validated by load_rules)
    op: Op
    threshold: float
    window_seconds: int
    aggregation: Aggregation = "avg"


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: Severity
    category: str         # throughput / latency / stability / efficiency / bottleneck / tuning
    condition: Condition
    message: str          # str.format template, supports {value} {threshold} {window}
    suggestion: str
    enabled: bool = True


def validate_rule(rule: Rule) -> None:
    """Raise ValueError if rule references unknown metric or has invalid op/agg."""
    if rule.condition.metric not in ALLOWED_METRICS:
        raise ValueError(
            f"rule {rule.id!r}: unknown metric {rule.condition.metric!r}. "
            f"Add to metrics_catalog.M or correct the rule."
        )
    if rule.condition.op not in ("<", "<=", ">", ">=", "==", "!="):
        raise ValueError(f"rule {rule.id!r}: invalid op {rule.condition.op!r}")
    if rule.condition.aggregation not in (
        "avg", "p50", "p95", "p99", "max", "min", "count"
    ):
        raise ValueError(
            f"rule {rule.id!r}: invalid aggregation {rule.condition.aggregation!r}"
        )
    if rule.condition.window_seconds <= 0:
        raise ValueError(f"rule {rule.id!r}: window_seconds must be > 0")
