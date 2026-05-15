"""Day 9 — RuleEngine 热加载：CRUD via RuleStore 应即时被下次 eval pass 看见。

不依赖文件系统 watch；engine 每个 tick 调 store.list() 取最新视图。
"""
from __future__ import annotations

import time

import pytest

from pping_lang.metrics_catalog import M
from pping_lang.rules.engine import RuleEngine
from pping_lang.rules.schema import Condition, Rule
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint


def _custom(rid="custom-1", threshold=50.0, op="<", enabled=True) -> Rule:
    return Rule(
        id=rid, name=rid, severity="warning", category="test",
        condition=Condition(
            metric=M.GPU_UTIL_PCT, op=op, threshold=threshold,
            window_seconds=10, aggregation="avg",
        ),
        message="{value:.0f} vs {threshold:.0f}",
        suggestion="x",
        enabled=enabled,
    )


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "hr.duckdb"
    sink = LocalSink(db_path=db, instance_id="hr", flush_interval_s=10.0)
    # 推几条 GPU util = 30 让默认 low-gpu-util (< 50) 会触发
    # 用真实 monotonic_ns 才能落在 rule 的 30s 窗口内
    base_ts = time.monotonic_ns()
    for i in range(5):
        sink.push_metric(MetricPoint(
            ts_ns=base_ts - i * 10**8, name=M.GPU_UTIL_PCT, value=30.0,
        ))
    sink._drain()
    store = RuleStore(override_path=tmp_path / "rules.json")
    yield store, sink, db
    if not sink._closed:
        sink.close()


def test_engine_with_store_reflects_initial_default_count(env):
    store, sink, db = env
    engine = RuleEngine(
        db_path=str(db), rules=store, sink=sink,
        eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    # 10 默认规则
    assert engine.num_rules == 10
    engine.stop()


def test_new_rule_via_store_picked_up_on_next_eval(env):
    store, sink, db = env
    engine = RuleEngine(
        db_path=str(db), rules=store, sink=sink,
        eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    initial = engine.num_rules

    # Add a custom rule via store (simulates POST /api/rules)
    store.upsert(_custom(rid="brand-new-rule"))

    # Next eval should see it
    assert engine.num_rules == initial + 1
    engine.stop()


def test_updated_rule_takes_effect_on_next_eval(env):
    store, sink, db = env
    # default low-gpu-util threshold 50 — with avg=30, would fire
    engine = RuleEngine(
        db_path=str(db), rules=store, sink=sink,
        eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    fires1 = engine.evaluate_once()
    initial_fires = fires1

    # Override low-gpu-util threshold to 25 — now avg=30 < 25 false
    overridden = _custom(rid="low-gpu-util", threshold=25.0, op="<")
    overridden = Rule(
        id="low-gpu-util", name="overridden", severity="warning",
        category="throughput",
        condition=Condition(
            metric=M.GPU_UTIL_PCT, op="<", threshold=25.0,
            window_seconds=30, aggregation="avg",
        ),
        message="m", suggestion="s",
    )
    store.upsert(overridden)

    # New eval pass: low-gpu-util now uses threshold 25, doesn't fire
    # (suppression already cleared since suppression_window_s=0 in fixture)
    fires2 = engine.evaluate_once()
    # fires2 should be one less than fires1 (low-gpu-util no longer fires)
    assert fires2 == initial_fires - 1, (
        f"updating rule should reduce fires; got {fires1} → {fires2}"
    )
    engine.stop()


def test_deleted_default_rule_soft_disable_drops_from_active(env):
    store, sink, db = env
    engine = RuleEngine(
        db_path=str(db), rules=store, sink=sink,
        eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    initial = engine.num_rules

    # Delete a default rule (soft disable)
    store.delete("low-gpu-util")
    # Active rules should drop by 1 (disabled rules not in _current_rules)
    assert engine.num_rules == initial - 1
    engine.stop()


def test_deleted_user_rule_disappears(env):
    store, sink, db = env
    store.upsert(_custom(rid="ephemeral"))
    engine = RuleEngine(
        db_path=str(db), rules=store, sink=sink,
        eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    n1 = engine.num_rules
    store.delete("ephemeral")
    assert engine.num_rules == n1 - 1
    engine.stop()


def test_re_enabling_picks_back_up(env):
    store, sink, db = env
    engine = RuleEngine(
        db_path=str(db), rules=store, sink=sink,
        eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    n1 = engine.num_rules

    # Disable via override
    disabled = Rule(
        id="low-gpu-util", name="x", severity="warning", category="throughput",
        condition=Condition(
            metric=M.GPU_UTIL_PCT, op="<", threshold=50.0,
            window_seconds=30, aggregation="avg",
        ),
        message="m", suggestion="s", enabled=False,
    )
    store.upsert(disabled)
    assert engine.num_rules == n1 - 1

    # Re-enable
    enabled = Rule(
        id="low-gpu-util", name="x", severity="warning", category="throughput",
        condition=Condition(
            metric=M.GPU_UTIL_PCT, op="<", threshold=50.0,
            window_seconds=30, aggregation="avg",
        ),
        message="m", suggestion="s", enabled=True,
    )
    store.upsert(enabled)
    assert engine.num_rules == n1
    engine.stop()


def test_static_list_mode_still_works(env):
    """Backward-compat: passing list[Rule] (not RuleStore) is still supported."""
    _, sink, db = env
    engine = RuleEngine(
        db_path=str(db),
        rules=[_custom(rid="static-1")],
        sink=sink,
        eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    assert engine.num_rules == 1
    engine.stop()
