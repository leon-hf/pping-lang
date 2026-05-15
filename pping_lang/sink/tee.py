"""TeeSink — fan-out 到多个下游 Sink。

每个 child 有自己的 buffer + bg 线程；TeeSink 只是 pusher。
duck-typed（不继承 Sink ABC，避免无谓的 abstract _flush 实现）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pping_lang.sink.base import Sink
    from pping_lang.types import Diagnosis, MetricPoint


class TeeSink:
    """Multi-sink wrapper. Each child handles its own buffering + flushing."""

    def __init__(self, *children: Sink) -> None:
        self._children: list[Sink] = list(children)

    def push_metric(self, p: MetricPoint) -> None:
        for s in self._children:
            s.push_metric(p)

    def push_diagnosis(self, d: Diagnosis) -> None:
        for s in self._children:
            s.push_diagnosis(d)

    def close(self) -> None:
        for s in self._children:
            s.close()

    @property
    def dropped_metrics(self) -> int:
        return sum(s.dropped_metrics for s in self._children)

    @property
    def dropped_diags(self) -> int:
        return sum(s.dropped_diags for s in self._children)

    @property
    def flush_errors(self) -> int:
        return sum(s.flush_errors for s in self._children)

    @property
    def queue_depth(self) -> int:
        return sum(s.queue_depth for s in self._children)

    @property
    def children(self) -> list[Sink]:
        return list(self._children)
