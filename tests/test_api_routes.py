"""API 端点测试 — TestClient 直跑 FastAPI app（不起 uvicorn）。"""
from __future__ import annotations

import time

import duckdb
import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.metrics_catalog import M
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink
from pping_lang.types import Diagnosis, MetricPoint


@pytest.fixture
def empty_app(tmp_path):
    """App with empty (just-created) DB and no live components."""
    db = tmp_path / "api.duckdb"
    sink = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    # Create schema by pushing one throwaway metric, then close
    sink.push_metric(MetricPoint(ts_ns=1, name=M.GPU_UTIL_PCT, value=0.0))
    sink.close()
    # Reopen sink for the app's reference (so health endpoint can read counters)
    sink2 = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db),
        instance_id="test-inst",
        engine_index=3,
        sink=sink2,
        rule_store=RuleStore(),
        nvml=None,
    )
    yield TestClient(app), db, sink2
    sink2.close()


@pytest.fixture
def app_with_data(tmp_path):
    """DB + in-memory sink state both pre-populated for endpoint tests.

    Day 18 dual-path note: the realtime endpoints (kpis/snapshot/recent ≤60s,
    roofline) read from Sink.latest() / Sink.recent() — in-memory ring
    buffers — so we MUST push test data through the same sink instance the
    app receives. The DuckDB file gets populated as a side effect of bg flush
    so the long-window endpoints (latency_trends, diagnoses_history) work too.
    """
    db = tmp_path / "api.duckdb"
    sink = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    base = time.time_ns()
    # Push 50 GPU util points over the last 10s
    for i in range(50):
        sink.push_metric(MetricPoint(
            ts_ns=base - int((50 - i) * 0.2 * 1e9),
            name=M.GPU_UTIL_PCT, value=30.0 + i * 0.5,
            engine_idx=0, gpu_idx=0,
        ))
    sink.push_metric(MetricPoint(
        ts_ns=base, name=M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, value=0.45,
    ))
    sink.push_diagnosis(Diagnosis(
        ts_ns=base, rule_id="low-gpu-util", severity="warning",
        triggered_value=34.0, threshold=50.0, window_seconds=30,
        message="GPU 平均利用率 34% 持续低于 50% 已 30s",
        suggestion="检查 batch 是否退化为 1",
        engine_idx=0,
    ))
    # Force a flush so the DuckDB file has the data for long-window queries.
    # Sink stays open — app reads its in-memory state for short-window queries.
    sink._drain()
    app = build_app(
        db_path=str(db),
        instance_id="test-inst",
        engine_index=0,
        sink=sink,
        rule_store=RuleStore(),
    )
    yield TestClient(app), db, sink
    sink.close()


