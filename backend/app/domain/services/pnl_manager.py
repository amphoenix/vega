"""
PnL Manager — orchestrates trade closure P&L computation.

Pure domain service — no I/O, no database, no event bus.
Given a closed trade, computes gross P&L, brokerage, and net P&L.
The application layer calls this, persists results, and emits events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..entities.trade import Trade, TradeState
from ..services.brokerage_calc import calc_brokerage, total_brokerage
from ..value_objects.money import PnL, Money, BrokerageBreakdown


@dataclass(frozen=True, slots=True)
class TradeResult:
    """Immutable result of a closed trade — everything needed for persistence."""
    trade_id: str
    symbol: str
    underlying: str
    direction: str
    trade_mode: str
    qty: int
    lot_size: int

    entry_price: float
    exit_price: float
    exit_reason: str

    gross_pnl: float
    brokerage: float
    net_pnl: float
    brokerage_breakdown: BrokerageBreakdown

    @property
    def is_winner(self) -> bool:
        return self.net_pnl > 0

    @property
    def is_loser(self) -> bool:
        return self.net_pnl < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'trade_id': self.trade_id,
            'symbol': self.symbol,
            'underlying': self.underlying,
            'direction': self.direction,
            'trade_mode': self.trade_mode,
            'qty': self.qty,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'exit_reason': self.exit_reason,
            'gross_pnl': self.gross_pnl,
            'brokerage': self.brokerage,
            'net_pnl': self.net_pnl,
        }


def compute_trade_result(
    trade: Trade,
    underlying: str = '',
    lot_size: int = 1,
) -> TradeResult:
    """Compute the full P&L result for a closed trade.

    Args:
        trade: A Trade in CLOSED state.
        underlying: The underlying index/asset (e.g. 'NIFTY').
        lot_size: Contract lot size (for F&O).

    Returns:
        TradeResult with gross, brokerage, and net P&L.

    Raises:
        ValueError: If trade is not in CLOSED state.
    """
    if trade.state != TradeState.CLOSED:
        raise ValueError(f'Trade {trade.trade_id} is {trade.state.value}, not closed')

    gross = trade.realized_gross_pnl
    brk = calc_brokerage(trade.entry_price, trade.exit_price, trade.qty)
    net = round(gross - brk.total, 2)

    return TradeResult(
        trade_id=trade.trade_id,
        symbol=trade.symbol,
        underlying=underlying,
        direction=trade.direction,
        trade_mode=trade.trade_mode,
        qty=trade.qty,
        lot_size=lot_size,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        exit_reason=trade.exit_reason,
        gross_pnl=gross,
        brokerage=brk.total,
        net_pnl=net,
        brokerage_breakdown=brk,
    )
