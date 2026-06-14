"""DiagnosisEngine 运行时 —— 周期跑诊断规则,把触发的诊断推到 sink。

每个 eval 周期:
  ① 从 DuckDB 取近窗 token 计数 → compute_operating_point → regime + 解析 MFU;
  ② metric_fn = DuckDB 真聚合,但 `vllm.perf.mfu_ratio` 缺时用解析 MFU 覆盖(喂 D1c/D3a);
  ③ evaluate(metric_fn, config, 规则, regime) → findings;
  ④ findings → Diagnosis(事实进 message,署名根因/处方进 suggestion),带抑制窗口。

与旧 RuleEngine 并存无碍(都往同一 sink 推 Diagnosis,/api/diagnoses 照常服务)。
"""
from __future__ import annotations

import logging
import os
import sys
from threading import Event, Thread
from typing import Any

from pping_lang.clock import wall_ns
from pping_lang.metrics_catalog import M
from pping_lang.rules.diagnosis_config import DiagnosisConfig
from pping_lang.rules.diagnosis_engine import db_metric_fn, evaluate
from pping_lang.rules.diagnosis_rules import DIAGNOSIS_RULES
from pping_lang.rules.operating_point import compute_operating_point
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis

logger = logging.getLogger(__name__)
_GLYPH = {"info": "i", "warning": "!", "critical": "X"}


class DiagnosisEngine:
    def __init__(
        self,
        db_path: str,
        sink: Sink,
        config: DiagnosisConfig,
        *,
        params: float | None = None,
        dtype_bytes: int = 2,
        peak_compute_tflops: float | None = None,
        peak_mem_bw_tbs: float | None = None,
        engine_index: int = 0,
        eval_interval_s: float = 1.0,
        suppression_window_s: float = 30.0,
        print_to_terminal: bool | None = None,
    ) -> None:
        self._db_path = db_path
        self._sink = sink
        self._cfg = config
        self._params = params
        self._dtype_b = dtype_bytes
        self._peak_c = peak_compute_tflops
        self._peak_bw = peak_mem_bw_tbs
        self._engine_index = engine_index
        self._eval_interval = eval_interval_s
        self._suppression_ns = int(suppression_window_s * 1e9)
        self._stop = Event()
        self._thread: Thread | None = None
        self._conn: Any = None
        self._last_fire_ns: dict[str, int] = {}
        if print_to_terminal is None:
            print_to_terminal = os.environ.get("PPING_LANG_DIAGNOSIS_PRINT", "1") != "0"
        self._print = print_to_terminal
        self.eval_count = 0
        self.fire_count = 0

    def start(self) -> None:
        if self._thread is None:
            self._thread = Thread(target=self._run, daemon=True, name="DiagnosisEngine")
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
        return self._evaluate_all()

    @property
    def config(self) -> DiagnosisConfig:
        return self._cfg

    def set_config(self, cfg: DiagnosisConfig) -> None:
        """热替换配置。eval 循环每轮开头读 self._cfg,引用赋值原子,无需锁。"""
        self._cfg = cfg

    # === internals ===

    def _run(self) -> None:
        while not self._stop.wait(self._eval_interval):
            try:
                self._evaluate_all()
            except Exception:
                logger.exception("[pping-lang] diagnosis eval pass failed")

    def _ensure_conn(self) -> Any:
        if self._conn is None:
            import duckdb

            from pping_lang.sink.local import SCHEMA_STATEMENTS
            self._conn = duckdb.connect(self._db_path)
            for stmt in SCHEMA_STATEMENTS:
                try:
                    self._conn.execute(stmt)
                except Exception:
                    pass
        return self._conn

    def _fetch_token_points(self, conn: Any, now_ns: int, window_s: int = 60) -> list[tuple[float, int]]:
        cutoff = now_ns - int(window_s * 1e9)
        try:
            rows = conn.execute(
                "SELECT value, ts_ns FROM metrics WHERE metric_name IN (?, ?) AND ts_ns >= ?",
                [M.VLLM_ITER_GEN_TOKENS, M.VLLM_ITER_PROMPT_TOKENS, cutoff],
            ).fetchall()
        except Exception:
            return []
        by_ts: dict[int, float] = {}
        for value, ts in rows:
            by_ts[int(ts)] = by_ts.get(int(ts), 0.0) + float(value)
        return [(v, ts) for ts, v in by_ts.items()]

    def _evaluate_all(self) -> int:
        self.eval_count += 1
        try:
            conn = self._ensure_conn()
        except Exception:
            logger.exception("[pping-lang] diagnosis: cannot open DuckDB")
            return 0
        now_ns = wall_ns()  # 查询 cutoff + Diagnosis 落库 ts,须用 wall(跨进程/重启可比)

        op = compute_operating_point(
            self._fetch_token_points(conn, now_ns),
            self._params, self._dtype_b, self._peak_c, self._peak_bw,
        )
        base_fn = db_metric_fn(conn, now_ns)

        def metric_fn(metric: str, window_s: int, agg: str):
            v = base_fn(metric, window_s, agg)
            # perf_stats 死时(vLLM 0.21)用解析 MFU 覆盖,喂 D1c/D3a
            if metric == M.VLLM_PERF_MFU_RATIO and v is None:
                return op.mfu
            return v

        findings = evaluate(metric_fn, self._cfg, DIAGNOSIS_RULES, op.regime)
        fires = 0
        for f in findings:
            last = self._last_fire_ns.get(f.rule_id, 0)
            if last and (now_ns - last) < self._suppression_ns:
                continue
            self._last_fire_ns[f.rule_id] = now_ns
            self.fire_count += 1
            fires += 1
            first_val = next(iter(f.values.values()), 0.0)
            sug = f"[推断] {f.hypothesis}"
            if f.suggestion:
                sug += f"  [建议] {f.suggestion}"
            self._sink.push_diagnosis(Diagnosis(
                ts_ns=now_ns,
                rule_id=f.rule_id,
                severity=f.severity,  # type: ignore[arg-type]
                triggered_value=float(first_val),
                threshold=0.0,
                window_seconds=0,
                message=f.name,
                suggestion=sug,
                engine_idx=self._engine_index,
                context={k: float(v) for k, v in f.values.items()} or None,
            ))
            if self._print:
                print(f"\n[pping-lang] [{_GLYPH.get(f.severity, '*')}] {f.severity.upper()}: {f.name}",
                      file=sys.stderr)
                print(f"  {sug}", file=sys.stderr, flush=True)
        return fires
