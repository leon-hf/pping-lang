"""Rules CRUD + test 端点测试。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from pping_lang.api.routes import build_app
from pping_lang.metrics_catalog import M
from pping_lang.rules.defaults import DEFAULT_RULES
from pping_lang.rules.store import RuleStore
from pping_lang.sink.local import LocalSink
from pping_lang.types import MetricPoint


def _new_rule(**overrides) -> dict:
    base = {
        "id": "custom-rule",
        "name": "Custom test rule",
        "severity": "info",
        "category": "test",
        "condition": {
            "metric": M.GPU_UTIL_PCT,
            "op": "<",
            "threshold": 25.0,
            "window_seconds": 30,
            "aggregation": "avg",
        },
        "message": "GPU low {value:.0f}%",
        "suggestion": "do something",
        "enabled": True,
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "crud.duckdb"
    rules_file = tmp_path / "user_rules.json"
    sink = LocalSink(db_path=db, instance_id="crud", flush_interval_s=10.0)
    store = RuleStore(override_path=rules_file)
    app = build_app(
        db_path=str(db), instance_id="crud", engine_index=0,
        sink=sink, rule_store=store,
    )
    yield TestClient(app), store, db, sink
    sink.close()


def test_post_creates_new_rule(client):
    c, store, _, _ = client
    r = c.post("/api/rules", json=_new_rule())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "custom-rule"
    assert body["is_default"] is False
    assert store.get("custom-rule") is not None


def test_post_conflict_when_id_exists(client):
    c, _, _, _ = client
    c.post("/api/rules", json=_new_rule())
    r = c.post("/api/rules", json=_new_rule())
    assert r.status_code == 409


def test_post_rejects_unknown_metric(client):
    c, _, _, _ = client
    bad = _new_rule()
    bad["condition"]["metric"] = "totally.fake"
    r = c.post("/api/rules", json=bad)
    assert r.status_code == 422


def test_post_rejects_invalid_op(client):
    c, _, _, _ = client
    bad = _new_rule()
    bad["condition"]["op"] = "!~"  # invalid op
    r = c.post("/api/rules", json=bad)
    assert r.status_code == 422


def test_put_updates_existing(client):
    c, store, _, _ = client
    c.post("/api/rules", json=_new_rule())
    r = c.put("/api/rules/custom-rule",
              json=_new_rule(severity="critical", message="updated msg"))
    assert r.status_code == 200, r.text
    assert store.get("custom-rule").severity == "critical"


def test_put_id_mismatch_rejected(client):
    c, _, _, _ = client
    c.post("/api/rules", json=_new_rule())
    r = c.put("/api/rules/custom-rule", json=_new_rule(id="other"))
    assert r.status_code == 422


def test_put_can_override_default_rule(client):
    c, store, _, _ = client
    overridden = _new_rule(id="low-gpu-util", severity="critical")
    overridden["condition"]["threshold"] = 30.0
    r = c.put("/api/rules/low-gpu-util", json=overridden)
    assert r.status_code == 200
    fresh = store.get("low-gpu-util")
    assert fresh.severity == "critical"
    assert fresh.condition.threshold == 30.0


def test_delete_user_rule_removes_completely(client):
    c, store, _, _ = client
    c.post("/api/rules", json=_new_rule())
    r = c.delete("/api/rules/custom-rule")
    assert r.status_code == 204
    assert store.get("custom-rule") is None


def test_delete_default_soft_disables(client):
    c, store, _, _ = client
    r = c.delete("/api/rules/low-gpu-util")
    assert r.status_code == 204
    fresh = store.get("low-gpu-util")
    assert fresh is not None  # still exists
    assert fresh.enabled is False  # but disabled


def test_delete_unknown_404(client):
    c, _, _, _ = client
    r = c.delete("/api/rules/does-not-exist")
    assert r.status_code == 404


def test_get_rules_marks_defaults(client):
    c, _, _, _ = client
    body = c.get("/api/rules").json()
    by_id = {r["id"]: r for r in body["rules"]}
    assert by_id["low-gpu-util"]["is_default"] is True


def test_get_rules_includes_user_after_create(client):
    c, _, _, _ = client
    c.post("/api/rules", json=_new_rule(id="my-extra"))
    body = c.get("/api/rules").json()
    ids = {r["id"] for r in body["rules"]}
    assert "my-extra" in ids


def test_test_endpoint_no_data_returns_no_fire(client):
    c, _, _, _ = client
    # No data in DB → would_fire should be None
    r = c.post("/api/rules/low-gpu-util/test")
    assert r.status_code == 200
    body = r.json()
    assert body["data_available"] is False
    assert body["would_fire"] is None


def test_test_endpoint_with_data_evaluates(client):
    c, _, db, sink = client
    # Push 5 GPU util samples averaging 30 (below 50 threshold)
    base = time.monotonic_ns()
    for i in range(5):
        sink.push_metric(MetricPoint(
            ts_ns=base - i * 10**8,  # within 1s
            name=M.GPU_UTIL_PCT, value=30.0,
        ))
    sink._drain()  # force flush

    r = c.post("/api/rules/low-gpu-util/test")
    body = r.json()
    assert body["data_available"] is True
    assert body["would_fire"] is True  # 30 < 50
    assert body["value"] == 30.0
    assert body["threshold"] == 50.0


def test_test_endpoint_with_override_uses_override(client):
    c, _, db, sink = client
    base = time.monotonic_ns()
    for i in range(5):
        sink.push_metric(MetricPoint(
            ts_ns=base - i * 10**8, name=M.GPU_UTIL_PCT, value=30.0,
        ))
    sink._drain()

    # Override threshold to 25 — 30 < 25 is false
    override = _new_rule(id="low-gpu-util", category="throughput")
    override["condition"]["threshold"] = 25.0
    r = c.post("/api/rules/low-gpu-util/test",
               json={"override": override})
    body = r.json()
    assert body["would_fire"] is False  # 30 not < 25


def test_persistence_across_store_reload(client, tmp_path):
    c, _, _, _ = client
    c.post("/api/rules", json=_new_rule(id="persisted"))
    rules_file = tmp_path / "user_rules.json"
    assert rules_file.exists()
    # New store from same file should see the rule
    fresh = RuleStore(override_path=rules_file)
    assert fresh.get("persisted") is not None
