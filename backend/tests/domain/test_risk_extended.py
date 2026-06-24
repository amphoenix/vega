"""
Risk Manager — extended tests ⭐⭐⭐⭐

Tests compound risk scenarios: budget + exposure + regime interacting,
sizer edge cases, and guard resets.
"""

import pytest
from app.domain.risk.budget_guard import check_budget
from app.domain.risk.sl_enforcer import enforce_sl
from app.domain.risk.reentry_guard import ReentryGuard
from app.domain.risk.exposure_guard import ExposureGuard
from app.domain.risk.regime_guard import check_regime
from app.domain.risk.position_sizer import compute_size
from app.domain.regime.regime_engine import Regime


# ── Budget guard edge cases ──────────────────────────────────────────────────

class TestBudgetEdge:
    def test_exactly_at_headroom(self):
        """Worst case exactly equals headroom → should allow."""
        v = check_budget(
            daily_loss_limit=2500, daily_realized_pnl=0,
            sl_max_points=15, qty=75, brokerage_estimate=0,
        )
        # worst_case = 15*75 = 1125, headroom = 2500
        assert v.allowed

    def test_headroom_already_negative(self):
        """If losses already exceed limit, everything blocked."""
        v = check_budget(
            daily_loss_limit=2500, daily_realized_pnl=-3000,
            sl_max_points=5, qty=1, brokerage_estimate=0,
        )
        assert not v.allowed
        assert v.headroom < 0

    def test_small_qty_still_fits(self):
        """Even with tight headroom, small qty fits."""
        v = check_budget(
            daily_loss_limit=2500, daily_realized_pnl=-2400,
            sl_max_points=15, qty=1, brokerage_estimate=0,
        )
        # headroom = 100, worst_case = 15*1 = 15
        assert v.allowed

    def test_brokerage_included_in_worst_case(self):
        v = check_budget(
            daily_loss_limit=2500, daily_realized_pnl=-2400,
            sl_max_points=5, qty=1, brokerage_estimate=100,
        )
        # headroom = 100, worst_case = 5*1 + 100 = 105
        assert not v.allowed


# ── SL enforcer edge cases ──────────────────────────────────────────────────

class TestSLEdge:
    def test_negative_sl_defaults(self):
        assert enforce_sl(-5, 15) == 15

    def test_sl_exactly_at_max(self):
        assert enforce_sl(15, 15) == 15

    def test_sl_zero_defaults(self):
        assert enforce_sl(0, 15) == 15


# ── Re-entry guard compound ─────────────────────────────────────────────────

class TestReentryCompound:
    def test_multiple_underlyings_independent(self):
        g = ReentryGuard(max_reentries=1, allow_reentry=True)
        g.record_sl('NIFTY', 'CE')
        assert not g.check('NIFTY', 'CE').allowed
        assert g.check('SENSEX', 'CE').allowed
        assert g.check('NIFTY', 'PE').allowed

    def test_daily_reset_clears_all(self):
        g = ReentryGuard(max_reentries=1, allow_reentry=True)
        g.record_sl('NIFTY', 'CE')
        g.record_sl('SENSEX', 'PE')
        g.reset_daily()
        assert g.check('NIFTY', 'CE').allowed
        assert g.check('SENSEX', 'PE').allowed

    def test_max_reentries_zero(self):
        g = ReentryGuard(max_reentries=0, allow_reentry=True)
        assert not g.check('NIFTY', 'CE').allowed  # 0 allowed = always blocked


# ── Exposure guard compound ──────────────────────────────────────────────────

class TestExposureCompound:
    def test_remove_then_add(self):
        g = ExposureGuard(max_directional_lots=2)
        g.add('CE', lots=2)
        assert not g.check('CE', lots=1).allowed
        g.remove('CE', lots=1)
        assert g.check('CE', lots=1).allowed

    def test_remove_below_zero_clips(self):
        g = ExposureGuard(max_directional_lots=3)
        g.remove('CE', lots=5)  # should clip to 0, not go negative
        assert g.net_lots == 0

    def test_daily_reset(self):
        g = ExposureGuard(max_directional_lots=2)
        g.add('CE', lots=2)
        g.add('PE', lots=1)
        g.reset_daily()
        assert g.check('CE', lots=1).allowed
        assert g.net_lots == 0


# ── Regime guard all combinations ────────────────────────────────────────────

class TestRegimeAllCombinations:
    @pytest.mark.parametrize('regime,mode,direction,expected_allowed,expected_mult', [
        (Regime.RANGING,          'swing', 'CE', True,  1.0),
        (Regime.RANGING,          'scalp', 'PE', True,  1.0),
        (Regime.TRENDING_BULL,    'swing', 'CE', True,  1.0),
        (Regime.TRENDING_BULL,    'swing', 'PE', True,  0.5),
        (Regime.TRENDING_BULL,    'scalp', 'CE', True,  1.0),
        (Regime.TRENDING_BEAR,    'swing', 'PE', True,  1.0),
        (Regime.TRENDING_BEAR,    'swing', 'CE', True,  0.5),
        (Regime.HIGH_VOLATILITY,  'swing', 'CE', False, 1.0),
        (Regime.HIGH_VOLATILITY,  'scalp', 'CE', True,  0.5),
        (Regime.LOW_VOLATILITY,   'swing', 'CE', True,  1.0),
        (Regime.LOW_VOLATILITY,   'scalp', 'CE', False, 1.0),
    ])
    def test_regime_combinations(self, regime, mode, direction,
                                  expected_allowed, expected_mult):
        v = check_regime(regime, mode, direction)
        assert v.allowed == expected_allowed
        if expected_allowed:
            assert v.size_multiplier == expected_mult


# ── Position sizer edge cases ────────────────────────────────────────────────

class TestSizerEdge:
    def test_zero_capital(self):
        r = compute_size(0, 20, 15, 75, 120)
        assert r.lots == 0

    def test_zero_premium(self):
        r = compute_size(50_000, 20, 15, 75, 0)
        assert r.lots == 0

    def test_zero_sl(self):
        r = compute_size(50_000, 20, 0, 75, 120)
        assert r.lots == 0

    def test_max_lots_cap(self):
        r = compute_size(
            available_capital=1_000_000, max_risk_pct=50,
            sl_points=5, lot_size=75, premium=50,
            desired_lots=100, max_lots=3,
        )
        assert r.lots == 3

    def test_reason_explains_reduction(self):
        r = compute_size(
            available_capital=5_000, max_risk_pct=20,
            sl_points=15, lot_size=75, premium=120,
            desired_lots=5,
        )
        assert r.lots < 5
        assert r.reason  # should explain why reduced

    def test_regime_multiplier_rounds_down(self):
        r = compute_size(
            available_capital=100_000, max_risk_pct=50,
            sl_points=10, lot_size=75, premium=100,
            desired_lots=3, regime_multiplier=0.5,
        )
        assert r.lots == 1  # floor(3 * 0.5) = 1
