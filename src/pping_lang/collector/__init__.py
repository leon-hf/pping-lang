"""Collector 子包 — 数据采集源到 MetricPoint 的转换。

子模块：
- nvml: NvmlSampler — GPU 物理层采样（utilization / memory / power / clock）
- vllm_stats: VllmStatsCollector — vLLM SchedulerStats / IterationStats 提取
- cupti: CuptiKernelCollector — GPU kernel 级时间分解（阶段 1a，CUPTI Activity）
"""
from pping_lang.collector.cupti import CuptiKernelCollector, KernelClassifier
from pping_lang.collector.nvml import NvmlSampler, detect_first_gpu_name
from pping_lang.collector.vllm_stats import VllmStatsCollector

__all__ = [
    "CuptiKernelCollector",
    "KernelClassifier",
    "NvmlSampler",
    "VllmStatsCollector",
    "detect_first_gpu_name",
]
