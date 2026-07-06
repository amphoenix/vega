"""Tests for risk domain modules."""

from app.domain.regime.regime_engine import Regime
from app.domain.risk.budget_guard import check_budget
from app.domain.risk.exposure_guard import ExposureGuard
from app.domain.risk.position_sizer import compute_size
from app.domain.risk.reentry_guard import ReentryGuard
from app.domain.risk.regime_guard import check_regime
from app.domain.risk.sl_enforcer import enforce_sl, sl_max_for_underlying

# ── Budget Guard ─────────────────────────────────────────────────────────────

def test_budget_allowed():
    v = check_budget(daily_loss_limit=2500, daily_realized_pnl=0, sl_max_points=15, qty=75)
    assert v.allowed
    assert v.worst_case_loss > 0


def test_budget_blocked():
    v = check_budget(daily_loss_limit=2500, daily_realized_pnl=-2400, sl_max_points=15, qty=75)
    assert not v.allowed
    assert 'BUDGET BLOCK' in v.reason


# ── SL Enforcer ──────────────────────────────────────────────────────────────

def test_sl_enforce_clamp():
    assert enforce_sl(20, 15) == 15  # clamped
    assert enforce_sl(10, 15) == 10  # not clamped
    assert enforce_sl(0, 15) == 15   # invalid → default


def test_sl_max_for_underlying():
    assert sl_max_for_underlying('SENSEX') == 50
    assert sl_max_for_underlying('NIFTY') == 15
    assert sl_max_for_underlying('BANKNIFTY') == 15


# ── Re-entry Guard ───────────────────────────────────────────────────────────

def test_reentry_allowed_then_blocked():
    g = ReentryGuard(max_reentries=1, allow_reentry=True)
    assert g.check('NIFTY', 'CE').allowed
    g.record_sl('NIFTY', 'CE')
    assert not g.check('NIFTY', 'CE').allowed
    # Different direction still allowed
    assert g.check('NIFTY', 'PE').allowed


def test_reentry_disabled():
    g = ReentryGuard(max_reentries=1, allow_reentry=False)
    assert not g.check('NIFTY', 'CE').allowed


# ── Exposure Guard ───────────────────────────────────────────────────────────

def test_exposure_within_limit():
    g = ExposureGuard(max_directional_lots=3)
    g.add('CE', lots=2)
    assert g.check('CE', lots=1).allowed   # 2+1=3 net = ok (at limit)
    g.add('CE', lots=1)                    # now at 3
    assert not g.check('CE', lots=1).allowed  # 3+1=4 net > 3 = blocked


def test_exposure_balanced():
    g = ExposureGuard(max_directional_lots=3)
    g.add('CE', lots=2)
    g.add('PE', lots=1)
    assert g.check('CE', lots=2).allowed  # net CE = 2+2-1 = 3 = ok


# ── Regime Guard ─────────────────────────────────────────────────────────────

def test_regime_high_vol_blocks_swing():
    v = check_regime(Regime.HIGH_VOLATILITY, 'swing')
    assert not v.allowed


def test_regime_high_vol_allows_scalp_half():
    v = check_regime(Regime.HIGH_VOLATILITY, 'scalp')
    assert v.allowed
    assert v.size_multiplier == 0.5


def test_regime_low_vol_blocks_scalp():
    v = check_regime(Regime.LOW_VOLATILITY, 'scalp')
    assert not v.allowed


def test_regime_counter_trend_half_size():
    v = check_regime(Regime.TRENDING_BULL, 'swing', direction='PE')
    assert v.allowed
    assert v.size_multiplier == 0.5


# ── Position Sizer ───────────────────────────────────────────────────────────

def test_size_basic():
    r = compute_size(
        available_capital=50_000, max_risk_pct=20, sl_points=15,
        lot_size=75, premium=120, desired_lots=1,
    )
    assert r.lots == 1
    assert r.qty == 75
    assert r.capital_at_risk == 15 * 75


def test_size_regime_reduction():
    r = compute_size(
        available_capital=50_000, max_risk_pct=20, sl_points=15,
        lot_size=75, premium=120, desired_lots=2, regime_multiplier=0.5,
    )
    assert r.lots == 1  # 2 * 0.5 = 1


def test_size_insufficient_capital():
    r = compute_size(
        available_capital=5_000, max_risk_pct=20, sl_points=15,
        lot_size=75, premium=120, desired_lots=3,
    )
    # Can only afford 5000 / (120*75) ≈ 0.55 → floor to 1 (min)
    assert r.lots == 1
