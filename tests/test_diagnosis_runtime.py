"""DiagnosisEngine 运行时端到端:真 DuckDB → 算 operating point(regime/MFU)→ 推 Diagnosis。

重点验 regime+解析 MFU 这条链(D1c 需要 compute_bound + MFU 低 + 前置 S1):
perf_stats 死(没 mfu 指标)也能靠解析 MFU 让 D1c 触发。
"""
from __future__ import annotations

import time

from pping_lang.metrics_catalog import M
from pping_lang.rules.diagnosis_config import default_config
from pping_lang.rules.diagnosis_runtime import DiagnosisEngine
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint


class _CaptureSink:
    def __init__(self):
        self.diags = []

    def push_metric(self, m):  # 运行时不用,但鸭子类型要有
        pass

    def push_diagnosis(self, d):
        self.diags.append(d)

    def close(self):
        pass


def test_runtime_fires_d1c_via_analytical_regime_and_mfu(tmp_path):
    db = tmp_path / "diag.duckdb"
    sink = LocalSink(db_path=db, instance_id="t", flush_interval_s=10.0)
    base = time.time_ns()
    # prefill 批:每 step 500 tokens,50ms 间隔 → AI=500 > ridge(95/0.448≈212) → compute_bound;
    # 吞吐 ≈ 2*0.5e9*500/0.05/1e12 = 10 TFLOPS → MFU=10/95≈0.105 < 0.2
    for i in range(12):
        sink.push_metric(MetricPoint(ts_ns=base + i * 50 * 10**6,
                                     name=M.VLLM_ITER_PROMPT_TOKENS, value=500.0))
    # 高 TTFT → 严格 SLA(code: 100ms)下 S1 触发
    for i in range(20):
        sink.push_metric(MetricPoint(ts_ns=base + i, name=M.VLLM_REQ_TTFT_MS, value=200.0))
    sink.close()

    cap = _CaptureSink()
    eng = DiagnosisEngine(
        str(db), cap, default_config("code"),
        params=0.5e9, dtype_bytes=2,
        peak_compute_tflops=95.0, peak_mem_bw_tbs=0.448,
        print_to_terminal=False,
    )
    eng.evaluate_once()

    ids = {d.rule_id for d in cap.diags}
    assert "S1" in ids                       # TTFT 200 > 100(SLA)
    assert "D1c" in ids                       # compute_bound + 解析 MFU<0.2 + 前置 S1 全通

    d1c = next(d for d in cap.diags if d.rule_id == "D1c")
    assert "[推断]" in d1c.suggestion          # 根因/处方署名进了 suggestion
    assert d1c.message == "MFU 偏低"             # message 是事实(纯)
    assert d1c.severity in ("info", "warning", "critical")
    assert d1c.context  # 带实测值


def test_runtime_quiet_when_idle(tmp_path):
    """空库(无流量)→ 不推任何诊断(不乱报)。"""
    db = tmp_path / "empty.duckdb"
    LocalSink(db_path=db, instance_id="t", flush_interval_s=10.0).close()
    cap = _CaptureSink()
    eng = DiagnosisEngine(str(db), cap, default_config("custom"),
                          params=0.5e9, peak_compute_tflops=95.0, peak_mem_bw_tbs=0.448,
                          print_to_terminal=False)
    eng.evaluate_once()
    assert cap.diags == []
