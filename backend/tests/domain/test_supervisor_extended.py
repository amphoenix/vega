"""
Trade Supervisor — extended tests ⭐⭐⭐⭐⭐

Covers compound scenarios: multiple gates interacting,
day rollover, force-reset, P&L restoration.
"""

import pytest
from app.domain.safety.kill_switch import KillSwitchEngine, KillSwitchConfig
from app.domain.supervisor.trade_supervisor import (
    TradeSupervisor, SupervisorConfig, Denial,
)


@pytest.fixture
def kill_switch():
    return KillSwitchEngine(KillSwitchConfig(
        daily_loss_soft=2000, daily_loss_hard=4000,
        drawdown_soft=3000, drawdown_hard=5000,
        loss_streak_soft=10, loss_streak_hard=15,
    ))


@pytest.fixture
def supervisor(kill_switch):
    return TradeSupervisor(SupervisorConfig(
        daily_loss_limit=2000,
        max_trades_per_day=5,
        max_consecutive_losses=3,
        cooldown_minutes=30,
        max_same_underlying_trades=2,
    ), kill_switch=kill_switch)


# ── Kill switch ──────────────────────────────────────────────────────────────

class TestKillSwitch:
    def test_gradual_loss_triggers_at_limit(self, supervisor, kill_switch):
        """Multiple small losses should eventually trigger kill switch."""
        for i in range(4):
            supervisor.record_trade_opened(f'T{i}', 'NIFTY')
            kill_switch.record_pnl(-400)
            supervisor.record_trade_closed(f'T{i}', pnl=-400, underlying='NIFTY')
        assert not supervisor.is_kill_switch_active  # -1600 < -2000

        supervisor.record_trade_opened('T4', 'NIFTY')
        kill_switch.record_pnl(-500)
        supervisor.record_trade_closed('T4', pnl=-500, underlying='NIFTY')
        assert supervisor.is_kill_switch_active  # -2100 ≥ -2000

    def test_wins_offset_losses(self, supervisor, kill_switch):
        """Profits should offset losses keeping budget alive."""
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-1500)
        supervisor.record_trade_closed('T1', pnl=-1500, underlying='NIFTY')
        assert not supervisor.is_kill_switch_active

        supervisor.record_trade_opened('T2', 'NIFTY')
        kill_switch.record_pnl(800)
        supervisor.record_trade_closed('T2', pnl=800, underlying='NIFTY')
        # Net: -700, still OK
        assert not supervisor.is_kill_switch_active
        assert supervisor.daily_pnl == -700.0

    def test_force_reset_kill_switch(self, supervisor, kill_switch):
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-2500)
        supervisor.record_trade_closed('T1', pnl=-2500, underlying='NIFTY')
        assert supervisor.is_kill_switch_active

        supervisor.force_reset_kill_switch()
        assert not supervisor.is_kill_switch_active
        # Should still allow trading after force reset
        denial = supervisor.check_permission('NIFTY', 'swing')
        assert denial is None  # force reset means we allow trading again

    def test_kill_switch_blocks_all_underlyings(self, supervisor, kill_switch):
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-2500)
        supervisor.record_trade_closed('T1', pnl=-2500, underlying='NIFTY')
        # All underlyings blocked
        for u in ['NIFTY', 'SENSEX', 'BANKNIFTY']:
            d = supervisor.check_permission(u, 'swing')
            assert d is not None and d.rule == 'KILL_SWITCH'


# ── Consecutive loss streak ──────────────────────────────────────────────────

