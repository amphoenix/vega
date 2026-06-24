"""
Position Store — thread-safe in-memory store for tracked positions.

Pure domain — no I/O, no persistence.
Infrastructure layer can serialize/deserialize for persistence.
"""

from __future__ import annotations

import threading
from typing import Iterator

from ..entities.position import Position, AlertStatus


class PositionStore:
    """Thread-safe container for live tracked positions."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._lock = threading.Lock()

    # ── Mutations ─────────────────────────────────────────────────────────

    def add(self, position: Position) -> None:
        """Track a new position. Raises if track_id already exists."""
        with self._lock:
            if position.track_id in self._positions:
                raise ValueError(f'Position {position.track_id} already tracked')
            self._positions[position.track_id] = position

    def remove(self, track_id: str) -> Position | None:
        """Remove and return a position. Returns None if not found."""
        with self._lock:
            return self._positions.pop(track_id, None)

    def update_premium(self, track_id: str, premium: float, spot: float = 0.0) -> Position | None:
        """Update current market price for a position. Returns updated position or None."""
        with self._lock:
            pos = self._positions.get(track_id)
            if pos:
                pos.current_premium = premium
                if spot:
                    pos.current_spot = spot
            return pos

    def update_alert(self, track_id: str, new_status: AlertStatus) -> AlertStatus | None:
        """Update alert status. Returns old status if changed, None if unchanged or not found."""
        with self._lock:
            pos = self._positions.get(track_id)
            if pos:
                return pos.update_alert(new_status)
            return None

    def mark_partial_exit(self, track_id: str) -> None:
        """Mark position as partially exited (T1 hit, 50% closed)."""
        with self._lock:
            pos = self._positions.get(track_id)
            if pos:
                pos.partial_exited = True

    def trail_sl(self, track_id: str, new_sl: float) -> None:
        """Trail stop-loss to new level (typically breakeven after T1)."""
        with self._lock:
            pos = self._positions.get(track_id)
            if pos:
                pos.stop_loss = new_sl
                pos.sl_trailed = True

    # ── Queries ───────────────────────────────────────────────────────────

    def get(self, track_id: str) -> Position | None:
        with self._lock:
            return self._positions.get(track_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._positions)

    def __contains__(self, track_id: str) -> bool:
        with self._lock:
            return track_id in self._positions

    def all(self) -> list[Position]:
        """Snapshot of all tracked positions."""
        with self._lock:
            return list(self._positions.values())

    def by_underlying(self, underlying: str) -> list[Position]:
        """All positions on a given underlying."""
        with self._lock:
            return [p for p in self._positions.values() if p.underlying == underlying]

    def by_mode(self, trade_mode: str) -> list[Position]:
        """All positions of a given mode (swing/scalp)."""
        with self._lock:
            return [p for p in self._positions.values() if p.trade_mode == trade_mode]

    @property
    def total_unrealized_pnl(self) -> float:
        with self._lock:
            return round(sum(p.unrealized_pnl for p in self._positions.values()), 2)

    def to_list(self) -> list[dict]:
        """Serialize all positions to dicts (for persistence/API)."""
        with self._lock:
            return [p.to_dict() for p in self._positions.values()]

    def clear(self) -> None:
        """Remove all positions (used in tests and day reset)."""
        with self._lock:
            self._positions.clear()
