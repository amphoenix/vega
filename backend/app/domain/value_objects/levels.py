"""
Levels — SL, T1, T2, EntryPrice. Validated, immutable.

These represent the price levels for a trade ticket.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TradeLevels:
    """Entry + exit levels for a trade, all in premium (₹)."""
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float

    def __post_init__(self):
        if self.entry_price <= 0:
            raise ValueError(f'entry_price must be positive, got {self.entry_price}')
        if self.stop_loss < 0:
            raise ValueError(f'stop_loss must be non-negative, got {self.stop_loss}')
        if self.target_1 <= self.entry_price:
            raise ValueError(f'target_1 ({self.target_1}) must exceed entry ({self.entry_price})')
        if self.target_2 <= self.target_1:
            raise ValueError(f'target_2 ({self.target_2}) must exceed target_1 ({self.target_1})')

    @property
    def sl_points(self) -> float:
        """Premium drop from entry to SL."""
        return round(self.entry_price - self.stop_loss, 2)

    @property
    def risk_reward_t1(self) -> float:
        """R:R to T1."""
        risk = self.sl_points
        if risk <= 0:
            return 0.0
        return round((self.target_1 - self.entry_price) / risk, 2)

    @property
    def risk_reward_t2(self) -> float:
        """R:R to T2."""
        risk = self.sl_points
        if risk <= 0:
            return 0.0
        return round((self.target_2 - self.entry_price) / risk, 2)


@dataclass(frozen=True, slots=True)
class SpotLevels:
    """SL / T1 / T2 in terms of spot (underlying index level)."""
    spot: float
    sl_spot: float
    t1_spot: float
    t2_spot: float
