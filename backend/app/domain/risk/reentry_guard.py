"""
Re-entry Guard — limits re-entries after stop-loss.

Tracks per-underlying, per-direction re-entry counts for the day.
Pure domain — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...shared.logger import get_logger

logger = get_logger('reentry_guard')


@dataclass(frozen=True, slots=True)
class ReentryVerdict:
    allowed: bool
    count: int          # re-entries used so far
    max_allowed: int
    reason: str = ''


class ReentryGuard:
    """Tracks re-entries per (underlying, direction) per day."""

    def __init__(self, max_reentries: int = 1, allow_reentry: bool = True) -> None:
        self._max = max_reentries
        self._allow = allow_reentry
        self._counts: dict[str, int] = {}  # "NIFTY_CE" → count

    def _key(self, underlying: str, direction: str) -> str:
        return f'{underlying.upper()}_{direction.upper()}'

    def check(self, underlying: str, direction: str) -> ReentryVerdict:
        """Check if a re-entry is allowed."""
        if not self._allow:
            return ReentryVerdict(
                allowed=False, count=0, max_allowed=0,
                reason='Re-entry after SL is disabled',
            )
        key = self._key(underlying, direction)
        count = self._counts.get(key, 0)
        if count >= self._max:
            return ReentryVerdict(
                allowed=False, count=count, max_allowed=self._max,
                reason=f'Max re-entries ({self._max}) used for {key}',
            )
        return ReentryVerdict(allowed=True, count=count, max_allowed=self._max)

    def record_sl(self, underlying: str, direction: str) -> None:
        """Record that a SL was hit (increments re-entry count)."""
        key = self._key(underlying, direction)
        self._counts[key] = self._counts.get(key, 0) + 1
        logger.info('Re-entry count for %s: %d / %d', key, self._counts[key], self._max)

    def reset_daily(self) -> None:
        """Midnight reset."""
        self._counts.clear()
