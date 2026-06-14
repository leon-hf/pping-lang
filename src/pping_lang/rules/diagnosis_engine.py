"""诊断规则求值核 —— 把"规则逻辑"和"数据源"解耦。

核心 `evaluate(rules, cfg, metric_fn, regime)` 是**纯函数**:
- `metric_fn(metric, window_s, agg) -> float | None` 提供某指标的窗口聚合值(没数据返 None);
- 返回触发的 `DiagFinding`(事实 + 署名的根因/处方)。

好处:同一套逻辑既能合成数据单测,又能喂真实指标(DuckDB / 远端 API)跑验证,
引擎本身不依赖 GPU/vLLM。两阶段求值实现"前置守卫":
  ① 先算每条规则的 `holds`(checks 是否成立);
  ② 再判 active = holds 且(无前置或任一前置 holds)且(无 requires_regime 或 regime 匹配)。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pping_lang.rules.diagnosis_config import DiagnosisConfig
from pping_lang.rules.diagnosis_rules import DIAGNOSIS_RULES, FactCheck, FactRule
from pping_lang.rules.engine import _OP_TO_FN

# (metric, window_seconds, aggregation) -> 聚合值 或 None(无数据)
MetricFn = Callable[[str, int, str], "float | None"]


@dataclass(frozen=True)
class DiagFinding:
    """一条触发的诊断:事实 + 署名推断(hypothesis/suggestion)。"""

    rule_id: str
    name: str                       # 客观事实(规则名)
    claim: str
    severity: str = "info"
    values: dict[str, float] = field(default_factory=dict)  # 触发时各 check 实测值
    hypothesis: str = ""            # 根因推断(署名,非判决)
    suggestion: str = ""            # 处方


def _check_value(metric_fn: MetricFn, c: FactCheck) -> float | None:
    if c.aggregation == "p99_over_p50":
        p99 = metric_fn(c.metric, c.window_seconds, "p99")
        p50 = metric_fn(c.metric, c.window_seconds, "p50")
        if p99 is None or p50 is None or p50 == 0:
            return None
        return p99 / p50
    return metric_fn(c.metric, c.window_seconds, c.aggregation)


def _threshold(c: FactCheck, cfg: DiagnosisConfig) -> float:
    if c.threshold_ref is not None:
        return float(getattr(cfg, c.threshold_ref))
    assert c.threshold is not None  # validate_rules 保证二选一
    return c.threshold


def _checks_hold(
    rule: FactRule, metric_fn: MetricFn, cfg: DiagnosisConfig,
) -> tuple[bool, dict[str, float]]:
    """评一条规则的 checks(按 match all/any),返回 (是否成立, 各 check 实测值)。"""
    flags: list[bool] = []
    values: dict[str, float] = {}
    for c in rule.checks:
        v = _check_value(metric_fn, c)
        key = f"{c.metric}:{c.aggregation}"
        if v is None:
            flags.append(False)
            continue
        values[key] = v
        flags.append(_OP_TO_FN[c.op](v, _threshold(c, cfg)))
    if not flags:
        return False, values
    held = all(flags) if rule.match == "all" else any(flags)
    return held, values


def evaluate(
    metric_fn: MetricFn,
    cfg: DiagnosisConfig,
    rules: tuple[FactRule, ...] = DIAGNOSIS_RULES,
    regime: str | None = None,
) -> list[DiagFinding]:
    """一次求值:返回所有触发的诊断。

    `regime`("memory_bound"/"compute_bound"/None)由外部传入(操作点解析,暂未回流后端
    时传 None;则 `requires_regime` 的规则不触发,优雅降级,不乱报)。
    """
    # ① 算 holds(分类器规则不参与,regime 由参数给)
    holds: dict[str, bool] = {}
    values: dict[str, dict[str, float]] = {}
    for r in rules:
        if r.kind == "classifier":
            continue
        h, vals = _checks_hold(r, metric_fn, cfg)
        holds[r.id] = h
        values[r.id] = vals

    # ② active = holds 且 前置任一 holds 且 regime 匹配
    findings: list[DiagFinding] = []
    for r in rules:
        if r.kind == "classifier" or not holds.get(r.id):
            continue
        if r.precondition and not any(holds.get(p) for p in r.precondition):
            continue
        if r.requires_regime is not None and r.requires_regime != regime:
            continue
        findings.append(DiagFinding(
            rule_id=r.id, name=r.name, claim=r.claim, severity=r.severity,
            values=values[r.id], hypothesis=r.hypothesis, suggestion=r.suggestion,
        ))
    return findings


def db_metric_fn(conn, now_ns: int) -> MetricFn:
    """从 DuckDB metrics 表构造 metric_fn(供 plugin 内实跑用)。"""
    from pping_lang.rules.engine import _AGG_TO_SQL

    def fn(metric: str, window_s: int, agg: str) -> float | None:
        agg_sql = _AGG_TO_SQL.get(agg)
        if agg_sql is None:
            return None
        cutoff = now_ns - int(window_s * 1e9)
        try:
            row = conn.execute(
                f"SELECT {agg_sql} FROM metrics WHERE metric_name = ? AND ts_ns >= ?",
                [metric, cutoff],
            ).fetchone()
        except Exception:
            return None
        return None if row is None or row[0] is None else float(row[0])

    return fn
