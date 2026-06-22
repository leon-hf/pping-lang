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

    预算 90KB→100KB→110KB→120KB:Kernel tab 扩成完整诊断面板;规则 tab 重做成
    策展事实规则只读 + 中心配置弹框 + 自定义规则 CRUD(列表 + 新建/编辑弹框),
    markup 实打实变多。once-load、gzip 后才十几 KB,为这点体积上构建工具不划算。"""
    ui = Path(__file__).parent.parent / "src" / "pping_lang" / "ui"
    html = (ui / "index.html").stat().st_size
    css = (ui / "dashboard.css").stat().st_size
    js = (ui / "dashboard.js").stat().st_size
    assert html < 120_000, f"index.html is {html} bytes, exceeds 120KB"
    assert css < 70_000, f"dashboard.css is {css} bytes, exceeds 70KB"
    assert js < 70_000, f"dashboard.js is {js} bytes, exceeds 70KB"


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


def test_rules_tab_references_diagnosis_endpoints(client):
    """规则 tab 改为读现役事实规则 + 改中心配置(不再是旧的自由 CRUD)。"""
    body = client.get("/").text + client.get("/dashboard.js").text  # UI 拆成 html+js
    assert "/api/diagnosis_rules" in body            # 读策展 + 自定义规则
    assert "/api/diagnosis_config" in body           # PUT 改中心 SLA/阈值
    assert "/api/diagnosis_rules/custom" in body     # 自定义规则 CRUD(同一引擎评)
    assert "saveConfig" in body and "saveRule" in body
    # 自定义规则的增删改回来了(走新引擎,非旧 RuleStore)
    assert "newRule" in body and "deleteRule" in body
    assert "/api/rules/" not in body                 # 不再用旧 RuleStore CRUD 端点


def test_rules_tab_config_editor_and_readonly_rules(client):
    """规则 tab:中心配置(顶部常驻条:业务形态 + SLA 常显,高级阈值折叠)+ 规则只读展示。"""
    body = client.get("/").text
    # 中心配置编辑器 —— 顶部常驻上下文条
    assert "cfg-bar" in body                          # 顶部配置条容器
    assert "cfgDraft.workload_form" in body           # 业务形态选择
    assert "cfgDraft.sla_ttft_p99_ms" in body         # SLA 常驻显示
    assert "advancedOpen" in body                     # 高级阈值折叠开关
    assert "advKeys()" in body and "cfgLabels" in body  # 高级阈值网格
    # 只读规则渲染
    for token in ["r.name", "r.hypothesis", "r.suggestion", "r.checks", "kindLabel"]:
        assert token in body, f"rules tab missing {token}"
