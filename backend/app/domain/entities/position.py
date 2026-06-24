"""
Position — a tracked trading position with alert status.

Represents a live or recently-closed position being monitored
by the position engine for SL/T1/T2/time exits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ...shared.time import datetime, now_ist


class AlertStatus(str, Enum):
    """Alert classification for a tracked position."""
    SAFE      = 'safe'
    NEAR_SL   = 'near_sl'
    SL_HIT    = 'sl_hit'
    NEAR_T1   = 'near_t1'
    PAST_T1   = 'past_t1'
    NEAR_T2   = 'near_t2'
    PAST_T2   = 'past_t2'
    TIME_EXIT = 'time_exit'


@dataclass
class Position:
    """A tracked trading position."""

    track_id: str
    symbol: str
    underlying: str                 # NIFTY, SENSEX, etc.
    direction: str                  # CE or PE
    trade_mode: str                 # swing or scalp
    qty: int
    lot_size: int

    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float

    current_premium: float = 0.0
    current_spot: float = 0.0
    alert_status: AlertStatus = AlertStatus.SAFE

    security_id: str = ''
    exchange: str = 'NFO'
    display_symbol: str = ''
    order_id: str = ''

    sl_trailed: bool = False        # True after T1 hit, SL moved to breakeven
    partial_exited: bool = False    # True after 50% exit at T1

    pinned_at: datetime = field(default_factory=now_ist)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Alert transition ─────────────────────────────────────────────────

    def update_alert(self, new_status: AlertStatus) -> AlertStatus | None:
        """Update alert status. Returns old status if changed, None if same."""
        if new_status == self.alert_status:
            return None
        old = self.alert_status
        self.alert_status = new_status
        return old

    # ── Queries ──────────────────────────────────────────────────────────

    @property
    def _is_short(self) -> bool:
        return self.direction == 'SHORT'

    @property
    def unrealized_pnl(self) -> float:
        if self._is_short:
            return round((self.entry_price - self.current_premium) * self.qty, 2)
        return round((self.current_premium - self.entry_price) * self.qty, 2)

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self._is_short:
            return round((self.entry_price - self.current_premium) / self.entry_price * 100, 2)
        return round((self.current_premium - self.entry_price) / self.entry_price * 100, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            'track_id': self.track_id,
            'symbol': self.symbol,
            'underlying': self.underlying,
            'direction': self.direction,
            'trade_mode': self.trade_mode,
            'qty': self.qty,
            'entry_price': self.entry_price,
            'current_premium': self.current_premium,
            'stop_loss': self.stop_loss,
            'target_1': self.target_1,
            'target_2': self.target_2,
            'alert_status': self.alert_status.value,
            'unrealized_pnl': self.unrealized_pnl,
            'pnl_pct': self.pnl_pct,
            'sl_trailed': self.sl_trailed,
        }
