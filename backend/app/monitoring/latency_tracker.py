"""
Latency Tracker — per-trade and per-stage latency measurement.

Tracks end-to-end latency from signal generation through execution:
    Signal → Supervisor → Risk → Order → Fill

Critical for scalping where milliseconds matter.

Design:
    - Start a trace for each decision pipeline invocation.
    - Record named checkpoints (stages) with monotonic timestamps.
    - Compute stage-to-stage and total latencies.
    - Maintain rolling statistics (P50, P95, P99, mean) per stage.

Pure domain — no I/O, thread-safe.
"""

from __future__ import annotations

import math
import threading
from collections import deque

from ..shared.time import monotonic_ns
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LatencyCheckpoint:
    """A single named point in the decision pipeline."""
    stage: str
    monotonic_ns: int  # monotonic_ns()


@dataclass
class LatencyTrace:
    """One complete pipeline execution trace.

    Usage:
        trace = tracker.start_trace('signal_123')
        trace.checkpoint('supervisor')
        trace.checkpoint('risk_check')
        trace.checkpoint('order_placed')
        trace.checkpoint('order_filled')
        tracker.finish_trace(trace)
    """
    trace_id: str
    checkpoints: list[LatencyCheckpoint] = field(default_factory=list)
    started_ns: int = 0
    finished_ns: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.started_ns == 0:
            self.started_ns = monotonic_ns()

    def checkpoint(self, stage: str) -> None:
        """Record a named checkpoint."""
        self.checkpoints.append(LatencyCheckpoint(
            stage=stage,
            monotonic_ns=monotonic_ns(),
        ))

    def finish(self) -> None:
        self.finished_ns = monotonic_ns()

    @property
    def is_finished(self) -> bool:
        return self.finished_ns > 0

    @property
    def total_ms(self) -> float:
        """Total elapsed time in milliseconds."""
        end = self.finished_ns if self.finished_ns else monotonic_ns()
        return (end - self.started_ns) / 1_000_000

    def stage_latencies_ms(self) -> dict[str, float]:
        """Compute latency between consecutive checkpoints in ms.

        Returns:
            {'start→supervisor': 1.2, 'supervisor→risk_check': 0.8, ...}
        """
        result: dict[str, float] = {}
        prev_ns = self.started_ns
        prev_name = 'start'

        for cp in self.checkpoints:
            key = f'{prev_name}\u2192{cp.stage}'
            result[key] = (cp.monotonic_ns - prev_ns) / 1_000_000
            prev_ns = cp.monotonic_ns
            prev_name = cp.stage

        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            'trace_id': self.trace_id,
            'total_ms': round(self.total_ms, 3),
            'stages': self.stage_latencies_ms(),
            'checkpoints': [c.stage for c in self.checkpoints],
            'metadata': self.metadata,
        }


# ── Rolling Statistics ───────────────────────────────────────────────────────

@dataclass
class LatencyStats:
    """Rolling statistics for a single stage or total pipeline."""
    name: str
    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float('inf')
    max_ms: float = 0.0
    _values: deque = field(default_factory=lambda: deque(maxlen=1000))

    def record(self, ms: float) -> None:
        self.count += 1
        self.total_ms += ms
        self.min_ms = min(self.min_ms, ms)
        self.max_ms = max(self.max_ms, ms)
        self._values.append(ms)

    @property
    def mean_ms(self) -> float:
        return round(self.total_ms / self.count, 3) if self.count else 0.0

    def percentile(self, p: float) -> float:
        """Compute percentile (0-100) from rolling window."""
        if not self._values:
            return 0.0
        sorted_vals = sorted(self._values)
        idx = max(0, min(len(sorted_vals) - 1,
                         int(math.ceil(p / 100 * len(sorted_vals))) - 1))
        return round(sorted_vals[idx], 3)

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'count': self.count,
            'mean_ms': self.mean_ms,
            'min_ms': round(self.min_ms, 3) if self.min_ms != float('inf') else 0,
            'max_ms': round(self.max_ms, 3),
            'p50_ms': self.p50,
            'p95_ms': self.p95,
            'p99_ms': self.p99,
        }


# ── Tracker ──────────────────────────────────────────────────────────────────

class LatencyTracker:
    """Central latency tracking for the trading pipeline.

    Usage:
        tracker = LatencyTracker()
        trace = tracker.start_trace('signal_123')
        trace.checkpoint('supervisor')
        trace.checkpoint('risk')
        trace.checkpoint('order')
        tracker.finish_trace(trace)

        stats = tracker.stats()
    """

    def __init__(self, max_traces: int = 500) -> None:
        self._traces: deque[LatencyTrace] = deque(maxlen=max_traces)
        self._stage_stats: dict[str, LatencyStats] = {}
        self._total_stats = LatencyStats(name='total')
        self._lock = threading.Lock()

    def start_trace(self, trace_id: str, **metadata: Any) -> LatencyTrace:
        """Begin a new latency trace."""
        return LatencyTrace(trace_id=trace_id, metadata=metadata)

    def finish_trace(self, trace: LatencyTrace) -> None:
        """Finalize and record a trace."""
        trace.finish()

        with self._lock:
            self._traces.append(trace)

            # Record total latency
            self._total_stats.record(trace.total_ms)

            # Record per-stage latencies
            for stage_key, ms in trace.stage_latencies_ms().items():
                if stage_key not in self._stage_stats:
                    self._stage_stats[stage_key] = LatencyStats(name=stage_key)
                self._stage_stats[stage_key].record(ms)

    def recent_traces(self, limit: int = 20) -> list[LatencyTrace]:
        """Return most recent traces."""
        with self._lock:
            traces = list(self._traces)
        return traces[-limit:]

    def stats(self) -> dict[str, Any]:
        """Aggregate latency statistics."""
        with self._lock:
            return {
                'total': self._total_stats.to_dict(),
                'stages': {k: v.to_dict() for k, v in self._stage_stats.items()},
            }

    def stage_stats(self, stage: str) -> LatencyStats | None:
        """Get stats for a specific stage transition."""
        with self._lock:
            return self._stage_stats.get(stage)

    @property
    def trace_count(self) -> int:
        with self._lock:
            return len(self._traces)

    def reset(self) -> None:
        """Clear all traces and stats (used in tests)."""
        with self._lock:
            self._traces.clear()
            self._stage_stats.clear()
            self._total_stats = LatencyStats(name='total')
