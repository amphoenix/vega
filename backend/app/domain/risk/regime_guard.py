"""
Regime Guard — blocks or adjusts trades based on current market regime.

Works with the RegimeEngine output to enforce regime-appropriate behaviour.
Pure domain — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..regime.regime_engine import Regime


@dataclass(frozen=True, slots=True)
class RegimeVerdict:
    allowed: bool
    regime: Regime
    size_multiplier: float = 1.0   # 0.5 = half size, 1.0 = full
    reason: str = ''


def check_regime(
    regime: Regime,
    trade_mode: str,            # swing or scalp
    direction: str = '',        # CE or PE (optional, for directional bias check)
) -> RegimeVerdict:
    """Decide if a trade should proceed given the current regime.

    Rules:
    - HIGH_VOLATILITY: block swing, allow scalp at 50% size
    - LOW_VOLATILITY: allow swing, block scalp (not enough momentum)
    - TRENDING_BULL + PE (counter-trend): half size
    - TRENDING_BEAR + CE (counter-trend): half size
    - RANGING: allow both at full size
    """
    mode = trade_mode.lower()
    d = direction.upper()

    if regime == Regime.HIGH_VOLATILITY:
        if mode == 'swing':
            return RegimeVerdict(
                allowed=False, regime=regime,
                reason='HIGH_VOLATILITY: swing trades blocked',
            )
        # Scalp allowed but at reduced size
        return RegimeVerdict(allowed=True, regime=regime, size_multiplier=0.5)

    if regime == Regime.LOW_VOLATILITY:
        if mode == 'scalp':
            return RegimeVerdict(
                allowed=False, regime=regime,
                reason='LOW_VOLATILITY: scalp trades blocked (insufficient momentum)',
            )
        return RegimeVerdict(allowed=True, regime=regime)

    # Counter-trend penalty
    if regime == Regime.TRENDING_BULL and d == 'PE':
        return RegimeVerdict(
            allowed=True, regime=regime, size_multiplier=0.5,
            reason='Counter-trend: PE in TRENDING_BULL, half size',
        )
    if regime == Regime.TRENDING_BEAR and d == 'CE':
        return RegimeVerdict(
            allowed=True, regime=regime, size_multiplier=0.5,
            reason='Counter-trend: CE in TRENDING_BEAR, half size',
        )

    # RANGING or trend-aligned
    return RegimeVerdict(allowed=True, regime=regime)
