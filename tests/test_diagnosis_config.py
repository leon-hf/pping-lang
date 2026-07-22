"""DiagnosisConfig 单测 —— SLA + 阈值集中可配,各规则引用它。"""
from __future__ import annotations

import json

import pytest

from pping_lang.rules.diagnosis_config import (
    DiagnosisConfig,
    default_config,
    from_dict,
    load_config,
    to_dict,
    validate_config,
)


def test_default_custom():
    c = default_config("custom")
    assert c.workload_form == "custom"
    assert c.sla_ttft_p99_ms == 2000.0
    assert c.sla_tpot_p99_ms == 50.0
    assert c.sla_e2e_p99_ms == 5000.0
    # 阈值用 dataclass 默认(MBU 用 NVML HBM 繁忙%,有界)
    assert c.mbu_high_pct == 85.0 and c.mbu_low_pct == 50.0
    assert c.mfu_low_ratio == 0.20 and c.min_running_reqs == 0.5
    assert c.stall_memory_throttle_pct == 25.0 and c.stall_memory_dep_pct == 25.0 and c.stall_math_pipe_pct == 25.0


@pytest.mark.parametrize("form,ttft,tpot,e2e", [
    ("chat", 1000.0, 50.0, 3000.0),
    ("rag", 3000.0, 50.0, 8000.0),
    ("agent", 1000.0, 50.0, 15000.0),
    ("reasoning", 1000.0, 30.0, 90000.0),
    ("code", 100.0, 20.0, 2000.0),
])
def test_workload_brings_sla_defaults(form, ttft, tpot, e2e):
    c = default_config(form)
    assert c.workload_form == form
    assert c.sla_ttft_p99_ms == ttft
    assert c.sla_tpot_p99_ms == tpot
    assert c.sla_e2e_p99_ms == e2e


def test_unknown_form_falls_back_to_custom():
    c = default_config("bogus")
    assert c.workload_form == "custom"


def test_from_dict_form_plus_override():
    # 选 rag 带出 SLA 默认,再单独覆盖 ttft + 一个阈值
    c = from_dict({"workload_form": "rag", "sla_ttft_p99_ms": 2500, "waiting_reqs": 80})
    assert c.workload_form == "rag"
    assert c.sla_ttft_p99_ms == 2500      # 覆盖生效
    assert c.sla_tpot_p99_ms == 50.0      # rag 默认保留
    assert c.waiting_reqs == 80
    assert c.kv_pressure_ratio == 0.90    # 未覆盖 → 默认


def test_from_dict_ignores_unknown_keys():
    c = from_dict({"workload_form": "chat", "bogus_key": 123})
    assert c.workload_form == "chat"


def test_to_dict_roundtrip():
    c = default_config("code")
    assert from_dict(to_dict(c)) == c


def test_validate_rejects_bad_form():
    with pytest.raises(ValueError):
        validate_config(DiagnosisConfig(workload_form="nope"))  # type: ignore[arg-type]


def test_validate_rejects_nonpositive_sla():
    with pytest.raises(ValueError):
        validate_config(DiagnosisConfig(sla_ttft_p99_ms=0))


def test_validate_rejects_mbu_low_ge_high():
    # mbu_low_pct 须 < mbu_high_pct;90 ≥ 85 → 拒
    with pytest.raises(ValueError):
        validate_config(DiagnosisConfig(mbu_low_pct=90, mbu_high_pct=85))


def test_validate_rejects_ratio_out_of_range():
    with pytest.raises(ValueError):
        validate_config(DiagnosisConfig(mfu_low_ratio=1.5))


def test_load_config_no_env_returns_custom(monkeypatch):
    monkeypatch.delenv("PPING_LANG_DIAGNOSIS_CONFIG", raising=False)
    assert load_config().workload_form == "custom"


def test_load_config_from_file(monkeypatch, tmp_path):
    p = tmp_path / "diag.json"
    p.write_text(json.dumps({"workload_form": "reasoning", "tail_ratio": 8}), encoding="utf-8")
    monkeypatch.setenv("PPING_LANG_DIAGNOSIS_CONFIG", str(p))
    c = load_config()
    assert c.workload_form == "reasoning"
    assert c.sla_tpot_p99_ms == 30.0   # reasoning 默认
    assert c.tail_ratio == 8


def test_load_config_bad_file_falls_back(monkeypatch, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setenv("PPING_LANG_DIAGNOSIS_CONFIG", str(p))
    assert load_config().workload_form == "custom"   # 不崩,回退默认


def test_load_config_invalid_values_falls_back(monkeypatch, tmp_path):
    p = tmp_path / "inv.json"
    p.write_text(json.dumps({"sla_ttft_p99_ms": -1}), encoding="utf-8")
    monkeypatch.setenv("PPING_LANG_DIAGNOSIS_CONFIG", str(p))
    assert load_config().workload_form == "custom"   # 校验失败 → 回退
