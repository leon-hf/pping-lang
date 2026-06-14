"""提取 vLLM SchedulerStats / IterationStats / FinishedRequestStats → MetricPoint。

字段映射表见 pre-impl-rfc §4.3。

派生指标：
- vllm.cudagraph.padding_ratio  = (padded - unpadded) / padded
- vllm.perf.mfu_ratio           = flops_per_gpu / (peak_flops * dt)
- vllm.perf.mem_bw_util_ratio   = (read+write) / (peak_bw * dt)

字段访问全部 defensive (`getattr(obj, attr, default)`)，兼容 vLLM 0.20.x 字段缺失。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pping_lang.clock import wall_ns
from pping_lang.hardware import GPUPeak
from pping_lang.metrics_catalog import M
from pping_lang.sink.base import Sink
from pping_lang.types import MetricPoint

if TYPE_CHECKING:
    from vllm.v1.metrics.stats import IterationStats, SchedulerStats


def _g(obj: Any, attr: str, default: Any = None) -> Any:
    return getattr(obj, attr, default)


class VllmStatsCollector:
    """vLLM stats → MetricPoint stream into a Sink.

    Stateless except for `_last_step_ts_ns` used to compute MFU dt.
    Single-thread expected (vLLM calls record() serially per engine).
    """

    def __init__(
        self,
        sink: Sink,
        engine_index: int = 0,
        gpu_peak: GPUPeak | None = None,
    ) -> None:
        self._sink = sink
        self._engine_index = engine_index
        self._gpu_peak = gpu_peak
        self._last_step_ts_ns: int | None = None

    def collect(
        self,
        scheduler_stats: SchedulerStats | None,
        iteration_stats: IterationStats | None,
    ) -> None:
        ts = wall_ns()
        if scheduler_stats is not None:
            self._collect_scheduler(scheduler_stats, ts)
        if iteration_stats is not None:
            self._collect_iteration(iteration_stats, ts)

    # === internals ===

    def _push(self, ts: int, name: str, value: float) -> None:
        self._sink.push_metric(MetricPoint(
            ts_ns=ts, name=name, value=value, engine_idx=self._engine_index,
        ))

    def _collect_scheduler(self, s: Any, ts: int) -> None:
        # Direct field mappings
        self._push(ts, M.VLLM_SCHEDULER_RUNNING_REQS, float(_g(s, "num_running_reqs", 0)))
        self._push(ts, M.VLLM_SCHEDULER_WAITING_REQS, float(_g(s, "num_waiting_reqs", 0)))
        self._push(ts, M.VLLM_SCHEDULER_SKIPPED_WAITING_REQS,
                   float(_g(s, "num_skipped_waiting_reqs", 0)))
        self._push(ts, M.VLLM_SCHEDULER_CURRENT_WAVE, float(_g(s, "current_wave", 0)))
        self._push(ts, M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO,
                   float(_g(s, "kv_cache_usage", 0.0)))

        # Prefix cache hit ratio (derived: hits / queries)
        pc = _g(s, "prefix_cache_stats")
        if pc is not None:
            queries = float(_g(pc, "queries", 0))
            hits = float(_g(pc, "hits", 0))
            if queries > 0:
                self._push(ts, M.VLLM_SCHEDULER_PREFIX_CACHE_HIT_RATIO, hits / queries)

        # KV evict events count
        evict = _g(s, "kv_cache_eviction_events")
        if evict is not None:
            self._push(ts, M.VLLM_SCHEDULER_KV_EVICT_EVENTS, float(len(evict)))

        # LoRA adapters
        wlr = _g(s, "waiting_lora_adapters")
        if wlr is not None:
            self._push(ts, M.VLLM_LORA_WAITING_ADAPTERS, float(len(wlr)))
        rlr = _g(s, "running_lora_adapters")
        if rlr is not None:
            self._push(ts, M.VLLM_LORA_RUNNING_ADAPTERS, float(len(rlr)))

        # Spec decoding
        spec = _g(s, "spec_decoding_stats")
        if spec is not None:
            self._push(ts, M.VLLM_SPEC_ACCEPTED_TOKENS,
                       float(_g(spec, "num_accepted_tokens", 0)))
            self._push(ts, M.VLLM_SPEC_DRAFT_TOKENS,
                       float(_g(spec, "num_draft_tokens", 0)))

        # CUDA graph (requires --enable-cudagraph-metrics; may be None)
        cg = _g(s, "cudagraph_stats")
        if cg is not None:
            unpadded = float(_g(cg, "num_unpadded_tokens", 0))
            padded = float(_g(cg, "num_padded_tokens", 0))
            paddings = float(_g(cg, "num_paddings", 0))
            self._push(ts, M.VLLM_CUDAGRAPH_UNPADDED_TOKENS, unpadded)
            self._push(ts, M.VLLM_CUDAGRAPH_PADDED_TOKENS, padded)
            self._push(ts, M.VLLM_CUDAGRAPH_PADDINGS, paddings)
            if padded > 0:
                # Derived: padding_ratio — marquee duty-cycle diagnostic
                self._push(ts, M.VLLM_CUDAGRAPH_PADDING_RATIO,
                           (padded - unpadded) / padded)

        # Perf stats — flops + bytes for MFU / memory bandwidth utilization
        perf = _g(s, "perf_stats")
        if perf is not None:
            flops = float(_g(perf, "num_flops_per_gpu", 0))
            read_b = float(_g(perf, "num_read_bytes_per_gpu", 0))
            write_b = float(_g(perf, "num_write_bytes_per_gpu", 0))
            self._push(ts, M.VLLM_PERF_FLOPS_PER_GPU, flops)
            self._push(ts, M.VLLM_PERF_READ_BYTES_PER_GPU, read_b)
            self._push(ts, M.VLLM_PERF_WRITE_BYTES_PER_GPU, write_b)

            # Derived: MFU and memory bandwidth utilization
            if self._last_step_ts_ns is not None and self._gpu_peak is not None:
                dt_s = (ts - self._last_step_ts_ns) / 1e9
                if dt_s > 0:
                    peak_flops = self._gpu_peak.bf16_tflops * 1e12
                    peak_bw_bs = self._gpu_peak.mem_bw_gbs * 1e9
                    if peak_flops > 0 and flops > 0:
                        self._push(ts, M.VLLM_PERF_MFU_RATIO,
                                   flops / (peak_flops * dt_s))
                    if peak_bw_bs > 0 and (read_b + write_b) > 0:
                        self._push(ts, M.VLLM_PERF_MEM_BW_UTIL_RATIO,
                                   (read_b + write_b) / (peak_bw_bs * dt_s))

        self._last_step_ts_ns = ts

    def _collect_iteration(self, it: Any, ts: int) -> None:
        self._push(ts, M.VLLM_ITER_GEN_TOKENS,
                   float(_g(it, "num_generation_tokens", 0)))

        pts = _g(it, "prompt_token_stats")
        if pts is not None:
            self._push(ts, M.VLLM_ITER_PROMPT_TOKENS, float(_g(pts, "total", 0)))
            self._push(ts, M.VLLM_ITER_PROMPT_CACHE_HIT_TOKENS,
                       float(_g(pts, "local_cache_hit", 0)))
            self._push(ts, M.VLLM_ITER_PROMPT_KV_TRANSFER_TOKENS,
                       float(_g(pts, "external_kv_transfer", 0)))

        self._push(ts, M.VLLM_ITER_PREEMPTED_REQS,
                   float(_g(it, "num_preempted_reqs", 0)))
        self._push(ts, M.VLLM_ITER_CORRUPTED_REQS,
                   float(_g(it, "num_corrupted_reqs", 0)))

        # Per-iter TTFT / ITL lists — vLLM unit is seconds, we store ms
        for t in _g(it, "time_to_first_tokens_iter", []) or []:
            self._push(ts, M.VLLM_REQ_TTFT_MS, float(t) * 1000.0)
        for t in _g(it, "inter_token_latencies_iter", []) or []:
            self._push(ts, M.VLLM_REQ_ITL_MS, float(t) * 1000.0)

        for fr in _g(it, "finished_requests", []) or []:
            self._collect_finished_req(fr, ts)

    def _collect_finished_req(self, fr: Any, ts: int) -> None:
        # Time fields: vLLM stores seconds, we store ms
        for src, dest in [
            ("e2e_latency", M.VLLM_REQ_E2E_LATENCY_MS),
            ("queued_time", M.VLLM_REQ_QUEUED_MS),
            ("prefill_time", M.VLLM_REQ_PREFILL_MS),
            ("inference_time", M.VLLM_REQ_INFERENCE_MS),
            ("decode_time", M.VLLM_REQ_DECODE_MS),
            ("mean_time_per_output_token", M.VLLM_REQ_TPOT_MS),
        ]:
            v = _g(fr, src)
            if v is not None:
                self._push(ts, dest, float(v) * 1000.0)

        # Token counts (already integers, no scaling)
        for src, dest in [
            ("num_prompt_tokens", M.VLLM_REQ_PROMPT_TOKENS),
            ("num_generation_tokens", M.VLLM_REQ_GEN_TOKENS),
            ("num_cached_tokens", M.VLLM_REQ_CACHED_TOKENS),
        ]:
            self._push(ts, dest, float(_g(fr, src, 0)))
