"""Tests for Regime Engine."""

from app.domain.regime.regime_engine import RegimeEngine, RegimeSnapshot, Regime


def test_high_vix_is_high_vol():
    engine = RegimeEngine()
    regime, changed = engine.classify(RegimeSnapshot(vix=25.0, adx=30, ema_slope=0.5))
    assert regime == Regime.HIGH_VOLATILITY
    assert changed  # default was RANGING


def test_low_vix_low_adx_is_low_vol():
    engine = RegimeEngine()
    regime, _ = engine.classify(RegimeSnapshot(vix=10.0, adx=15, ema_slope=0.1))
    assert regime == Regime.LOW_VOLATILITY


def test_trending_bull():
    engine = RegimeEngine()
    regime, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=28, ema_slope=0.5))
    assert regime == Regime.TRENDING_BULL


def test_trending_bear():
    engine = RegimeEngine()
    regime, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=30, ema_slope=-0.3))
    assert regime == Regime.TRENDING_BEAR


def test_ranging():
    engine = RegimeEngine()
    regime, _ = engine.classify(RegimeSnapshot(vix=15.0, adx=16, ema_slope=0.01, rsi=50))
    assert regime == Regime.RANGING


def test_transition_detection():
    engine = RegimeEngine()
    _, changed1 = engine.classify(RegimeSnapshot(vix=15, adx=28, ema_slope=0.5))
    assert changed1  # RANGING → TRENDING_BULL

    _, changed2 = engine.classify(RegimeSnapshot(vix=15, adx=28, ema_slope=0.5))
    assert not changed2  # same regime


def test_reset():
    engine = RegimeEngine()
    engine.classify(RegimeSnapshot(vix=25, adx=30, ema_slope=0.5))
    assert engine.current == Regime.HIGH_VOLATILITY
    engine.reset()
    assert engine.current == Regime.RANGING
