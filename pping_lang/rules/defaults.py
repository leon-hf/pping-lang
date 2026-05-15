"""v0.1 内置默认规则（10 条）— 见 design §10.4。

变更：原设计 11 条，但 ttft-tpot-imbalance 需要复合条件（all/any 比较两个 metric），
v0.2 才支持。v0.1 暂跳过，留待 v0.2 复合条件能力上线后补。
"""
from __future__ import annotations

from pping_lang.metrics_catalog import M
from pping_lang.rules.schema import Condition, Rule

DEFAULT_RULES: list[Rule] = [
    # --- throughput ---
    Rule(
        id="low-gpu-util",
        name="GPU 利用率偏低",
        severity="warning",
        category="throughput",
        condition=Condition(
            metric=M.GPU_UTIL_PCT, op="<", threshold=50.0,
            window_seconds=30, aggregation="avg",
        ),
        message="GPU 平均利用率 {value:.0f}% 持续低于 {threshold:.0f}% 已 {window}s",
        suggestion="检查 batch 是否退化为 1，或开启连续 batching",
    ),
    Rule(
        id="queue-buildup",
        name="请求队列堆积",
        severity="warning",
        category="throughput",
        condition=Condition(
            metric=M.VLLM_SCHEDULER_WAITING_REQS, op=">", threshold=50.0,
            window_seconds=30, aggregation="avg",
        ),
        message="等待队列长度 {value:.0f} > {threshold:.0f} 已 {window}s",
        suggestion="增大 max_num_seqs，或检查上游限流",
    ),
    Rule(
        id="batch-degraded",
        name="batch 退化",
        severity="warning",
        category="throughput",
        condition=Condition(
            metric=M.VLLM_SCHEDULER_RUNNING_REQS, op="<=", threshold=1.0,
            window_seconds=30, aggregation="avg",
        ),
        message="并发请求数 {value:.1f} ≤ {threshold} 已 {window}s（无法发挥 batching 优势）",
        suggestion="增加客户端并发，或检查路由是否串行化",
    ),
    Rule(
        id="high-cudagraph-padding",
        name="CUDA graph padding 过高",
        severity="warning",
        category="throughput",
        condition=Condition(
            metric=M.VLLM_CUDAGRAPH_PADDING_RATIO, op=">", threshold=0.3,
            window_seconds=60, aggregation="avg",
        ),
        message="CUDA graph padding 比例 {value:.0%}，约 {value:.0%} 的 GPU 算力浪费在补 0",
        suggestion="调小 max_num_seqs 或开启更细粒度的 cudagraph capture（PIECEWISE 模式）",
    ),

    # --- latency ---
    Rule(
        id="kv-cache-pressure",
        name="KV cache 压力大",
        severity="warning",
        category="latency",
        condition=Condition(
            metric=M.VLLM_SCHEDULER_KV_CACHE_USAGE_RATIO, op=">", threshold=0.9,
            window_seconds=10, aggregation="avg",
        ),
        message="KV cache 用量 {value:.0%} > {threshold:.0%} 已 {window}s（即将触发抢占）",
        suggestion="降低 max_num_seqs / max_model_len，或升级显存更大的 GPU",
    ),
    Rule(
        id="high-ttft-p99",
        name="TTFT p99 偏高",
        severity="warning",
        category="latency",
        condition=Condition(
            metric=M.VLLM_REQ_TTFT_MS, op=">", threshold=2000.0,
            window_seconds=60, aggregation="p99",
        ),
        message="TTFT p99 = {value:.0f}ms > {threshold:.0f}ms（首 token 慢）",
        suggestion="检查 prefill 是否被 decode 阻塞；考虑开 chunked_prefill",
    ),

    # --- stability ---
    Rule(
        id="preemption-spike",
        name="抢占频发",
        severity="critical",
        category="stability",
        condition=Condition(
            metric=M.VLLM_ITER_PREEMPTED_REQS, op=">", threshold=1.0,
            window_seconds=10, aggregation="avg",
        ),
        message="抢占率 {value:.1f}/iter > {threshold:.0f}（KV cache 不足）",
        suggestion="降低 max_num_seqs 或 max_model_len",
    ),

    # --- efficiency ---
    Rule(
        id="low-prefix-cache-hit",
        name="prefix cache 命中率低",
        severity="info",
        category="efficiency",
        condition=Condition(
            metric=M.VLLM_SCHEDULER_PREFIX_CACHE_HIT_RATIO, op="<", threshold=0.10,
            window_seconds=60, aggregation="avg",
        ),
        message="prefix cache 命中率 {value:.0%} < {threshold:.0%}",
        suggestion="检查 prompt 模板是否有公共前缀；或开 enable_prefix_caching",
    ),
    Rule(
        id="low-mfu",
        name="MFU 偏低（计算资源浪费）",
        severity="warning",
        category="efficiency",
        condition=Condition(
            metric=M.VLLM_PERF_MFU_RATIO, op="<", threshold=0.20,
            window_seconds=60, aggregation="avg",
        ),
        message="MFU = {value:.0%} < {threshold:.0%}（理论峰值的小部分都没跑到）",
        suggestion="检查 padding ratio / batch 大小 / dtype（应为 bf16/fp16）",
    ),

    # --- bottleneck ---
    Rule(
        id="memory-bw-saturated",
        name="显存带宽接近饱和",
        severity="warning",
        category="bottleneck",
        condition=Condition(
            metric=M.VLLM_PERF_MEM_BW_UTIL_RATIO, op=">", threshold=0.9,
            window_seconds=30, aggregation="avg",
        ),
        message="显存带宽利用率 {value:.0%} > {threshold:.0%}（memory-bound）",
        suggestion="增大 batch 走 compute-bound 区，或换更大带宽 GPU（如 H100/H200）",
    ),
]