def test_health_returns_ok(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["instance_id"] == "test-inst"
    assert body["engine_index"] == 3
    assert "version" in body
    assert "sink" in body
    assert "rules" in body
    assert "nvml" in body


def test_health_when_no_rule_engine(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/health")
    body = r.json()
    # No rule_engine but rule_store has 10 defaults — health falls back to store count
    assert body["rules"]["num"] == 10
    assert body["rules"]["eval_count"] == 0
    assert body["nvml"]["enabled"] is False


def test_metrics_available_returns_catalog(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/metrics/available")
    assert r.status_code == 200
    body = r.json()
    metrics = body["metrics"]
    assert M.GPU_UTIL_PCT in metrics
    assert M.VLLM_CUDAGRAPH_PADDING_RATIO in metrics
    assert len(metrics) >= 35
    assert metrics == sorted(metrics)  # sorted output


def test_metrics_recent_returns_points(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/metrics/recent", params={
        "name": M.GPU_UTIL_PCT, "seconds": 60,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == M.GPU_UTIL_PCT
    assert len(body["points"]) > 0
    # chronologically ordered (oldest first)
    ts_values = [p["ts_ns"] for p in body["points"]]
    assert ts_values == sorted(ts_values)


def test_metrics_recent_unknown_metric_422(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/metrics/recent", params={"name": "fake.metric"})
    assert r.status_code == 422


def test_metrics_recent_invalid_seconds(empty_app):
    client, _, _ = empty_app
    # seconds < 1 should be rejected
    r = client.get("/api/metrics/recent", params={
        "name": M.GPU_UTIL_PCT, "seconds": 0,
    })
    assert r.status_code == 422


def test_metrics_snapshot(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/metrics/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert M.GPU_UTIL_PCT in body["metrics"]
    point = body["metrics"][M.GPU_UTIL_PCT]
    assert "value" in point
    assert "ts_ns" in point


def test_metrics_snapshot_empty_when_no_data(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/metrics/snapshot", params={"seconds": 1})
    body = r.json()
    # very recent window — likely empty
    assert isinstance(body["metrics"], dict)


def test_diagnoses_list(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/diagnoses")
    assert r.status_code == 200
    body = r.json()
    assert len(body["diagnoses"]) >= 1
    d = body["diagnoses"][0]
    assert d["rule_id"] == "low-gpu-util"
    assert d["severity"] == "warning"
    assert d["triggered_value"] == 34.0
    assert "message" in d
    assert "suggestion" in d


def test_diagnoses_history(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/diagnoses/history")
    body = r.json()
    assert len(body["diagnoses"]) >= 1


def test_diagnoses_deduped_by_rule(tmp_path):
    """A rule re-fires every eval cycle, so the ring holds D3a×N. /api/diagnoses
    should surface each rule once (its latest firing), not once per cycle."""
    db = tmp_path / "dedup.duckdb"
    sink = LocalSink(db_path=db, instance_id="test-inst", flush_interval_s=10.0)
    base = time.time_ns()
    # low-gpu-util fires 3x (newer = higher triggered_value); insertion order = ts order
    for i in range(3):
        sink.push_diagnosis(Diagnosis(
            ts_ns=base + int(i * 1e9), rule_id="low-gpu-util", severity="warning",
            triggered_value=30.0 + i, threshold=50.0, window_seconds=30,
            message=f"util {30 + i}", suggestion="x", engine_idx=0,
        ))
    # a second, distinct rule fires once — must be kept
    sink.push_diagnosis(Diagnosis(
        ts_ns=base + int(0.5 * 1e9), rule_id="batch-degraded", severity="info",
        triggered_value=1.0, threshold=1.0, window_seconds=30,
        message="batch 1", suggestion="y", engine_idx=0,
    ))
    app = build_app(
        db_path=str(db), instance_id="test-inst", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    client = TestClient(app)
    try:
        diags = client.get("/api/diagnoses").json()["diagnoses"]
        rule_ids = [d["rule_id"] for d in diags]
        assert len(diags) == 2                      # one card per rule, not per cycle
        assert rule_ids.count("low-gpu-util") == 1
        assert rule_ids.count("batch-degraded") == 1
        kept = next(d for d in diags if d["rule_id"] == "low-gpu-util")
        assert kept["triggered_value"] == 32.0      # the latest firing survived
        hist = client.get("/api/diagnoses/history").json()["diagnoses"]
        assert [d["rule_id"] for d in hist].count("low-gpu-util") == 1
    finally:
        sink.close()


def test_system_reports_pping_version(app_with_data):
    """/api/system carries the pping-lang package version (consistent with
    /api/health), so the hero endpoint is self-contained."""
    client, _, _ = app_with_data
    body = client.get("/api/system").json()
    assert "version" in body
    assert body["version"] == client.get("/api/health").json()["version"]


def test_rules_list_returns_defaults(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/rules")
    assert r.status_code == 200
    body = r.json()
    assert len(body["rules"]) == 10
    ids = {r["id"] for r in body["rules"]}
    assert "low-gpu-util" in ids
    assert "high-cudagraph-padding" in ids
    assert "low-mfu" in ids


def test_rules_list_serialization_format(empty_app):
    client, _, _ = empty_app
    body = client.get("/api/rules").json()
    rule = body["rules"][0]
    # condition is a nested dict
    assert "condition" in rule
    assert "metric" in rule["condition"]
    assert "op" in rule["condition"]
    assert "threshold" in rule["condition"]
    assert "window_seconds" in rule["condition"]
    assert "aggregation" in rule["condition"]


def test_rule_get_by_id(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/rules/low-gpu-util")
    assert r.status_code == 200
    assert r.json()["id"] == "low-gpu-util"


def test_rule_get_unknown_404(empty_app):
    client, _, _ = empty_app
    r = client.get("/api/rules/does-not-exist")
    assert r.status_code == 404


def test_instances_list(app_with_data):
    client, _, _ = app_with_data
    r = client.get("/api/instances")
    body = r.json()
    assert "test-inst" in body["instances"]


def test_diagnosis_rules_endpoint_lists_fact_rules_and_config(empty_app):
    """/api/diagnosis_rules 暴露现役事实规则 + 中心配置(阈值已解析)。"""
    client, _db, _sink = empty_app
    data = client.get("/api/diagnosis_rules").json()
    ids = {r["id"] for r in data["rules"]}
    assert ids == {"A", "B", "C", "D"}                     # 4 瓶颈在列
    assert data["active"] is False                          # 无引擎实例(只浏览)
    assert "sla_ttft_p99_ms" in data["config"]              # 中心配置透出
    assert "custom" in data["workload_forms"]
    # 每个瓶颈暴露多条检测手段(detector),带测量层
    a = next(r for r in data["rules"] if r["id"] == "A")
    assert len(a["detectors"]) >= 2
    assert {d["layer"] for d in a["detectors"]} <= {"L1", "L2", "L3", "L4", "L5"}
    # 阈值已按配置解析成具体数(A roofline 手段的 MFU check 引 mfu_low_ratio)
    all_checks = [c for det in a["detectors"] for c in det["checks"]]
    mfu_check = next(c for c in all_checks if c["threshold_ref"] == "mfu_low_ratio")
    assert mfu_check["threshold"] == data["config"]["mfu_low_ratio"]
    # 名字 = 事实/瓶颈,根因/处方署名分列
    assert "[推断]" not in a["name"] and a["hypothesis"]


def test_update_diagnosis_config_validates_and_echoes(empty_app):
    """PUT /api/diagnosis_config：合法配置回显解析后的值;无引擎时 applied=False。"""
    client, _db, _sink = empty_app
    r = client.put("/api/diagnosis_config", json={"workload_form": "code", "mfu_low_ratio": 0.15})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] is False                         # 测试 app 没接引擎
    assert body["config"]["mfu_low_ratio"] == 0.15
    assert body["config"]["workload_form"] == "code"
    # 规则 A 的 MFU 阈值随之变成 0.15(在某条 detector 的 check 里)
    a = next(x for x in body["rules"] if x["id"] == "A")
    all_checks = [c for det in a["detectors"] for c in det["checks"]]
    mfu_check = next(c for c in all_checks if c["threshold_ref"] == "mfu_low_ratio")
    assert mfu_check["threshold"] == 0.15


def test_update_diagnosis_config_rejects_invalid(empty_app):
    """非法配置 → 400(挡住越界阈值)。"""
    client, _db, _sink = empty_app
    r = client.put("/api/diagnosis_config", json={"mbu_low_pct": 90, "mbu_high_pct": 80})
    assert r.status_code == 400


def test_diagnosis_config_hot_reload_into_running_engine(tmp_path):
    """接了真 DiagnosisEngine 时,PUT 热生效：engine.config 立即变。"""
    from pping_lang.rules.diagnosis_config import default_config
    from pping_lang.rules.diagnosis_runtime import DiagnosisEngine
    db = tmp_path / "dr.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    eng = DiagnosisEngine(sink, default_config("custom"), print_to_terminal=False)
    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore(), diag_engine=eng)
    client = TestClient(app)
    try:
        assert client.get("/api/diagnosis_rules").json()["active"] is True
        r = client.put("/api/diagnosis_config", json={"workload_form": "code", "sla_tpot_p99_ms": 7.0})
        assert r.json()["applied"] is True
        assert eng.config.sla_tpot_p99_ms == 7.0          # 热替换进了运行中的引擎
        assert eng.config.workload_form == "code"
    finally:
        eng.stop()
        sink.close()


def test_kernels_endpoint_with_data(tmp_path):
    """/api/kernels 打包 kernel 分解;class_shares 按占比降序。"""
    db = tmp_path / "k.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    base = time.time_ns()
    for name, val in [
        (M.KERNEL_SHARE_ATTENTION_PCT, 42.0),
        (M.KERNEL_SHARE_GEMM_PCT, 31.0),
        (M.KERNEL_SHARE_OTHER_PCT, 27.0),
        (M.KERNEL_GPU_BUSY_PCT, 88.0),
        (M.KERNEL_LAUNCH_COUNT_PER_S, 4128.0),
        (M.KERNEL_MEAN_DUR_US, 12.5),
        (M.KERNEL_IN_GRAPH_PCT, 89.0),
        (M.PPING_LANG_CUPTI_CB_MS, 0.06),
    ]:
        sink.push_metric(MetricPoint(ts_ns=base, name=name, value=val))
    app = build_app(
        db_path=str(db), instance_id="x", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    client = TestClient(app)
    try:
        r = client.get("/api/kernels")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        # 降序：attention(42) > gemm(31) > other(27)
        shares = data["class_shares"]
        assert [c["cls"] for c in shares] == ["attention", "gemm", "other"]
        assert data["gpu_busy_pct"] == 88.0
        assert data["launch_count_per_s"] == 4128.0
        assert data["in_graph_pct"] == 89.0
        assert data["overhead_cb_ms"] == 0.06
    finally:
        sink.close()


def test_kernels_endpoint_top_kernels_from_collector(tmp_path):
    """/api/kernels 透出 collector 的原始 per-kernel 明细(未洗的 mangled 名)。"""
    db = tmp_path / "kd.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)

    class _FakeCupti:
        last_snapshot_ts = time.time_ns()
        last_window_ns = int(1.0 * 1e9)

        def top_kernels(self):
            return [
                {"name": "flash_fwd_kernel", "cls": "attention", "count": 120,
                 "total_ms": 5.2, "mean_us": 43.0, "pct": 42.0, "in_graph_pct": 100.0},
                {"name": "cutlass_80_tensorop_s16816gemm_f16", "cls": "gemm", "count": 96,
                 "total_ms": 3.8, "mean_us": 39.6, "pct": 31.0, "in_graph_pct": 100.0},
            ]

    app = build_app(
        db_path=str(db), instance_id="x", engine_index=0,
        sink=sink, rule_store=RuleStore(), cupti=_FakeCupti(),
    )
    client = TestClient(app)
    try:
        data = client.get("/api/kernels").json()
        assert data["enabled"] is True  # 有原始明细即算启用
        tk = data["top_kernels"]
        assert tk[0]["name"] == "flash_fwd_kernel"
        assert tk[1]["name"] == "cutlass_80_tensorop_s16816gemm_f16"
        assert tk[0]["count"] == 120
        # 数据时刻字段：刚 stamp 的 ts → age 很小;窗宽 1s
        assert data["snapshot_age_s"] is not None and data["snapshot_age_s"] < 5.0
        assert abs(data["rollup_window_s"] - 1.0) < 0.01
    finally:
        sink.close()


def test_kernel_findings_logic():
    """诊断结论：从 kernel 指标派生人话结论(GEMM-bound / 单kernel / launch-bound / 碎片化)。"""
    from pping_lang.api.routes import _kernel_findings
    class_shares = [{"cls": "gemm", "pct": 87.0}, {"cls": "attention", "pct": 6.0}]
    top_kernels = [{"name": "cutlass_x", "cls": "gemm", "pct": 50.0}] + [
        {"name": f"gemm_{i}", "cls": "gemm", "pct": 1.0} for i in range(10)
    ]
    findings = _kernel_findings(
        class_shares=class_shares, top_kernels=top_kernels,
        sync_share=27.0, memcpy_share=0.1, in_graph=76.0,
        launch_rate=47000.0, overhead_cb_ms=80.0, window_s=2.0,
    )
    titles = " | ".join(f["title"] for f in findings)
    assert "bound" in titles  # GEMM-bound
    assert "单 kernel" in titles
    assert "Launch-bound" in titles
    assert "碎片化" in titles
    assert "采集开销" in titles  # 80ms/2s = 4% ≥ 3%
    assert all(f["level"] in ("info", "warning", "critical") for f in findings)
    # 每条结论带认识论分类(claim),不夹带"无身份的结论"
    assert all(
        f["claim"] in ("measurement", "derived", "hypothesis", "suggestion", "unavailable")
        for f in findings
    )
    by_title = {f["title"]: f["claim"] for f in findings}
    assert by_title.get("Launch-bound（同步等待高）") == "hypothesis"   # 因果归因 = 解读
    assert any(t.endswith("-bound") and c == "derived" for t, c in by_title.items())  # X-bound = 派生事实
    assert by_title.get("采集开销偏高") == "measurement"               # 自身实测


def test_rule_claim_taxonomy():
    """规则带认识论分类 claim(与领域 category 正交);默认 derived,校验拒绝非法值。"""
    import dataclasses

    import pytest

    from pping_lang.api.routes import _rule_to_dict
    from pping_lang.rules.defaults import DEFAULT_RULES
    from pping_lang.rules.schema import (
        ALLOWED_CLAIMS,
        Condition,
        Rule,
        validate_rule,
    )

    r = Rule(
        id="r", name="r", severity="info", category="x",
        condition=Condition(metric=M.GPU_UTIL_PCT, op="<", threshold=1.0, window_seconds=10),
        message="m", suggestion="s",
    )
    assert r.claim == "derived"                       # 默认派生事实
    assert _rule_to_dict(r)["claim"] == "derived"     # 序列化透出

    for d in DEFAULT_RULES:                           # 所有内置规则 claim 合法
        assert d.claim in ALLOWED_CLAIMS
        validate_rule(d)

    bad = dataclasses.replace(r, claim="totally-bogus")  # 非法 claim 被拒
    with pytest.raises(ValueError):
        validate_rule(bad)


def test_kernel_findings_quiet_when_healthy():
    """没有明显瓶颈时不硬凑结论。"""
    from pping_lang.api.routes import _kernel_findings
    findings = _kernel_findings(
        class_shares=[{"cls": "gemm", "pct": 35.0}, {"cls": "attention", "pct": 30.0}],
        top_kernels=[{"name": "k", "cls": "gemm", "pct": 20.0}],
        sync_share=3.0, memcpy_share=1.0, in_graph=90.0,
        launch_rate=2000.0, overhead_cb_ms=2.0, window_s=2.0,
    )
    assert findings == []


def test_kernel_findings_i18n_en():
    """findings 支持 lang='en'：英文无中文;默认 zh 不变。kernel + deep-evidence(stall)两套都测。"""
    import re

    from pping_lang.api.routes import _kernel_findings, _stall_findings
    cjk = re.compile(r"[一-鿿]")
    kw = dict(
        class_shares=[{"cls": "gemm", "pct": 87.0}],
        top_kernels=[{"name": "cutlass_x", "cls": "gemm", "pct": 50.0}]
        + [{"name": f"g{i}", "cls": "gemm", "pct": 1.0} for i in range(10)],
        sync_share=27.0, memcpy_share=12.0, in_graph=30.0,
        launch_rate=47000.0, overhead_cb_ms=80.0, window_s=2.0,
    )
    en = _kernel_findings(lang="en", **kw)
    zh = _kernel_findings(**kw)  # default zh
    assert en and zh
    en_txt = " | ".join(f["title"] + f["detail"] for f in en)
    assert not cjk.search(en_txt), f"en kernel findings still have CJK: {en_txt}"
    assert "Launch-bound" in en_txt and "GEMM" in en_txt
    assert cjk.search(" | ".join(f["title"] + f["detail"] for f in zh))  # default stays Chinese

    result = {
        "available": True,
        "stall_shares": [{"cls": "memory_dependency", "pct": 60.0},
                         {"cls": "scheduler_slack", "pct": 45.0}],
        "kernel_table": [{"kernel": "k", "dominant_stall": "memory_dependency", "dominant_pct": 70.0}],
        "overhead": {"hwfull": 1, "dropped": 5},
    }
    sf_en = _stall_findings(result, lang="en")
    assert sf_en and not cjk.search(" | ".join(f["title"] + f["detail"] for f in sf_en))
    assert cjk.search(" | ".join(f["title"] + f["detail"] for f in _stall_findings(result)))  # default zh


def test_kernels_flamegraph_endpoint(tmp_path):
    """/api/kernels/flamegraph 透出 collector 的 Python→kernel 火焰图树。"""
    db = tmp_path / "fg.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)

    class _FakeCupti:
        def flamegraph(self):
            return {"name": "root", "kind": "root", "value": 100, "children": [
                {"name": "step", "kind": "python", "value": 100, "children": [
                    {"name": "flash_fwd", "kind": "kernel", "value": 100, "children": []},
                ]},
            ]}

    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore(), cupti=_FakeCupti())
    try:
        data = TestClient(app).get("/api/kernels/flamegraph").json()
        assert data["available"] is True
        assert data["tree"]["name"] == "root"
        assert data["tree"]["children"][0]["name"] == "step"
    finally:
        sink.close()


def test_kernels_flamegraph_unavailable_without_collector(empty_app):
    client, _, _ = empty_app
    data = client.get("/api/kernels/flamegraph").json()
    assert data["available"] is False
    assert data["tree"] is None


def test_kernels_timeline_endpoint(tmp_path):
    """/api/kernels/timeline 透出 collector 的执行时间线。"""
    db = tmp_path / "tl.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)

    class _FakeCupti:
        def timeline(self, max_events=800):
            return {"span_ns": 450, "streams": [7], "count": 1,
                    "events": [{"start": 50, "dur": 100, "cls": "attention",
                                "kind": "kernel", "stream": 7, "in_graph": 1, "name": "flash_fwd"}]}

    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore(), cupti=_FakeCupti())
    try:
        data = TestClient(app).get("/api/kernels/timeline").json()
        assert data["available"] is True
        assert data["timeline"]["count"] == 1
        assert data["timeline"]["events"][0]["name"] == "flash_fwd"
    finally:
        sink.close()


def test_kernels_timeline_unavailable_without_collector(empty_app):
    client, _, _ = empty_app
    data = client.get("/api/kernels/timeline").json()
    assert data["available"] is False and data["timeline"] is None


def test_kernels_trends_endpoint(tmp_path):
    """/api/kernels/trends 从内存环返回 kernel 指标时序(给实时趋势图)。"""
    db = tmp_path / "tr2.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    base = time.time_ns()
    for i in range(5):
        ts = base - int((5 - i) * 2 * 1e9)
        sink.push_metric(MetricPoint(ts_ns=ts, name=M.KERNEL_GPU_BUSY_PCT, value=60.0 + i))
        sink.push_metric(MetricPoint(ts_ns=ts, name=M.KERNEL_SHARE_GEMM_PCT, value=80.0))
    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore())
    try:
        d = TestClient(app).get("/api/kernels/trends?seconds=60").json()
        assert d["available"] is True
        assert len(d["series"]["gpu_busy"]) == 5
        assert d["series"]["gemm"][0]["v"] == 80.0
    finally:
        sink.close()


def test_kernels_trace_endpoint(tmp_path):
    """/api/kernels/trace 透出 Chrome Trace JSON。"""
    db = tmp_path / "tr.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)

    class _FakeCupti:
        def chrome_trace(self):
            return {"displayTimeUnit": "ms", "traceEvents": [
                {"ph": "X", "name": "flash_fwd", "cat": "attention", "pid": 0, "tid": 7,
                 "ts": 0.0, "dur": 0.1, "args": {}}]}

    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore(), cupti=_FakeCupti())
    try:
        data = TestClient(app).get("/api/kernels/trace").json()
        assert data["available"] is True
        assert data["trace"]["traceEvents"][0]["name"] == "flash_fwd"
    finally:
        sink.close()


def test_kernels_endpoint_disabled_when_no_data(empty_app):
    """无 kernel 数据(CUPTI 未启用 / 无 GPU)→ enabled=False,不 500。"""
    client, _, _ = empty_app
    r = client.get("/api/kernels")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is False
    assert data["class_shares"] == []
    assert data["gpu_busy_pct"] is None


def test_diagnoses_empty_when_no_db_tables(tmp_path):
    """API should not 500 when DuckDB file/tables don't exist yet."""
    db = tmp_path / "empty.duckdb"
    duckdb.connect(str(db)).close()  # create file but no tables
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="x", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    client = TestClient(app)
    try:
        r = client.get("/api/diagnoses")
        assert r.status_code == 200
        assert r.json()["diagnoses"] == []
        r = client.get("/api/metrics/snapshot")
        assert r.status_code == 200
        assert r.json()["metrics"] == {}
    finally:
        sink.close()


# === Deep Evidence (阶段 2 PC Sampling 按需取证) ===

def test_deep_evidence_endpoint_runs_and_concludes(tmp_path):
    """POST /api/kernels/deep_evidence 跑取证窗 → stall 分解 + findings。"""
    from pping_lang.collector.cupti import (
        CuptiKernelCollector,
        FakeActivitySource,
        FakePcSamplingLib,
        PcSamplingController,
        StallSample,
    )
    db = tmp_path / "de.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    R = "smsp__pcsamp_warps_issue_stalled_"
    lib = FakePcSamplingLib(drain_batches=[
        [],   # baseline drain(窗前清零)
        [
            StallSample("flash_fwd_kernel", R + "long_scoreboard", 80),
            StallSample("flash_fwd_kernel", R + "selected", 20),
        ],
    ])
    coll = CuptiKernelCollector(
        sink, source=FakeActivitySource(),
        pc_sampling=PcSamplingController(lib, sink=sink),
    )
    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink, rule_store=RuleStore(), cupti=coll)
    client = TestClient(app)
    try:
        # window=0 → 不真睡,只收尾 drain 一次
        data = client.post("/api/kernels/deep_evidence?window=0&period_log2=12").json()
        assert data["available"] is True
        assert data["sample_total"] == 100.0
        shares = {d["cls"]: d["pct"] for d in data["stall_shares"]}
        assert abs(shares["memory_dependency"] - 100.0) < 1e-6   # 80 全是 stall,selected=issued
        titles = [f["title"] for f in data["findings"]]
        assert any("访存依赖" in t for t in titles)
        # GET 读最近结果
        g = client.get("/api/kernels/deep_evidence").json()
        assert g["available_now"] is True
        assert g["last"]["sample_total"] == 100.0
    finally:
        sink.close()


def test_deep_evidence_unavailable_without_collector(empty_app):
    client, _db, _sink = empty_app
    data = client.post("/api/kernels/deep_evidence?window=0").json()
    assert data["available"] is False
    g = client.get("/api/kernels/deep_evidence").json()
    assert g["available_now"] is False and g["last"] is None
