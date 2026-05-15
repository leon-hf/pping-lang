"""规则加载 — defaults + JSON override 文件 merge by id。"""
from __future__ import annotations

import json

import pytest

from pping_lang.metrics_catalog import M
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.loader import get_active_rules, load_rules_from_file


def test_get_active_returns_defaults_when_no_override(monkeypatch):
    monkeypatch.delenv("PPING_LANG_RULES_PATH", raising=False)
    rules = get_active_rules()
    assert len(rules) == len(DEFAULT_RULES)


def test_load_rules_from_valid_json(tmp_path):
    rules_data = [
        {
            "id": "custom-1",
            "name": "Custom rule",
            "severity": "info",
            "category": "test",
            "condition": {
                "metric": M.GPU_UTIL_PCT,
                "op": "<",
                "threshold": 25,
                "window_seconds": 15,
                "aggregation": "avg",
            },
            "message": "GPU low {value:.0f}%",
            "suggestion": "do something",
        },
    ]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules_data), encoding="utf-8")

    rules = load_rules_from_file(path)
    assert len(rules) == 1
    assert rules[0].id == "custom-1"
    assert rules[0].condition.threshold == 25.0
    assert rules[0].condition.aggregation == "avg"


def test_override_replaces_default_by_id(tmp_path, monkeypatch):
    """同 id 的 override 应取代 default。"""
    override_data = [
        {
            "id": "low-gpu-util",  # exists in DEFAULT_RULES
            "name": "Custom GPU low",
            "severity": "critical",  # changed from default warning
            "category": "throughput",
            "condition": {
                "metric": M.GPU_UTIL_PCT,
                "op": "<",
                "threshold": 30,  # changed from 50
                "window_seconds": 60,
                "aggregation": "avg",
            },
            "message": "Custom: {value:.0f}",
            "suggestion": "custom",
        },
    ]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(override_data), encoding="utf-8")
    monkeypatch.setenv("PPING_LANG_RULES_PATH", str(path))

    rules = get_active_rules()
    by_id = {r.id: r for r in rules}
    # Total count unchanged (override doesn't add)
    assert len(rules) == len(DEFAULT_RULES)
    # The overridden one has new values
    overridden = by_id["low-gpu-util"]
    assert overridden.severity == "critical"
    assert overridden.condition.threshold == 30.0


def test_invalid_metric_in_override_rejected(tmp_path):
    bad = [{
        "id": "x", "name": "x", "severity": "info", "category": "x",
        "condition": {
            "metric": "totally.unknown",
            "op": "<", "threshold": 1, "window_seconds": 10, "aggregation": "avg",
        },
        "message": "m", "suggestion": "s",
    }]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown metric"):
        load_rules_from_file(path)


def test_malformed_override_falls_back_to_defaults(tmp_path, monkeypatch, caplog):
    """损坏的 override 文件不应让进程挂掉，degrade 到 defaults + warning。"""
    path = tmp_path / "broken.json"
    path.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setenv("PPING_LANG_RULES_PATH", str(path))

    rules = get_active_rules()
    assert len(rules) == len(DEFAULT_RULES)  # fell back to defaults


def test_aggregation_defaults_to_avg_when_omitted(tmp_path):
    data = [{
        "id": "x", "name": "x", "severity": "info", "category": "x",
        "condition": {
            "metric": M.GPU_UTIL_PCT, "op": "<", "threshold": 1, "window_seconds": 10,
            # aggregation omitted
        },
        "message": "m", "suggestion": "s",
    }]
    path = tmp_path / "r.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    rules = load_rules_from_file(path)
    assert rules[0].condition.aggregation == "avg"
