"""
In-memory rate limiting (single server process).

* FailedAuthLimiter — N wrong keys from one client address within a window locks that address out
  for a while. During a lockout NOTHING from that address is evaluated, so the key cannot be probed.
* RequestLimiter — requests per minute per person (identity), so one runaway client or leaked key
  cannot hammer the server.
Memory is bounded: stale entries are dropped and the tables have a hard size cap.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

from ..shared.time import monotonic

_MAX_TRACKED = 10_000


class FailedAuthLimiter:
    def __init__(self, max_failures: int = 5, window_sec: float = 60.0, lockout_sec: float = 900.0,
                 clock: Callable[[], float] = monotonic) -> None:
        self.max_failures = max_failures
        self.window = window_sec
        self.lockout = lockout_sec
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str) -> int:
        """Seconds the client must still wait (0 = allowed)."""
        now = self._clock()
        with self._lock:
            until = self._locked_until.get(client)
            if until is None:
                return 0
            if until <= now:
                del self._locked_until[client]
                self._failures.pop(client, None)
                return 0
            return int(until - now) + 1

    def record_failure(self, client: str) -> int:
        """Count a wrong key. Returns the lockout length in seconds if this failure triggered one, else 0."""
        now = self._clock()
        with self._lock:
            self._prune(now)
            hits = self._failures.setdefault(client, deque())
            hits.append(now)
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.max_failures:
                self._locked_until[client] = now + self.lockout
                return int(self.lockout)
            return 0

    def _prune(self, now: float) -> None:
        if len(self._failures) < _MAX_TRACKED and len(self._locked_until) < _MAX_TRACKED:
            return
        for client in [c for c, t in self._locked_until.items() if t <= now]:
            del self._locked_until[client]
        for client in [c for c, h in self._failures.items() if not h or now - h[-1] > self.window]:
            del self._failures[client]
        if len(self._failures) >= _MAX_TRACKED:      # still full: forget the oldest half
            for client in list(self._failures)[: _MAX_TRACKED // 2]:
                del self._failures[client]


class RequestLimiter:
    """Sliding one-minute window per identity. limit_per_min <= 0 disables it."""

    def __init__(self, limit_per_min: int = 1200, clock: Callable[[], float] = monotonic) -> None:
        self.limit = limit_per_min
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, identity: str) -> int:
        """0 = allowed; otherwise seconds until a slot frees up."""
        if self.limit <= 0:
            return 0
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(identity, deque())
            while hits and now - hits[0] >= 60.0:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, int(60.0 - (now - hits[0])) + 1)
            hits.append(now)
            if len(self._hits) > _MAX_TRACKED:
                for name in [n for n, h in self._hits.items() if not h or now - h[-1] >= 60.0]:
                    del self._hits[name]
            return 0
