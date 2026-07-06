"""
Trade Supervisor — gatekeeper for trade permission.

Enforces:
  1. Daily loss limit (kill switch)
  2. Consecutive loss streak cap
  3. Max trades per day
  4. Instrument cooldown after SL
  5. Correlation limit (max CE+PE on same underlying)
  6. Session rules (no new trades outside safe hours)
  7. Max open trades (total concurrent positions)
  8. Duplicate position detection (same symbol already open)

Does NOT decide sizing — that's domain/risk/position_sizer.py.
Pure domain — no I/O, no framework imports.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...shared.logger import get_logger
from ...shared.time import datetime, now_ist
from ..safety.kill_switch import KillSwitchEngine

logger = get_logger('supervisor')


@dataclass
class SupervisorConfig:
    """Config for the trade supervisor, loaded from Settings."""
    daily_loss_limit: float = 2500.0
    max_trades_per_day: int = 10
    max_consecutive_losses: int = 3
    cooldown_minutes: int = 30         # per-instrument cooldown after SL
    max_same_underlying_trades: int = 2  # max open trades on same underlying
    max_open_trades: int = 5               # max total concurrent positions
    allow_outside_safe_hours: bool = False


@dataclass
class _InstrumentState:
    last_sl_time: datetime | None = None
    sl_count: int = 0


@dataclass
class Denial:
    """Reason a trade was denied."""
    rule: str
    detail: str

    def __str__(self) -> str:
        return f'[{self.rule}] {self.detail}'


class TradeSupervisor:
    """Permission-only gate — decides if a trade CAN be taken.

    Stateful per-day. Call reset_daily() at midnight.
    """

    def __init__(self, config: SupervisorConfig | None = None,
                 kill_switch: KillSwitchEngine | None = None) -> None:
        self.config = config or SupervisorConfig()
        self._kill_switch_engine = kill_switch or KillSwitchEngine()
        self._trade_count: int = 0
        self._instrument_state: dict[str, _InstrumentState] = {}
        self._open_positions: dict[str, str] = {}  # track_id → underlying
        self._open_symbols: set[str] = set()         # symbols with open positions

    # ── Core gate ────────────────────────────────────────────────────────

    def check_permission(
        self,
        underlying: str,
        trade_mode: str,
        is_safe_hours: bool = True,
        symbol: str = '',
    ) -> Denial | None:
        """Return None if trade is allowed, or a Denial explaining why not."""

        if self._kill_switch_engine.is_blocked():
            return Denial('KILL_SWITCH', f'Kill switch active: {self._kill_switch_engine.level.value}')

        if self._trade_count >= self.config.max_trades_per_day:
            return Denial('MAX_TRADES', f'{self._trade_count} trades today (limit {self.config.max_trades_per_day})')

        losses = self.consecutive_losses
        if losses >= self.config.max_consecutive_losses:
            return Denial('LOSS_STREAK', f'{losses} consecutive losses')

        if not is_safe_hours and not self.config.allow_outside_safe_hours:
            return Denial('SESSION', 'Outside safe trading hours')

        # Instrument cooldown
        inst = self._instrument_state.get(underlying)
        if inst and inst.last_sl_time:
            elapsed = (now_ist() - inst.last_sl_time).total_seconds() / 60
            if elapsed < self.config.cooldown_minutes:
                remaining = int(self.config.cooldown_minutes - elapsed)
                return Denial('COOLDOWN', f'{underlying} on cooldown ({remaining}min left)')

        # Max open trades (total concurrent)
        if len(self._open_positions) >= self.config.max_open_trades:
            return Denial('MAX_OPEN_TRADES', f'{len(self._open_positions)} positions open (limit {self.config.max_open_trades})')

        # Duplicate position detection (same symbol already tracked)
        if symbol and symbol in self._open_symbols:
            return Denial('DUPLICATE_POSITION', f'{symbol} already has an open position')

        # Correlation guard — max open positions on same underlying
        open_on_underlying = sum(1 for u in self._open_positions.values() if u == underlying)
        if open_on_underlying >= self.config.max_same_underlying_trades:
            return Denial('CORRELATION', f'{open_on_underlying} open on {underlying} (limit {self.config.max_same_underlying_trades})')

        return None

    # ── State mutations ──────────────────────────────────────────────────

    def record_trade_opened(self, track_id: str, underlying: str, symbol: str = '') -> None:
        """Called when a trade entry is confirmed."""
        self._trade_count += 1
        self._open_positions[track_id] = underlying
        if symbol:
            self._open_symbols.add(symbol)

    def record_trade_closed(self, track_id: str, pnl: float, was_sl: bool = False,
                            underlying: str = '', symbol: str = '') -> None:
        """Called when a trade is closed.

        Note: P&L and consecutive loss tracking is delegated to KillSwitchEngine.
        Caller must also call kill_switch.record_pnl() (or use the event bus).
        """
        # Remove from open tracking
        self._open_positions.pop(track_id, None)
        if symbol:
            self._open_symbols.discard(symbol)

        if was_sl and underlying:
            inst = self._instrument_state.setdefault(underlying, _InstrumentState())
            inst.last_sl_time = now_ist()
            inst.sl_count += 1

    def set_daily_pnl(self, pnl: float) -> None:
        """Restore P&L from persistence (server restart).

        Delegates to KillSwitchEngine which is the single source of truth.
        """
        self._kill_switch_engine.restore_state(
            daily_pnl=pnl, peak_pnl=max(pnl, 0.0), consecutive_losses=0,
        )

    def reset_daily(self) -> None:
        """Midnight reset — new trading day."""
        self._trade_count = 0
        self._instrument_state.clear()
        # Note: _open_positions and _open_symbols NOT cleared — overnight positions persist
        logger.info('Supervisor daily state reset')

    def force_reset_kill_switch(self) -> None:
        """Manual override — delegates to KillSwitchEngine."""
        self._kill_switch_engine.disarm(reason='Supervisor manual reset')
        logger.warning('Kill switch manually reset via supervisor')

    # ── Queries ──────────────────────────────────────────────────────────

    @property
    def daily_pnl(self) -> float:
        return self._kill_switch_engine.daily_pnl

    @property
    def is_kill_switch_active(self) -> bool:
        return self._kill_switch_engine.is_blocked()

    @property
    def trade_count(self) -> int:
        return self._trade_count

    @property
    def consecutive_losses(self) -> int:
        return self._kill_switch_engine.consecutive_losses

    @property
    def open_position_count(self) -> int:
        return len(self._open_positions)
