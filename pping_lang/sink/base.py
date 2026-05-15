"""Sink ABC — fire-and-forget contract per pre-impl-rfc §3.

Hot-path contract:
    push_metric() / push_diagnosis() MUST return in <5μs.
    Implementation: O(1) deque.append (atomic under GIL); no I/O, no
    serialization, no allocation beyond the deque slot. Overflow drops
    oldest (deque maxlen) and increments self._dropped_*.

All flushing happens on a daemon background thread that wakes every
flush_interval_s seconds and calls _flush(metrics, diags). Subclasses
implement _flush only.

Exceptions in _flush are caught, counted, and logged — never propagated
to vLLM (design §3.1: any bug must not bring down vLLM).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import deque
from threading import Event, Thread
from typing import Final

from pping_lang.types import Diagnosis, MetricPoint

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE: Final = 16384
DEFAULT_DIAG_QUEUE_SIZE: Final = 1024
DEFAULT_FLUSH_INTERVAL_S: Final = 5.0


class Sink(ABC):
    """Bounded in-memory buffers + bg flush thread. Subclasses implement _flush."""

    def __init__(
        self,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        diag_queue_size: int = DEFAULT_DIAG_QUEUE_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
    ) -> None:
        self._metric_q: deque[MetricPoint] = deque(maxlen=queue_size)
        self._diag_q: deque[Diagnosis] = deque(maxlen=diag_queue_size)
        self._flush_interval = flush_interval_s
        self._stop = Event()
        self._dropped_metrics = 0
        self._dropped_diags = 0
        self._flush_errors = 0
        self._closed = False
        self._thread = Thread(
            target=self._run,
            daemon=True,
            name=f"{type(self).__name__}-flush",
        )
        self._thread.start()

    # === Hot path: must stay <5μs ===

    def push_metric(self, p: MetricPoint) -> None:
        if len(self._metric_q) == self._metric_q.maxlen:
            self._dropped_metrics += 1
        self._metric_q.append(p)

    def push_diagnosis(self, d: Diagnosis) -> None:
        if len(self._diag_q) == self._diag_q.maxlen:
            self._dropped_diags += 1
        self._diag_q.append(d)

    # === Self-observability ===

    @property
    def dropped_metrics(self) -> int:
        return self._dropped_metrics

    @property
    def dropped_diags(self) -> int:
        return self._dropped_diags

    @property
    def flush_errors(self) -> int:
        return self._flush_errors

    @property
    def queue_depth(self) -> int:
        return len(self._metric_q) + len(self._diag_q)

    # === Lifecycle ===

    def close(self) -> None:
        """Stop bg thread, perform final flush. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=self._flush_interval * 2)
        self._drain()  # final flush in caller's thread

    # === Bg thread internals ===

    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self._drain()

    def _drain(self) -> None:
        # Snapshot then clear (each step atomic under GIL).
        metrics = list(self._metric_q)
        self._metric_q.clear()
        diags = list(self._diag_q)
        self._diag_q.clear()
        if not metrics and not diags:
            return
        try:
            self._flush(metrics, diags)
        except Exception:
            self._flush_errors += 1
            logger.exception(
                "%s._flush raised; %d metrics + %d diags dropped",
                type(self).__name__, len(metrics), len(diags),
            )

    @abstractmethod
    def _flush(
        self,
        metrics: list[MetricPoint],
        diags: list[Diagnosis],
    ) -> None:
        """Subclass impl: drain a batch to underlying storage / network."""
