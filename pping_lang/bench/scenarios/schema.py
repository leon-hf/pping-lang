"""Bench scenario dataclasses + validation — see design doc §7.2.

v0.1: StaticScenario only (fixed / sweep / matrix). Dynamic profiles land in Week 2.

Why dataclass not pydantic: stdlib only, lighter, and we don't need serde from JSON
at the call sites (API handlers convert dict→StaticScenario explicitly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ApiKind = Literal["chat", "completions"]


# ===== SLO constraints (constraint-string syntax, inspired by llm-optimizer) =====
#
# Spec grammar:
#     spec       := constraint (";" constraint)*
#     constraint := metric (":" percentile)? op value unit?
#     metric     := ttft | tpot | e2e | error_rate
#     percentile := mean | p50 | p90 | p95 | p99
#     op         := < | <= | > | >=
#     value      := float
#     unit       := ms | s          # required for latency, forbidden for error_rate
#
# Examples:
#     "ttft:p99<500ms"
#     "ttft:p99<500ms;tpot:p99<50ms;error_rate<0.01"
#     "e2e:p95<2s"

_LATENCY_METRICS: frozenset[str] = frozenset({"ttft", "tpot", "e2e"})
_VALID_METRICS:   frozenset[str] = _LATENCY_METRICS | {"error_rate"}
_VALID_PCTS:      frozenset[str] = frozenset({"mean", "p50", "p90", "p95", "p99"})
_VALID_OPS:       frozenset[str] = frozenset({"<", "<=", ">", ">="})

_CONSTRAINT_RE = re.compile(
    r"^(?P<metric>[a-z][a-z0-9_]*)"  # metric name (allow digits after first letter, e.g. e2e)
    r"(?::(?P<pct>\w+))?"            # optional :percentile
    r"\s*(?P<op>[<>]=?)\s*"          # op: < <= > >=
    r"(?P<val>\d+(?:\.\d+)?)"        # numeric value
    r"(?P<unit>ms|s)?$",             # optional unit
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Threshold:
    """A single SLO constraint.

    `value` is always normalized to milliseconds for latency metrics, raw fraction
    for error_rate. `percentile` is None iff metric == "error_rate".
    """

    metric: str
    percentile: str | None
    op: str
    value: float

    def check(self, summary: object) -> bool | None:
        """Evaluate against a RunSummary. True=pass, False=fail, None=no data."""
        if self.metric == "error_rate":
            actual = getattr(summary, "error_rate", None)
        else:
            stats = getattr(summary, f"{self.metric}_ms", None)
            if stats is None or getattr(stats, "n", 0) == 0:
                return None
            actual = getattr(stats, self.percentile or "", None)
        if actual is None:
            return None
        return _op_apply(self.op, float(actual), self.value)

    def as_spec(self) -> str:
        if self.metric == "error_rate":
            return f"{self.metric}{self.op}{_fmt_num(self.value)}"
        return f"{self.metric}:{self.percentile}{self.op}{_fmt_num(self.value)}ms"


def _op_apply(op: str, actual: float, target: float) -> bool:
    if op == "<":
        return actual < target
    if op == "<=":
        return actual <= target
    if op == ">":
        return actual > target
    if op == ">=":
        return actual >= target
    raise ValueError(f"unsupported op: {op!r}")


def _fmt_num(v: float) -> str:
    # Drop trailing .0 for cleaner echo; keep fractional precision otherwise
    return f"{v:g}"


@dataclass(frozen=True, slots=True)
class SLO:
    """Set of thresholds. Empty SLO evaluates to 'n/a'."""

    thresholds: tuple[Threshold, ...] = ()

    def is_empty(self) -> bool:
        return not self.thresholds

    def as_spec(self) -> str:
        return ";".join(t.as_spec() for t in self.thresholds)

    @classmethod
    def from_spec(cls, spec: str | None) -> SLO:
        """Parse a spec string into an SLO. Empty / None → empty SLO."""
        if spec is None:
            return cls(thresholds=())
        thresholds: list[Threshold] = []
        for raw in spec.split(";"):
            part = raw.strip()
            if not part:
                continue
            thresholds.append(_parse_one(part))
        return cls(thresholds=tuple(thresholds))


def _parse_one(part: str) -> Threshold:
    m = _CONSTRAINT_RE.match(part)
    if not m:
        raise ValueError(
            f"invalid SLO constraint {part!r}: expected e.g. 'ttft:p99<500ms' or 'error_rate<0.01'"
        )
    metric = m["metric"].lower()
    pct = m["pct"].lower() if m["pct"] else None
    op = m["op"]
    val = float(m["val"])
    unit = m["unit"].lower() if m["unit"] else None

    if metric not in _VALID_METRICS:
        raise ValueError(
            f"unknown SLO metric {metric!r}; allowed: {sorted(_VALID_METRICS)}"
        )
    if op not in _VALID_OPS:
        raise ValueError(f"unsupported op {op!r}; allowed: {sorted(_VALID_OPS)}")

    if metric == "error_rate":
        if pct is not None:
            raise ValueError(f"error_rate cannot take percentile: {part!r}")
        if unit is not None:
            raise ValueError(f"error_rate must be unitless (e.g. 'error_rate<0.01'): {part!r}")
        if not 0.0 <= val <= 1.0:
            raise ValueError(f"error_rate must be in [0, 1] (got {val}): {part!r}")
    else:
        if pct is None:
            raise ValueError(
                f"latency metric {metric!r} requires percentile, e.g. '{metric}:p99<500ms': {part!r}"
            )
        if pct not in _VALID_PCTS:
            raise ValueError(
                f"unknown percentile {pct!r}; allowed: {sorted(_VALID_PCTS)}"
            )
        if unit is None:
            raise ValueError(
                f"latency value needs unit 'ms' or 's': {part!r}"
            )
        if unit == "s":
            val *= 1000.0

    return Threshold(metric=metric, percentile=pct, op=op, value=val)


# ===== Scenario =====


@dataclass(slots=True)
class StaticScenario:
    """A reproducible, fixed-shape benchmark run.

    Either `duration_s` or `num_requests` must be set (mutually exclusive).
    """

    name: str
    endpoint: str
    model: str

    # Request shape
    prompt_tokens: int = 500
    output_tokens: int = 100
    api: ApiKind = "chat"

    # Load shape
    concurrency: int = 16
    duration_s: int | None = 60
    num_requests: int | None = None
    warmup_s: int = 5

    # Timeouts / safety
    timeout_s: float = 30.0
    fail_fast: bool = False  # abort whole run on first request error

    # Optional sweep (single-dim only in v0.1; matrix lives in a separate type later)
    sweep_param: str | None = None
    sweep_values: list[int | float] = field(default_factory=list)

    # Optional pass/fail criteria
    slo: SLO | None = None

    # Optional explicit prompt content (otherwise synthesized to hit target token count)
    prompt_text: str | None = None

    def validate(self) -> None:
        """Raise ValueError with all issues joined; called at runner start."""
        errors: list[str] = []
        if not self.name:
            errors.append("name is required")
        if not self.endpoint:
            errors.append("endpoint is required")
        if not self.model:
            errors.append("model is required")
        if self.prompt_tokens <= 0:
            errors.append(f"prompt_tokens must be > 0 (got {self.prompt_tokens})")
        if self.output_tokens <= 0:
            errors.append(f"output_tokens must be > 0 (got {self.output_tokens})")
        if self.concurrency <= 0:
            errors.append(f"concurrency must be > 0 (got {self.concurrency})")
        if self.warmup_s < 0:
            errors.append(f"warmup_s must be >= 0 (got {self.warmup_s})")
        if self.timeout_s <= 0:
            errors.append(f"timeout_s must be > 0 (got {self.timeout_s})")
        if self.api not in ("chat", "completions"):
            errors.append(f"api must be 'chat' or 'completions' (got {self.api!r})")

        # Duration / num_requests is XOR
        has_dur = self.duration_s is not None
        has_n = self.num_requests is not None
        if has_dur == has_n:
            errors.append("exactly one of duration_s / num_requests must be set")
        if has_dur and self.duration_s is not None and self.duration_s <= 0:
            errors.append(f"duration_s must be > 0 (got {self.duration_s})")
        if has_n and self.num_requests is not None and self.num_requests <= 0:
            errors.append(f"num_requests must be > 0 (got {self.num_requests})")

        # Sweep consistency
        if self.sweep_param is not None and not self.sweep_values:
            errors.append(f"sweep_param={self.sweep_param!r} set but sweep_values empty")
        if self.sweep_values and self.sweep_param is None:
            errors.append("sweep_values set but sweep_param missing")
        if self.sweep_param is not None and self.sweep_param not in _SWEEPABLE:
            errors.append(
                f"sweep_param={self.sweep_param!r} not sweepable; "
                f"allowed: {sorted(_SWEEPABLE)}"
            )

        if errors:
            raise ValueError("invalid StaticScenario: " + "; ".join(errors))


_SWEEPABLE: frozenset[str] = frozenset({
    "concurrency", "prompt_tokens", "output_tokens",
})
