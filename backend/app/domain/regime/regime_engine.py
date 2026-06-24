"""
Regime Engine — market regime classification.

Outputs one of:
    TRENDING_BULL
    TRENDING_BEAR
    RANGING
    HIGH_VOLATILITY
    LOW_VOLATILITY

Inputs: VIX, ADX, EMA slope, breadth.
Pure domain — no I/O. Consumes pre-fetched data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Regime(str, Enum):
    TRENDING_BULL    = 'TRENDING_BULL'
    TRENDING_BEAR    = 'TRENDING_BEAR'
    RANGING          = 'RANGING'
    HIGH_VOLATILITY  = 'HIGH_VOLATILITY'
    LOW_VOLATILITY   = 'LOW_VOLATILITY'


@dataclass(frozen=True, slots=True)
class RegimeSnapshot:
    """Input data for regime classification."""
    vix: float                      # India VIX
    adx: float                      # ADX (14-period)
    ema_slope: float                # slope of EMA-20 (positive = up, negative = down)
    rsi: float = 50.0               # RSI-14
    spot: float = 0.0               # current spot for reference


@dataclass
class RegimeConfig:
    """Regime classification thresholds — injectable for runtime tuning."""
    vix_high: float = 20.0          # VIX above this → HIGH_VOLATILITY
    vix_low: float = 12.0           # VIX below this → LOW_VOLATILITY
    adx_trending: float = 25.0      # ADX above this → trending
    adx_weak: float = 18.0          # ADX below this → ranging
    slope_bull: float = 0.0         # EMA slope > 0 → bullish
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0


class RegimeEngine:
    """Classifies market regime from technical indicators.

    Stateful: tracks current regime and detects transitions.
    """

    def __init__(self, config: RegimeConfig | None = None) -> None:
        self.config = config or RegimeConfig()
        self._current: Regime = Regime.RANGING
        self._last_snapshot: RegimeSnapshot | None = None

    @property
    def current(self) -> Regime:
        return self._current

    @property
    def last_snapshot(self) -> RegimeSnapshot | None:
        return self._last_snapshot

    def classify(self, snap: RegimeSnapshot) -> tuple[Regime, bool]:
        """Classify regime from indicators.

        Returns:
            (regime, changed) — the new regime and whether it transitioned.
        """
        self._last_snapshot = snap
        new_regime = self._compute(snap)
        changed = new_regime != self._current
        self._current = new_regime
        return new_regime, changed

    def _compute(self, s: RegimeSnapshot) -> Regime:
        """Core classification logic.

        Priority order:
          1. VIX extremes override everything
          2. ADX + EMA slope determine trend vs range
          3. RSI provides secondary confirmation
        """
        c = self.config
        # VIX overrides
        if s.vix >= c.vix_high:
            return Regime.HIGH_VOLATILITY
        if s.vix <= c.vix_low:
            # Low VIX can still be trending
            if s.adx >= c.adx_trending:
                if s.ema_slope > c.slope_bull:
                    return Regime.TRENDING_BULL
                return Regime.TRENDING_BEAR
            return Regime.LOW_VOLATILITY

        # Normal VIX range — use ADX + slope
        if s.adx >= c.adx_trending:
            if s.ema_slope > c.slope_bull:
                return Regime.TRENDING_BULL
            return Regime.TRENDING_BEAR

        if s.adx <= c.adx_weak:
            return Regime.RANGING

        # Transition zone (ADX between adx_weak and adx_trending)
        # Use RSI as tiebreaker
        if s.rsi >= c.rsi_overbought:
            return Regime.TRENDING_BULL
        if s.rsi <= c.rsi_oversold:
            return Regime.TRENDING_BEAR
        return Regime.RANGING

    def reset(self) -> None:
        """Reset to default state (used in day rollover)."""
        self._current = Regime.RANGING
        self._last_snapshot = None
