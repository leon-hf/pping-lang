"""ApiServer 实际起 uvicorn 验证端到端 — 用 port=0 让 OS 分配避免冲突。"""
from __future__ import annotations

import urllib.request

import pytest

from pping_lang.api.routes import build_app
from pping_lang.api.server import ApiServer
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint


@pytest.fixture
def server(tmp_path):
    db = tmp_path / "lifecycle.duckdb"
    sink = LocalSink(db_path=db, instance_id="lifecycle", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(ts_ns=1, name="gpu.utilization_pct", value=42.0))
    app = build_app(
        db_path=str(db),
        instance_id="lifecycle",
        engine_index=0,
        sink=sink,
        rule_store=RuleStore(),
    )
    server = ApiServer(app, host="127.0.0.1", port=0)  # OS-assigned port
    server.start()
    try:
        yield server
    finally:
        server.stop()
        sink.close()


def test_server_starts_and_serves_health(server):
    url = f"{server.url}/api/health"
    with urllib.request.urlopen(url, timeout=2.0) as resp:
        assert resp.status == 200
        body = resp.read().decode()
    assert '"status":"ok"' in body or '"status": "ok"' in body


def test_server_url_uses_actual_port(server):
    """Port=0 → OS assigns; .url should reflect the real port."""
    # Should be a non-zero port number
    port_str = server.url.split(":")[-1]
    assert int(port_str) > 0
    assert int(port_str) != 0


def test_server_stop_idempotent(server):
    server.stop()
    server.stop()  # must not raise
