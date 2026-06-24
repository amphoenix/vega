"""
Regime Engine — extended tests ⭐⭐⭐⭐⭐

Covers edge cases, boundary thresholds, rapid transitions,
and multi-step regime walks.
"""

import pytest
from app.domain.regime.regime_engine import RegimeEngine, RegimeSnapshot, Regime


@pytest.fixture
def engine():
    return RegimeEngine()


# ── Boundary thresholds ──────────────────────────────────────────────────────

class TestVIXBoundaries:
    def test_vix_exactly_at_high_threshold(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=20.0, adx=25, ema_slope=0.5))
        assert r == Regime.HIGH_VOLATILITY

    def test_vix_just_below_high(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=19.99, adx=25, ema_slope=0.5))
        assert r != Regime.HIGH_VOLATILITY

    def test_vix_exactly_at_low_threshold(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=12.0, adx=15, ema_slope=0.1))
        assert r == Regime.LOW_VOLATILITY

    def test_vix_just_above_low(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=12.01, adx=15, ema_slope=0.1))
        assert r != Regime.LOW_VOLATILITY


class TestADXBoundaries:
    def test_adx_exactly_at_trending(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=25.0, ema_slope=0.5))
        assert r == Regime.TRENDING_BULL

    def test_adx_just_below_trending(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=24.9, ema_slope=0.5, rsi=50))
        assert r != Regime.TRENDING_BULL

    def test_adx_exactly_at_weak(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=18.0, ema_slope=0.1, rsi=50))
        assert r == Regime.RANGING

    def test_adx_in_transition_zone_rsi_overbought(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=22, ema_slope=0.1, rsi=72))
        assert r == Regime.TRENDING_BULL

    def test_adx_in_transition_zone_rsi_oversold(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=22, ema_slope=0.1, rsi=28))
        assert r == Regime.TRENDING_BEAR

    def test_adx_in_transition_zone_rsi_neutral(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=22, ema_slope=0.1, rsi=50))
        assert r == Regime.RANGING


# ── Regime walks (multi-step state transitions) ──────────────────────────────

class TestRegimeWalks:
    def test_ranging_to_bull_to_high_vol(self, engine):
        """Simulate market going from calm → trending → volatile."""
        r1, c1 = engine.classify(RegimeSnapshot(vix=14, adx=16, ema_slope=0.01, rsi=50))
        assert r1 == Regime.RANGING  # initial classify always RANGING
        # c1 is False because default was already RANGING — no change

        r2, c2 = engine.classify(RegimeSnapshot(vix=15, adx=28, ema_slope=0.5))
        assert r2 == Regime.TRENDING_BULL and c2

        r3, c3 = engine.classify(RegimeSnapshot(vix=22, adx=30, ema_slope=0.8))
        assert r3 == Regime.HIGH_VOLATILITY and c3

    def test_high_vol_to_low_vol_without_intermediate(self, engine):
        """VIX can crash from high to low in one step."""
        engine.classify(RegimeSnapshot(vix=25, adx=30, ema_slope=0.5))
        assert engine.current == Regime.HIGH_VOLATILITY

        r, changed = engine.classify(RegimeSnapshot(vix=11, adx=15, ema_slope=0.01))
        assert r == Regime.LOW_VOLATILITY
        assert changed

    def test_no_change_on_repeated_input(self, engine):
        snap = RegimeSnapshot(vix=15, adx=28, ema_slope=0.5)
        _, c1 = engine.classify(snap)
        _, c2 = engine.classify(snap)
        _, c3 = engine.classify(snap)
        assert c1 is True
        assert c2 is False
        assert c3 is False

    def test_rapid_flipping(self, engine):
        """Market flips between bull and bear — each should register as changed."""
        bull = RegimeSnapshot(vix=15, adx=28, ema_slope=0.5)
        bear = RegimeSnapshot(vix=15, adx=28, ema_slope=-0.5)

        _, c1 = engine.classify(bull)
        assert c1  # RANGING → BULL
        _, c2 = engine.classify(bear)
        assert c2  # BULL → BEAR
        _, c3 = engine.classify(bull)
        assert c3  # BEAR → BULL


# ── Low VIX trending (VIX ≤ 12 but strong ADX) ──────────────────────────────

class TestLowVIXTrending:
    def test_low_vix_with_strong_adx_bull(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=10.0, adx=28, ema_slope=0.5))
        assert r == Regime.TRENDING_BULL

    def test_low_vix_with_strong_adx_bear(self, engine):
        r, _ = engine.classify(RegimeSnapshot(vix=10.0, adx=28, ema_slope=-0.5))
        assert r == Regime.TRENDING_BEAR


# ── Reset ────────────────────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears_state(self, engine):
        engine.classify(RegimeSnapshot(vix=25, adx=30, ema_slope=0.5))
        engine.reset()
        assert engine.current == Regime.RANGING
        assert engine.last_snapshot is None

    def test_classify_after_reset(self, engine):
        engine.classify(RegimeSnapshot(vix=25, adx=30, ema_slope=0.5))
        engine.reset()
        r, c = engine.classify(RegimeSnapshot(vix=15, adx=28, ema_slope=0.5))
        assert r == Regime.TRENDING_BULL
        assert c  # changed from RANGING default
