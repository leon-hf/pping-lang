"""Sink 抽象 — fire-and-forget 数据出口。

子模块：
- base: Sink ABC（ring buffer + bg flush thread）
- local: LocalSink（Embedded 模式：DuckDB）
- (v0.2) remote: RemoteSink（Sidecar/Centralized：OTLP/gRPC）
- (v0.3) otel: OTelSink（Stateless 模式）
"""
from pping_lang.sink.base import (
    DEFAULT_DIAG_QUEUE_SIZE,
    DEFAULT_FLUSH_INTERVAL_S,
    DEFAULT_QUEUE_SIZE,
    Sink,
)
from pping_lang.sink.local import LocalSink
from pping_lang.sink.tee import TeeSink

__all__ = [
    "Sink",
    "LocalSink",
    "TeeSink",
    "DEFAULT_QUEUE_SIZE",
    "DEFAULT_DIAG_QUEUE_SIZE",
    "DEFAULT_FLUSH_INTERVAL_S",
]