class TestLossStreak:
    def test_streak_with_different_underlyings(self, supervisor, kill_switch):
        """Consecutive losses on different underlyings still count."""
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-100)
        supervisor.record_trade_closed('T1', pnl=-100, underlying='NIFTY')
        supervisor.record_trade_opened('T2', 'SENSEX')
        kill_switch.record_pnl(-100)
        supervisor.record_trade_closed('T2', pnl=-100, underlying='SENSEX')
        supervisor.record_trade_opened('T3', 'BANKNIFTY')
        kill_switch.record_pnl(-100)
        supervisor.record_trade_closed('T3', pnl=-100, underlying='BANKNIFTY')
        d = supervisor.check_permission('NIFTY', 'swing')
        assert d is not None and d.rule == 'LOSS_STREAK'

    def test_single_win_resets_entire_streak(self, supervisor, kill_switch):
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-100)
        supervisor.record_trade_closed('T1', pnl=-100, underlying='NIFTY')
        supervisor.record_trade_opened('T2', 'NIFTY')
        kill_switch.record_pnl(-100)
        supervisor.record_trade_closed('T2', pnl=-100, underlying='NIFTY')
        # 2 consecutive losses
        supervisor.record_trade_opened('T3', 'NIFTY')
        kill_switch.record_pnl(50)
        supervisor.record_trade_closed('T3', pnl=50, underlying='NIFTY')  # win!
        assert supervisor.consecutive_losses == 0
        assert supervisor.check_permission('NIFTY', 'swing') is None

    def test_breakeven_is_not_a_loss(self, supervisor, kill_switch):
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-100)
        supervisor.record_trade_closed('T1', pnl=-100, underlying='NIFTY')
        supervisor.record_trade_opened('T2', 'NIFTY')
        kill_switch.record_pnl(-100)
        supervisor.record_trade_closed('T2', pnl=-100, underlying='NIFTY')
        supervisor.record_trade_opened('T3', 'NIFTY')
        kill_switch.record_pnl(0)
        supervisor.record_trade_closed('T3', pnl=0, underlying='NIFTY')  # breakeven
        assert supervisor.consecutive_losses == 0  # P&L 0 is not negative


# ── Max trades ───────────────────────────────────────────────────────────────

class TestMaxTrades:
    def test_closed_trades_still_count(self, supervisor):
        """Even closed trades count toward max_trades_per_day."""
        for i in range(5):
            supervisor.record_trade_opened(f'T{i}', 'NIFTY')
            supervisor.record_trade_closed(f'T{i}', pnl=100, underlying='NIFTY')
        d = supervisor.check_permission('NIFTY', 'swing')
        assert d is not None and d.rule == 'MAX_TRADES'


# ── Correlation guard ────────────────────────────────────────────────────────

class TestCorrelation:
    def test_closing_frees_slot(self, supervisor):
        supervisor.record_trade_opened('T1', 'NIFTY')
        supervisor.record_trade_opened('T2', 'NIFTY')
        d = supervisor.check_permission('NIFTY', 'swing')
        assert d is not None and d.rule == 'CORRELATION'

        supervisor.record_trade_closed('T1', pnl=100, underlying='NIFTY')
        assert supervisor.check_permission('NIFTY', 'swing') is None

    def test_different_underlyings_independent(self, supervisor):
        supervisor.record_trade_opened('T1', 'NIFTY')
        supervisor.record_trade_opened('T2', 'NIFTY')
        # NIFTY full but SENSEX still fine
        assert supervisor.check_permission('SENSEX', 'swing') is None


# ── Day rollover ─────────────────────────────────────────────────────────────

class TestDayRollover:
    def test_full_reset(self, supervisor, kill_switch):
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-2500)
        supervisor.record_trade_closed('T1', pnl=-2500, was_sl=True, underlying='NIFTY')
        assert supervisor.is_kill_switch_active
        assert supervisor.trade_count == 1

        kill_switch.reset_daily()
        supervisor.reset_daily()
        assert not supervisor.is_kill_switch_active
        assert supervisor.trade_count == 0
        assert supervisor.consecutive_losses == 0
        assert supervisor.daily_pnl == 0.0

    def test_open_positions_persist_after_reset(self, supervisor):
        """Overnight positions survive midnight reset."""
        supervisor.record_trade_opened('OVERNIGHT', 'NIFTY')
        supervisor.reset_daily()
        # Position still counted for correlation
        assert supervisor.open_position_count == 1


