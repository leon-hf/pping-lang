"""报告生成测试 — 模板渲染、各 section 数据提取、API 端点。"""
from __future__ import annotations

import time

import duckdb
import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.hardware import GPUPeak
from pping_lang.metrics_catalog import M
from pping_lang.report.analysis import (
    config_audit,
    executive_summary,
    roofline_data,
    rules_summary,
    top_diagnoses,
    trend_data,
)
from pping_lang.report.generator import generate_report
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink
from pping_lang.types import Diagnosis, MetricPoint


@pytest.fixture
def populated_db(tmp_path):
    """DB with realistic synthetic data spanning ~10 seconds."""
    db = tmp_path / "rep.duckdb"
    sink = LocalSink(db_path=db, instance_id="rep", flush_interval_s=10.0)
    base = time.monotonic_ns()
    # GPU util ~75% over 50 samples
    for i in range(50):
        sink.push_metric(MetricPoint(
            ts_ns=base - i * 10**8, name=M.GPU_UTIL_PCT, value=75.0,
        ))
    # KV cache ~0.45
    for i in range(20):
        sink.push_metric(MetricPoint(
            ts_ns=base - i * 5 * 10**8,
            name=M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, value=0.45,
        ))
    # MFU ~0.05
    for i in range(20):
        sink.push_metric(MetricPoint(
            ts_ns=base - i * 5 * 10**8,
            name=M.VLLM_PERF_MFU_RATIO, value=0.05,
        ))
    # CUDA padding ratio ~0.7
    for i in range(20):
        sink.push_metric(MetricPoint(
            ts_ns=base - i * 5 * 10**8,
            name=M.VLLM_CUDAGRAPH_PADDING_RATIO, value=0.7,
        ))
    # Some TTFTs
    for ttft in [120, 180, 250, 300, 350, 500, 800, 1500, 2200, 3000]:
        sink.push_metric(MetricPoint(
            ts_ns=base, name=M.VLLM_REQ_TTFT_MS, value=float(ttft),
        ))
    # E2E latency = total request count
    for _ in range(10):
        sink.push_metric(MetricPoint(
            ts_ns=base, name=M.VLLM_REQ_E2E_LATENCY_MS, value=2000.0,
        ))
    # Diagnoses
    sink.push_diagnosis(Diagnosis(
        ts_ns=base, rule_id="high-cudagraph-padding", severity="warning",
        triggered_value=0.7, threshold=0.3, window_seconds=60,
        message="CUDA padding 70%", suggestion="调小 max_num_seqs",
    ))
    sink.push_diagnosis(Diagnosis(
        ts_ns=base, rule_id="low-mfu", severity="warning",
        triggered_value=0.05, threshold=0.20, window_seconds=60,
        message="MFU 5%", suggestion="检查 padding",
    ))
    # Perf stats (for roofline)
    for i in range(10):
        ts = base - i * 10**8
        sink.push_metric(MetricPoint(ts_ns=ts, name=M.VLLM_PERF_FLOPS_PER_GPU, value=1e12))
        sink.push_metric(MetricPoint(ts_ns=ts, name=M.VLLM_PERF_READ_BYTES_PER_GPU, value=5e9))
        sink.push_metric(MetricPoint(ts_ns=ts, name=M.VLLM_PERF_WRITE_BYTES_PER_GPU, value=1e9))
    sink.close()
    return db


def test_executive_summary_extracts_kpis(populated_db):
    conn = duckdb.connect(str(populated_db))
    s = executive_summary(conn, since_ns=0)
    conn.close()
    assert s["total_requests"] == 10
    assert s["ttft_p50_ms"] is not None
    assert s["ttft_p99_ms"] is not None
    assert s["ttft_p99_ms"] > s["ttft_p50_ms"]
    assert s["gpu_util_avg_pct"] == pytest.approx(75.0)
    assert s["mfu_avg_pct"] == pytest.approx(5.0)  # 0.05 * 100, fp tolerance
    assert s["padding_ratio_avg_pct"] == pytest.approx(70.0)
    assert s["total_diagnoses"] == 2


