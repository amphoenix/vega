"""
Analytics Engine — tests for production analytics.

Tests core metrics, drawdown, streaks, regime breakdown,
mode/market breakdown, and session summary.
"""

from datetime import datetime, timedelta

from app.domain.analytics.engine import (
    AnalyticsEngine,
)
from app.domain.entities.trade import Trade
from app.domain.value_objects.instrument import crypto_instrument
from app.shared.time import IST

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_trade(
    pnl: float,
    trade_id: str = '',
    mode: str = 'swing',
    regime: str = '',
    hold_minutes: float = 5.0,
    instrument=None,
) -> Trade:
    """Create a closed trade with a specific realized P&L."""
    t = Trade(
        trade_id=trade_id or f'T-{id(pnl)}',
        trade_mode=mode,
        direction='CE',
        qty=75,
        instrument=instrument,
        metadata={'regime': regime} if regime else {},
    )
    base = datetime(2026, 6, 18, 10, 0, tzinfo=IST)
    t.fill(100.0)
    t.filled_at = base
    # Set exit price so realized_gross_pnl = pnl
    # pnl = (exit - entry) * qty → exit = entry + pnl/qty
    exit_price = 100.0 + (pnl / 75)
    t.close(exit_price, reason='test')
    t.closed_at = base + timedelta(minutes=hold_minutes)
    return t


def _batch(pnls: list[float], **kwargs) -> list[Trade]:
    return [_make_trade(p, trade_id=f'T-{i}', **kwargs) for i, p in enumerate(pnls)]


# ═════════════════════════════════════════════════════════════════════════════
# Core Metrics
# ═════════════════════════════════════════════════════════════════════════════

class TestCoreMetrics:
    def test_empty(self):
        m = AnalyticsEngine.compute_core([])
        assert m.total_trades == 0
        assert m.win_rate == 0.0

    def test_all_winners(self):
        trades = _batch([100, 200, 300])
        m = AnalyticsEngine.compute_core(trades)
        assert m.total_trades == 3
        assert m.winners == 3
        assert m.losers == 0
        assert m.win_rate == 1.0
        assert m.gross_pnl == 600.0
        assert m.avg_win == 200.0
        assert m.largest_win == 300.0
        assert m.profit_factor == float('inf')

    def test_all_losers(self):
        trades = _batch([-100, -200, -300])
        m = AnalyticsEngine.compute_core(trades)
        assert m.winners == 0
        assert m.losers == 3
        assert m.win_rate == 0.0
        assert m.gross_pnl == -600.0
        assert m.avg_loss == -200.0
        assert m.largest_loss == -300.0
        assert m.profit_factor == 0.0

    def test_mixed(self):
        trades = _batch([200, -100, 300, -50, 0])
        m = AnalyticsEngine.compute_core(trades)
        assert m.total_trades == 5
        assert m.winners == 2
        assert m.losers == 2
        assert m.breakeven == 1
        assert m.win_rate == 0.4
        assert m.gross_pnl == 350.0
        assert m.avg_win == 250.0    # (200+300)/2
        assert m.avg_loss == -75.0   # (-100+-50)/2
        pf = 500 / 150  # gross_wins / gross_losses
        assert abs(m.profit_factor - round(pf, 2)) < 0.01

    def test_expectancy(self):
        trades = _batch([200, -100, 300, -50])
        m = AnalyticsEngine.compute_core(trades)
        # expectancy = (wr * avg_win) + ((1-wr) * avg_loss)
        expected = (0.5 * 250) + (0.5 * -75)
        assert abs(m.expectancy - round(expected, 2)) < 0.01

    def test_hold_time(self):
        trades = _batch([100, 200], hold_minutes=10.0)
        m = AnalyticsEngine.compute_core(trades)
        assert m.avg_hold_minutes == 10.0

    def test_ignores_non_closed(self):
        t = Trade(trade_id='OPEN-1', direction='CE', qty=75)
        t.fill(100.0)
        # Not closed — should be ignored
        trades = _batch([100]) + [t]
        m = AnalyticsEngine.compute_core(trades)
        assert m.total_trades == 1

    def test_ignores_cancelled(self):
        t = Trade(trade_id='CANCEL-1', direction='CE', qty=75)
        t.cancel('test')
        trades = _batch([100]) + [t]
        m = AnalyticsEngine.compute_core(trades)
        assert m.total_trades == 1


# ═════════════════════════════════════════════════════════════════════════════
# Drawdown
# ═════════════════════════════════════════════════════════════════════════════

