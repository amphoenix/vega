"""
Instrument — unified tradeable entity across all markets.

Covers:
  - Indian F&O:     NIFTY26JUN18500CE (NIFTY/BNF/FINNIFTY/SENSEX)
  - Crypto perps:   BTC/USDT:USDT
  - Crypto options: BTC/USDT (future — Deribit-style)
  - Polymarket:     "Will X happen?" (condition_id based)

The OptionLeg value object still exists for F&O-specific fields
(strike, expiry, Greeks). Instrument wraps it at a higher level.

Flow: Strategy → AssetClass → Instrument → Exchange

Pure domain — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...shared.time import date
from .market import AssetClass, Currency, ExchangeId, MarketType


@dataclass(frozen=True, slots=True)
class Instrument:
    """A single tradeable thing on any supported market."""

    symbol: str                         # Canonical symbol — 'BTC/USDT', 'NIFTY26JUN18500CE', condition_id
    market: MarketType                  # Which market
    exchange: ExchangeId                # Which venue
    asset_class: AssetClass = AssetClass.INDIAN_OPTION
    base_currency: str = ''             # BTC in BTC/USDT, NIFTY in options
    quote_currency: str = ''            # USDT in BTC/USDT, INR in options
    settle_currency: Currency = Currency.INR

    # F&O-specific (optional)
    underlying: str = ''                # NIFTY, SENSEX, BANKNIFTY
    strike: float = 0.0
    expiry: date | None = None
    option_type: str = ''               # 'CE' or 'PE'
    lot_size: int = 1

    # Crypto-specific (optional)
    is_perpetual: bool = False
    min_qty: float = 0.0
    tick_size: float = 0.0
    maker_fee: float = 0.0
    taker_fee: float = 0.0

    # Polymarket-specific (optional)
    condition_id: str = ''              # Polymarket condition ID
    question: str = ''                  # "Will X happen?"
    outcome: str = ''                   # 'YES' or 'NO'

    @property
    def display_name(self) -> str:
        if self.market == MarketType.POLYMARKET:
            return f'{self.question[:50]} [{self.outcome}]' if self.question else self.symbol
        return self.symbol

    @property
    def is_option(self) -> bool:
        return bool(self.option_type and self.strike > 0)

    @property
    def is_crypto(self) -> bool:
        return self.market == MarketType.CRYPTO

    @property
    def is_prediction(self) -> bool:
        return self.market == MarketType.POLYMARKET

    def __str__(self) -> str:
        return f'{self.exchange.value}:{self.symbol}'


# ── Factory helpers ──────────────────────────────────────────────────────────

def indian_fo_instrument(
    trading_symbol: str,
    underlying: str,
    strike: float,
    expiry: date,
    option_type: str,
    lot_size: int,
    exchange: ExchangeId = ExchangeId.INDMONEY,
) -> Instrument:
    """Create an Indian F&O option instrument."""
    return Instrument(
        symbol=trading_symbol,
        market=MarketType.INDIAN_FO,
        exchange=exchange,
        asset_class=AssetClass.INDIAN_OPTION,
        base_currency=underlying,
        quote_currency='INR',
        settle_currency=Currency.INR,
        underlying=underlying,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        lot_size=lot_size,
    )


def crypto_instrument(
    symbol: str,
    exchange: ExchangeId = ExchangeId.BINANCE,
    is_perpetual: bool = False,
    min_qty: float = 0.001,
    tick_size: float = 0.01,
    maker_fee: float = 0.001,
    taker_fee: float = 0.001,
) -> Instrument:
    """Create a crypto instrument from a CCXT-style symbol like 'BTC/USDT'."""
    parts = symbol.split('/')
    base = parts[0] if parts else symbol
    quote = parts[1].split(':')[0] if len(parts) > 1 else 'USDT'
    return Instrument(
        symbol=symbol,
        market=MarketType.CRYPTO,
        exchange=exchange,
        asset_class=AssetClass.CRYPTO_PERP,
        base_currency=base,
        quote_currency=quote,
        settle_currency=Currency.USDT,
        is_perpetual=is_perpetual,
        min_qty=min_qty,
        tick_size=tick_size,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )


def crypto_option_instrument(
    symbol: str,
    underlying: str,
    strike: float,
    expiry: date,
    option_type: str,
    exchange: ExchangeId = ExchangeId.DERIBIT,
    min_qty: float = 0.1,
    tick_size: float = 0.0005,
    maker_fee: float = 0.0003,
    taker_fee: float = 0.0003,
) -> Instrument:
    """Create a crypto option instrument (Deribit/Bybit/OKX style).

    Example symbol: 'BTC/USD:BTC-250627-100000-C' (CCXT unified format)
    """
    base = underlying.split('/')[0] if '/' in underlying else underlying
    return Instrument(
        symbol=symbol,
        market=MarketType.CRYPTO,
        exchange=exchange,
        asset_class=AssetClass.CRYPTO_OPTION,
        base_currency=base,
        quote_currency='USD',
        settle_currency=Currency.BTC if base == 'BTC' else Currency.ETH,
        underlying=underlying,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        is_perpetual=False,
        min_qty=min_qty,
        tick_size=tick_size,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )


def crypto_futures_instrument(
    symbol: str,
    exchange: ExchangeId = ExchangeId.BINANCE,
    expiry: date | None = None,
    min_qty: float = 0.001,
    tick_size: float = 0.01,
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0005,
) -> Instrument:
    """Create a crypto dated futures instrument (quarterly delivery).

    Example symbol: 'BTC/USDT:USDT-250627' (CCXT unified format)
    """
    parts = symbol.split('/')
    base = parts[0] if parts else symbol
    quote = parts[1].split(':')[0] if len(parts) > 1 else 'USDT'
    return Instrument(
        symbol=symbol,
        market=MarketType.CRYPTO,
        exchange=exchange,
        asset_class=AssetClass.CRYPTO_FUTURES,
        base_currency=base,
        quote_currency=quote,
        settle_currency=Currency.USDT,
        is_perpetual=False,
        expiry=expiry,
        min_qty=min_qty,
        tick_size=tick_size,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )


def polymarket_instrument(
    condition_id: str,
    question: str,
    outcome: str = 'YES',
    exchange: ExchangeId = ExchangeId.POLYMARKET,
) -> Instrument:
    """Create a Polymarket prediction instrument."""
    return Instrument(
        symbol=condition_id,
        market=MarketType.POLYMARKET,
        exchange=exchange,
        asset_class=AssetClass.PREDICTION_MARKET,
        base_currency=outcome,
        quote_currency='USDC',
        settle_currency=Currency.USDC,
        condition_id=condition_id,
        question=question,
        outcome=outcome,
    )
