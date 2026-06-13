"""P0-C 实测 scaling 闭环 API 测试 —— 状态机 + 入参校验(不真压测)。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.hardware import GPUPeak
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "scaling.duckdb"
    sink = LocalSink(db_path=db, instance_id="sc-test", flush_interval_s=10.0)
    app = build_app(
        db_path=str(db), instance_id="sc-test", engine_index=0,
        sink=sink, rule_store=RuleStore(),
        gpu_peak=GPUPeak(bf16_tflops=95.0, mem_bw_gbs=448.0),
    )
    yield TestClient(app)
    sink.close()


def test_scaling_initial_state(client):
    r = client.get("/api/roofline/scaling")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["result"] is None


def test_roofline_includes_scaling_field(client):
    r = client.get("/api/roofline")
    assert r.status_code == 200
    assert "scaling" in r.json()  # 没跑过为 None,但键必须在(前端依赖)
    assert r.json()["scaling"] is None


def test_sweep_rejects_without_model_arch(client):
    # 无 vllm_config → 无 _params → 400(换算不了 TFLOPs),且不进入 running 态
    r = client.post("/api/roofline/scaling_sweep")
    assert r.status_code == 400
    assert client.get("/api/roofline/scaling").json()["running"] is False
