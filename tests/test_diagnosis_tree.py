"""诊断决策树规则定义 + 校验单测。"""
from __future__ import annotations

import dataclasses

import pytest

from pping_lang.metrics_catalog import ALLOWED_METRICS
from pping_lang.rules.diagnosis_config import DiagnosisConfig
from pping_lang.rules.diagnosis_tree import (
    DIAGNOSIS_RULES,
    FactCheck,
    FactRule,
    validate_tree,
)

_CONFIG_FIELDS = {f.name for f in dataclasses.fields(DiagnosisConfig)}


def test_builtin_tree_valid():
    validate_tree()  # 不抛即过


def test_count_and_ids_unique():
    ids = [r.id for r in DIAGNOSIS_RULES]
    assert len(ids) == 13
    assert len(set(ids)) == 13
    assert "regime-classify" in ids
    assert {"S1", "S2", "S4", "S5"} <= set(ids)


def test_regime_is_classifier_without_checks():
    regime = next(r for r in DIAGNOSIS_RULES if r.id == "regime-classify")
    assert regime.kind == "classifier"
    assert regime.checks == ()


def test_all_metrics_known():
    for r in DIAGNOSIS_RULES:
        for c in r.checks:
            assert c.metric in ALLOWED_METRICS, f"{r.id}: {c.metric}"


def test_all_threshold_refs_are_config_fields():
    for r in DIAGNOSIS_RULES:
        for c in r.checks:
            if c.threshold_ref is not None:
                assert c.threshold_ref in _CONFIG_FIELDS, f"{r.id}: {c.threshold_ref}"


def test_each_check_has_exactly_one_threshold_source():
    for r in DIAGNOSIS_RULES:
        for c in r.checks:
            assert (c.threshold_ref is None) != (c.threshold is None), r.id


def test_preconditions_reference_known_rules():
    ids = {r.id for r in DIAGNOSIS_RULES}
    for r in DIAGNOSIS_RULES:
        for p in r.precondition:
            assert p in ids, f"{r.id} -> {p}"


def test_fact_rules_names_are_facts_not_conclusions():
    """名字是事实,根因/处方在 hypothesis/suggestion(署名分离)。"""
    for r in DIAGNOSIS_RULES:
        # 根因词不应出现在规则名里(那是推断,该在 hypothesis)
        for word in ("受限", "队头阻塞", "假象", "瓶颈"):
            if r.kind == "classifier":
                continue  # regime 名里含"定位",允许
            assert word not in r.name, f"{r.id} 名字含结论词: {r.name}"


def test_d3a_is_composite_both_low():
    d3a = next(r for r in DIAGNOSIS_RULES if r.id == "D3a")
    assert d3a.match == "all"
    metrics = {c.metric for c in d3a.checks}
    assert metrics == {"vllm.perf.mfu_ratio", "gpu.mem_util_pct"}


def test_s4_is_any_match():
    s4 = next(r for r in DIAGNOSIS_RULES if r.id == "S4")
    assert s4.match == "any"


# ---- validation 拒绝非法 ----

def _rule(**kw):
    base = dict(id="X", name="x", kind="fact",
                checks=(FactCheck("gpu.utilization_pct", "<", "mfu_low_ratio", None, 30, "avg"),))
    base.update(kw)
    return FactRule(**base)


def test_validate_rejects_dup_ids():
    with pytest.raises(ValueError):
        validate_tree((_rule(id="A"), _rule(id="A")))


def test_validate_rejects_unknown_metric():
    bad = _rule(checks=(FactCheck("gpu.bogus_metric", "<", "mfu_low_ratio", None, 30, "avg"),))
    with pytest.raises(ValueError):
        validate_tree((bad,))


def test_validate_rejects_bad_threshold_ref():
    bad = _rule(checks=(FactCheck("gpu.utilization_pct", "<", "nonexistent_cfg", None, 30, "avg"),))
    with pytest.raises(ValueError):
        validate_tree((bad,))


def test_validate_rejects_both_threshold_sources():
    bad = _rule(checks=(FactCheck("gpu.utilization_pct", "<", "mfu_low_ratio", 0.5, 30, "avg"),))
    with pytest.raises(ValueError):
        validate_tree((bad,))


def test_validate_rejects_precondition_to_unknown():
    with pytest.raises(ValueError):
        validate_tree((_rule(precondition=("ZZZ",)),))


def test_validate_rejects_classifier_with_checks():
    with pytest.raises(ValueError):
        validate_tree((_rule(kind="classifier"),))


def test_validate_rejects_nonclassifier_without_checks():
    with pytest.raises(ValueError):
        validate_tree((_rule(checks=()),))


def test_validate_rejects_bad_regime():
    with pytest.raises(ValueError):
        validate_tree((_rule(requires_regime="sideways"),))
