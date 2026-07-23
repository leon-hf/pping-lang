"""DiagnosisEngine 运行时端到端：**纯内存** → 算 operating point(regime/MFU)→ 推 Diagnosis。

引擎只读 sink 的内存环(不碰 DuckDB)。重点验 regime+解析 MFU 这条链(D1c 需要
compute_bound + MFU 低 + 前置 S1):perf_stats 死(没 mfu 指标)也能靠解析 MFU 让 D1c 触发。
诊断推进 sink 后,从 sink.recent_diagnoses() 内存环读回(命中即可见,无刷盘滞后)。
"""
from __future__ import annotations

import time

from pping_lang.metrics_catalog import M
from pping_lang.rules.diagnosis_config import default_config
from pping_lang.rules.diagnosis_runtime import DiagnosisEngine
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint


def test_runtime_fires_A_via_analytical_mfu_and_nvml_mbu(tmp_path):
    """perf_stats 死时：有活在跑(空载守卫)+ 解析 MFU 低 + NVML 兜底 MBU 低 → A 喂不饱触发。"""
    db = tmp_path / "diag.duckdb"
    sink = LocalSink(db_path=db, instance_id="t", flush_interval_s=10.0)
    base = time.time_ns()
    # decode 小批：每 step 100 tokens,50ms 间隔 →
    # 吞吐 ≈ 2*0.5e9*100/0.05/1e12 = 2 TFLOPS → 解析 MFU=2/95≈0.021 < 0.2(低)
    for i in range(12):
        sink.push_metric(MetricPoint(ts_ns=base + i * 50 * 10**6,
                                     name=M.VLLM_ITER_PROMPT_TOKENS, value=100.0))
    # 空载守卫：确有请求在跑(avg running_reqs 2 > 0.5)
    for i in range(8):
        sink.push_metric(MetricPoint(ts_ns=base + i, name=M.VLLM_SCHEDULER_RUNNING_REQS, value=2.0))
    # 实测 MBU 不出 → 退回 NVML mem_util/100 = 0.30 < 0.50 → MBU 低
    for i in range(8):
        sink.push_metric(MetricPoint(ts_ns=base + i, name=M.GPU_MEM_UTIL_PCT, value=30.0))

    eng = DiagnosisEngine(
        sink, default_config("custom"),
        params=0.5e9, dtype_bytes=2,
        peak_compute_tflops=95.0, peak_mem_bw_tbs=0.448,
        print_to_terminal=False,
    )
    eng.evaluate_once()

    diags = sink.recent_diagnoses(0, 100)
    ids = {d["rule_id"] for d in diags}
    assert "A" in ids                          # 解析 MFU<0.2 且 NVML MBU<50 → 双低 → 喂不饱

    a = next(d for d in diags if d["rule_id"] == "A")
    assert "[推断]" in a["suggestion"]           # 根因/处方进了 suggestion
    assert a["severity"] in ("info", "warning", "critical")
    assert a["context"]  # 带实测值
    sink.close()


def test_runtime_quiet_when_idle(tmp_path):
    """空 sink(无流量)→ 不推任何诊断(不乱报)。"""
    db = tmp_path / "empty.duckdb"
    sink = LocalSink(db_path=db, instance_id="t", flush_interval_s=10.0)
    eng = DiagnosisEngine(sink, default_config("custom"),
                          params=0.5e9, peak_compute_tflops=95.0, peak_mem_bw_tbs=0.448,
                          print_to_terminal=False)
    eng.evaluate_once()
    assert sink.recent_diagnoses(0, 100) == []
    sink.close()
