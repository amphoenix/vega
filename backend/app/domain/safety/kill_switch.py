"""
Kill Switch Engine — system-level safety circuit breaker.

Sits ABOVE the Trade Supervisor. The supervisor has a simple daily-P&L
kill switch; this engine provides:

  1. Two severity levels:
     - SOFT  → block new trades, keep monitoring existing positions
     - HARD  → block new trades AND request immediate exit of all positions

  2. Multiple independent triggers:
     - DAILY_LOSS     → cumulative daily net P&L exceeds threshold
     - DRAWDOWN       → drawdown from peak daily P&L exceeds threshold
     - LOSS_STREAK    → N consecutive losing trades
     - ERROR_RATE     → too many broker/system errors in a window
     - MANUAL         → operator explicitly arms the kill switch

  3. Persistent state — survives server restarts via state_store
  4. Audit trail — every activation/deactivation is logged
  5. Manual arm/disarm with reason tracking

Pure domain — no I/O, no framework imports.
The application layer handles persistence and event emission.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ...shared.logger import get_logger
from ...shared.time import datetime, now_ist

logger = get_logger('kill_switch')


class KillSwitchLevel(str, Enum):
    OFF  = 'off'
    SOFT = 'soft'   # no new trades
    HARD = 'hard'   # no new trades + close all positions


class TriggerType(str, Enum):
    DAILY_LOSS   = 'daily_loss'
    DRAWDOWN     = 'drawdown'
    LOSS_STREAK  = 'loss_streak'
    ERROR_RATE   = 'error_rate'
    MANUAL       = 'manual'


@dataclass(frozen=True, slots=True)
class KillSwitchActivation:
    """Record of a kill switch activation or deactivation."""
    timestamp: datetime
    level: KillSwitchLevel
    trigger: TriggerType
    reason: str
    value: float = 0.0        # the metric value that triggered it
    threshold: float = 0.0    # the threshold that was exceeded
    deactivated: bool = False # True for deactivation events

    def to_dict(self) -> dict[str, Any]:
        return {
            'timestamp': self.timestamp.isoformat(),
            'level': self.level.value,
            'trigger': self.trigger.value,
            'reason': self.reason,
            'value': self.value,
            'threshold': self.threshold,
            'deactivated': self.deactivated,
        }


@dataclass
class KillSwitchConfig:
    """Thresholds for automatic kill switch triggers."""
    # SOFT triggers
    daily_loss_soft: float = 2000.0       # ₹ net loss → SOFT
    drawdown_soft: float = 1500.0         # ₹ drop from peak → SOFT
    loss_streak_soft: int = 3             # consecutive losses → SOFT

    # HARD triggers (more severe thresholds)
    daily_loss_hard: float = 4000.0       # ₹ net loss → HARD
    drawdown_hard: float = 3000.0         # ₹ drop from peak → HARD
    loss_streak_hard: int = 5             # consecutive losses → HARD

    # Error rate
    max_errors_per_window: int = 5        # errors in window → SOFT
    error_window_minutes: int = 10


class KillSwitchEngine:
    """System-level kill switch — circuit breaker for the trading system.

    Thread-safe. Checks are O(1). The application layer calls `evaluate()`
    after each trade close or error, and `is_blocked()` before each trade.
    """

    def __init__(self, config: KillSwitchConfig | None = None) -> None:
        self.config = config or KillSwitchConfig()
        self._level: KillSwitchLevel = KillSwitchLevel.OFF
        self._active_triggers: dict[TriggerType, KillSwitchActivation] = {}
        self._history: list[KillSwitchActivation] = []
        self._lock = threading.Lock()

        # Tracked metrics
        self._daily_pnl: float = 0.0
        self._peak_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._error_timestamps: list[datetime] = []

    # ── Core queries ──────────────────────────────────────────────────────

    @property
    def level(self) -> KillSwitchLevel:
        with self._lock:
            return self._level

    @property
    def is_active(self) -> bool:
        return self.level != KillSwitchLevel.OFF

    @property
    def is_soft(self) -> bool:
        return self.level == KillSwitchLevel.SOFT

    @property
    def is_hard(self) -> bool:
        return self.level == KillSwitchLevel.HARD

    def is_blocked(self) -> bool:
        """Should new trades be blocked? True for SOFT or HARD."""
        return self.is_active

    def should_close_all(self) -> bool:
        """Should all existing positions be closed immediately? True for HARD only."""
        return self.is_hard

    # ── Metric updates ────────────────────────────────────────────────────

    def record_pnl(self, trade_pnl: float) -> list[KillSwitchActivation]:
        """Record a trade P&L and check thresholds.

        Returns list of new activations triggered (empty if none).
        """
        activations: list[KillSwitchActivation] = []
        with self._lock:
            self._daily_pnl += trade_pnl
            if self._daily_pnl > self._peak_pnl:
                self._peak_pnl = self._daily_pnl

            if trade_pnl < 0:
                self._consecutive_losses += 1
            else:
                self._consecutive_losses = 0

            activations = self._evaluate_thresholds()
        return activations

    def record_error(self) -> list[KillSwitchActivation]:
        """Record a system/broker error and check error rate threshold.

        Returns list of new activations triggered (empty if none).
        """
        activations: list[KillSwitchActivation] = []
        with self._lock:
            now = now_ist()
            self._error_timestamps.append(now)
            # Prune old errors outside the window
            cutoff = now.timestamp() - (self.config.error_window_minutes * 60)
            self._error_timestamps = [
                t for t in self._error_timestamps if t.timestamp() > cutoff
            ]
            activations = self._evaluate_thresholds()
        return activations

    # ── Manual controls ───────────────────────────────────────────────────

    def arm(self, level: KillSwitchLevel = KillSwitchLevel.SOFT,
            reason: str = 'Manual activation') -> KillSwitchActivation:
        """Manually arm the kill switch."""
        with self._lock:
            activation = KillSwitchActivation(
                timestamp=now_ist(),
                level=level,
                trigger=TriggerType.MANUAL,
                reason=reason,
            )
            self._activate(activation)
            return activation

    def disarm(self, reason: str = 'Manual deactivation') -> KillSwitchActivation:
        """Manually disarm the kill switch."""
        with self._lock:
            deactivation = KillSwitchActivation(
                timestamp=now_ist(),
                level=KillSwitchLevel.OFF,
                trigger=TriggerType.MANUAL,
                reason=reason,
                deactivated=True,
            )
            self._level = KillSwitchLevel.OFF
            self._active_triggers.clear()
            self._history.append(deactivation)
            logger.warning('Kill switch DISARMED: %s', reason)
            return deactivation

    # ── State management ──────────────────────────────────────────────────

    def restore_state(self, daily_pnl: float, peak_pnl: float,
                      consecutive_losses: int,
                      level: str = 'off') -> list[KillSwitchActivation]:
        """Restore state from persistence after server restart.

        Re-evaluates thresholds so the kill switch auto-activates if
        the restored state already exceeds limits.
        """
        with self._lock:
            self._daily_pnl = daily_pnl
            self._peak_pnl = peak_pnl
            self._consecutive_losses = consecutive_losses
            try:
                self._level = KillSwitchLevel(level)
            except ValueError:
                self._level = KillSwitchLevel.OFF
            return self._evaluate_thresholds()

    def reset_daily(self) -> None:
        """Midnight reset — new trading day."""
        with self._lock:
            self._daily_pnl = 0.0
            self._peak_pnl = 0.0
            self._consecutive_losses = 0
            self._error_timestamps.clear()
            self._level = KillSwitchLevel.OFF
            self._active_triggers.clear()
            logger.info('Kill switch engine daily reset')

    # ── Queries ───────────────────────────────────────────────────────────

    @property
    def daily_pnl(self) -> float:
        with self._lock:
            return self._daily_pnl

    @property
    def drawdown(self) -> float:
        """Current drawdown from peak (positive number = bad)."""
        with self._lock:
            return round(self._peak_pnl - self._daily_pnl, 2)

    @property
    def consecutive_losses(self) -> int:
        with self._lock:
            return self._consecutive_losses

    @property
    def active_triggers(self) -> list[KillSwitchActivation]:
        with self._lock:
            return list(self._active_triggers.values())

    @property
    def history(self) -> list[KillSwitchActivation]:
        with self._lock:
            return list(self._history)

    def status(self) -> dict[str, Any]:
        """Full status snapshot for API/dashboard."""
        with self._lock:
            return {
                'level': self._level.value,
                'is_active': self._level != KillSwitchLevel.OFF,
                'daily_pnl': self._daily_pnl,
                'peak_pnl': self._peak_pnl,
                'drawdown': round(self._peak_pnl - self._daily_pnl, 2),
                'consecutive_losses': self._consecutive_losses,
                'error_count': len(self._error_timestamps),
                'active_triggers': [a.to_dict() for a in self._active_triggers.values()],
                'history_count': len(self._history),
            }

    # ── Internal ──────────────────────────────────────────────────────────

    def _evaluate_thresholds(self) -> list[KillSwitchActivation]:
        """Check all thresholds and activate if exceeded. Must hold lock."""
        activations: list[KillSwitchActivation] = []

        # Daily loss
        if self._daily_pnl <= -abs(self.config.daily_loss_hard):
            a = self._try_activate(TriggerType.DAILY_LOSS, KillSwitchLevel.HARD,
                                   f'Daily loss ₹{self._daily_pnl:.0f} exceeds hard limit',
                                   self._daily_pnl, self.config.daily_loss_hard)
            if a:
                activations.append(a)
        elif self._daily_pnl <= -abs(self.config.daily_loss_soft):
            a = self._try_activate(TriggerType.DAILY_LOSS, KillSwitchLevel.SOFT,
                                   f'Daily loss ₹{self._daily_pnl:.0f} exceeds soft limit',
                                   self._daily_pnl, self.config.daily_loss_soft)
            if a:
                activations.append(a)

        # Drawdown
        dd = self._peak_pnl - self._daily_pnl
        if dd >= self.config.drawdown_hard:
            a = self._try_activate(TriggerType.DRAWDOWN, KillSwitchLevel.HARD,
                                   f'Drawdown ₹{dd:.0f} from peak exceeds hard limit',
                                   dd, self.config.drawdown_hard)
            if a:
                activations.append(a)
        elif dd >= self.config.drawdown_soft:
            a = self._try_activate(TriggerType.DRAWDOWN, KillSwitchLevel.SOFT,
                                   f'Drawdown ₹{dd:.0f} from peak exceeds soft limit',
                                   dd, self.config.drawdown_soft)
            if a:
                activations.append(a)

        # Loss streak
        if self._consecutive_losses >= self.config.loss_streak_hard:
            a = self._try_activate(TriggerType.LOSS_STREAK, KillSwitchLevel.HARD,
                                   f'{self._consecutive_losses} consecutive losses (hard limit)',
                                   float(self._consecutive_losses), float(self.config.loss_streak_hard))
            if a:
                activations.append(a)
        elif self._consecutive_losses >= self.config.loss_streak_soft:
            a = self._try_activate(TriggerType.LOSS_STREAK, KillSwitchLevel.SOFT,
                                   f'{self._consecutive_losses} consecutive losses (soft limit)',
                                   float(self._consecutive_losses), float(self.config.loss_streak_soft))
            if a:
                activations.append(a)

        # Error rate
        if len(self._error_timestamps) >= self.config.max_errors_per_window:
            a = self._try_activate(TriggerType.ERROR_RATE, KillSwitchLevel.SOFT,
                                   f'{len(self._error_timestamps)} errors in {self.config.error_window_minutes}min',
                                   float(len(self._error_timestamps)),
                                   float(self.config.max_errors_per_window))
            if a:
                activations.append(a)

        return activations

    def _try_activate(self, trigger: TriggerType, level: KillSwitchLevel,
                      reason: str, value: float, threshold: float,
                      ) -> KillSwitchActivation | None:
        """Activate if this trigger isn't already active at this level or higher.

        Returns the activation record, or None if already active.
        """
        existing = self._active_triggers.get(trigger)
        if existing and existing.level == level:
            return None  # already active at same level
        # Upgrade: SOFT→HARD is allowed
        if existing and existing.level == KillSwitchLevel.HARD:
            return None  # already at HARD, can't go higher

        activation = KillSwitchActivation(
            timestamp=now_ist(),
            level=level,
            trigger=trigger,
            reason=reason,
            value=value,
            threshold=threshold,
        )
        self._activate(activation)
        return activation

    def _activate(self, activation: KillSwitchActivation) -> None:
        """Apply an activation. Must hold lock."""
        self._active_triggers[activation.trigger] = activation
        self._history.append(activation)

        # Level is the highest among all active triggers
        new_level = max(
            (a.level for a in self._active_triggers.values()),
            key=lambda l: ['off', 'soft', 'hard'].index(l.value),
        )
        if new_level != self._level:
            old = self._level
            self._level = new_level
            logger.warning('Kill switch %s → %s: [%s] %s',
                           old.value, new_level.value,
                           activation.trigger.value, activation.reason)
        else:
            logger.warning('Kill switch trigger added [%s] %s (level stays %s)',
                           activation.trigger.value, activation.reason, self._level.value)
