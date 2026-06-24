"""
Underlying — enum for tradable indices + lot sizes and strike steps.

Pure domain — no I/O, no framework imports.
"""

from __future__ import annotations

from enum import Enum


class Underlying(str, Enum):
    NIFTY     = 'NIFTY'
    BANKNIFTY = 'BANKNIFTY'
    FINNIFTY  = 'FINNIFTY'
    SENSEX    = 'SENSEX'
    MIDCPNIFTY = 'MIDCPNIFTY'

    @property
    def lot_size(self) -> int:
        return _LOT_SIZES.get(self, 1)

    @property
    def strike_step(self) -> int:
        return _STRIKE_STEPS.get(self, 50)

    @property
    def yahoo_ticker(self) -> str:
        return _YAHOO_TICKERS.get(self, '')

    @property
    def exchange(self) -> str:
        if self == Underlying.SENSEX:
            return 'BFO'
        return 'NFO'

    @classmethod
    def from_ticker(cls, ticker: str) -> Underlying:
        """Resolve a yahoo ticker (^NSEI, ^BSESN) or base name to Underlying."""
        t = (ticker or '').upper().replace('.NS', '').replace('.BO', '').lstrip('^')
        mapped = _TICKER_MAP.get(t, t)
        try:
            return cls(mapped)
        except ValueError:
            raise ValueError(f'Unknown underlying: {ticker!r}')


_LOT_SIZES: dict[Underlying, int] = {
    Underlying.NIFTY:      75,
    Underlying.BANKNIFTY:  30,
    Underlying.FINNIFTY:   40,
    Underlying.SENSEX:     20,
    Underlying.MIDCPNIFTY: 50,
}

_STRIKE_STEPS: dict[Underlying, int] = {
    Underlying.NIFTY:      50,
    Underlying.BANKNIFTY:  100,
    Underlying.FINNIFTY:   50,
    Underlying.SENSEX:     100,
    Underlying.MIDCPNIFTY: 25,
}

_YAHOO_TICKERS: dict[Underlying, str] = {
    Underlying.NIFTY:      '^NSEI',
    Underlying.BANKNIFTY:  '^NSEBANK',
    Underlying.FINNIFTY:   '^CNXFIN',
    Underlying.SENSEX:     '^BSESN',
    Underlying.MIDCPNIFTY: '^NSEMDCP50',
}

_TICKER_MAP: dict[str, str] = {
    'NSEI':       'NIFTY',
    'NSEBANK':    'BANKNIFTY',
    'CNXFIN':     'FINNIFTY',
    'BSESN':      'SENSEX',
    'NSEMDCP50':  'MIDCPNIFTY',
}
