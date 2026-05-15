"""Plugin × Sink 端到端 — env-var 配置 + DuckDB 内容验证。"""
from __future__ import annotations

import duckdb

from pping_lang.metrics_catalog import M
from pping_lang.plugin import PpingLangStatLogger


def test_record_pushes_overhead_metric(tmp_path, monkeypatch):
    db = tmp_path / "test.duckdb"
    monkeypatch.setenv("PPING_LANG_DB_PATH", str(db))
    monkeypatch.setenv("PPING_LANG_INSTANCE_ID", "plugin-test")

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=3)
    plugin.log_engine_initialized()
    for _ in range(5):
        plugin.record(None, None)
    plugin._sink.close()

    conn = duckdb.connect(str(db))
    rows = conn.execute(
        "SELECT metric_name, engine_idx, instance_id FROM metrics WHERE metric_name = ?",
        [M.PPING_LANG_RECORD_OVERHEAD_US],
    ).fetchall()
    conn.close()
    assert len(rows) == 5
    assert all(r[0] == M.PPING_LANG_RECORD_OVERHEAD_US for r in rows)
    assert all(r[1] == 3 for r in rows)
    assert all(r[2] == "plugin-test" for r in rows)


def test_record_noop_before_log_engine_initialized():
    """record() before log_engine_initialized must be a silent no-op (defensive)."""
    p = PpingLangStatLogger(vllm_config=None, engine_index=0)
    # No sink yet — must not raise
    p.record(None, None)
    p.record(None, None, mm_cache_stats=None, engine_idx=0)


def test_default_instance_id_uses_engine_index(tmp_path, monkeypatch):
    db = tmp_path / "test.duckdb"
    monkeypatch.setenv("PPING_LANG_DB_PATH", str(db))
    monkeypatch.delenv("PPING_LANG_INSTANCE_ID", raising=False)

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=5)
    plugin.log_engine_initialized()
    plugin.record(None, None)
    plugin._sink.close()

    conn = duckdb.connect(str(db))
    instance = conn.execute("SELECT DISTINCT instance_id FROM metrics").fetchone()[0]
    conn.close()
    assert instance == "local-5"


def test_overhead_value_is_nonnegative(tmp_path, monkeypatch):
    """heartbeat 测的是 record() 内部耗时，应当 >= 0。"""
    db = tmp_path / "test.duckdb"
    monkeypatch.setenv("PPING_LANG_DB_PATH", str(db))

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=0)
    plugin.log_engine_initialized()
    plugin.record(None, None)
    plugin._sink.close()

    conn = duckdb.connect(str(db))
    value = conn.execute(
        "SELECT value FROM metrics WHERE metric_name = ?",
        [M.PPING_LANG_RECORD_OVERHEAD_US],
    ).fetchone()[0]
    conn.close()
    assert value >= 0.0
