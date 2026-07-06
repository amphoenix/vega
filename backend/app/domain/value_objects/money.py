"""
Money, PnL, Brokerage — immutable value objects for financial amounts.

Multi-currency: INR for Indian F&O, USDT/USDC for crypto/Polymarket.
Immutable (frozen dataclass).
"""

from __future__ import annotations

from dataclasses import dataclass

_CURRENCY_SYMBOLS = {
    'INR': '₹', 'USDT': '$', 'USDC': '$',
    'BTC': '₿', 'ETH': 'Ξ',
}


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in a specific currency, always rounded to 2 decimal places."""
    amount: float
    currency: str = 'INR'

    def __post_init__(self):
        object.__setattr__(self, 'amount', round(self.amount, 2))

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f'Cannot mix {self.currency} and {other.currency}')

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: int | float) -> Money:
        return Money(self.amount * factor, self.currency)

    def __rmul__(self, factor: int | float) -> Money:
        return self.__mul__(factor)

    def __gt__(self, other: Money) -> bool:
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        return self.amount >= other.amount

    def __lt__(self, other: Money) -> bool:
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        return self.amount <= other.amount

    def __bool__(self) -> bool:
        return self.amount != 0.0

    def __str__(self) -> str:
        sym = _CURRENCY_SYMBOLS.get(self.currency, '')
        return f'{sym}{self.amount:,.2f} {self.currency}'

    @classmethod
    def zero(cls, currency: str = 'INR') -> Money:
        return cls(0.0, currency)

    def to_currency(self, target: str, rate: float) -> Money:
        """Convert to another currency at the given rate."""
        return Money(self.amount * rate, target)


@dataclass(frozen=True, slots=True)
class PnL:
    """Realized P&L for a single trade."""
    gross: Money
    brokerage: Money

    @property
    def net(self) -> Money:
        return self.gross - self.brokerage

    @classmethod
    def from_trade(cls, entry_premium: float, exit_premium: float,
                   qty: int, brokerage: float) -> PnL:
        gross = Money((exit_premium - entry_premium) * qty)
        return cls(gross=gross, brokerage=Money(brokerage))


@dataclass(frozen=True, slots=True)
class BrokerageBreakdown:
    """Full brokerage breakdown for one round-trip F&O trade."""
    flat_brokerage: float
    exchange_txn: float
    stt: float
    sebi: float
    stamp: float
    gst: float

    @property
    def total(self) -> float:
        return round(
            self.flat_brokerage + self.exchange_txn + self.stt +
            self.sebi + self.stamp + self.gst, 2
        )
