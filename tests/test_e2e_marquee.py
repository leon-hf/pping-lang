"""端到端 marquee 测试 — 验证 v0.1 卖点诊断真能从合成 stats 一路打到 diagnoses 表。

链路：合成 SchedulerStats → VllmStatsCollector → Sink → DuckDB → RuleEngine → Diagnosis

不依赖 NVML / 真 vLLM。Plugin 完整数据流，只是 stats 来源是合成的。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import duckdb
import pytest

from pping_lang.hardware import GPUPeak
from pping_lang.plugin import PpingLangStatLogger


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PPING_LANG_DB_PATH", str(tmp_path / "e2e.duckdb"))
    monkeypatch.setenv("PPING_LANG_INSTANCE_ID", "e2e")
    monkeypatch.setenv("PPING_LANG_FLUSH_INTERVAL_S", "0.2")
    monkeypatch.setenv("PPING_LANG_RULE_EVAL_INTERVAL_S", "0.2")
    monkeypatch.setenv("PPING_LANG_DISABLE_NVML", "1")
    monkeypatch.setenv("PPING_LANG_DISABLE_API", "1")  # avoid uvicorn port collisions
    monkeypatch.setenv("PPING_LANG_DIAGNOSIS_PRINT", "0")  # silence stderr in tests

    p = PpingLangStatLogger(vllm_config=None, engine_index=0)
    p.log_engine_initialized()
    # Inject demo GPU peak (no NVML, so plugin couldn't auto-detect)
    if p._collector is not None:
        p._collector._gpu_peak = GPUPeak(bf16_tflops=989.0, mem_bw_gbs=3350.0)
    yield p, tmp_path / "e2e.duckdb"
    if p._rule_engine is not None:
        p._rule_engine.stop()
    if p._sink is not None and not p._sink._closed:
        p._sink.close()


def _bad_scheduler() -> SimpleNamespace:
    """高 padding (70%) + 低 MFU (~5%) 的合成 SchedulerStats。"""
    return SimpleNamespace(
        num_running_reqs=8,
        num_waiting_reqs=2,
        kv_cache_usage=0.45,
        prefix_cache_stats=SimpleNamespace(queries=100, hits=15),  # 15% hit
        cudagraph_stats=SimpleNamespace(
            num_unpadded_tokens=300,
            num_padded_tokens=1000,  # 70% padding
            num_paddings=10,
            runtime_mode="PIECEWISE",
        ),
        perf_stats=SimpleNamespace(
            num_flops_per_gpu=int(4.9e12),         # ~5% MFU @ dt=0.1s, H100
            num_read_bytes_per_gpu=int(1675e8),    # 50% bw util
            num_write_bytes_per_gpu=0,
            debug_stats=None,
        ),
    )


def test_marquee_rules_fire_end_to_end(plugin):
    """Day 5 acceptance：完整链路喂 unhealthy 数据 → padding & MFU 规则触发。"""
    p, db_path = plugin

    # 推 ~80 steps over ~8s — 给数据累积 + 让 rule engine bg 线程多次评估
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        p.record(_bad_scheduler(), None)
        time.sleep(0.1)

    # 强制 sink 落盘后再关闭 rule engine
    p._sink._drain()
    # 给 rule engine 至少一次评估机会（覆盖最新落盘的数据）
    p._rule_engine.evaluate_once()
    p._sink._drain()
    p._rule_engine.stop()
    p._sink.close()

    # 验证 diagnoses 表里有 marquee 规则触发
    conn = duckdb.connect(str(db_path))
    diag_rows = conn.execute(
        "SELECT rule_id, severity, message FROM diagnoses ORDER BY ts_ns"
    ).fetchall()
    conn.close()

    rule_ids = {r[0] for r in diag_rows}
    assert "high-cudagraph-padding" in rule_ids, (
        f"high-cudagraph-padding 应触发（70% padding > 30% threshold）。"
        f"已触发: {rule_ids}"
    )
    assert "low-mfu" in rule_ids, (
        f"low-mfu 应触发（~5% MFU < 20% threshold）。"
        f"已触发: {rule_ids}"
    )
    # 同时 prefix-cache (15% < 10% threshold? no — 15% > 10%, 不应触发)
    # low-prefix-cache-hit threshold 是 10%，hits=15/queries=100=0.15 > 0.10，不该触发
    assert "low-prefix-cache-hit" not in rule_ids


def test_marquee_diagnosis_message_format(plugin):
    """触发的 message 应是已渲染的人类可读文本（不是 raw template）。"""
    p, db_path = plugin

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        p.record(_bad_scheduler(), None)
        time.sleep(0.1)
    p._sink._drain()
    p._rule_engine.evaluate_once()
    p._sink._drain()
    p._rule_engine.stop()
    p._sink.close()

    conn = duckdb.connect(str(db_path))
    rows = conn.execute(
        "SELECT message FROM diagnoses WHERE rule_id = 'high-cudagraph-padding'"
    ).fetchall()
    conn.close()
    assert rows
    msg = rows[0][0]
    # 模板已渲染 — 不应包含 {value}/{threshold} 占位符
    assert "{" not in msg
    assert "%" in msg  # padding 比例以 % 显示
    assert "padding" in msg.lower() or "补 0" in msg


def test_metrics_table_has_derived_values(plugin):
    """派生指标 padding_ratio / mfu_ratio / mem_bw_util_ratio 都进了 DuckDB。"""
    p, db_path = plugin

    for _ in range(20):
        p.record(_bad_scheduler(), None)
        time.sleep(0.05)
    p._sink._drain()
    p._rule_engine.stop()
    p._sink.close()

    conn = duckdb.connect(str(db_path))
    derived_names = conn.execute(
        "SELECT DISTINCT metric_name FROM metrics WHERE metric_name LIKE '%ratio%'"
    ).fetchall()
    conn.close()
    names = {r[0] for r in derived_names}
    assert "vllm.cudagraph.padding_ratio" in names
    assert "vllm.perf.mfu_ratio" in names
    assert "vllm.perf.mem_bw_util_ratio" in names
