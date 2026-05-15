"""规则引擎 — 周期查 DuckDB 窗口聚合，触发即推 Diagnosis 并打印终端。

设计选择（pre-impl-rfc §7 Day 4 决策点）：
  窗口聚合实现：直接 SQL 查 DuckDB，不维护内存 ring buffer。
  理由：
    - 一份代码同样适用 Centralized 模式（rule engine 在 server 端查全局 DB）
    - DuckDB 列式 + 索引，秒级 SELECT AVG/QUANTILE 性能足够
    - 数据延迟 ≤ flush_interval_s（默认 5s），acceptable for v0.1

去重/抑制（pre-impl-rfc §7 Day 6 决策点）：
  同一规则在 suppression_window_s（默认 30s）内不重复触发。
  避免 dashboard 一直闪同一条 warning。

打印：触发时往 stderr 输出彩色块，便于本地 demo 视觉。
关闭：PPING_LANG_DIAGNOSIS_PRINT=0
"""
from __future__ import annotations

import logging
import os
import sys
import time
from threading import Event, Thread
from typing import Any

from pping_lang.rules.schema import Aggregation, Op, Rule, validate_rule
from pping_lang.rules.store import RuleStore
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis

logger = logging.getLogger(__name__)

DEFAULT_EVAL_INTERVAL_S = 1.0
DEFAULT_SUPPRESSION_WINDOW_S = 30.0

_AGG_TO_SQL: dict[Aggregation, str] = {
    "avg": "AVG(value)",
    "max": "MAX(value)",
    "min": "MIN(value)",
    "count": "CAST(COUNT(value) AS DOUBLE)",
    "p50": "QUANTILE_CONT(value, 0.5)",
    "p95": "QUANTILE_CONT(value, 0.95)",
    "p99": "QUANTILE_CONT(value, 0.99)",
}

_OP_TO_FN: dict[Op, Any] = {
    "<":  lambda a, t: a < t,
    "<=": lambda a, t: a <= t,
    ">":  lambda a, t: a > t,
    ">=": lambda a, t: a >= t,
    "==": lambda a, t: a == t,
    "!=": lambda a, t: a != t,
}

_SEVERITY_GLYPH = {"info": "i", "warning": "!", "critical": "X"}


def evaluate_condition_against_db(
    conn: Any,
    condition: "Condition",
    now_ns: int,
) -> tuple[bool | None, float | None]:
    """Evaluate a condition's SQL aggregation against the metrics table.

    Returns (would_fire, actual_value):
    - (None, None) — no data in window or unsupported aggregation
    - (False, value) — data present but condition not met
    - (True, value)  — would trigger

    Side-effect free: doesn't push diagnoses, doesn't update suppression.
    Used by both RuleEngine._evaluate_one (for real eval) and the
    /api/rules/{id}/test endpoint (for preview).
    """
    from pping_lang.rules.schema import Condition  # avoid circular at module top
    assert isinstance(condition, Condition)
    agg_sql = _AGG_TO_SQL.get(condition.aggregation)
    if agg_sql is None:
        return None, None
    cutoff_ns = now_ns - int(condition.window_seconds * 1e9)
    sql = (
        f"SELECT {agg_sql} FROM metrics "
        f"WHERE metric_name = ? AND ts_ns >= ?"
    )
    try:
        row = conn.execute(sql, [condition.metric, cutoff_ns]).fetchone()
    except Exception:
        return None, None
    if row is None or row[0] is None:
        return None, None
    actual = float(row[0])
    fired = _OP_TO_FN[condition.op](actual, condition.threshold)
    return fired, actual