class TestDrawdown:
    def test_empty(self):
        dd = AnalyticsEngine.compute_drawdown([])
        assert dd.max_drawdown == 0.0

    def test_no_drawdown(self):
        trades = _batch([100, 200, 300])
        dd = AnalyticsEngine.compute_drawdown(trades, starting_capital=10000)
        assert dd.max_drawdown == 0.0
        assert dd.current_drawdown == 0.0
        assert dd.peak_equity == 10600.0

    def test_simple_drawdown(self):
        # Start 10000, +500=10500 (peak), -200=10300, -300=10000
        trades = _batch([500, -200, -300])
        dd = AnalyticsEngine.compute_drawdown(trades, starting_capital=10000)
        assert dd.max_drawdown == 500.0   # 10500 → 10000
        assert dd.peak_equity == 10500.0
        assert dd.current_drawdown == 500.0

    def test_recovery_resets_dd(self):
        # 10000 → +500=10500 (peak) → -200=10300 → +400=10700 (new peak) → -100=10600
        trades = _batch([500, -200, 400, -100])
        dd = AnalyticsEngine.compute_drawdown(trades, starting_capital=10000)
        assert dd.max_drawdown == 200.0    # 10500→10300 (first dd=200 > second dd=100)
        assert dd.current_drawdown == 100.0  # from peak 10700 → 10600
        assert dd.peak_equity == 10700.0

    def test_drawdown_pct(self):
        trades = _batch([1000, -500])
        dd = AnalyticsEngine.compute_drawdown(trades, starting_capital=10000)
        # peak = 11000, dd = 500, pct = 500/11000*100 ≈ 4.55%
        assert dd.max_drawdown_pct == round(500 / 11000 * 100, 2)

    def test_zero_starting_capital(self):
        trades = _batch([100, -50])
        dd = AnalyticsEngine.compute_drawdown(trades, starting_capital=0)
        assert dd.peak_equity == 100.0
        assert dd.max_drawdown == 50.0


# ═════════════════════════════════════════════════════════════════════════════
# Streaks
# ═════════════════════════════════════════════════════════════════════════════

class TestStreaks:
    def test_empty(self):
        s = AnalyticsEngine.compute_streaks([])
        assert s.max_win_streak == 0
        assert s.max_loss_streak == 0

    def test_all_wins(self):
        trades = _batch([100, 200, 300])
        s = AnalyticsEngine.compute_streaks(trades)
        assert s.max_win_streak == 3
        assert s.max_loss_streak == 0
        assert s.current_streak == 3

    def test_all_losses(self):
        trades = _batch([-100, -200, -300])
        s = AnalyticsEngine.compute_streaks(trades)
        assert s.max_win_streak == 0
        assert s.max_loss_streak == 3
        assert s.current_streak == -3

    def test_mixed(self):
        # W, W, W, L, L, W, L
        trades = _batch([100, 100, 100, -50, -50, 100, -50])
        s = AnalyticsEngine.compute_streaks(trades)
        assert s.max_win_streak == 3
        assert s.max_loss_streak == 2
        assert s.current_streak == -1

    def test_breakeven_doesnt_break_streak(self):
        # W, W, BE, W → streak should be 4 (breakeven doesn't reset)
        trades = _batch([100, 100, 0, 100])
        s = AnalyticsEngine.compute_streaks(trades)
        assert s.current_streak == 3  # breakeven doesn't count as win


# ═════════════════════════════════════════════════════════════════════════════
# Regime Performance
# ═════════════════════════════════════════════════════════════════════════════

class TestRegimePerformance:
    def test_empty(self):
        assert AnalyticsEngine.compute_by_regime([]) == []

    def test_single_regime(self):
        trades = _batch([100, -50, 200], regime='TRENDING_BULL')
        rp = AnalyticsEngine.compute_by_regime(trades)
        assert len(rp) == 1
        assert rp[0].regime == 'TRENDING_BULL'
        assert rp[0].trades == 3
        assert rp[0].gross_pnl == 250.0
        assert rp[0].win_rate == round(2/3, 4)

    def test_multi_regime(self):
        t1 = _make_trade(200, 'T-1', regime='TRENDING_BULL')
        t2 = _make_trade(-100, 'T-2', regime='HIGH_VOLATILITY')
        t3 = _make_trade(150, 'T-3', regime='TRENDING_BULL')
        t4 = _make_trade(-50, 'T-4', regime='HIGH_VOLATILITY')
        rp = AnalyticsEngine.compute_by_regime([t1, t2, t3, t4])
        assert len(rp) == 2

        bull = next(r for r in rp if r.regime == 'TRENDING_BULL')
        assert bull.trades == 2
        assert bull.gross_pnl == 350.0
        assert bull.win_rate == 1.0

        hv = next(r for r in rp if r.regime == 'HIGH_VOLATILITY')
        assert hv.trades == 2
        assert hv.gross_pnl == -150.0
        assert hv.win_rate == 0.0

    def test_unknown_regime(self):
        trades = _batch([100])  # no regime set
        rp = AnalyticsEngine.compute_by_regime(trades)
        assert rp[0].regime == 'UNKNOWN'


