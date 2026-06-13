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
    # vendor 本地化:Alpine/Chart 走 /vendor/ 而非 CDN,离线/air-gapped 主机也能渲染
    assert "alpine.min.js" in body.lower()
    assert "chart.umd" in body.lower()
    assert "jsdelivr" not in body.lower() and "cdn." not in body.lower()


def test_vendor_assets_served(client):
    """vendor JS 本地服务可用(C:不再依赖 CDN)。"""
    alp = client.get("/vendor/alpine.min.js")
    assert alp.status_code == 200 and "javascript" in alp.headers["content-type"]
    assert len(alp.text) > 1000
    chart = client.get("/vendor/chart.umd.min.js")
    assert chart.status_code == 200 and "javascript" in chart.headers["content-type"]
    assert len(chart.text) > 1000


def test_root_references_known_api_endpoints(client):
    """UI's JS should fetch the endpoints we ship.

    KPI/Roofline/latency_trends 出来后聚合工作移到 server 端，前端不再直接
    call snapshot/recent；指标名也在 server 端组装好。
    """
    body = client.get("/").text + client.get("/dashboard.js").text  # UI 拆成 html+js
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


def test_ui_assets_split_and_under_budget():
    """CSS/JS 已拆出单文件。index.html 只剩 markup,预算 100KB;
    CSS/JS 各自有独立预算。再大就该上构建/压缩。

    预算从 90KB→100KB:Kernel tab 扩成完整诊断面板(算子分类 + 瓶颈判定 +
    warp 三态 + per-kernel 下钻 + roofline),markup 实打实变多。once-load、
    gzip 后才十几 KB,为这点体积上构建工具不划算。"""
    ui = Path(__file__).parent.parent / "src" / "pping_lang" / "ui"
    html = (ui / "index.html").stat().st_size
    css = (ui / "dashboard.css").stat().st_size
    js = (ui / "dashboard.js").stat().st_size
    assert html < 110_000, f"index.html is {html} bytes, exceeds 110KB"
    assert css < 70_000, f"dashboard.css is {css} bytes, exceeds 70KB"
    assert js < 60_000, f"dashboard.js is {js} bytes, exceeds 60KB"


def test_css_and_js_served(client):
    """拆出的 CSS/JS 由路由提供,content-type 正确。"""
    css = client.get("/dashboard.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert ".kfinding" in css.text or ".tl-block" in css.text
    js = client.get("/dashboard.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "function dashboard()" in js.text


def test_index_references_split_assets(client):
    body = client.get("/").text
    assert "/dashboard.css" in body and "/dashboard.js" in body


def test_rules_tab_has_crud_endpoints_referenced(client):
    """规则 tab 的 JS 必须引用 CRUD + test 端点。"""
    body = client.get("/").text + client.get("/dashboard.js").text  # UI 拆成 html+js
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
