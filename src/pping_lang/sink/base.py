"""Sink ABC — fire-and-forget contract per pre-impl-rfc §3.

Hot-path contract:
    push_metric() / push_diagnosis() MUST return in <5μs.
    Implementation: O(1) deque.append (atomic under GIL); no I/O, no
    serialization, no allocation beyond the deque slot. Overflow drops
    oldest (deque maxlen) and increments self._dropped_*.

Backpressure (Day 17 / real-vllm WSL实测):
    NVML 100ms × 7 fields + per-iter vllm stats can push >100 metrics/s.
    Default queue 16384 + flush 5s overflows under sustained load.
    Solution: backpressure signal — push side sets a wake Event when queue
    reaches `flush_wakeup_threshold`; flush thread waits on either timeout
    OR signal. Drains more responsively without raising hot-path cost.

Dual-path read model (Day 18 / "实时" tab 延迟整改):
    Persistence (DuckDB) and live dashboard reads are now decoupled. The
    persistence path stays: push → deque → bg flush → _flush(...) → storage,
    used for archival/historical queries (>60s windows, diagnoses replay,
    HTML reports).

    Live path: push_metric ALSO updates two in-memory structures —
      _latest: name → (value, ts_ns)    last seen, dict overwrite, O(1)
      _recent: name → ring buffer       bounded ts-stamped points, O(1) append
    API handlers serving the realtime KPI marquee / 60s windows read these
    directly via .latest() / .recent(), bypassing DuckDB entirely. Cost:
    ~6MB RAM for 30 metrics × 2000 points; zero I/O on the read path;
    dashboard latency = HTTP poll interval (1–2s), not flush+checkpoint+SQL.

All flushing happens on a daemon background thread that wakes every
flush_interval_s seconds OR on backpressure signal, and calls
_flush(metrics, diags). Subclasses implement _flush only.

Exceptions in _flush are caught, counted, and logged — never propagated
to vLLM (design §3.1: any bug must not bring down vLLM).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from threading import Event, Thread
from typing import Final

from pping_lang.clock import wall_ns
from pping_lang.types import Diagnosis, MetricPoint

logger = logging.getLogger(__name__)

# Default queue sized to absorb ~10 min of NVML 100ms × 7 fields + bursts.
# Memory cost: 65536 × ~64 bytes/point ≈ 4 MB; trivial.
DEFAULT_QUEUE_SIZE: Final = 65536
DEFAULT_DIAG_QUEUE_SIZE: Final = 1024
DEFAULT_FLUSH_INTERVAL_S: Final = 5.0
# When queue is at this fraction of capacity, push side wakes the flush thread
# early (don't wait for the interval). Lower → more responsive but more wakeups.
DEFAULT_BACKPRESSURE_THRESHOLD: Final = 0.5
# Per-metric ring buffer for the live read path. 2000 points covers:
#   - NVML at 10 Hz → 200 s of history
#   - vLLM scheduler at 100 Hz → 20 s of history
# Both wider than the 60 s KPI window, so KPI quantiles see enough data
# even under heavy load. Memory: ~30 metrics × 2000 × 24 B ≈ 1.5 MB.
DEFAULT_LIVE_RING_SIZE: Final = 2000


class Sink(ABC):
    """Bounded in-memory buffers + bg flush thread. Subclasses implement _flush."""

    def __init__(
        self,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        diag_queue_size: int = DEFAULT_DIAG_QUEUE_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        backpressure_threshold: float = DEFAULT_BACKPRESSURE_THRESHOLD,
        live_ring_size: int = DEFAULT_LIVE_RING_SIZE,
    ) -> None:
        self._metric_q: deque[MetricPoint] = deque(maxlen=queue_size)
        self._diag_q: deque[Diagnosis] = deque(maxlen=diag_queue_size)
        self._flush_interval = flush_interval_s
        # Backpressure: wake the flush thread early when queue passes this mark
        self._flush_wakeup_threshold = max(1, int(queue_size * backpressure_threshold))
        # Live read path (see module docstring) — read by API handlers, NEVER
        # touched on bg flush. Single writer (push_metric) + many readers; all
        # ops are GIL-atomic so no lock needed.
        self._live_ring_size = live_ring_size
        self._latest: dict[str, tuple[float, int]] = {}
        self._recent: dict[str, deque[tuple[float, int]]] = defaultdict(
            lambda: deque(maxlen=self._live_ring_size)
        )
        self._wake = Event()
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
        q = self._metric_q
        if len(q) == q.maxlen:
            self._dropped_metrics += 1
        q.append(p)
        # Live read path — dict assign + deque append are both GIL-atomic.
        # `_recent[name]` triggers defaultdict factory on first push; cheap
        # one-time cost per new metric name.
        self._latest[p.name] = (p.value, p.ts_ns)
        self._recent[p.name].append((p.value, p.ts_ns))
        # Backpressure: nudge flush thread early when queue is filling up.
        # Event.set() is O(1) and idempotent — safe to call every push.
        if len(q) >= self._flush_wakeup_threshold:
            self._wake.set()

    # === Live read API (for dashboard hot path; no DuckDB) ===

    def latest(self, name: str) -> tuple[float, int] | None:
        """Last (value, ts_ns) pushed for `name`, or None if never seen."""
        return self._latest.get(name)

    def recent(self, name: str, seconds: float) -> list[tuple[float, int]]:
        """Points pushed for `name` within the last `seconds`, oldest first.

        Returns at most `live_ring_size` points (= ring buffer capacity); if
        the actual rate is higher, callers see only the most recent ones.
        For windows >60s use a DuckDB-backed query, the ring isn't sized for it.
        """
        dq = self._recent.get(name)
        if not dq:
            return []
        cutoff_ns = wall_ns() - int(seconds * 1e9)
        # list(dq) snapshots under GIL; iterating the snapshot is safe even if
        # writer appends concurrently. Items older than cutoff are filtered out.
        return [(v, t) for v, t in list(dq) if t >= cutoff_ns]

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
        # Wake the flush thread immediately — it's blocked on _wake.wait(timeout)
        # and would otherwise sit for the full interval before noticing _stop.
        self._wake.set()
        self._thread.join(timeout=self._flush_interval * 2)
        self._drain()  # final flush in caller's thread

    # === Bg thread internals ===

    def _run(self) -> None:
        # Wake on either timer OR backpressure signal from push side.
        # We use _wake.wait() rather than _stop.wait() so the flush thread can
        # be nudged early. Stop is checked after wait returns.
        while not self._stop.is_set():
            self._wake.wait(timeout=self._flush_interval)
            self._wake.clear()
            if self._stop.is_set():
                break
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
