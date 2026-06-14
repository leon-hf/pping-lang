"""RuleEngine 测试 — 用真实 DuckDB + LocalSink，端到端验证窗口聚合 + 触发 + 抑制。"""
from __future__ import annotations

import time

import duckdb
import pytest

from pping_lang.metrics_catalog import M
from pping_lang.rules.engine import RuleEngine
from pping_lang.rules.schema import Condition, Rule
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint


def _rule(
    rid: str = "r1",
    metric: str = M.GPU_UTIL_PCT,
    op: str = "<",
    threshold: float = 50.0,
    window: int = 10,
    agg: str = "avg",
    severity: str = "warning",
) -> Rule:
    return Rule(
        id=rid, name=rid, severity=severity, category="test",  # type: ignore[arg-type]
        condition=Condition(metric=metric, op=op, threshold=threshold,  # type: ignore[arg-type]
                            window_seconds=window, aggregation=agg),  # type: ignore[arg-type]
        message="{value:.1f} vs {threshold:.1f} over {window}s",
        suggestion="fix it",
    )


@pytest.fixture
def db_with_metrics(tmp_path):
    """LocalSink wired up; caller pushes metrics + closes; engine reads from same file."""
    db = tmp_path / "rules.duckdb"
    sink = LocalSink(db_path=db, instance_id="test", flush_interval_s=10.0)
    yield sink, db
    if not sink._closed:
        sink.close()


def _push_window(sink, metric_name: str, values: list[float]):
    base_ts = time.time_ns()
    for i, v in enumerate(values):
        sink.push_metric(MetricPoint(ts_ns=base_ts + i, name=metric_name, value=v))


def test_rule_fires_when_avg_below_threshold(db_with_metrics):
    sink, db = db_with_metrics
    _push_window(sink, M.GPU_UTIL_PCT, [30.0, 35.0, 40.0])  # avg=35 < 50
    sink.close()

    engine = RuleEngine(
        db_path=str(db),
        rules=[_rule()],
        sink=sink,
        eval_interval_s=10.0,
        suppression_window_s=0,  # no suppression for this test
        print_to_terminal=False,
    )
    fires = engine.evaluate_once()
    engine.stop()
    assert fires == 1


def test_rule_does_not_fire_when_threshold_not_met(db_with_metrics):
    sink, db = db_with_metrics
    _push_window(sink, M.GPU_UTIL_PCT, [70.0, 80.0, 90.0])  # avg=80, not < 50
    sink.close()

    engine = RuleEngine(
        db_path=str(db), rules=[_rule()], sink=sink,
        eval_interval_s=10.0, suppression_window_s=0, print_to_terminal=False,
    )
    fires = engine.evaluate_once()
    engine.stop()
    assert fires == 0


def test_no_data_no_fire(db_with_metrics):
    """Window with no metrics → rule should not fire (NULL aggregate)."""
    sink, db = db_with_metrics
    sink.close()  # close without pushing anything → DB doesn't exist
    # Recreate empty DB so engine has something to query
    duckdb.connect(str(db)).close()

    engine = RuleEngine(
        db_path=str(db), rules=[_rule()], sink=sink,
        eval_interval_s=10.0, suppression_window_s=0, print_to_terminal=False,
    )
    fires = engine.evaluate_once()
    engine.stop()
    assert fires == 0


def test_suppression_blocks_duplicate_fires_within_window(db_with_metrics):
    sink, db = db_with_metrics
    _push_window(sink, M.GPU_UTIL_PCT, [10.0])
    sink.close()

    engine = RuleEngine(
        db_path=str(db), rules=[_rule()], sink=sink,
        eval_interval_s=10.0, suppression_window_s=60.0, print_to_terminal=False,
    )
    f1 = engine.evaluate_once()
    f2 = engine.evaluate_once()  # immediate second pass
    engine.stop()
    assert f1 == 1
    assert f2 == 0  # suppressed


