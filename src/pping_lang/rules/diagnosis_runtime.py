"""DiagnosisEngine 运行时 —— 周期跑诊断规则,把触发的诊断推到 sink。

**纯内存评估**(不碰 DuckDB):每个 eval 周期都读 sink 的内存环(`sink.recent`),
不再每秒查 DuckDB(去掉了进程内分析库的读争用 + 刷盘滞后)。

每个 eval 周期:
  ① 从内存环取近窗 token 计数 → compute_operating_point → regime + 解析 MFU;
  ② metric_fn = 内存环聚合(_agg_in_memory),`vllm.perf.mfu_ratio` 缺时用解析 MFU 覆盖(喂 D1c/D3a);
  ③ evaluate(metric_fn, config, 规则, regime) → findings;
  ④ findings → Diagnosis(事实进 message,署名根因/处方进 suggestion),带抑制窗口。

诊断推到 sink 后进 sink 的内存诊断环 → /api/diagnoses 即时可见(无刷盘滞后)。
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
from pping_lang.rules.diagnosis_engine import evaluate
from pping_lang.rules.diagnosis_rules import DIAGNOSIS_RULES
from pping_lang.rules.engine import _agg_in_memory
from pping_lang.rules.operating_point import compute_operating_point
from pping_lang.sink.base import Sink
from pping_lang.types import Diagnosis

logger = logging.getLogger(__name__)
_GLYPH = {"info": "i", "warning": "!", "critical": "X"}


class DiagnosisEngine:
    def __init__(
        self,
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
        custom_store: Any = None,
    ) -> None:
        self._sink = sink
        self._cfg = config
        # 自定义规则(用户在 UI 建的)与策展规则同一评估器评。None = 没接 store。
        self._custom_store = custom_store
        self._params = params
        self._dtype_b = dtype_bytes
        self._peak_c = peak_compute_tflops
        self._peak_bw = peak_mem_bw_tbs
        self._engine_index = engine_index
        self._eval_interval = eval_interval_s
        self._suppression_ns = int(suppression_window_s * 1e9)
        self._stop = Event()
        self._thread: Thread | None = None
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

    def _fetch_token_points(self, window_s: int = 60) -> list[tuple[float, int]]:
        """近窗 prefill+decode token 计数(从内存环),按 ts 合并 → 操作点输入。"""
        by_ts: dict[int, float] = {}
        for name in (M.VLLM_ITER_GEN_TOKENS, M.VLLM_ITER_PROMPT_TOKENS):
            for value, ts in self._sink.recent(name, window_s):
                by_ts[int(ts)] = by_ts.get(int(ts), 0.0) + float(value)
        return [(v, ts) for ts, v in by_ts.items()]

    def _evaluate_all(self) -> int:
        self.eval_count += 1
        now_ns = wall_ns()  # Diagnosis 落库 ts,用 wall(跨进程/重启可比)

        op = compute_operating_point(
            self._fetch_token_points(),
            self._params, self._dtype_b, self._peak_c, self._peak_bw,
        )

        def metric_fn(metric: str, window_s: int, agg: str):
            # perf_stats 死时(vLLM 0.21)用解析 MFU 覆盖,喂 D1c/D3a
            if metric == M.VLLM_PERF_MFU_RATIO:
                pts = self._sink.recent(metric, window_s)
                v = _agg_in_memory([val for val, _ in pts], agg) if pts else None
                return v if v is not None else op.mfu
            pts = self._sink.recent(metric, window_s)
            if not pts:
                return None
            return _agg_in_memory([val for val, _ in pts], agg)

        # 策展规则 + 用户自定义规则,一起评(同一评估器、同一展示路径)
        rules = DIAGNOSIS_RULES
        if self._custom_store is not None:
            rules = DIAGNOSIS_RULES + self._custom_store.fact_rules()
        findings = evaluate(metric_fn, self._cfg, rules, op.regime)
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
