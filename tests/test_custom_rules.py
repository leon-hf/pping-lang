"""自定义规则:store 校验/持久化 + 经 DiagnosisEngine 与策展规则同评 + API CRUD。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.metrics_catalog import M
from pping_lang.rules.custom_store import CustomRuleStore
from pping_lang.rules.diagnosis_config import default_config
from pping_lang.rules.diagnosis_runtime import DiagnosisEngine
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint


def _rule(**kw):
    base = dict(name="GPU 利用率偏低", metric="gpu.utilization_pct", op="<",
               threshold=50.0, window_seconds=30, aggregation="avg", severity="warning")
    base.update(kw)
    return base


def test_store_add_validate_persist_roundtrip(tmp_path):
    path = tmp_path / "custom.json"
    store = CustomRuleStore(str(path))
    rec = store.add(_rule(hypothesis="可能 batch 退化", suggestion="查并发"))
    assert rec["id"].startswith("custom-")
    assert rec["custom"] is True
    assert path.exists()                       # 落盘
    # 重新加载 → 还在
    store2 = CustomRuleStore(str(path))
    assert len(store2.list_dicts()) == 1
    # 转 FactRule:单 check、无前置、measurement
    fr = store2.fact_rules()[0]
    assert fr.kind == "fact" and fr.precondition == () and fr.claim == "measurement"
    assert fr.checks[0].metric == "gpu.utilization_pct" and fr.checks[0].threshold == 50.0


def test_store_rejects_bad_rule(tmp_path):
    store = CustomRuleStore(str(tmp_path / "c.json"))
    with pytest.raises(ValueError):
        store.add(_rule(metric="not.a.metric"))
    with pytest.raises(ValueError):
        store.add(_rule(op="≈"))
    with pytest.raises(ValueError):
        store.add(_rule(name=""))
    with pytest.raises(ValueError):
        store.add(_rule(window_seconds=0))      # 窗口必须 > 0
    # add 总是自动分配 id(忽略传入的 id),所以不会和内置 S1 撞
    assert store.add(_rule(id="S1"))["id"].startswith("custom-")


def test_store_update_delete(tmp_path):
    store = CustomRuleStore(str(tmp_path / "c.json"))
    rec = store.add(_rule())
    rid = rec["id"]
    up = store.update(rid, _rule(threshold=10.0, name="改名"))
    assert up["id"] == rid and up["threshold"] == 10.0 and up["name"] == "改名"
    assert store.delete(rid) is True
    assert store.list_dicts() == []
    with pytest.raises(KeyError):
        store.update(rid, _rule())


def test_custom_rule_fires_through_same_engine(tmp_path):
    """自定义规则和策展规则走同一 DiagnosisEngine(读 sink 内存环):命中即报。"""
    db = tmp_path / "d.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    base = time.time_ns()
    for i in range(20):   # GPU util 持续 5% < 自定义阈值 50
        sink.push_metric(MetricPoint(ts_ns=base + i, name=M.GPU_UTIL_PCT, value=5.0))

    store = CustomRuleStore(str(tmp_path / "c.json"))
    store.add(_rule(name="GPU 利用率偏低", metric="gpu.utilization_pct", op="<",
                    threshold=50.0, window_seconds=60))

    eng = DiagnosisEngine(sink, default_config("custom"),
                          custom_store=store, print_to_terminal=False)
    eng.evaluate_once()
    diags = sink.recent_diagnoses(0, 100)
    ids = {d["rule_id"] for d in diags}
    assert any(i.startswith("custom-") for i in ids)   # 自定义规则触发了
    d = next(x for x in diags if x["rule_id"].startswith("custom-"))
    assert d["message"] == "GPU 利用率偏低"               # 事实名直接当 message
    sink.close()


@pytest.fixture
def app_with_store(tmp_path):
    db = tmp_path / "api.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(ts_ns=1, name=M.GPU_UTIL_PCT, value=0.0))
    sink.close()
    sink2 = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    store = CustomRuleStore(str(tmp_path / "c.json"))
    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink2, rule_store=RuleStore(), custom_store=store)
    yield TestClient(app), store
    sink2.close()


def test_api_custom_rule_crud(app_with_store):
    client, store = app_with_store
    # 建
    r = client.post("/api/diagnosis_rules/custom", json=_rule(name="自建规则"))
    assert r.status_code == 200
    rid = r.json()["id"]
    # GET 里能看到 custom_rules + 可编辑标记
    data = client.get("/api/diagnosis_rules").json()
    assert data["custom_editable"] is True
    assert any(c["id"] == rid for c in data["custom_rules"])
    assert len(data["rules"]) >= 13            # 策展规则仍在
    # 改
    r2 = client.put(f"/api/diagnosis_rules/custom/{rid}", json=_rule(name="改了", threshold=7.0))
    assert r2.status_code == 200 and r2.json()["threshold"] == 7.0
    # 删
    assert client.delete(f"/api/diagnosis_rules/custom/{rid}").status_code == 200
    assert client.delete(f"/api/diagnosis_rules/custom/{rid}").status_code == 404
    # 非法 → 400
    assert client.post("/api/diagnosis_rules/custom", json=_rule(metric="x")).status_code == 400


def test_api_custom_unavailable_without_store(tmp_path):
    """没接 store(引擎没跑)→ CRUD 503,GET 仍可浏览策展规则。"""
    db = tmp_path / "api.duckdb"
    sink = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    sink.push_metric(MetricPoint(ts_ns=1, name=M.GPU_UTIL_PCT, value=0.0))
    sink.close()
    sink2 = LocalSink(db_path=db, instance_id="x", flush_interval_s=10.0)
    app = build_app(db_path=str(db), instance_id="x", engine_index=0,
                    sink=sink2, rule_store=RuleStore())
    client = TestClient(app)
    try:
        assert client.get("/api/diagnosis_rules").json()["custom_editable"] is False
        assert client.post("/api/diagnosis_rules/custom", json=_rule()).status_code == 503
    finally:
        sink2.close()
