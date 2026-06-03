"""Plugin × Sink 端到端 — env-var 配置 + DuckDB 内容验证。"""
from __future__ import annotations

import duckdb
import pytest

from pping_lang.metrics_catalog import M
from pping_lang.plugin import PpingLangStatLogger


@pytest.fixture(autouse=True)
def _isolate_plugin(monkeypatch):
    """Disable API / NVML / Rules — these tests focus on Sink wiring only."""
    monkeypatch.setenv("PPING_LANG_DISABLE_API", "1")
    monkeypatch.setenv("PPING_LANG_DISABLE_NVML", "1")
    monkeypatch.setenv("PPING_LANG_DISABLE_RULES", "1")


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


def test_cupti_disabled_by_default(tmp_path, monkeypatch):
    """CUPTI 是 opt-in：不设 PPING_LANG_ENABLE_CUPTI 时不应创建采集器。"""
    monkeypatch.setenv("PPING_LANG_DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.delenv("PPING_LANG_ENABLE_CUPTI", raising=False)

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=0)
    plugin.log_engine_initialized()
    try:
        assert plugin._cupti is None
    finally:
        plugin._sink.close()


def test_cupti_enabled_creates_collector_and_degrades_gracefully(tmp_path, monkeypatch):
    """设 PPING_LANG_ENABLE_CUPTI=1 应创建采集器；无 cupti/GPU 的环境(如 CI/Windows)
    下应优雅禁用(enabled=False)而非崩溃。"""
    monkeypatch.setenv("PPING_LANG_DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setenv("PPING_LANG_ENABLE_CUPTI", "1")

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=0)
    plugin.log_engine_initialized()
    try:
        assert plugin._cupti is not None
        # 本测试环境无 cupti-python/GPU → 优雅禁用,record() 仍正常
        assert plugin._cupti.enabled is False
        plugin.record(None, None)  # must not raise
    finally:
        plugin._sink.close()
