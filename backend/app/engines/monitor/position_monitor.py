"""
Position Monitor — repricing loop for tracked positions.

Polls current premium at regular intervals, classifies alert status,
and emits domain events when status changes or exits are triggered.

Runs as a background thread managed by the engine layer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

from ...domain.entities.position import AlertStatus, Position
from ...domain.events.events import (
    AlertStatusChanged,
    PositionClosed,
)
from ...domain.services.alert_classifier import classify_alert
from ...domain.services.position_store import PositionStore
from ...shared.logger import get_logger
from ...shared.time import sleep

logger = get_logger('position_monitor')


class PriceProvider(Protocol):
    """Protocol for fetching current premium. Implemented by broker/ticker."""
    def get_ltp(self, symbol: str) -> float | None:
        """Return last traded price for a symbol, or None if unavailable."""
        ...


class PositionMonitor:
    """Polls positions and classifies alert status.

    Responsibilities:
      1. For each tracked position, poll current premium
      2. Classify new alert status
      3. Emit AlertStatusChanged when status transitions
      4. Emit PositionClosed when SL_HIT or TIME_EXIT detected
      5. Handle partial exit (T1 trail SL to breakeven)

    The monitor does NOT place exit orders — it emits events.
    Application handlers react to those events and trigger exits.
    """

    def __init__(
        self,
        store: PositionStore,
        price_provider: PriceProvider,
        publish: Callable,
        poll_interval: float = 1.0,
        time_exit_checker: Callable[[], bool] | None = None,
    ) -> None:
        self._store = store
        self._price = price_provider
        self._publish = publish
        self._poll_interval = poll_interval
        self._time_exit_checker = time_exit_checker

        self._running = False
        self._thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='position-monitor')
        self._thread.start()
        logger.info('Position monitor started (poll=%.1fs)', self._poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info('Position monitor stopped')

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Core loop ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._poll_once()
            except Exception:
                logger.exception('Position monitor poll error')
            sleep(self._poll_interval)

    def _poll_once(self) -> None:
        """Single poll cycle — reprice all positions and classify alerts."""
        positions = self._store.all()
        if not positions:
            return

        # Check time exit (e.g. 15:00 IST force close)
        is_time_exit = self._time_exit_checker() if self._time_exit_checker else False

        for pos in positions:
            self._process_position(pos, is_time_exit)

    def _process_position(self, pos: Position, is_time_exit: bool) -> None:
        """Reprice and classify a single position."""

        # Time exit overrides everything
        if is_time_exit:
            old_status = self._store.update_alert(pos.track_id, AlertStatus.TIME_EXIT)
            if old_status is not None:
                self._emit_alert_change(pos, old_status, AlertStatus.TIME_EXIT)
            self._emit_close(pos, 'time_exit')
            return

        # Fetch current premium
        premium = self._price.get_ltp(pos.symbol)
        if premium is None:
            return

        # Update stored premium
        self._store.update_premium(pos.track_id, premium)

        # Classify alert
        new_status = classify_alert(pos, premium)
        old_status = self._store.update_alert(pos.track_id, new_status)

        # Emit alert change if status transitioned
        if old_status is not None:
            self._emit_alert_change(pos, old_status, new_status)

        # Handle SL hit → close
        if new_status == AlertStatus.SL_HIT:
            self._emit_close(pos, 'sl_hit')
            return

        # Handle T1 hit → trail SL + partial exit
        if new_status == AlertStatus.PAST_T1 and not pos.sl_trailed:
            self._store.trail_sl(pos.track_id, pos.entry_price)
            logger.info('T1 hit for %s — SL trailed to breakeven (₹%.2f)',
                       pos.symbol, pos.entry_price)

        # Handle T2 hit → close remaining
        if new_status == AlertStatus.PAST_T2:
            self._emit_close(pos, 'past_t2')

    # ── Event emission ────────────────────────────────────────────────────

    def _emit_alert_change(self, pos: Position, old: AlertStatus, new: AlertStatus) -> None:
        self._publish(AlertStatusChanged(
            track_id=pos.track_id,
            symbol=pos.symbol,
            old_status=old.value,
            new_status=new.value,
        ))

    def _emit_close(self, pos: Position, reason: str) -> None:
        self._publish(PositionClosed(
            track_id=pos.track_id,
            symbol=pos.symbol,
            exit_reason=reason,
            entry_price=pos.entry_price,
            exit_price=pos.current_premium,
            qty=pos.qty,
            realized_pnl=pos.unrealized_pnl,
            trade_mode=pos.trade_mode,
        ))

    # ── Manual ────────────────────────────────────────────────────────────

    def poll_now(self) -> None:
        """Trigger an immediate poll (useful for tests and manual refresh)."""
        self._poll_once()
