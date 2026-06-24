"""
Event handlers — wired in main.py lifespan.

These are thin functions that receive domain events and route them
to the appropriate domain services (kill switch, supervisor, etc.).
"""

from __future__ import annotations

from ...shared.logger import get_logger
from ...domain.events.events import (
    PositionClosed, PnLUpdated, KillSwitchTriggered, DayRolled, SignalGenerated,
)

logger = get_logger('handlers')


def make_position_closed_handler(
    kill_switch_engine,
    supervisor,
):
    """Factory: returns a handler that routes PositionClosed events."""

    def on_position_closed(event: PositionClosed) -> None:
        pnl = event.realized_pnl
        track_id = event.track_id

        # 1. Route P&L to kill switch engine
        activations = kill_switch_engine.record_pnl(pnl)
        for a in activations:
            logger.warning('Kill switch activated: %s', a.reason)

        # 2. Notify supervisor
        supervisor.record_trade_closed(
            track_id=track_id,
            pnl=pnl,
            underlying=event.symbol,
        )

        logger.info(
            'PositionClosed handled: %s pnl=%.0f mode=%s',
            track_id, pnl, event.trade_mode,
        )

    return on_position_closed


def make_day_rolled_handler(
    kill_switch_engine,
    supervisor,
):
    """Factory: returns a handler that resets daily state at midnight."""

    def on_day_rolled(event: DayRolled) -> None:
        kill_switch_engine.reset_daily()
        supervisor.reset_daily()
        logger.info(
            'Day rolled: %s -> %s — all daily state reset',
            event.old_date, event.new_date,
        )

    return on_day_rolled
