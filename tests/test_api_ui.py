"""UI 静态资产测试 — / 路由返回单文件 HTML。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.sink.local import LocalSink


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "ui.duckdb"
    sink = LocalSink(db_path=db, instance_id="ui-test", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="ui-test", engine_index=0,
        sink=sink, rules=DEFAULT_RULES,
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
    """UI's JS should fetch the endpoints we ship."""
    body = client.get("/").text
    for endpoint in [
        "/api/health",
        "/api/metrics/snapshot",
        "/api/metrics/recent",
        "/api/diagnoses",
    ]:
        assert endpoint in body, f"UI does not reference {endpoint}"


def test_root_references_marquee_metrics(client):
    """Dashboard 必须把 marquee 指标显示出来。"""
    body = client.get("/").text
    for metric in [
        "gpu.utilization_pct",
        "vllm.cudagraph.padding_ratio",
        "vllm.perf.mfu_ratio",
    ]:
        assert metric in body, f"UI missing marquee metric {metric}"


def test_ui_file_under_size_budget():
    """单文件 HTML 应该轻，目标 < 30KB 以保证瞬时加载。"""
    ui = Path(__file__).parent.parent / "pping_lang" / "ui" / "index.html"
    size = ui.stat().st_size
    assert size < 30_000, f"UI file is {size} bytes, exceeds 30KB budget"
