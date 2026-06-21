"""自定义诊断规则 —— 用户在 UI 建的规则。

和策展事实规则(DIAGNOSIS_RULES)**走同一个评估器、同一条展示路径**:
DiagnosisEngine 每轮把 `DIAGNOSIS_RULES + 本 store 的 FactRule` 一起评估,命中就经
sink→DuckDB→/api/diagnoses→UI 冒出来。没有"旧引擎"那一套。

每条自定义规则 = 单 check 的 FactRule(固定阈值,不引用中心配置;无前置/无 regime 门
→ 命中即报)。claim="measurement"(直接测量越界)。存为 JSON 落盘,重启不丢。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from pping_lang.metrics_catalog import ALLOWED_METRICS
from pping_lang.rules.diagnosis_rules import (
    DIAGNOSIS_RULES,
    FactAgg,
    FactCheck,
    FactRule,
)

logger = logging.getLogger(__name__)

_OPS = ("<", "<=", ">", ">=", "==", "!=")
_SEVS = ("info", "warning", "critical")
_CURATED_IDS = frozenset(r.id for r in DIAGNOSIS_RULES)


class CustomRuleStore:
    """用户自定义规则的存储(JSON 落盘)+ 校验 + dict↔FactRule。线程安全。"""

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path) if path else None
        self._rules: list[dict] = []
        self._lock = threading.Lock()
        self._load()

    # === 持久化 ===
    def _load(self) -> None:
        if not (self._path and self._path.exists()):
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self._rules = [r for r in raw if isinstance(r, dict)]
        except Exception:
            logger.exception("[pping-lang] 读自定义规则失败 %s", self._path)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._rules, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.exception("[pping-lang] 存自定义规则失败 %s", self._path)

    # === 读 ===
    def list_dicts(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._rules]

    def fact_rules(self) -> tuple[FactRule, ...]:
        """转成 FactRule 供引擎评估;坏记录跳过(不连累整体)。"""
        with self._lock:
            out: list[FactRule] = []
            for d in self._rules:
                try:
                    out.append(_to_fact_rule(d))
                except Exception:
                    logger.warning("[pping-lang] 跳过坏自定义规则 %r", d.get("id"))
            return tuple(out)

    # === 写 ===
    def add(self, d: dict) -> dict:
        with self._lock:
            rec = _validate(d, existing=self._ids(), keep_id=False)
            self._rules.append(rec)
            self._save()
            return rec

    def update(self, rule_id: str, d: dict) -> dict:
        with self._lock:
            idx = next((i for i, r in enumerate(self._rules) if r["id"] == rule_id), None)
            if idx is None:
                raise KeyError(rule_id)
            rec = _validate({**d, "id": rule_id}, existing=self._ids() - {rule_id}, keep_id=True)
            self._rules[idx] = rec
            self._save()
            return rec

    def delete(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r["id"] != rule_id]
            changed = len(self._rules) != before
            if changed:
                self._save()
            return changed

    def _ids(self) -> set[str]:
        return {r["id"] for r in self._rules}


def _to_fact_rule(d: dict) -> FactRule:
    return FactRule(
        id=d["id"], name=d["name"], kind="fact", severity=d.get("severity", "warning"),
        checks=(FactCheck(
            d["metric"], d["op"], None, float(d["threshold"]),
            int(d.get("window_seconds", 60)), d.get("aggregation", "avg"),
        ),),
        claim="measurement",
        hypothesis=str(d.get("hypothesis", "") or ""),
        suggestion=str(d.get("suggestion", "") or ""),
    )


def _validate(d: dict, *, existing: set[str], keep_id: bool) -> dict:
    """校验 + 归一成存储 dict。非法抛 ValueError。"""
    name = str(d.get("name", "")).strip()
    if not name:
        raise ValueError("name 必填")
    metric = d.get("metric")
    if metric not in ALLOWED_METRICS:
        raise ValueError(f"未知指标 {metric!r}")
    op = d.get("op")
    if op not in _OPS:
        raise ValueError(f"非法操作符 {op!r}")
    try:
        threshold = float(d["threshold"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("threshold 必须是数字")
    agg = d.get("aggregation", "avg")
    if agg not in FactAgg:
        raise ValueError(f"非法聚合 {agg!r}")
    window = int(d.get("window_seconds", 60))
    if window <= 0:
        raise ValueError("window_seconds 必须 > 0")
    sev = d.get("severity", "warning")
    if sev not in _SEVS:
        raise ValueError(f"非法 severity {sev!r}")
    rid = d.get("id") if keep_id else None
    if not rid:
        rid = _gen_id(existing | set(_CURATED_IDS))
    if rid in _CURATED_IDS:
        raise ValueError(f"id {rid!r} 与内置规则冲突")
    if not keep_id and rid in existing:
        raise ValueError(f"id {rid!r} 已存在")
    return {
        "id": rid, "name": name, "metric": metric, "op": op, "threshold": threshold,
        "window_seconds": window, "aggregation": agg, "severity": sev,
        "hypothesis": str(d.get("hypothesis", "") or ""),
        "suggestion": str(d.get("suggestion", "") or ""),
        "custom": True,
    }


def _gen_id(taken: set[str]) -> str:
    n = 1
    while f"custom-{n}" in taken:
        n += 1
    return f"custom-{n}"
