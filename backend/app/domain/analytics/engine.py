"""
Analytics Engine — production analytics (Edge Layer).

Answers:
  - Why did I lose today?
  - Win rate?  Profit factor?  Expectancy?
  - Max drawdown?  Drawdown duration?
  - Best regime?  Worst regime?
  - Streak analysis?

Takes a list of closed Trade objects, computes metrics.
Pure domain — no I/O, no DB, no HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from ...shared.time import date, timedelta
from typing import Sequence

from ..entities.trade import Trade, TradeState
from ..regime.regime_engine import Regime
from ..value_objects.market import MarketType


# ═════════════════════════════════════════════════════════════════════════════
# Data containers
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class CoreMetrics:
    """Top-line trading performance numbers."""
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    breakeven: int = 0
    win_rate: float = 0.0              # winners / total (0-1)
    gross_pnl: float = 0.0            # sum of all realized P&L
    avg_win: float = 0.0              # average winning trade P&L
    avg_loss: float = 0.0             # average losing trade P&L (negative)
    largest_win: float = 0.0
    largest_loss: float = 0.0         # negative
    profit_factor: float = 0.0        # gross_wins / abs(gross_losses)
    expectancy: float = 0.0           # (win_rate * avg_win) + ((1-win_rate) * avg_loss)
    avg_hold_minutes: float = 0.0     # average time from fill to close


@dataclass(frozen=True, slots=True)
class DrawdownMetrics:
    """Drawdown analysis from equity curve."""
    max_drawdown: float = 0.0         # largest peak-to-trough in absolute terms
    max_drawdown_pct: float = 0.0     # as percentage of peak equity
    current_drawdown: float = 0.0
    peak_equity: float = 0.0
    trades_in_max_dd: int = 0         # how many trades during worst drawdown


@dataclass(frozen=True, slots=True)
class StreakMetrics:
    """Win/loss streak analysis."""
    current_streak: int = 0           # positive = wins, negative = losses
    max_win_streak: int = 0
    max_loss_streak: int = 0


@dataclass(frozen=True, slots=True)
class RegimePerformance:
    """Performance breakdown by market regime."""
    regime: str = ''
    trades: int = 0
    win_rate: float = 0.0
    gross_pnl: float = 0.0
    avg_pnl: float = 0.0


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """End-of-day / end-of-session summary."""
    date: str = ''
    core: CoreMetrics = field(default_factory=CoreMetrics)
    drawdown: DrawdownMetrics = field(default_factory=DrawdownMetrics)
    streaks: StreakMetrics = field(default_factory=StreakMetrics)
    by_regime: list[RegimePerformance] = field(default_factory=list)
    by_mode: dict[str, CoreMetrics] = field(default_factory=dict)
    by_market: dict[str, CoreMetrics] = field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════════
# Analytics Engine
# ═════════════════════════════════════════════════════════════════════════════

class AnalyticsEngine:
    """Computes production analytics from closed trades.

    Stateless computation — pass in trades, get back metrics.
    No I/O, no persistence.
    """

    # ── Core Metrics ────────────────────────────────────────────────────

    @staticmethod
    def compute_core(trades: Sequence[Trade]) -> CoreMetrics:
        """Compute top-line metrics from closed trades."""
        closed = [t for t in trades if t.state == TradeState.CLOSED]
        if not closed:
            return CoreMetrics()

        pnls = [t.realized_gross_pnl for t in closed]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        breakeven = [p for p in pnls if p == 0]

        gross_wins = sum(winners) if winners else 0.0
        gross_losses = abs(sum(losers)) if losers else 0.0

        total = len(closed)
        win_rate = len(winners) / total if total else 0.0
        avg_win = (gross_wins / len(winners)) if winners else 0.0
        avg_loss = (sum(losers) / len(losers)) if losers else 0.0  # negative

        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float('inf') if gross_wins > 0 else 0.0
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

        # Average hold time
        hold_minutes = []
        for t in closed:
            if t.filled_at and t.closed_at:
                delta = (t.closed_at - t.filled_at).total_seconds() / 60.0
                hold_minutes.append(delta)
        avg_hold = (sum(hold_minutes) / len(hold_minutes)) if hold_minutes else 0.0

        return CoreMetrics(
            total_trades=total,
            winners=len(winners),
            losers=len(losers),
            breakeven=len(breakeven),
            win_rate=round(win_rate, 4),
            gross_pnl=round(sum(pnls), 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            largest_win=round(max(winners), 2) if winners else 0.0,
            largest_loss=round(min(losers), 2) if losers else 0.0,
            profit_factor=round(profit_factor, 2) if profit_factor != float('inf') else float('inf'),
            expectancy=round(expectancy, 2),
            avg_hold_minutes=round(avg_hold, 1),
        )

    # ── Drawdown ────────────────────────────────────────────────────────

    @staticmethod
    def compute_drawdown(trades: Sequence[Trade], starting_capital: float = 0.0) -> DrawdownMetrics:
        """Compute drawdown metrics from equity curve.

        Trades should be in chronological order (by closed_at).
        """
        closed = [t for t in trades if t.state == TradeState.CLOSED]
        if not closed:
            return DrawdownMetrics()

        # Build equity curve
        equity = starting_capital
        peak = equity
        max_dd = 0.0
        max_dd_peak = equity
        dd_start_idx = 0
        worst_dd_trades = 0
        current_dd_trades = 0

        for i, t in enumerate(closed):
            equity += t.realized_gross_pnl

            if equity > peak:
                peak = equity
                current_dd_trades = 0
            else:
                current_dd_trades += 1

            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
                max_dd_peak = peak
                worst_dd_trades = current_dd_trades

        current_dd = peak - equity
        max_dd_pct = (max_dd / max_dd_peak * 100) if max_dd_peak > 0 else 0.0

        return DrawdownMetrics(
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            current_drawdown=round(current_dd, 2),
            peak_equity=round(peak, 2),
            trades_in_max_dd=worst_dd_trades,
        )

    # ── Streaks ─────────────────────────────────────────────────────────

    @staticmethod
    def compute_streaks(trades: Sequence[Trade]) -> StreakMetrics:
        """Compute win/loss streak analysis."""
        closed = [t for t in trades if t.state == TradeState.CLOSED]
        if not closed:
            return StreakMetrics()

        current = 0
        max_win = 0
        max_loss = 0

        for t in closed:
            pnl = t.realized_gross_pnl
            if pnl > 0:
                current = current + 1 if current > 0 else 1
            elif pnl < 0:
                current = current - 1 if current < 0 else -1
            # breakeven doesn't break streak

            if current > max_win:
                max_win = current
            if current < max_loss:
                max_loss = current

        return StreakMetrics(
            current_streak=current,
            max_win_streak=max_win,
            max_loss_streak=abs(max_loss),
        )

    # ── Regime Performance ──────────────────────────────────────────────

    @staticmethod
    def compute_by_regime(trades: Sequence[Trade]) -> list[RegimePerformance]:
        """Break down performance by market regime.

        Expects trade.metadata['regime'] to be set (str matching Regime enum value).
        Trades without regime metadata are bucketed as 'UNKNOWN'.
        """
        closed = [t for t in trades if t.state == TradeState.CLOSED]
        if not closed:
            return []

        buckets: dict[str, list[float]] = {}
        for t in closed:
            regime = t.metadata.get('regime', 'UNKNOWN')
            buckets.setdefault(regime, []).append(t.realized_gross_pnl)

        results = []
        for regime, pnls in sorted(buckets.items()):
            wins = [p for p in pnls if p > 0]
            total = len(pnls)
            results.append(RegimePerformance(
                regime=regime,
                trades=total,
                win_rate=round(len(wins) / total, 4) if total else 0.0,
                gross_pnl=round(sum(pnls), 2),
                avg_pnl=round(sum(pnls) / total, 2) if total else 0.0,
            ))

        return results

    # ── Group by mode / market ──────────────────────────────────────────

    @staticmethod
    def compute_by_mode(trades: Sequence[Trade]) -> dict[str, CoreMetrics]:
        """Compute CoreMetrics per trade_mode (swing, scalp, research)."""
        buckets: dict[str, list[Trade]] = {}
        for t in trades:
            if t.state == TradeState.CLOSED:
                buckets.setdefault(t.trade_mode, []).append(t)
        return {mode: AnalyticsEngine.compute_core(ts) for mode, ts in buckets.items()}

    @staticmethod
    def compute_by_market(trades: Sequence[Trade]) -> dict[str, CoreMetrics]:
        """Compute CoreMetrics per market type."""
        buckets: dict[str, list[Trade]] = {}
        for t in trades:
            if t.state == TradeState.CLOSED:
                buckets.setdefault(t.market_type.value, []).append(t)
        return {mk: AnalyticsEngine.compute_core(ts) for mk, ts in buckets.items()}

    # ── Session Summary ─────────────────────────────────────────────────

    @staticmethod
    def session_summary(
        trades: Sequence[Trade],
        session_date: str = '',
        starting_capital: float = 0.0,
    ) -> SessionSummary:
        """Full end-of-session analytics snapshot.

        One call to rule them all — computes core, drawdown, streaks,
        regime breakdown, mode breakdown, market breakdown.
        """
        return SessionSummary(
            date=session_date,
            core=AnalyticsEngine.compute_core(trades),
            drawdown=AnalyticsEngine.compute_drawdown(trades, starting_capital),
            streaks=AnalyticsEngine.compute_streaks(trades),
            by_regime=AnalyticsEngine.compute_by_regime(trades),
            by_mode=AnalyticsEngine.compute_by_mode(trades),
            by_market=AnalyticsEngine.compute_by_market(trades),
        )
