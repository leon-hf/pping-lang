"""验证 entry point 已通过 pyproject.toml 正确注册。

这是 Day 1 的核心验收：vLLM 启动时通过 importlib.metadata 能找到我们的插件。
"""
from __future__ import annotations

import importlib.metadata

ENTRY_POINT_GROUP = "vllm.stat_logger_plugins"


def test_entry_point_registered():
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    names = {ep.name for ep in eps}
    assert "pping_lang" in names, (
        f"pping_lang entry point 未注册到 {ENTRY_POINT_GROUP}。"
        f"已注册的： {names or '(空)'}。"
        f"是否执行过 `pip install -e .`？"
    )


def test_entry_point_resolves_to_plugin_class():
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    ep = next(ep for ep in eps if ep.name == "pping_lang")
    cls = ep.load()
    assert cls.__name__ == "PpingLangStatLogger"
    assert cls.__module__ == "pping_lang.plugin"


def test_plugin_can_be_instantiated_without_vllm_config():
    from pping_lang.plugin import PpingLangStatLogger

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=0)
    assert plugin.engine_index == 0
    assert plugin.vllm_config is None


def test_plugin_methods_are_callable():
    """所有 abstract 方法都能调用，不抛。"""
    from pping_lang.plugin import PpingLangStatLogger

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=2)
    plugin.log_engine_initialized()
    plugin.log()
    plugin.record(None, None)
    plugin.record(None, None, mm_cache_stats=None, engine_idx=2)
    plugin.record_sleep_state(is_awake=1, level=0)


def test_plugin_engine_index_preserved():
    from pping_lang.plugin import PpingLangStatLogger

    plugin = PpingLangStatLogger(vllm_config=None, engine_index=7)
    assert plugin.engine_index == 7
