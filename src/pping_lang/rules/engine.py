"""规则条件 helper —— 窗口聚合 + 比较算子。

历史：这里曾是 `RuleEngine`(周期查 DuckDB 做窗口聚合的扁平规则引擎)。它已被
`rules.diagnosis_runtime.DiagnosisEngine`(纯内存环评估的事实规则引擎)完全取代,
并随「插件进程去 DuckDB」一并退役。仅保留两个仍被复用的纯函数：

- `_agg_in_memory` —— Python 侧窗口聚合(DiagnosisEngine 的 metric_fn 用)。
- `_OP_TO_FN`      —— 条件比较算子表(/api/rules/{id}/test 预览用)。
"""
from __future__ import annotations

from typing import Any

from pping_lang.rules.schema import Aggregation, Op


def _agg_in_memory(values: list[float], agg: Aggregation) -> float | None:
    """Python 侧窗口聚合。p50/p95/p99 用排序线性插值(对齐 DuckDB QUANTILE_CONT)。"""
    if not values:
        return None
    if agg == "avg":
        return sum(values) / len(values)
    if agg == "max":
        return max(values)
    if agg == "min":
        return min(values)
    if agg == "sum":
        return float(sum(values))
    if agg == "count":
        return float(len(values))
    if agg in ("p50", "p95", "p99"):
        q = {"p50": 0.5, "p95": 0.95, "p99": 0.99}[agg]
        s = sorted(values)
        n = len(s)
        if n == 1:
            return s[0]
        rank = q * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        return s[lo] + (s[hi] - s[lo]) * frac
    return None


_OP_TO_FN: dict[Op, Any] = {
    "<":  lambda a, t: a < t,
    "<=": lambda a, t: a <= t,
    ">":  lambda a, t: a > t,
    ">=": lambda a, t: a >= t,
    "==": lambda a, t: a == t,
    "!=": lambda a, t: a != t,
}
