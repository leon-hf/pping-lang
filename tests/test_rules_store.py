"""RuleStore — defaults + user JSON overrides 持久化层。"""
from __future__ import annotations

import json

import pytest

from pping_lang.metrics_catalog import M
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.schema import Condition, Rule
from pping_lang.rules.store import RuleStore


def _custom_rule(rid="custom-1", **overrides) -> Rule:
    return Rule(
        id=rid, name="custom", severity="info", category="test",
        condition=Condition(
            metric=M.GPU_UTIL_PCT, op="<", threshold=10.0,
            window_seconds=10, aggregation="avg",
        ),
        message="m", suggestion="s",
        **overrides,
    )


def test_in_memory_store_returns_only_defaults():
    s = RuleStore()
    assert len(s.list()) == len(DEFAULT_RULES)


def test_get_returns_default():
    s = RuleStore()
    r = s.get("low-gpu-util")
    assert r is not None
    assert r.id == "low-gpu-util"


def test_get_unknown_returns_none():
    assert RuleStore().get("nope") is None


def test_is_default_distinguishes():
    s = RuleStore()
    assert s.is_default("low-gpu-util")
    assert not s.is_default("custom-x")


def test_upsert_persists_to_file(tmp_path):
    f = tmp_path / "rules.json"
    s = RuleStore(override_path=f)
    s.upsert(_custom_rule())
    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["id"] == "custom-1"


def test_upsert_in_memory_only_when_no_path():
    s = RuleStore()
    s.upsert(_custom_rule())
    assert s.get("custom-1") is not None
    assert len(s.list()) == len(DEFAULT_RULES) + 1


def test_user_overrides_default_by_id(tmp_path):
    f = tmp_path / "rules.json"
    s = RuleStore(override_path=f)
    # Override a default rule's threshold
    overridden = Rule(
        id="low-gpu-util", name="Custom", severity="critical",
        category="throughput",
        condition=Condition(
            metric=M.GPU_UTIL_PCT, op="<", threshold=30.0,
            window_seconds=60, aggregation="avg",
        ),
        message="m", suggestion="s",
    )
    s.upsert(overridden)
    r = s.get("low-gpu-util")
    assert r.severity == "critical"
    assert r.condition.threshold == 30.0
    # list size unchanged (override doesn't add)
    assert len(s.list()) == len(DEFAULT_RULES)


def test_delete_user_only_removes(tmp_path):
    f = tmp_path / "rules.json"
    s = RuleStore(override_path=f)
    s.upsert(_custom_rule())
    s.delete("custom-1")
    assert s.get("custom-1") is None
    # File should reflect empty user list
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data == []


def test_delete_default_soft_disables(tmp_path):
    f = tmp_path / "rules.json"
    s = RuleStore(override_path=f)
    s.delete("low-gpu-util")
    r = s.get("low-gpu-util")
    assert r is not None  # still exists
    assert r.enabled is False  # but disabled


def test_delete_unknown_raises():
    s = RuleStore()
    with pytest.raises(KeyError):
        s.delete("does-not-exist")


def test_persist_roundtrip(tmp_path):
    f = tmp_path / "rules.json"
    s1 = RuleStore(override_path=f)
    s1.upsert(_custom_rule(rid="a"))
    s1.upsert(_custom_rule(rid="b"))

    # New store re-reads from file
    s2 = RuleStore(override_path=f)
    assert s2.get("a") is not None
    assert s2.get("b") is not None
    assert len(s2.list()) == len(DEFAULT_RULES) + 2


def test_invalid_metric_rejected(tmp_path):
    s = RuleStore(override_path=tmp_path / "r.json")
    bad = Rule(
        id="x", name="x", severity="info", category="x",
        condition=Condition(
            metric="not.in.catalog", op="<", threshold=1.0,
            window_seconds=10, aggregation="avg",
        ),
        message="m", suggestion="s",
    )
    with pytest.raises(ValueError, match="unknown metric"):
        s.upsert(bad)
    # Nothing was persisted
    assert not (tmp_path / "r.json").exists() or json.loads(
        (tmp_path / "r.json").read_text(encoding="utf-8")
    ) == []


def test_corrupt_user_file_falls_back_to_defaults(tmp_path):
    f = tmp_path / "rules.json"
    f.write_text("{ not valid json", encoding="utf-8")
    s = RuleStore(override_path=f)
    # Falls back: still serves defaults
    assert len(s.list()) == len(DEFAULT_RULES)
