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

from pping_lang.clock import wall_ns
from pping_lang.rules.schema import Aggregation, Condition, Op, Rule, validate_rule
from pping_lang.rules.store import RuleStore
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis

logger = logging.getLogger(__name__)

DEFAULT_EVAL_INTERVAL_S = 1.0
DEFAULT_SUPPRESSION_WINDOW_S = 30.0

_AGG_TO_SQL: dict[str, str] = {
    "avg": "AVG(value)",
    "max": "MAX(value)",
    "min": "MIN(value)",
    "sum": "SUM(value)",
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


def _agg_in_memory(values: list[float], agg: Aggregation) -> float | None:
    """Python-side aggregation — kept for ad-hoc use; engine itself batches
    aggregation directly into DuckDB via UNION ALL (much faster).
    """
    if not values:
        return None
    if agg == "avg":
        return sum(values) / len(values)
    if agg == "max":
        return max(values)
    if agg == "min":
        return min(values)
    if agg == "sum":
        return float(sum(values))
    if agg == "count":
        return float(len(values))
    if agg in ("p50", "p95", "p99"):
        q = {"p50": 0.5, "p95": 0.95, "p99": 0.99}[agg]
        s = sorted(values)
        n = len(s)
        if n == 1:
            return s[0]
        rank = q * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        return s[lo] + (s[hi] - s[lo]) * frac
    return None

_SEVERITY_GLYPH = {"info": "i", "warning": "!", "critical": "X"}


def evaluate_condition_against_db(
    conn: Any,
    condition: Condition,
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

            from pping_lang.sink.local import SCHEMA_STATEMENTS
            self._conn = duckdb.connect(self._db_path)
            # Without this, the engine may have opened the conn BEFORE LocalSink
            # created the `metrics` table — DuckDB would then keep throwing
            # CatalogException every tick. CREATE IF NOT EXISTS is idempotent
            # and means both writers (LocalSink) and readers (this engine) own
            # the schema regardless of init order.
            for stmt in SCHEMA_STATEMENTS:
                try:
                    self._conn.execute(stmt)
                except Exception:
                    logger.debug("[pping-lang] schema bootstrap stmt failed (ok if other conn already ran it)")
        return self._conn

    def _evaluate_all(self) -> int:
        self.eval_count += 1
        t_start = time.monotonic_ns()
        try:
            conn = self._ensure_conn()
        except Exception:
            logger.exception("[pping-lang] could not open DuckDB for rule eval")
            return 0
        now_ns = wall_ns()  # 查询 metrics 窗口的 cutoff,须与落库 ts(wall)同源
        rules = self._current_rules()
        fires = 0
        try:
            # Batched fetch + in-memory aggregation. We tried 4 approaches and
            # benchmarked on real-vllm with sustained load (Day 17-18):
            #   • per-rule SQL QUANTILE_CONT          ~90 ms  (original)
            #   • batched fetch + Python aggregation  ~25 ms  ← we use this
            #   • numpy fetchnumpy + np.percentile    ~44 ms  (string mask overhead)
            #   • single UNION ALL of all aggregates  ~76 ms  (DuckDB doesn't
            #                                                  parallelize branches)
            # Going below 25ms requires an in-memory ring buffer instead of
            # DuckDB-backed queries — that's a v0.2 architecture change.
            by_metric = self._fetch_metric_values(conn, rules, now_ns)
            for rule in rules:
                try:
                    if self._evaluate_one_against_fetched(rule, by_metric, now_ns):
                        fires += 1
                except Exception:
                    logger.exception("[pping-lang] rule %s eval failed", rule.id)
        except Exception:
            logger.exception("[pping-lang] batched eval failed; rules skipped this tick")
        # Self-observability: how long this eval pass took (Day 12)
        from pping_lang.metrics_catalog import M as _M
        from pping_lang.types import MetricPoint as _MP
        elapsed_ms = (time.monotonic_ns() - t_start) / 1e6
        try:
            self._sink.push_metric(_MP(
                ts_ns=wall_ns(),
                name=_M.PPING_LANG_RULE_EVAL_MS,
                value=elapsed_ms,
                engine_idx=self._engine_index,
            ))
        except Exception:
            pass
        return fires

    def _fetch_metric_values(
        self, conn: Any, rules: list[Rule], now_ns: int,
    ) -> dict[str, list[tuple[float, int]]]:
        """One SQL → {metric_name: [(value, ts_ns), ...]} covering all rules.

        Per metric we pull rows covering the WIDEST window any rule needs.
        Then `_evaluate_one_against_fetched` slices each rule's narrower window
        client-side. Result: O(rules) → O(1) DB round-trips per eval tick.
        """
        if not rules:
            return {}
        widest_window_s: dict[str, int] = {}
        for r in rules:
            m = r.condition.metric
            w = int(r.condition.window_seconds)
            if w > widest_window_s.get(m, 0):
                widest_window_s[m] = w
        if not widest_window_s:
            return {}
        metric_names = list(widest_window_s.keys())
        max_window = max(widest_window_s.values())
        overall_cutoff = now_ns - int(max_window * 1e9)
        placeholders = ", ".join(["?"] * len(metric_names))
        sql = (
            f"SELECT metric_name, value, ts_ns FROM metrics "
            f"WHERE metric_name IN ({placeholders}) AND ts_ns >= ?"
        )
        rows = conn.execute(sql, [*metric_names, overall_cutoff]).fetchall()
        by_metric: dict[str, list[tuple[float, int]]] = {m: [] for m in metric_names}
        for name, value, ts_ns in rows:
            by_metric[name].append((float(value), int(ts_ns)))
        return by_metric

    def _evaluate_one_against_fetched(
        self,
        rule: Rule,
        by_metric: dict[str, list[tuple[float, int]]],
        now_ns: int,
    ) -> bool:
        # Suppression
        last = self._last_fire_ns.get(rule.id, 0)
        if last and (now_ns - last) < self._suppression_window_ns:
            return False

        cond = rule.condition
        rows = by_metric.get(cond.metric)
        if not rows:
            return False
        window_cutoff = now_ns - int(cond.window_seconds * 1e9)
        values = [v for v, ts in rows if ts >= window_cutoff]
        if not values:
            return False
        actual = _agg_in_memory(values, cond.aggregation)
        if actual is None:
            return False
        if not _OP_TO_FN[cond.op](actual, cond.threshold):
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

    def _evaluate_one(self, conn: Any, rule: Rule, now_ns: int) -> bool:
        """Legacy per-rule path. Kept for API `/api/rules/{id}/test` test endpoint."""
        last = self._last_fire_ns.get(rule.id, 0)
        if last and (now_ns - last) < self._suppression_window_ns:
            return False

        cond = rule.condition
        fired, actual = evaluate_condition_against_db(conn, cond, now_ns)
        if not fired or actual is None:
            return False

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
