"""
Budget Guard — pre-trade check ensuring worst-case loss fits within daily headroom.

Pure domain — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    allowed: bool
    headroom: float          # remaining daily budget
    worst_case_loss: float   # max loss this trade could incur
    reason: str = ''


def check_budget(
    daily_loss_limit: float,
    daily_realized_pnl: float,
    sl_max_points: float,
    qty: int,
    brokerage_estimate: float = 50.0,
) -> BudgetVerdict:
    """Check if a new trade fits within the daily budget.

    Args:
        daily_loss_limit: max daily loss allowed (positive, e.g. 2500)
        daily_realized_pnl: cumulative net P&L today (negative = losses)
        sl_max_points: max SL in index points for this underlying
        qty: number of contracts
        brokerage_estimate: estimated round-trip brokerage
    """
    headroom = daily_loss_limit + daily_realized_pnl
    worst_case = (sl_max_points * qty) + brokerage_estimate

    if worst_case > headroom:
        return BudgetVerdict(
            allowed=False,
            headroom=round(headroom, 2),
            worst_case_loss=round(worst_case, 2),
            reason=f'BUDGET BLOCK: worst_case ₹{worst_case:.0f} > headroom ₹{headroom:.0f}',
        )
    return BudgetVerdict(
        allowed=True,
        headroom=round(headroom, 2),
        worst_case_loss=round(worst_case, 2),
    )
