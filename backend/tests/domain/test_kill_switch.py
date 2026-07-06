"""
Tests for Kill Switch Engine — system-level safety circuit breaker.
"""

from app.domain.safety.kill_switch import (
    KillSwitchConfig,
    KillSwitchEngine,
    KillSwitchLevel,
    TriggerType,
)


def _engine(**overrides) -> KillSwitchEngine:
    defaults = dict(
        daily_loss_soft=2000, daily_loss_hard=4000,
        drawdown_soft=1500, drawdown_hard=3000,
        loss_streak_soft=3, loss_streak_hard=5,
        max_errors_per_window=5, error_window_minutes=10,
    )
    defaults.update(overrides)
    return KillSwitchEngine(KillSwitchConfig(**defaults))


class TestInitialState:
    def test_starts_off(self):
        e = _engine()
        assert e.level == KillSwitchLevel.OFF
        assert not e.is_active
        assert not e.is_blocked()
        assert not e.should_close_all()

    def test_zero_metrics(self):
        e = _engine()
        assert e.daily_pnl == 0.0
        assert e.drawdown == 0.0
        assert e.consecutive_losses == 0


class TestDailyLoss:
    def test_soft_trigger(self):
        e = _engine()
        acts = e.record_pnl(-2100)  # exceeds soft limit of 2000
        assert e.is_soft
        assert e.is_blocked()
        assert not e.should_close_all()
        assert len(acts) >= 1  # daily_loss + possibly drawdown
        dl = [a for a in acts if a.trigger == TriggerType.DAILY_LOSS]
        assert len(dl) == 1
        assert dl[0].level == KillSwitchLevel.SOFT

    def test_hard_trigger(self):
        e = _engine()
        acts = e.record_pnl(-4100)  # exceeds hard limit of 4000
        assert e.is_hard
        assert e.should_close_all()
        # Should have both SOFT and HARD in the same evaluation? No,
        # HARD supersedes SOFT for same trigger
        hard_acts = [a for a in acts if a.level == KillSwitchLevel.HARD]
        assert len(hard_acts) >= 1

    def test_incremental_to_hard(self):
        e = _engine()
        e.record_pnl(-2100)  # SOFT
        assert e.is_soft
        acts = e.record_pnl(-2100)  # total -4200 → HARD
        assert e.is_hard

    def test_no_trigger_below_threshold(self):
        e = _engine()
        acts = e.record_pnl(-500)
        assert not e.is_active
        assert len(acts) == 0


class TestDrawdown:
    def test_soft_drawdown(self):
        e = _engine()
        e.record_pnl(1000)   # peak = 1000
        acts = e.record_pnl(-2600)  # daily = -1600, drawdown = 1000 - (-1600) = 2600 > soft(1500)
        assert e.is_active
        dd_acts = [a for a in acts if a.trigger == TriggerType.DRAWDOWN]
        assert len(dd_acts) >= 1

    def test_hard_drawdown(self):
        e = _engine()
        e.record_pnl(2000)    # peak = 2000
        e.record_pnl(-5100)   # daily = -3100, drawdown = 2000 - (-3100) = 5100 > hard(3000)
        assert e.is_hard

    def test_no_drawdown_on_flat(self):
        e = _engine()
        e.record_pnl(-500)
        assert e.drawdown == 500.0  # from peak 0 to -500
        assert not e.is_active  # below soft threshold


class TestLossStreak:
    def test_soft_streak(self):
        e = _engine()
        for _ in range(3):
            e.record_pnl(-100)
        assert e.is_soft
        assert e.consecutive_losses == 3

    def test_hard_streak(self):
        e = _engine()
        for _ in range(5):
            e.record_pnl(-100)
        assert e.is_hard

    def test_win_resets_streak(self):
        e = _engine()
        e.record_pnl(-100)
        e.record_pnl(-100)
        e.record_pnl(200)  # resets
        assert e.consecutive_losses == 0
        assert not e.is_active

    def test_streak_exact_boundary(self):
        e = _engine()
        for _ in range(2):
            e.record_pnl(-100)
        assert not e.is_active  # 2 < soft(3)


class TestErrorRate:
    def test_error_rate_soft(self):
        e = _engine(max_errors_per_window=3)
        e.record_error()
        e.record_error()
        acts = e.record_error()  # 3rd error → triggers
        assert e.is_soft
        err_acts = [a for a in acts if a.trigger == TriggerType.ERROR_RATE]
        assert len(err_acts) == 1

    def test_below_threshold(self):
        e = _engine(max_errors_per_window=5)
        for _ in range(4):
            e.record_error()
        assert not e.is_active


