"""
Exposure Guard — manages net directional exposure across instruments.

Prevents over-concentration in one direction (all CE or all PE).
Pure domain — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...shared.logger import get_logger

logger = get_logger('exposure_guard')


@dataclass(frozen=True, slots=True)
class ExposureVerdict:
    allowed: bool
    ce_lots: int
    pe_lots: int
    net_direction: str       # 'NEUTRAL', 'BULLISH', 'BEARISH'
    reason: str = ''


class ExposureGuard:
    """Tracks net directional exposure across all open positions."""

    def __init__(self, max_directional_lots: int = 4) -> None:
        self._max = max_directional_lots
        self._ce_lots: int = 0
        self._pe_lots: int = 0

    def check(self, direction: str, lots: int = 1) -> ExposureVerdict:
        """Check if adding lots in this direction is allowed."""
        if direction.upper() == 'CE':
            new_ce = self._ce_lots + lots
            if new_ce - self._pe_lots > self._max:
                return ExposureVerdict(
                    allowed=False, ce_lots=self._ce_lots, pe_lots=self._pe_lots,
                    net_direction=self._net_label(),
                    reason=f'CE exposure would be {new_ce} lots (net {new_ce - self._pe_lots} > {self._max})',
                )
        else:
            new_pe = self._pe_lots + lots
            if new_pe - self._ce_lots > self._max:
                return ExposureVerdict(
                    allowed=False, ce_lots=self._ce_lots, pe_lots=self._pe_lots,
                    net_direction=self._net_label(),
                    reason=f'PE exposure would be {new_pe} lots (net {new_pe - self._ce_lots} > {self._max})',
                )
        return ExposureVerdict(
            allowed=True, ce_lots=self._ce_lots, pe_lots=self._pe_lots,
            net_direction=self._net_label(),
        )

    def add(self, direction: str, lots: int = 1) -> None:
        """Record opening of lots."""
        if direction.upper() == 'CE':
            self._ce_lots += lots
        else:
            self._pe_lots += lots

    def remove(self, direction: str, lots: int = 1) -> None:
        """Record closing of lots."""
        if direction.upper() == 'CE':
            self._ce_lots = max(0, self._ce_lots - lots)
        else:
            self._pe_lots = max(0, self._pe_lots - lots)

    def _net_label(self) -> str:
        diff = self._ce_lots - self._pe_lots
        if diff > 0:
            return 'BULLISH'
        if diff < 0:
            return 'BEARISH'
        return 'NEUTRAL'

    def reset_daily(self) -> None:
        self._ce_lots = 0
        self._pe_lots = 0

    @property
    def net_lots(self) -> int:
        return abs(self._ce_lots - self._pe_lots)