def test_diagnosis_pushed_to_sink(db_with_metrics):
    sink, db = db_with_metrics
    _push_window(sink, M.GPU_UTIL_PCT, [10.0, 20.0])  # avg=15
    sink._drain()  # flush metrics so the rule engine can see them

    engine = RuleEngine(
        db_path=str(db), rules=[_rule(rid="low-gpu")], sink=sink,
        eval_interval_s=10.0, suppression_window_s=0, print_to_terminal=False,
    )
    engine.evaluate_once()
    engine.stop()

    # Engine pushed the diagnosis to sink._diag_q; flush it to DB.
    sink._drain()
    sink.close()

    conn = duckdb.connect(str(db))
    rows = conn.execute(
        "SELECT rule_id, severity, triggered_value, threshold, window_seconds, message "
        "FROM diagnoses"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    rid, sev, val, thr, win, msg = rows[0]
    assert rid == "low-gpu"
    assert sev == "warning"
    assert val == 15.0
    assert thr == 50.0
    assert win == 10
    assert msg == "15.0 vs 50.0 over 10s"


def test_p99_aggregation(db_with_metrics):
    sink, db = db_with_metrics
    # 5% outliers — QUANTILE_CONT 线性插值在 99% 处会落到 outlier 区间
    _push_window(sink, M.VLLM_REQ_TTFT_MS, [100.0] * 95 + [5000.0] * 5)
    sink.close()

    engine = RuleEngine(
        db_path=str(db),
        rules=[_rule(metric=M.VLLM_REQ_TTFT_MS, op=">", threshold=2000.0,
                     window=60, agg="p99")],
        sink=sink, eval_interval_s=10.0, suppression_window_s=0,
        print_to_terminal=False,
    )
    fires = engine.evaluate_once()
    engine.stop()
    assert fires == 1  # p99 lands in the outlier region


def test_disabled_rule_skipped(db_with_metrics):
    sink, db = db_with_metrics
    _push_window(sink, M.GPU_UTIL_PCT, [10.0])
    sink.close()

    rule = _rule()
    disabled = Rule(
        id=rule.id, name=rule.name, severity=rule.severity, category=rule.category,
        condition=rule.condition, message=rule.message, suggestion=rule.suggestion,
        enabled=False,
    )
    engine = RuleEngine(
        db_path=str(db), rules=[disabled], sink=sink,
        eval_interval_s=10.0, suppression_window_s=0, print_to_terminal=False,
    )
    assert engine.num_rules == 0
    fires = engine.evaluate_once()
    engine.stop()
    assert fires == 0


def test_invalid_rule_rejected_at_construction(db_with_metrics):
    sink, db = db_with_metrics
    bad = Rule(
        id="x", name="x", severity="info", category="x",
        condition=Condition(
            metric="not.in.catalog", op="<", threshold=1.0,
            window_seconds=10, aggregation="avg",
        ),
        message="m", suggestion="s",
    )
    with pytest.raises(ValueError, match="unknown metric"):
        RuleEngine(db_path=str(db), rules=[bad], sink=sink, print_to_terminal=False)


def test_engine_bg_thread_runs(db_with_metrics):
    """Smoke test: start/stop the bg eval thread, no exceptions."""
    sink, db = db_with_metrics
    _push_window(sink, M.GPU_UTIL_PCT, [10.0])
    sink.close()

    engine = RuleEngine(
        db_path=str(db), rules=[_rule()], sink=sink,
        eval_interval_s=0.05, suppression_window_s=0, print_to_terminal=False,
    )
    engine.start()
    # 等后台线程至少跳两次;轮询(最多 ~2s)而非固定 sleep,机器负载高时也不抖
    deadline = time.time() + 2.0
    while engine.eval_count < 2 and time.time() < deadline:
        time.sleep(0.02)
    engine.stop()
    assert engine.eval_count >= 2