class TestManualControls:
    def test_manual_arm_soft(self):
        e = _engine()
        activation = e.arm(KillSwitchLevel.SOFT, reason='Testing')
        assert e.is_soft
        assert activation.trigger == TriggerType.MANUAL
        assert activation.reason == 'Testing'

    def test_manual_arm_hard(self):
        e = _engine()
        e.arm(KillSwitchLevel.HARD, reason='Emergency')
        assert e.is_hard
        assert e.should_close_all()

    def test_disarm(self):
        e = _engine()
        e.arm(KillSwitchLevel.HARD)
        deact = e.disarm(reason='False alarm')
        assert not e.is_active
        assert deact.deactivated
        assert deact.reason == 'False alarm'

    def test_disarm_clears_all_triggers(self):
        e = _engine()
        e.record_pnl(-2100)  # SOFT via daily loss
        for _ in range(3):
            e.record_pnl(-100)  # SOFT via loss streak too
        assert e.is_active
        assert len(e.active_triggers) >= 1
        e.disarm()
        assert len(e.active_triggers) == 0
        assert not e.is_active


class TestMultipleTriggers:
    def test_highest_level_wins(self):
        e = _engine()
        e.arm(KillSwitchLevel.SOFT, reason='testing soft')
        assert e.is_soft
        e.record_pnl(-4100)  # HARD via daily loss
        assert e.is_hard  # upgraded to HARD

    def test_soft_stays_if_hard_not_reached(self):
        e = _engine()
        e.record_pnl(-2100)  # SOFT daily loss
        for _ in range(3):
            e.record_pnl(-50)  # SOFT loss streak (already counted)
        assert e.is_soft  # still soft, not hard


class TestStateManagement:
    def test_restore_state(self):
        e = _engine()
        e.restore_state(daily_pnl=-2500, peak_pnl=0, consecutive_losses=3, level='soft')
        assert e.is_soft
        assert e.daily_pnl == -2500
        assert e.consecutive_losses == 3

    def test_restore_invalid_level(self):
        e = _engine()
        e.restore_state(daily_pnl=0, peak_pnl=0, consecutive_losses=0, level='bogus')
        assert not e.is_active  # defaults to OFF

    def test_reset_daily(self):
        e = _engine()
        e.record_pnl(-2100)  # SOFT
        assert e.is_active
        e.reset_daily()
        assert not e.is_active
        assert e.daily_pnl == 0.0
        assert e.consecutive_losses == 0
        assert e.drawdown == 0.0


class TestStatus:
    def test_status_dict(self):
        e = _engine()
        s = e.status()
        assert s['level'] == 'off'
        assert s['is_active'] is False
        assert 'daily_pnl' in s
        assert 'drawdown' in s
        assert 'active_triggers' in s

    def test_status_after_activation(self):
        e = _engine()
        e.record_pnl(-2100)
        s = e.status()
        assert s['level'] == 'soft'
        assert s['is_active'] is True
        assert len(s['active_triggers']) >= 1


class TestHistory:
    def test_history_tracks_activations(self):
        e = _engine()
        e.arm(KillSwitchLevel.SOFT)
        e.disarm()
        assert len(e.history) == 2

    def test_activation_to_dict(self):
        e = _engine()
        act = e.arm(KillSwitchLevel.SOFT, reason='test')
        d = act.to_dict()
        assert d['level'] == 'soft'
        assert d['trigger'] == 'manual'
        assert d['reason'] == 'test'
        assert isinstance(d['timestamp'], str)


class TestIdempotency:
    def test_same_trigger_doesnt_duplicate(self):
        e = _engine()
        acts1 = e.record_pnl(-2100)  # SOFT
        acts2 = e.record_pnl(-100)   # still SOFT, same trigger
        # Second call should not create a new activation for daily_loss
        assert len(acts2) == 0 or all(
            a.trigger != TriggerType.DAILY_LOSS for a in acts2
        )

    def test_upgrade_soft_to_hard(self):
        e = _engine()
        e.record_pnl(-2100)  # SOFT
        acts = e.record_pnl(-2100)  # total -4200 → HARD
        hard_acts = [a for a in acts if a.trigger == TriggerType.DAILY_LOSS
                     and a.level == KillSwitchLevel.HARD]
        assert len(hard_acts) == 1
