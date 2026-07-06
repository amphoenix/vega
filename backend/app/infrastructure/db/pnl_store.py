"""
P&L store — re-exports state persistence from state_store + brokerage calc.

Centralises all pnl-related names so engines (fo_scanner, scalp_scanner,
order_executor) import from one place instead of directly wiring to state_store
and brokerage_calc separately.
"""

from __future__ import annotations

from .state_store import (
    get_state as get_trading_state,
    set_state as set_trading_state,
    today_net_by_mode,
    _conn,
)
from ...domain.services.brokerage_calc import segment_brokerage as _seg_brokerage


def last_trade_date() -> str | None:
    """Return the most recent trade date as YYYY-MM-DD, or None if no trades."""
    try:
        with _conn() as c:
            row = c.execute('SELECT MAX(date) AS d FROM pnl_trades').fetchone()
        return row['d'] if row and row['d'] else None
    except Exception:
        return None


def record_trade(
    mode: str,
    symbol: str,
    underlying: str,
    entry_prem: float,
    exit_prem: float,
    qty: int,
    lot_size: int,
    brokerage_or_exit_reason='',
    exit_reason: str = '',
    *,
    market_type: str = '',
    direction: str = '',
    order_id: str = '',
    exit_order_id: str = '',
    display_symbol: str = '',
) -> dict:
    """Wrapper that accepts both old signature (no brokerage arg) and new one.

    Old callers pass exit_reason as 8th positional arg (string).
    New callers pass brokerage (float) as 8th arg and exit_reason as 9th.
    """
    from .state_store import record_trade as _record

    if isinstance(brokerage_or_exit_reason, str):
        brokerage = _seg_brokerage(mode, entry_prem, exit_prem, qty)
        reason = brokerage_or_exit_reason or exit_reason
    else:
        brokerage = float(brokerage_or_exit_reason or 0)
        reason = exit_reason

    # Derive market_type: explicit kwarg wins, then mode-based lookup, then 'fo'
    _mt_map = {'forex': 'forex', 'crypto': 'crypto', 'poly': 'poly'}
    mt = market_type or _mt_map.get(mode, 'fo')

    return _record(
        mode, symbol, underlying,
        entry_prem, exit_prem, qty, lot_size,
        brokerage, reason,
        market_type=mt,
        direction=direction,
        order_id=order_id,
        exit_order_id=exit_order_id,
        display_symbol=display_symbol,
    )
