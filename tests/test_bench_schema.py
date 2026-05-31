"""StaticScenario.validate() — defensive contract守卫."""
from __future__ import annotations

import pytest

from pping_lang.bench.scenarios.schema import SLO, StaticScenario


def _ok(**overrides) -> StaticScenario:
    """Minimal valid scenario; overrides applied on top."""
    base = dict(
        name="t", endpoint="http://x:8000", model="m",
        concurrency=4, duration_s=30, num_requests=None,
    )
    base.update(overrides)
    return StaticScenario(**base)


def test_valid_scenario_passes():
    _ok().validate()  # should not raise


def test_slo_empty_by_default():
    s = SLO()
    assert s.is_empty()
    assert s.thresholds == ()
    assert s.as_spec() == ""


def test_required_string_fields():
    with pytest.raises(ValueError, match="name"):
        _ok(name="").validate()
    with pytest.raises(ValueError, match="endpoint"):
        _ok(endpoint="").validate()
    with pytest.raises(ValueError, match="model"):
        _ok(model="").validate()


@pytest.mark.parametrize("field,bad", [
    ("prompt_tokens", 0),
    ("prompt_tokens", -1),
    ("output_tokens", 0),
    ("concurrency", 0),
    ("timeout_s", 0),
])
def test_positive_numeric_fields(field, bad):
    with pytest.raises(ValueError, match=field):
        _ok(**{field: bad}).validate()


def test_warmup_negative_rejected():
    with pytest.raises(ValueError, match="warmup_s"):
        _ok(warmup_s=-1).validate()


def test_api_kind_strict():
    with pytest.raises(ValueError, match="api"):
        _ok(api="rest").validate()  # type: ignore[arg-type]


def test_duration_xor_num_requests_both_set_rejected():
    with pytest.raises(ValueError, match="duration_s / num_requests"):
        _ok(duration_s=30, num_requests=100).validate()


def test_duration_xor_num_requests_both_unset_rejected():
    with pytest.raises(ValueError, match="duration_s / num_requests"):
        _ok(duration_s=None, num_requests=None).validate()


def test_num_requests_path_accepted():
    _ok(duration_s=None, num_requests=50).validate()


def test_sweep_param_without_values_rejected():
    with pytest.raises(ValueError, match="sweep_values empty"):
        _ok(sweep_param="concurrency").validate()


def test_sweep_values_without_param_rejected():
    with pytest.raises(ValueError, match="sweep_param missing"):
        _ok(sweep_values=[1, 2, 4]).validate()


def test_sweep_param_must_be_allowed():
    with pytest.raises(ValueError, match="not sweepable"):
        _ok(sweep_param="model", sweep_values=["a"]).validate()


def test_sweep_valid():
    _ok(sweep_param="concurrency", sweep_values=[1, 2, 4, 8]).validate()


def test_all_errors_joined_in_one_message():
    with pytest.raises(ValueError) as exc:
        StaticScenario(
            name="", endpoint="", model="",
            prompt_tokens=-1, concurrency=0,
            duration_s=None, num_requests=None,
        ).validate()
    msg = str(exc.value)
    # Multiple distinct issues all surfaced at once
    assert "name" in msg
    assert "endpoint" in msg
    assert "model" in msg
    assert "prompt_tokens" in msg
    assert "concurrency" in msg
