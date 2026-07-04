"""诊断求值核 —— 把"规则逻辑"和"数据源"解耦。

`evaluate(metric_fn, cfg, rules)` 是**纯函数**:
- `metric_fn(metric, window_s, agg) -> float | None` 提供某指标的窗口聚合值(没数据返 None);
- 每个瓶颈(`FactRule`)有多条独立检测手段(`Detector`,各是一组 check 的 AND);
- **任一手段命中 → 瓶颈触发**(OR);返回触发的 `DiagFinding`(带命中了哪几条手段 = 几路互证)。

好处:同一套逻辑既能合成数据单测,又能喂真实指标跑验证,引擎本身不依赖 GPU/vLLM。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pping_lang.rules.diagnosis_config import DiagnosisConfig
from pping_lang.rules.diagnosis_rules import DIAGNOSIS_RULES, Detector, FactCheck, FactRule
from pping_lang.rules.engine import _OP_TO_FN

# (metric, window_seconds, aggregation) -> 聚合值 或 None(无数据)
MetricFn = Callable[[str, int, str], "float | None"]


@dataclass(frozen=True)
class DiagFinding:
    """一个触发的瓶颈:事实 + 署名推断 + 命中了哪几条检测手段(几路互证)。"""

    rule_id: str
    name: str                       # 客观事实(瓶颈名)
    severity: str = "info"
    values: dict[str, float] = field(default_factory=dict)  # 各 check 实测值(key = metric:agg)
    hypothesis: str = ""            # 根因推断(署名)
    suggestion: str = ""            # 处方
    fired_detectors: tuple[str, ...] = ()   # 命中的手段 key(len = 几路互证)


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


def _detector_holds(
    det: Detector, metric_fn: MetricFn, cfg: DiagnosisConfig, values: dict[str, float],
) -> bool:
    """评一条检测手段(checks 的 AND)。把每个有数据的 check 值并进 `values`(供 UI 复算 + 当证据)。"""
    held = True
    for c in det.checks:
        v = _check_value(metric_fn, c)
        if v is not None:
            values[f"{c.metric}:{c.aggregation}"] = v
        if v is None or not _OP_TO_FN[c.op](v, _threshold(c, cfg)):
            held = False
    return held and bool(det.checks)


def evaluate(
    metric_fn: MetricFn,
    cfg: DiagnosisConfig,
    rules: tuple[FactRule, ...] = DIAGNOSIS_RULES,
    regime: str | None = None,   # 兼容旧签名;现不依赖 regime(纯物理判)
) -> list[DiagFinding]:
    """一次求值:每个瓶颈任一 detector 命中即触发,返回所有触发的诊断。"""
    findings: list[DiagFinding] = []
    for r in rules:
        values: dict[str, float] = {}
        fired = [det.key for det in r.detectors
                 if _detector_holds(det, metric_fn, cfg, values)]
        if fired:
            findings.append(DiagFinding(
                rule_id=r.id, name=r.name, severity=r.severity,
                values=values, hypothesis=r.hypothesis, suggestion=r.suggestion,
                fired_detectors=tuple(fired),
            ))
    return findings
