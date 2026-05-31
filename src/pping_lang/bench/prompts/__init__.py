"""Bench prompts: builtin datasets + JSONL loader for user files.

Public API:
    load_prompts(source, target_tokens) -> list[str]
    available_builtins() -> tuple[str, ...]
    BUILTIN_DATASETS

See `loader.py` for the source-string syntax.
"""
from pping_lang.bench.prompts.loader import (
    BUILTIN_DATASETS,
    available_builtins,
    load_prompts,
)

__all__ = ["load_prompts", "available_builtins", "BUILTIN_DATASETS"]