class RuleEngine:
    """Periodic SQL-based rule evaluator. Runs on a daemon thread.

    Rules can be supplied as either:
    - a static list (legacy / tests) — engine uses snapshot, no hot reload
    - a RuleStore — engine queries store on every eval tick (Day 9 hot reload)
                    so CRUD via /api/rules takes effect within ~1 eval interval
    """

    def __init__(
        self,
        db_path: str,
        rules: list[Rule] | RuleStore,
        sink: Sink,
        engine_index: int = 0,
        eval_interval_s: float = DEFAULT_EVAL_INTERVAL_S,
        suppression_window_s: float = DEFAULT_SUPPRESSION_WINDOW_S,
        print_to_terminal: bool | None = None,
    ) -> None:
        if isinstance(rules, RuleStore):
            self._store: RuleStore | None = rules
            self._static_rules: list[Rule] | None = None
            # Validate current snapshot at construction (catches bad user JSON)
            for r in rules.list():
                validate_rule(r)
        else:
            self._store = None
            for r in rules:
                validate_rule(r)
            self._static_rules = list(rules)
        self._db_path = db_path
        self._sink = sink
        self._engine_index = engine_index
        self._eval_interval = eval_interval_s
        self._suppression_window_ns = int(suppression_window_s * 1e9)
        self._stop = Event()
        self._thread: Thread | None = None
        self._conn: Any = None  # lazy init in bg thread
        self._last_fire_ns: dict[str, int] = {}
        if print_to_terminal is None:
            print_to_terminal = os.environ.get("PPING_LANG_DIAGNOSIS_PRINT", "1") != "0"
        self._print = print_to_terminal
        # Test/inspection hooks
        self.eval_count = 0
        self.fire_count = 0

    # === public lifecycle ===

    def _current_rules(self) -> list[Rule]:
        """Return the live, enabled rule set for this eval pass."""
        if self._store is not None:
            return [r for r in self._store.list() if r.enabled]
        return [r for r in (self._static_rules or []) if r.enabled]

    @property
    def num_rules(self) -> int:
        return len(self._current_rules())

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(target=self._run, daemon=True, name="RuleEngine")
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=self._eval_interval * 2)
        self._thread = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def evaluate_once(self) -> int:
        """Run one evaluation pass synchronously (for tests). Returns # fires."""
        return self._evaluate_all()

    # === internals ===

    def _run(self) -> None:
        # First eval after one interval (let data accumulate)
        while not self._stop.wait(self._eval_interval):
            try:
                self._evaluate_all()
            except Exception:
                logger.exception("[pping-lang] rule eval pass failed")

    def _ensure_conn(self) -> Any:
        if self._conn is None:
            import duckdb
            self._conn = duckdb.connect(self._db_path)
        return self._conn

    def _evaluate_all(self) -> int:
        self.eval_count += 1
        t_start = time.monotonic_ns()
        try:
            conn = self._ensure_conn()
        except Exception:
            logger.exception("[pping-lang] could not open DuckDB for rule eval")
            return 0
        now_ns = time.monotonic_ns()
        fires = 0
        # Snapshot rules per-tick — picks up CRUD changes (hot reload, Day 9)
        for rule in self._current_rules():
            try:
                if self._evaluate_one(conn, rule, now_ns):
                    fires += 1
            except Exception:
                logger.exception("[pping-lang] rule %s eval failed", rule.id)
        # Self-observability: how long this eval pass took (Day 12)
        from pping_lang.metrics_catalog import M as _M
        from pping_lang.types import MetricPoint as _MP
        elapsed_ms = (time.monotonic_ns() - t_start) / 1e6
        try:
            self._sink.push_metric(_MP(
                ts_ns=time.monotonic_ns(),
                name=_M.PPING_LANG_RULE_EVAL_MS,
                value=elapsed_ms,
                engine_idx=self._engine_index,
            ))
        except Exception:
            pass
        return fires

    def _evaluate_one(self, conn: Any, rule: Rule, now_ns: int) -> bool:
        # Suppression
        last = self._last_fire_ns.get(rule.id, 0)
        if last and (now_ns - last) < self._suppression_window_ns:
            return False

        cond = rule.condition
        fired, actual = evaluate_condition_against_db(conn, cond, now_ns)
        if not fired or actual is None:
            return False

        # Trigger
        self._last_fire_ns[rule.id] = now_ns
        self.fire_count += 1
        message = rule.message.format(
            value=actual, threshold=cond.threshold, window=cond.window_seconds,
        )
        diag = Diagnosis(
            ts_ns=now_ns,
            rule_id=rule.id,
            severity=rule.severity,
            triggered_value=actual,
            threshold=cond.threshold,
            window_seconds=cond.window_seconds,
            message=message,
            suggestion=rule.suggestion,
            engine_idx=self._engine_index,
        )
        self._sink.push_diagnosis(diag)
        if self._print:
            self._print_diag(rule, message)
        return True

    def _print_diag(self, rule: Rule, message: str) -> None:
        glyph = _SEVERITY_GLYPH.get(rule.severity, "*")
        sev = rule.severity.upper()
        print(f"\n[pping-lang] [{glyph}] {sev}: {rule.name}", file=sys.stderr)
        print(f"  {message}", file=sys.stderr)
        print(f"  -> {rule.suggestion}", file=sys.stderr, flush=True)
