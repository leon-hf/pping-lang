"""4 瓶颈诊断规则(detector-group 模型)定义 + 校验单测。

每个瓶颈有多条独立检测手段(Detector),任一命中即触发。
"""
from __future__ import annotations

import dataclasses

import pytest

from pping_lang.metrics_catalog import ALLOWED_METRICS
from pping_lang.rules.diagnosis_config import DiagnosisConfig
from pping_lang.rules.diagnosis_rules import (
    DIAGNOSIS_RULES,
    Detector,
    FactCheck,
    FactRule,
    validate_rules,
)

_CONFIG_FIELDS = {f.name for f in dataclasses.fields(DiagnosisConfig)}


def _all_checks(r):
    return [c for det in r.detectors for c in det.checks]


def test_builtin_rules_valid():
    validate_rules()  # 不抛即过


def test_exactly_four_bottlenecks():
    assert [r.id for r in DIAGNOSIS_RULES] == ["A", "B", "C", "D"]


def test_each_bottleneck_has_multiple_detectors():
    by = {r.id: r for r in DIAGNOSIS_RULES}
    assert len(by["A"].detectors) >= 2   # roofline + kernel_slack
    assert len(by["B"].detectors) == 3
    assert len(by["C"].detectors) == 2
    assert len(by["D"].detectors) == 2


def test_detector_keys_unique_and_layers_valid():
    for r in DIAGNOSIS_RULES:
        keys = [d.key for d in r.detectors]
        assert len(keys) == len(set(keys)), r.id
        for d in r.detectors:
            assert d.layer in ("L1", "L2", "L3", "L4", "L5"), f"{r.id}/{d.key}"
            assert d.name, f"{r.id}/{d.key} 无名"


def test_all_metrics_known():
    for r in DIAGNOSIS_RULES:
        for c in _all_checks(r):
            assert c.metric in ALLOWED_METRICS, f"{r.id}: {c.metric}"


def test_all_threshold_refs_are_config_fields():
    for r in DIAGNOSIS_RULES:
        for c in _all_checks(r):
            if c.threshold_ref is not None:
                assert c.threshold_ref in _CONFIG_FIELDS, f"{r.id}: {c.threshold_ref}"


def test_each_check_has_exactly_one_threshold_source():
    for r in DIAGNOSIS_RULES:
        for c in _all_checks(r):
            assert (c.threshold_ref is None) != (c.threshold is None), r.id


def test_A_roofline_detector_idle_guarded_plus_kernel_slack():
    a = next(r for r in DIAGNOSIS_RULES if r.id == "A")
    rf = next(d for d in a.detectors if d.key == "roofline")
    assert {c.metric for c in rf.checks} == {
        "vllm.scheduler.running_reqs", "vllm.perf.mfu_ratio", "gpu.mem_util_pct",
    }
    assert any(d.key == "kernel_slack" for d in a.detectors)   # 第二条独立手段


def test_D_detectors_kv_and_preempt():
    d = next(r for r in DIAGNOSIS_RULES if r.id == "D")
    assert {det.key for det in d.detectors} == {"kv_pressure", "preemption"}
    assert d.severity == "critical"


def test_every_rule_carries_signed_inference():
    for r in DIAGNOSIS_RULES:
        assert r.hypothesis and r.suggestion, r.id


# ---- validation 拒绝非法 ----

def _det(**kw):
    base = dict(key="x", name="x", layer="L1",
                checks=(FactCheck("gpu.mem_util_pct", "<", "mfu_low_ratio", None, 30, "avg"),))
    base.update(kw)
    return Detector(**base)


def _rule(**kw):
    base = dict(id="X", name="x", detectors=(_det(),))
    base.update(kw)
    return FactRule(**base)


def test_validate_rejects_dup_ids():
    with pytest.raises(ValueError):
        validate_rules((_rule(id="A"), _rule(id="A")))


def test_validate_rejects_unknown_metric():
    bad = _rule(detectors=(_det(checks=(FactCheck("gpu.bogus", "<", "mfu_low_ratio", None, 30, "avg"),)),))
    with pytest.raises(ValueError):
        validate_rules((bad,))


def test_validate_rejects_bad_threshold_ref():
    bad = _rule(detectors=(_det(checks=(FactCheck("gpu.mem_util_pct", "<", "nope", None, 30, "avg"),)),))
    with pytest.raises(ValueError):
        validate_rules((bad,))


def test_validate_rejects_both_threshold_sources():
    bad = _rule(detectors=(_det(checks=(FactCheck("gpu.mem_util_pct", "<", "mfu_low_ratio", 0.5, 30, "avg"),)),))
    with pytest.raises(ValueError):
        validate_rules((bad,))


def test_validate_rejects_without_detectors():
    with pytest.raises(ValueError):
        validate_rules((_rule(detectors=()),))


def test_validate_rejects_bad_layer():
    with pytest.raises(ValueError):
        validate_rules((_rule(detectors=(_det(layer="L9"),)),))


def test_validate_rejects_dup_detector_keys():
    with pytest.raises(ValueError):
        validate_rules((_rule(detectors=(_det(key="a"), _det(key="a"))),))
