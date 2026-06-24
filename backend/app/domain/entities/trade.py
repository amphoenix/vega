"""
Trade entity — Finite State Machine for trade lifecycle.

States: PENDING → OPEN → PARTIAL → CLOSED
                              ↘ CANCELLED
Invalid transitions raise InvalidStateTransition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ...shared.logger import get_logger
from ...shared.time import datetime, now_ist
from ..exceptions import InvalidStateTransition
from ..value_objects.instrument import Instrument
from ..value_objects.market import MarketType
from ..value_objects.option_leg import OptionLeg

logger = get_logger('trade')


class TradeState(str, Enum):
    PENDING   = 'pending'
    OPEN      = 'open'
    PARTIAL   = 'partial'
    CLOSED    = 'closed'
    CANCELLED = 'cancelled'


_VALID_TRANSITIONS: dict[TradeState, set[TradeState]] = {
    TradeState.PENDING:   {TradeState.OPEN, TradeState.CANCELLED},
    TradeState.OPEN:      {TradeState.PARTIAL, TradeState.CLOSED},
    TradeState.PARTIAL:   {TradeState.CLOSED},
    TradeState.CLOSED:    set(),
    TradeState.CANCELLED: set(),
}


@dataclass
class Trade:
    """A single trade with FSM lifecycle.

    Works across markets:
      - Indian F&O: option_leg set, direction='CE'/'PE'
      - Crypto: instrument set, direction='LONG'/'SHORT'
      - Polymarket: instrument set, direction='YES'/'NO'
    """

    trade_id: str
    option_leg: OptionLeg | None = None         # F&O-specific (legacy compat)
    instrument: Instrument | None = None        # unified multi-market instrument
    trade_mode: str = 'swing'                   # 'swing', 'scalp', 'research'
    direction: str = ''                         # 'CE'/'PE' or 'LONG'/'SHORT' or 'YES'/'NO'
    qty: int | float = 0                        # int for F&O, float for crypto
    state: TradeState = TradeState.PENDING

    entry_price: float = 0.0
    exit_price: float = 0.0
    partial_exit_price: float = 0.0
    partial_exit_qty: int = 0

    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0

    order_id: str = ''
    exit_reason: str = ''

    created_at: datetime = field(default_factory=now_ist)
    filled_at: datetime | None = None
    closed_at: datetime | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    # ── FSM transitions ──────────────────────────────────────────────────

    def _transition(self, target: TradeState) -> None:
        if target not in _VALID_TRANSITIONS.get(self.state, set()):
            raise InvalidStateTransition(self.state, target)
        self.state = target

    def fill(self, price: float, order_id: str = '') -> None:
        """PENDING → OPEN. Called when broker confirms the entry fill."""
        self._transition(TradeState.OPEN)
        self.entry_price = price
        self.order_id = order_id
        self.filled_at = now_ist()
        logger.info('Trade %s FILLED @ ₹%.2f [%s]', self.trade_id, price, order_id)

    def partial_exit(self, price: float, qty: int) -> None:
        """OPEN → PARTIAL. T1 hit — exit 50%, trail SL to breakeven."""
        self._transition(TradeState.PARTIAL)
        self.partial_exit_price = price
        self.partial_exit_qty = qty

    def close(self, price: float, reason: str = '') -> None:
        """OPEN/PARTIAL → CLOSED. Full exit."""
        self._transition(TradeState.CLOSED)
        self.exit_price = price
        self.exit_reason = reason
        self.closed_at = now_ist()
        logger.info('Trade %s CLOSED @ ₹%.2f reason=%s pnl=₹%.2f',
                    self.trade_id, price, reason, self.realized_gross_pnl)

    def cancel(self, reason: str = '') -> None:
        """PENDING → CANCELLED. Order rejected or signal expired."""
        self._transition(TradeState.CANCELLED)
        self.exit_reason = reason
        self.closed_at = now_ist()
        logger.warning('Trade %s CANCELLED: %s', self.trade_id, reason)

    # ── Queries ──────────────────────────────────────────────────────────

    @property
    def market_type(self) -> MarketType:
        """Which market this trade belongs to."""
        if self.instrument:
            return self.instrument.market
        return MarketType.INDIAN_FO

    @property
    def symbol(self) -> str:
        """Canonical symbol for display."""
        if self.instrument:
            return self.instrument.symbol
        if self.option_leg:
            return self.option_leg.trading_symbol
        return self.trade_id

    @property
    def is_open(self) -> bool:
        return self.state in (TradeState.OPEN, TradeState.PARTIAL)

    @property
    def is_terminal(self) -> bool:
        return self.state in (TradeState.CLOSED, TradeState.CANCELLED)

    @property
    def remaining_qty(self) -> int:
        if self.state == TradeState.PARTIAL:
            return self.qty - self.partial_exit_qty
        return self.qty

    @property
    def realized_gross_pnl(self) -> float:
        """Gross P&L (before brokerage) once closed."""
        if self.state == TradeState.CLOSED:
            partial_pnl = (self.partial_exit_price - self.entry_price) * self.partial_exit_qty
            remaining = self.qty - self.partial_exit_qty
            final_pnl = (self.exit_price - self.entry_price) * remaining
            return round(partial_pnl + final_pnl, 2)
        return 0.0