# ── P&L restore (server restart) ────────────────────────────────────────────

class TestPnLRestore:
    def test_restore_triggers_kill_switch(self, supervisor, kill_switch):
        kill_switch.restore_state(daily_pnl=-2500.0, peak_pnl=0.0, consecutive_losses=0)
        assert supervisor.is_kill_switch_active

    def test_restore_healthy_pnl(self, supervisor):
        supervisor.set_daily_pnl(-500.0)
        assert not supervisor.is_kill_switch_active
        assert supervisor.daily_pnl == -500.0


# ── Compound scenarios ───────────────────────────────────────────────────────

class TestMaxOpenTrades:
    def test_blocks_when_limit_reached(self):
        s = TradeSupervisor(SupervisorConfig(max_open_trades=2))
        s.record_trade_opened('T1', 'NIFTY', symbol='NIFTY24JUN25000CE')
        s.record_trade_opened('T2', 'SENSEX', symbol='SENSEX24JUN80000PE')
        d = s.check_permission('BANKNIFTY', 'swing')
        assert d is not None
        assert d.rule == 'MAX_OPEN_TRADES'

    def test_allows_after_close(self):
        s = TradeSupervisor(SupervisorConfig(max_open_trades=2))
        s.record_trade_opened('T1', 'NIFTY', symbol='NIFTY24JUN25000CE')
        s.record_trade_opened('T2', 'SENSEX', symbol='SENSEX24JUN80000PE')
        s.record_trade_closed('T1', pnl=100, symbol='NIFTY24JUN25000CE')
        d = s.check_permission('BANKNIFTY', 'swing')
        assert d is None

    def test_default_limit_is_5(self):
        s = TradeSupervisor()
        assert s.config.max_open_trades == 5


class TestDuplicatePosition:
    def test_blocks_same_symbol(self):
        s = TradeSupervisor()
        s.record_trade_opened('T1', 'NIFTY', symbol='NIFTY24JUN25000CE')
        d = s.check_permission('NIFTY', 'swing', symbol='NIFTY24JUN25000CE')
        assert d is not None
        assert d.rule == 'DUPLICATE_POSITION'

    def test_allows_different_symbol_same_underlying(self):
        s = TradeSupervisor()
        s.record_trade_opened('T1', 'NIFTY', symbol='NIFTY24JUN25000CE')
        d = s.check_permission('NIFTY', 'swing', symbol='NIFTY24JUN25100CE')
        assert d is None

    def test_allows_after_close(self):
        s = TradeSupervisor()
        s.record_trade_opened('T1', 'NIFTY', symbol='NIFTY24JUN25000CE')
        s.record_trade_closed('T1', pnl=100, symbol='NIFTY24JUN25000CE')
        d = s.check_permission('NIFTY', 'swing', symbol='NIFTY24JUN25000CE')
        assert d is None

    def test_no_check_when_symbol_empty(self):
        """Backward compat — if symbol not passed, skip duplicate check."""
        s = TradeSupervisor()
        s.record_trade_opened('T1', 'NIFTY')
        d = s.check_permission('NIFTY', 'swing')
        assert d is None  # no symbol = no duplicate check


class TestCompound:
    def test_multiple_gates_first_match_wins(self, supervisor, kill_switch):
        """When multiple gates fail, the first checked rule is returned."""
        # Trigger kill switch
        supervisor.record_trade_opened('T1', 'NIFTY')
        kill_switch.record_pnl(-2500)
        supervisor.record_trade_closed('T1', pnl=-2500, underlying='NIFTY')
        # Kill switch is checked first
        d = supervisor.check_permission('NIFTY', 'swing', is_safe_hours=False)
        assert d.rule == 'KILL_SWITCH'  # not SESSION
