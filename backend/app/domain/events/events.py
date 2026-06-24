"""
Domain Events — immutable data objects emitted by the trading system.

Events are fire-and-forget. No side effects, no I/O, no framework imports.
Handlers in application/handlers/ react to these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ...shared.time import datetime, now_ist


class EventType(str, Enum):
    SIGNAL_GENERATED       = 'signal_generated'
    TRADE_APPROVED         = 'trade_approved'
    TRADE_REJECTED         = 'trade_rejected'
    ORDER_PLACED           = 'order_placed'
    ORDER_FILLED           = 'order_filled'
    POSITION_CLOSED        = 'position_closed'
    PNL_UPDATED            = 'pnl_updated'
    DAY_ROLLED             = 'day_rolled'
    KILL_SWITCH_TRIGGERED  = 'kill_switch_triggered'
    ALERT_STATUS_CHANGED   = 'alert_status_changed'
    REGIME_CHANGED         = 'regime_changed'
    EXPOSURE_LIMIT_HIT     = 'exposure_limit_hit'


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base class for all domain events."""
    event_type: EventType
    timestamp: datetime = field(default_factory=now_ist)


# ── Signal Events ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SignalGenerated(DomainEvent):
    """Emitted by a scanner when a tradable signal is produced."""
    event_type: EventType = field(default=EventType.SIGNAL_GENERATED, init=False)
    symbol: str = ''
    underlying: str = ''           # ^NSEI or ^BSESN
    direction: str = ''            # CE or PE
    confidence: float = 0.0
    trade_mode: str = ''           # swing or scalp
    signal_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TradeApproved(DomainEvent):
    """Emitted when a signal passes all risk gates and is approved for execution."""
    event_type: EventType = field(default=EventType.TRADE_APPROVED, init=False)
    trade_id: str = ''
    symbol: str = ''
    underlying: str = ''
    direction: str = ''
    qty: int = 0
    trade_mode: str = ''
    approved_by: str = ''          # which risk gates were checked


@dataclass(frozen=True, slots=True)
class TradeRejected(DomainEvent):
    """Emitted when a signal is rejected by supervisor or risk gates."""
    event_type: EventType = field(default=EventType.TRADE_REJECTED, init=False)
    symbol: str = ''
    underlying: str = ''
    direction: str = ''
    trade_mode: str = ''
    rejected_by: str = ''          # which gate rejected: KILL_SWITCH, LOSS_STREAK, etc.
    reason: str = ''               # human-readable denial detail


# ── Order Events ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class OrderPlaced(DomainEvent):
    """Emitted when a buy/sell order is sent to the broker."""
    event_type: EventType = field(default=EventType.ORDER_PLACED, init=False)
    order_id: str = ''
    symbol: str = ''
    side: str = ''                 # BUY or SELL
    qty: int = 0
    price: float = 0.0
    trade_mode: str = ''


@dataclass(frozen=True, slots=True)
class OrderFilled(DomainEvent):
    """Emitted when the broker confirms an order fill."""
    event_type: EventType = field(default=EventType.ORDER_FILLED, init=False)
    order_id: str = ''
    symbol: str = ''
    side: str = ''
    qty: int = 0
    fill_price: float = 0.0
    trade_mode: str = ''
    track_id: str = ''


# ── Position Events ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class PositionClosed(DomainEvent):
    """Emitted when a tracked position is fully closed."""
    event_type: EventType = field(default=EventType.POSITION_CLOSED, init=False)
    track_id: str = ''
    symbol: str = ''
    exit_reason: str = ''          # sl_hit, past_t1, past_t2, time_exit, manual
    entry_price: float = 0.0
    exit_price: float = 0.0
    qty: int = 0
    realized_pnl: float = 0.0
    trade_mode: str = ''


@dataclass(frozen=True, slots=True)
class AlertStatusChanged(DomainEvent):
    """Emitted when a position's alert status transitions (e.g. safe → near_sl)."""
    event_type: EventType = field(default=EventType.ALERT_STATUS_CHANGED, init=False)
    track_id: str = ''
    symbol: str = ''
    old_status: str = ''
    new_status: str = ''


# ── PnL Events ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class PnLUpdated(DomainEvent):
    """Emitted after a trade's P&L is recorded."""
    event_type: EventType = field(default=EventType.PNL_UPDATED, init=False)
    trade_mode: str = ''           # swing or scalp
    realized_pnl: float = 0.0     # this trade's P&L
    daily_pnl: float = 0.0        # cumulative day P&L after this trade
    brokerage: float = 0.0


# ── Risk Events ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class KillSwitchTriggered(DomainEvent):
    """Emitted when cumulative daily loss exceeds the limit."""
    event_type: EventType = field(default=EventType.KILL_SWITCH_TRIGGERED, init=False)
    trade_mode: str = ''
    daily_pnl: float = 0.0
    limit: float = 0.0


@dataclass(frozen=True, slots=True)
class ExposureLimitHit(DomainEvent):
    """Emitted when net directional exposure exceeds the allowed maximum."""
    event_type: EventType = field(default=EventType.EXPOSURE_LIMIT_HIT, init=False)
    direction: str = ''            # CE or PE (bullish or bearish)
    current_exposure: int = 0      # number of open lots in this direction
    max_allowed: int = 0


# ── Regime Events ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class RegimeChanged(DomainEvent):
    """Emitted when the market regime classification transitions."""
    event_type: EventType = field(default=EventType.REGIME_CHANGED, init=False)
    old_regime: str = ''
    new_regime: str = ''           # TRENDING_BULL, TRENDING_BEAR, RANGING, HIGH_VOL, LOW_VOL
    vix: float = 0.0
    adx: float = 0.0


# ── Day Events ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DayRolled(DomainEvent):
    """Emitted at midnight IST when daily state resets."""
    event_type: EventType = field(default=EventType.DAY_ROLLED, init=False)
    old_date: str = ''
    new_date: str = ''
