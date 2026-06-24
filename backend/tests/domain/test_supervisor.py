"""Tests for Trade Supervisor."""

from app.domain.safety.kill_switch import KillSwitchEngine, KillSwitchConfig
from app.domain.supervisor.trade_supervisor import TradeSupervisor, SupervisorConfig


def test_allows_first_trade():
    s = TradeSupervisor()
    assert s.check_permission('NIFTY', 'swing') is None


def test_kill_switch_after_loss():
    ks = KillSwitchEngine(KillSwitchConfig(daily_loss_soft=1000, daily_loss_hard=2000))
    s = TradeSupervisor(SupervisorConfig(daily_loss_limit=1000), kill_switch=ks)
    s.record_trade_opened('T1', 'NIFTY')
    ks.record_pnl(-1100)
    s.record_trade_closed('T1', pnl=-1100, was_sl=True, underlying='NIFTY')
    denial = s.check_permission('NIFTY', 'swing')
    assert denial is not None
    assert denial.rule == 'KILL_SWITCH'


def test_max_trades():
    s = TradeSupervisor(SupervisorConfig(max_trades_per_day=2))
    s.record_trade_opened('T1', 'NIFTY')
    s.record_trade_opened('T2', 'SENSEX')
    denial = s.check_permission('NIFTY', 'swing')
    assert denial is not None
    assert denial.rule == 'MAX_TRADES'


def test_loss_streak():
    ks = KillSwitchEngine(KillSwitchConfig(
        daily_loss_soft=9999, daily_loss_hard=9999,
        loss_streak_soft=10, loss_streak_hard=20,
    ))
    s = TradeSupervisor(SupervisorConfig(max_consecutive_losses=2), kill_switch=ks)
    s.record_trade_opened('T1', 'NIFTY')
    ks.record_pnl(-100)
    s.record_trade_closed('T1', pnl=-100, underlying='NIFTY')
    s.record_trade_opened('T2', 'NIFTY')
    ks.record_pnl(-100)
    s.record_trade_closed('T2', pnl=-100, underlying='NIFTY')
    denial = s.check_permission('NIFTY', 'swing')
    assert denial is not None
    assert denial.rule == 'LOSS_STREAK'


def test_winning_trade_resets_streak():
    ks = KillSwitchEngine(KillSwitchConfig(
        daily_loss_soft=9999, daily_loss_hard=9999,
        loss_streak_soft=10, loss_streak_hard=20,
    ))
    s = TradeSupervisor(SupervisorConfig(max_consecutive_losses=2), kill_switch=ks)
    s.record_trade_opened('T1', 'NIFTY')
    ks.record_pnl(-100)
    s.record_trade_closed('T1', pnl=-100, underlying='NIFTY')
    s.record_trade_opened('T2', 'NIFTY')
    ks.record_pnl(200)
    s.record_trade_closed('T2', pnl=200, underlying='NIFTY')
    # streak reset to 0
    assert s.check_permission('NIFTY', 'swing') is None


def test_session_block():
    s = TradeSupervisor(SupervisorConfig(allow_outside_safe_hours=False))
    denial = s.check_permission('NIFTY', 'swing', is_safe_hours=False)
    assert denial is not None
    assert denial.rule == 'SESSION'


def test_daily_reset():
    ks = KillSwitchEngine(KillSwitchConfig(daily_loss_soft=500, daily_loss_hard=1000))
    s = TradeSupervisor(SupervisorConfig(daily_loss_limit=500), kill_switch=ks)
    s.record_trade_opened('T1', 'NIFTY')
    ks.record_pnl(-600)
    s.record_trade_closed('T1', pnl=-600, was_sl=True, underlying='NIFTY')
    assert s.is_kill_switch_active
    ks.reset_daily()
    s.reset_daily()
    assert not s.is_kill_switch_active
    assert s.trade_count == 0


def test_correlation_guard():
    s = TradeSupervisor(SupervisorConfig(max_same_underlying_trades=1))
    s.record_trade_opened('T1', 'NIFTY')
    denial = s.check_permission('NIFTY', 'swing')
    assert denial is not None
    assert denial.rule == 'CORRELATION'
    # Different underlying is fine
    assert s.check_permission('SENSEX', 'swing') is None
