"""
SL Enforcer — validates and clamps stop-loss levels.

Ensures SL never exceeds the maximum allowed points per underlying.
Pure domain — no I/O.
"""

from __future__ import annotations


def enforce_sl(
    sl_points: float,
    sl_max_points: float,
) -> float:
    """Clamp SL to the maximum allowed.

    Args:
        sl_points: proposed SL distance in index points
        sl_max_points: maximum allowed SL points for this underlying

    Returns:
        Clamped SL points (never exceeds sl_max_points)
    """
    if sl_points <= 0:
        return sl_max_points  # default to max if invalid
    return min(sl_points, sl_max_points)


def sl_max_for_underlying(underlying: str, config_nifty: int = 15,
                          config_sensex: int = 50) -> int:
    """Return the max SL points for a given underlying.

    Uses the config values from Settings (SL_MAX_POINTS_NIFTY, SL_MAX_POINTS_SENSEX).
    """
    u = (underlying or '').upper()
    if u in ('SENSEX', 'BSESN', '^BSESN'):
        return config_sensex
    return config_nifty  # NIFTY, BANKNIFTY, FINNIFTY, etc.