def test_top_diagnoses_groups_by_rule(populated_db):
    conn = duckdb.connect(str(populated_db))
    diags = top_diagnoses(conn, since_ns=0)
    conn.close()
    assert len(diags) == 2
    ids = {d["rule_id"] for d in diags}
    assert ids == {"high-cudagraph-padding", "low-mfu"}


def test_trend_data_extracts_series(populated_db):
    conn = duckdb.connect(str(populated_db))
    trends = trend_data(conn, since_ns=0)
    conn.close()
    assert "GPU 利用率 %" in trends
    assert len(trends["GPU 利用率 %"]) == 50


def test_roofline_data_pairs_flops_bytes(populated_db):
    conn = duckdb.connect(str(populated_db))
    roof = roofline_data(conn, since_ns=0)
    conn.close()
    assert len(roof) >= 5
    for p in roof:
        assert p["ai"] > 0
        assert p["throughput_tflops"] > 0


def test_rules_summary_counts_per_rule(populated_db):
    conn = duckdb.connect(str(populated_db))
    rs = rules_summary(conn, since_ns=0)
    conn.close()
    assert len(rs) == 2
    counts = {r["rule_id"]: r["fire_count"] for r in rs}
    assert counts["high-cudagraph-padding"] == 1
    assert counts["low-mfu"] == 1


def test_config_audit_returns_suggestions_from_data(populated_db):
    conn = duckdb.connect(str(populated_db))
    audit = config_audit(conn, since_ns=0, vllm_config=None)
    conn.close()
    # padding > 30% triggers suggestion
    assert any("padding" in s["title"].lower() for s in audit["suggestions"])
    assert audit["config"] is None  # no vllm_config provided


def test_generate_report_returns_html(populated_db):
    html = generate_report(
        db_path=str(populated_db),
        instance_id="rep", seconds=60, plotly_mode="cdn",
    )
    assert "<!DOCTYPE html>" in html
    assert "pping-lang report" in html
    assert "rep" in html  # instance_id
    # Marquee data should appear
    assert "70" in html  # padding ratio
    assert "5" in html   # MFU


def test_generate_report_with_gpu_peak_includes_roofline(populated_db):
    peak = GPUPeak(bf16_tflops=989.0, mem_bw_gbs=3350.0)
    html = generate_report(
        db_path=str(populated_db),
        instance_id="rep", seconds=60,
        gpu_peak=peak, plotly_mode="cdn",
    )
    # Roofline section should reference peak
    assert "989" in html or "3350" in html


def test_generate_report_inline_plotly_is_self_contained(populated_db):
    html = generate_report(
        db_path=str(populated_db),
        instance_id="rep", seconds=60, plotly_mode="inline",
    )
    # Inline plotly puts the entire library in the HTML
    assert len(html) > 1_000_000  # > 1MB indicates inline plotly


def test_generate_report_handles_empty_db(tmp_path):
    """Report should not crash when DB has no data."""
    db = tmp_path / "empty.duckdb"
    duckdb.connect(str(db)).close()
    sink = LocalSink(db_path=db, instance_id="empty", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=0.0))
    sink.close()

    html = generate_report(
        db_path=str(db), instance_id="empty", seconds=60, plotly_mode="cdn",
    )
    assert "<!DOCTYPE html>" in html
    assert "empty" in html  # instance_id


def test_api_report_endpoint(populated_db):
    """API endpoint returns HTML with correct content-type."""
    sink = LocalSink(db_path=populated_db, instance_id="rep", flush_interval_s=10.0)
    app = build_app(
        db_path=str(populated_db), instance_id="rep", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    client = TestClient(app)
    try:
        r = client.get("/api/report?seconds=60")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "pping-lang report" in r.text
        # Content-Disposition for filename suggestion
        assert "Content-Disposition" in r.headers
        assert "pping-lang-report-rep" in r.headers["Content-Disposition"]
    finally:
        sink.close()


def test_api_report_invalid_seconds_rejected(populated_db):
    sink = LocalSink(db_path=populated_db, instance_id="rep", flush_interval_s=10.0)
    app = build_app(
        db_path=str(populated_db), instance_id="rep", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    client = TestClient(app)
    try:
        r = client.get("/api/report?seconds=10")  # below 60 min
        assert r.status_code == 422
    finally:
        sink.close()
