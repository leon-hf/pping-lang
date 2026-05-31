"""bench.prompts.loader — dispatch + builtin datasets + file: + errors."""
from __future__ import annotations

import json

import pytest

from pping_lang.bench.prompts import (
    BUILTIN_DATASETS,
    available_builtins,
    load_prompts,
)

# ===== synthetic (fallback / default) =====


def test_synthetic_returns_filler_of_requested_length():
    out = load_prompts("synthetic", target_tokens=100)
    assert len(out) == 1
    assert len(out[0]) > 50  # something nontrivial


def test_empty_source_treated_as_synthetic():
    assert load_prompts("", target_tokens=50) == load_prompts("synthetic", target_tokens=50)


# ===== builtin datasets =====


@pytest.mark.parametrize("name", BUILTIN_DATASETS)
def test_all_builtin_datasets_loadable_and_nonempty(name):
    prompts = load_prompts(f"builtin:{name}")
    assert len(prompts) > 0
    # Every prompt should be a non-empty string
    for p in prompts:
        assert isinstance(p, str)
        assert p.strip()


def test_available_builtins_contains_expected():
    names = available_builtins()
    assert "mixed-short" in names
    assert "mixed-long" in names
    assert "code" in names


def test_mixed_long_actually_long():
    """Sanity: mixed-long entries should average meaningfully longer than mixed-short.

    Catches accidental swapped files / truncation during packaging.
    """
    short = load_prompts("builtin:mixed-short")
    long_ = load_prompts("builtin:mixed-long")
    avg_short = sum(len(p) for p in short) / len(short)
    avg_long = sum(len(p) for p in long_) / len(long_)
    assert avg_long > avg_short * 5, (
        f"mixed-long avg {avg_long:.0f} chars / mixed-short avg {avg_short:.0f} chars"
    )


def test_unknown_builtin_raises():
    with pytest.raises(ValueError, match="not found"):
        load_prompts("builtin:nonexistent-dataset-xyz")


def test_empty_builtin_name_raises():
    with pytest.raises(ValueError, match="empty"):
        load_prompts("builtin:")


# ===== file: source =====


def test_file_loads_user_jsonl(tmp_path):
    p = tmp_path / "my.jsonl"
    p.write_text(
        '{"prompt": "first prompt"}\n'
        '{"prompt": "second prompt"}\n'
        '\n'  # empty line tolerated
        '{"prompt": "third"}\n',
        encoding="utf-8",
    )
    out = load_prompts(f"file:{p}")
    assert out == ["first prompt", "second prompt", "third"]


def test_file_missing_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_prompts(f"file:{tmp_path / 'nope.jsonl'}")


def test_file_empty_raises(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("\n\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="zero valid prompts"):
        load_prompts(f"file:{p}")


def test_file_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"prompt": "ok"}\n{not json}\n', encoding="utf-8")
    # Error includes line-number suffix like "file:...:2: not valid JSON"
    with pytest.raises(ValueError, match=":2: not valid JSON"):
        load_prompts(f"file:{p}")


def test_file_missing_prompt_key_raises(tmp_path):
    p = tmp_path / "wrong-shape.jsonl"
    p.write_text(json.dumps({"text": "wrong key"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'prompt' key"):
        load_prompts(f"file:{p}")


def test_file_empty_prompt_string_raises(tmp_path):
    p = tmp_path / "empty-prompt.jsonl"
    p.write_text(json.dumps({"prompt": ""}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty string"):
        load_prompts(f"file:{p}")


# ===== dispatcher edge cases =====


def test_unknown_source_kind_raises():
    with pytest.raises(ValueError, match="unknown prompt_source"):
        load_prompts("ftp://server/prompts.txt")


def test_runner_imports_loader_at_runtime():
    """Guard: bench.runner must be able to call load_prompts."""
    from pping_lang.bench.runner import load_prompts as runner_loader
    assert runner_loader is load_prompts
