"""
Position Sizer — computes lot count for a trade.

Inputs: capital, risk %, SL, lot size, premium, regime multiplier.
Pure domain — no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SizeResult:
    lots: int
    qty: int                # lots × lot_size
    capital_at_risk: float  # worst-case loss in ₹
    risk_pct: float         # as % of available capital
    reason: str = ''


def compute_size(
    available_capital: float,
    max_risk_pct: float,             # e.g. 20.0 (%)
    sl_points: float,                # stop-loss in premium points
    lot_size: int,
    premium: float,                  # current option premium
    desired_lots: int = 1,           # user's preferred lot count
    regime_multiplier: float = 1.0,  # from regime_guard (0.5 or 1.0)
    max_lots: int = 5,               # hard cap
) -> SizeResult:
    """Compute how many lots to trade.

    Constraints (all must pass):
      1. User's desired lots (from config)
      2. Regime multiplier (0.5 in counter-trend / high-vol)
      3. Max risk % of capital
      4. Capital must cover premium × qty
      5. Hard lot cap

    Returns the most conservative of all constraints.
    """
    if available_capital <= 0 or premium <= 0 or sl_points <= 0 or lot_size <= 0:
        return SizeResult(lots=0, qty=0, capital_at_risk=0, risk_pct=0,
                          reason='Invalid inputs')

    # Apply regime multiplier to desired lots
    adjusted_lots = max(1, math.floor(desired_lots * regime_multiplier))

    # Risk-based cap: max lots such that SL loss ≤ max_risk_pct of capital
    max_risk_amount = available_capital * (max_risk_pct / 100.0)
    risk_per_lot = sl_points * lot_size
    if risk_per_lot > 0:
        risk_lots = max(1, int(max_risk_amount / risk_per_lot))
    else:
        risk_lots = adjusted_lots

    # Capital-based cap: must afford premium × qty
    capital_per_lot = premium * lot_size
    if capital_per_lot > 0:
        afford_lots = max(1, int(available_capital / capital_per_lot))
    else:
        afford_lots = adjusted_lots

    # Take the most conservative
    final_lots = min(adjusted_lots, risk_lots, afford_lots, max_lots)
    final_lots = max(1, final_lots)
    qty = final_lots * lot_size
    capital_at_risk = round(sl_points * qty, 2)
    risk_pct = round(capital_at_risk / available_capital * 100, 2) if available_capital else 0.0

    reason = ''
    if final_lots < desired_lots:
        constraints = []
        if adjusted_lots < desired_lots:
            constraints.append(f'regime({regime_multiplier}x)')
        if risk_lots < desired_lots:
            constraints.append(f'risk({max_risk_pct}%)')
        if afford_lots < desired_lots:
            constraints.append('capital')
        if max_lots < desired_lots:
            constraints.append(f'hard_cap({max_lots})')
        reason = f'Reduced from {desired_lots} to {final_lots}: ' + ', '.join(constraints)

    return SizeResult(
        lots=final_lots,
        qty=qty,
        capital_at_risk=capital_at_risk,
        risk_pct=risk_pct,
        reason=reason,
    )
