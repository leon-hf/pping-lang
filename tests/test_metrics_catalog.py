"""metrics_catalog 测试 — 命名规范守卫。

这里的 test_naming_convention_* 是规范守卫：任何新加的 metric 名都必须遵守命名约定，
否则 CI 会爆。这是 RFC §5 决策的兜底。
"""
from __future__ import annotations

from pping_lang.metrics_catalog import ALLOWED_METRICS, M, OTEL_PREFIX, to_otel_name


# 允许的单位 suffix（带量纲指标必须以下列之一结尾）
UNIT_SUFFIXES = (
    "_ms", "_s", "_us",
    "_bytes",
    "_pct", "_ratio",
    "_w", "_c",
    "_mhz",
    "_total",
)

# 无量纲（计数、id、padded/unpadded counter 等）允许不带 suffix。
# 加新的"无单位 metric"时显式列入这里，强制审稿。
UNITLESS_OK_PREFIXES = (
    # vLLM scheduler counters/gauges
    "vllm.scheduler.running_reqs",
    "vllm.scheduler.waiting_reqs",
    "vllm.scheduler.skipped_waiting_reqs",
    "vllm.scheduler.current_wave",
    "vllm.scheduler.kv_evict_events",
    "vllm.lora.",
    # vLLM iter counters
    "vllm.iter.gen_tokens",
    "vllm.iter.prompt_tokens",
    "vllm.iter.prompt_cache_hit_tokens",
    "vllm.iter.prompt_kv_transfer_tokens",
    "vllm.iter.preempted_reqs",
    "vllm.iter.corrupted_reqs",
    # vLLM req token counts
    "vllm.req.prompt_tokens",
    "vllm.req.gen_tokens",
    "vllm.req.cached_tokens",
    # cudagraph counters
    "vllm.cudagraph.unpadded_tokens",
    "vllm.cudagraph.padded_tokens",
    "vllm.cudagraph.paddings",
    # perf raw counters (per_gpu 是 vLLM 语义不是单位)
    "vllm.perf.flops_per_gpu",
    "vllm.perf.read_bytes_per_gpu",
    "vllm.perf.write_bytes_per_gpu",
    # spec
    "vllm.spec.accepted_tokens",
    "vllm.spec.draft_tokens",
)


def test_all_constants_in_allowed():
    assert M.GPU_UTIL_PCT in ALLOWED_METRICS
    assert M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO in ALLOWED_METRICS
    assert M.VLLM_CUDAGRAPH_PADDING_RATIO in ALLOWED_METRICS
    assert M.VLLM_PERF_MFU_RATIO in ALLOWED_METRICS
    assert M.PPING_LANG_SINK_DROPPED_TOTAL in ALLOWED_METRICS


def test_allowed_count_reasonable():
    # Day 1 至少有这么多；后续只增不减
    assert len(ALLOWED_METRICS) >= 35


def test_unknown_metric_not_allowed():
    assert "totally.fake.metric" not in ALLOWED_METRICS


def test_otel_prefix_applied():
    assert to_otel_name(M.GPU_UTIL_PCT) == "pping_lang.gpu.utilization_pct"
    assert to_otel_name("vllm.req.ttft_ms") == "pping_lang.vllm.req.ttft_ms"
    assert OTEL_PREFIX == "pping_lang."


def test_naming_lowercase_only():
    """所有内部 metric 名应该是 lowercase + 点分 + snake_case。"""
    for name in ALLOWED_METRICS:
        for ch in name:
            assert ch.islower() or ch in "._0123456789", (
                f"{name!r} 含非法字符 {ch!r} —— 命名规范要求 lowercase snake_case 点分"
            )


def test_naming_unit_suffix_or_whitelisted():
    """带量纲的 metric 必须有合法 suffix；无量纲的需在白名单。"""
    for name in ALLOWED_METRICS:
        if name.startswith(UNITLESS_OK_PREFIXES):
            continue
        assert name.endswith(UNIT_SUFFIXES), (
            f"{name!r} 缺少单位 suffix。允许: {UNIT_SUFFIXES}，"
            f"或加入 UNITLESS_OK_PREFIXES 白名单（需审稿）。"
        )


def test_naming_starts_with_known_domain():
    """domain 必须是 gpu / vllm / pping_lang 之一。"""
    domains = ("gpu.", "vllm.", "pping_lang.")
    for name in ALLOWED_METRICS:
        assert name.startswith(domains), (
            f"{name!r} 的 domain 未知。允许: {domains}"
        )
