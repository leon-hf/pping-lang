"""UI 静态资产测试 — / 路由返回单文件 HTML。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "ui.duckdb"
    sink = LocalSink(db_path=db, instance_id="ui-test", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="ui-test", engine_index=0,
        sink=sink, rule_store=RuleStore(),
    )
    yield TestClient(app)
    sink.close()


def test_root_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "<!DOCTYPE html>" in body
    assert "pping-lang" in body


def test_root_contains_required_libs(client):
    body = client.get("/").text
    assert "alpinejs" in body.lower()
    assert "chart.js" in body.lower() or "chart.umd" in body.lower()


def test_root_references_known_api_endpoints(client):
    """UI's JS should fetch the endpoints we ship.

    KPI/Roofline/latency_trends 出来后聚合工作移到 server 端，前端不再直接
    call snapshot/recent；指标名也在 server 端组装好。
    """
    body = client.get("/").text
    for endpoint in [
        "/api/health",
        "/api/system",
        "/api/kpis",
        "/api/roofline",
        "/api/latency_trends",
        "/api/diagnoses",
    ]:
        assert endpoint in body, f"UI does not reference {endpoint}"


def test_root_references_marquee_kpi_labels(client):
    """Dashboard 必须把 marquee KPI 标签暴露给用户（中文为主、英文术语共存）。

    服务端 `/api/kpis` 返回的是命名字段（ttft_p99_ms 等），原始 metric 名不再
    出现在 HTML 里。改成检查用户能看到的 label。
    """
    body = client.get("/").text
    for label in ["TTFT", "TPOT", "MFU", "KV cache", "padding", "Roofline"]:
        assert label in body, f"UI missing marquee KPI label {label!r}"


def test_ui_file_under_size_budget():
    """单文件 HTML 应该轻。加完压测 tab 已经在 ~80KB；预算放宽到 100KB；
    超过这条就该考虑拆 vendor CSS/JS 出去或上 esbuild。"""
    ui = Path(__file__).parent.parent / "src" / "pping_lang" / "ui" / "index.html"
    size = ui.stat().st_size
    assert size < 100_000, f"UI file is {size} bytes, exceeds 100KB budget"


def test_rules_tab_has_crud_endpoints_referenced(client):
    """规则 tab 的 JS 必须引用 CRUD + test 端点。"""
    body = client.get("/").text
    # All four CRUD verbs + test
    assert "/api/rules/" in body  # PUT/DELETE/test patterns use this prefix
    assert "method: 'PUT'" in body or "method:\"PUT\"" in body or "PUT" in body
    assert "DELETE" in body
    assert "/test" in body


def test_rules_tab_form_includes_all_required_fields(client):
    """规则 form 必须能编辑所有规则字段。"""
    body = client.get("/").text
    for field in ["editing.id", "editing.name", "editing.severity",
                  "editing.category", "editing.condition.metric",
                  "editing.condition.op", "editing.condition.threshold",
                  "editing.condition.window_seconds",
                  "editing.condition.aggregation",
                  "editing.message", "editing.suggestion",
                  "editing.enabled"]:
        assert field in body, f"rules form missing field {field}"
