"""OTel 输出 — Day 11 实现。

子模块：
- sink: OTelSink — Sink 子类，把 metric / diagnosis 推 OTLP

设计原则（design §13）：
- vLLM 已有请求级 OTel trace，pping-lang 不重复
- pping-lang 输出：(1) GPU 物理层 metrics（NVML 衍生）
                   (2) vLLM stats 衍生 metrics（含派生 padding_ratio / mfu_ratio）
                   (3) 诊断 events 作为 OTel log signal
- GenAI semconv 翻译层 v0.2 加（vLLM SpanAttributes → semconv v1.37+）
"""
from pping_lang.otel.sink import OTelSink

__all__ = ["OTelSink"]