# ═════════════════════════════════════════════════════════════════════════════
# By Mode / By Market
# ═════════════════════════════════════════════════════════════════════════════

class TestByMode:
    def test_single_mode(self):
        trades = _batch([100, -50], mode='swing')
        by_mode = AnalyticsEngine.compute_by_mode(trades)
        assert 'swing' in by_mode
        assert by_mode['swing'].total_trades == 2

    def test_multi_mode(self):
        t1 = _make_trade(100, 'S-1', mode='swing')
        t2 = _make_trade(-50, 'C-1', mode='scalp')
        t3 = _make_trade(200, 'C-2', mode='scalp')
        by_mode = AnalyticsEngine.compute_by_mode([t1, t2, t3])
        assert by_mode['swing'].total_trades == 1
        assert by_mode['scalp'].total_trades == 2
        assert by_mode['scalp'].gross_pnl == 150.0


class TestByMarket:
    def test_multi_market(self):
        fo_trade = _make_trade(100, 'FO-1')  # default = INDIAN_FO
        crypto_trade = _make_trade(200, 'CR-1', instrument=crypto_instrument('BTC/USDT'))
        by_market = AnalyticsEngine.compute_by_market([fo_trade, crypto_trade])
        assert 'indian_fo' in by_market
        assert 'crypto' in by_market
        assert by_market['indian_fo'].gross_pnl == 100.0
        assert by_market['crypto'].gross_pnl == 200.0


# ═════════════════════════════════════════════════════════════════════════════
# Session Summary
# ═════════════════════════════════════════════════════════════════════════════

class TestSessionSummary:
    def test_full_summary(self):
        trades = [
            _make_trade(200, 'T-1', mode='swing', regime='TRENDING_BULL'),
            _make_trade(-100, 'T-2', mode='swing', regime='HIGH_VOLATILITY'),
            _make_trade(300, 'T-3', mode='scalp', regime='TRENDING_BULL'),
            _make_trade(-50, 'T-4', mode='scalp', regime='RANGING'),
            _make_trade(150, 'T-5', mode='swing', regime='TRENDING_BULL'),
        ]
        summary = AnalyticsEngine.session_summary(
            trades, session_date='2026-06-18', starting_capital=50000,
        )

        # Core
        assert summary.core.total_trades == 5
        assert summary.core.winners == 3
        assert summary.core.losers == 2
        assert summary.core.gross_pnl == 500.0

        # Drawdown computed
        assert summary.drawdown.peak_equity > 0

        # Streaks
        assert summary.streaks.max_win_streak >= 1

        # Regime breakdown
        assert len(summary.by_regime) == 3  # TRENDING_BULL, HIGH_VOLATILITY, RANGING
        bull = next(r for r in summary.by_regime if r.regime == 'TRENDING_BULL')
        assert bull.trades == 3
        assert bull.win_rate == 1.0

        # Mode breakdown
        assert 'swing' in summary.by_mode
        assert 'scalp' in summary.by_mode
        assert summary.by_mode['swing'].total_trades == 3
        assert summary.by_mode['scalp'].total_trades == 2

        # Market breakdown (all F&O by default)
        assert 'indian_fo' in summary.by_market

        assert summary.date == '2026-06-18'

    def test_empty_session(self):
        summary = AnalyticsEngine.session_summary([])
        assert summary.core.total_trades == 0
        assert summary.by_regime == []
        assert summary.by_mode == {}


# ═════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_single_trade_win(self):
        trades = _batch([500])
        m = AnalyticsEngine.compute_core(trades)
        assert m.total_trades == 1
        assert m.win_rate == 1.0
        assert m.profit_factor == float('inf')
        assert m.expectancy == 500.0

    def test_single_trade_loss(self):
        trades = _batch([-500])
        m = AnalyticsEngine.compute_core(trades)
        assert m.total_trades == 1
        assert m.win_rate == 0.0
        assert m.profit_factor == 0.0
        assert m.expectancy == -500.0

    def test_large_dataset(self):
        """Performance check with 1000 trades."""
        import random
        random.seed(42)
        pnls = [random.uniform(-200, 300) for _ in range(1000)]
        trades = _batch(pnls)
        summary = AnalyticsEngine.session_summary(trades, starting_capital=100000)
        assert summary.core.total_trades == 1000
        assert summary.drawdown.peak_equity > 0
