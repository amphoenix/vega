"""
OptionLeg — strike, expiry, option type, lot size.

Immutable value object representing one option contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...shared.time import date


class OptionType(str, Enum):
    CE = 'CE'
    PE = 'PE'

    @property
    def is_call(self) -> bool:
        return self == OptionType.CE

    @property
    def is_put(self) -> bool:
        return self == OptionType.PE

    @classmethod
    def from_bias(cls, bias: str) -> OptionType:
        """BULL → CE, BEAR → PE."""
        b = bias.upper()
        if b in ('BULL', 'CE', 'CALL'):
            return cls.CE
        if b in ('BEAR', 'PE', 'PUT'):
            return cls.PE
        raise ValueError(f'Unknown bias: {bias!r}')


@dataclass(frozen=True, slots=True)
class OptionLeg:
    """One option contract — immutable."""
    trading_symbol: str
    strike: int
    expiry: date
    option_type: OptionType
    lot_size: int
    exchange: str = 'NFO'
    security_id: str = ''
    display_symbol: str = ''

    @property
    def days_to_expiry(self) -> int:
        return max(0, (self.expiry - date.today()).days)

    @property
    def is_expiry_day(self) -> bool:
        return self.expiry == date.today()
