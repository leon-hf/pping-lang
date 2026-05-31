"""单一 metric 名常量源 — see pre-impl-rfc §5.

任何代码引用 metric 名都从 M 类来。新增 metric 必须先加常量再用。
规则引擎加载时校验 condition.metric ∈ ALLOWED_METRICS，未命中拒收。
OTel 导出时机械加 `pping_lang.` 前缀，不做单位转换。
"""
from __future__ import annotations


class M:
    """Metric 名常量。命名规范：<domain>.<subsystem>?.<name>_<unit>?"""

    # GPU 物理层 (NVML, 100ms)
    GPU_UTIL_PCT = "gpu.utilization_pct"
    GPU_MEM_UTIL_PCT = "gpu.mem_util_pct"
    GPU_MEM_USED_BYTES = "gpu.mem_used_bytes"
    GPU_POWER_W = "gpu.power_w"
    GPU_TEMP_C = "gpu.temp_c"
    GPU_SM_CLOCK_MHZ = "gpu.sm_clock_mhz"
    GPU_MEM_CLOCK_MHZ = "gpu.mem_clock_mhz"

    # vLLM SchedulerStats (每 step)
    VLLM_SCHEDULER_RUNNING_REQS = "vllm.scheduler.running_reqs"
    VLLM_SCHEDULER_WAITING_REQS = "vllm.scheduler.waiting_reqs"
    VLLM_SCHEDULER_SKIPPED_WAITING_REQS = "vllm.scheduler.skipped_waiting_reqs"
    VLLM_SCHEDULER_CURRENT_WAVE = "vllm.scheduler.current_wave"
    VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO = "vllm.scheduler.kv_cache_usage_ratio"
    VLLM_SCHEDULER_PREFIX_CACHE_HIT_RATIO = "vllm.scheduler.prefix_cache_hit_ratio"
    VLLM_SCHEDULER_KV_EVICT_EVENTS = "vllm.scheduler.kv_evict_events"
    VLLM_LORA_WAITING_ADAPTERS = "vllm.lora.waiting_adapters"
    VLLM_LORA_RUNNING_ADAPTERS = "vllm.lora.running_adapters"

    # vLLM IterationStats (每 step)
    VLLM_ITER_GEN_TOKENS = "vllm.iter.gen_tokens"
    VLLM_ITER_PROMPT_TOKENS = "vllm.iter.prompt_tokens"
    VLLM_ITER_PROMPT_CACHE_HIT_TOKENS = "vllm.iter.prompt_cache_hit_tokens"
    VLLM_ITER_PROMPT_KV_TRANSFER_TOKENS = "vllm.iter.prompt_kv_transfer_tokens"
    VLLM_ITER_PREEMPTED_REQS = "vllm.iter.preempted_reqs"
    VLLM_ITER_CORRUPTED_REQS = "vllm.iter.corrupted_reqs"

    # vLLM 请求级 (FinishedRequestStats)
    VLLM_REQ_TTFT_MS = "vllm.req.ttft_ms"
    VLLM_REQ_ITL_MS = "vllm.req.itl_ms"
    VLLM_REQ_E2E_LATENCY_MS = "vllm.req.e2e_latency_ms"
    VLLM_REQ_QUEUED_MS = "vllm.req.queued_ms"
    VLLM_REQ_PREFILL_MS = "vllm.req.prefill_ms"
    VLLM_REQ_INFERENCE_MS = "vllm.req.inference_ms"
    VLLM_REQ_DECODE_MS = "vllm.req.decode_ms"
    VLLM_REQ_TPOT_MS = "vllm.req.tpot_ms"
    VLLM_REQ_PROMPT_TOKENS = "vllm.req.prompt_tokens"
    VLLM_REQ_GEN_TOKENS = "vllm.req.gen_tokens"
    VLLM_REQ_CACHED_TOKENS = "vllm.req.cached_tokens"

    # vLLM CUDA graph (cudagraph_stats)
    VLLM_CUDAGRAPH_UNPADDED_TOKENS = "vllm.cudagraph.unpadded_tokens"
    VLLM_CUDAGRAPH_PADDED_TOKENS = "vllm.cudagraph.padded_tokens"
    VLLM_CUDAGRAPH_PADDINGS = "vllm.cudagraph.paddings"
    VLLM_CUDAGRAPH_PADDING_RATIO = "vllm.cudagraph.padding_ratio"  # derived

    # vLLM perf (perf_stats) — 注意 _per_gpu 是 vLLM 语义，不是单位
    VLLM_PERF_FLOPS_PER_GPU = "vllm.perf.flops_per_gpu"
    VLLM_PERF_READ_BYTES_PER_GPU = "vllm.perf.read_bytes_per_gpu"
    VLLM_PERF_WRITE_BYTES_PER_GPU = "vllm.perf.write_bytes_per_gpu"
    VLLM_PERF_MFU_RATIO = "vllm.perf.mfu_ratio"  # derived
    VLLM_PERF_MEM_BW_UTIL_RATIO = "vllm.perf.mem_bw_util_ratio"  # derived

    # vLLM spec decoding
    VLLM_SPEC_ACCEPTED_TOKENS = "vllm.spec.accepted_tokens"
    VLLM_SPEC_DRAFT_TOKENS = "vllm.spec.draft_tokens"

    # pping-lang 自我观测
    PPING_LANG_SINK_DROPPED_TOTAL = "pping_lang.sink.dropped_total"
    PPING_LANG_RECORD_OVERHEAD_US = "pping_lang.overhead.record_us"
    PPING_LANG_RULE_EVAL_MS = "pping_lang.overhead.rule_eval_ms"


def _collect_metric_names() -> frozenset[str]:
    return frozenset(
        v for k, v in vars(M).items()
        if not k.startswith("_") and isinstance(v, str)
    )


ALLOWED_METRICS: frozenset[str] = _collect_metric_names()


OTEL_PREFIX = "pping_lang."


def to_otel_name(internal_name: str) -> str:
    """内部命名 → OTel 导出命名。机械加前缀，不做单位转换。"""
    return OTEL_PREFIX + internal_name
