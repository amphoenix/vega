"""
Market, Asset, Currency, Exchange enums — multi-market foundation.

Vega trades:
  - INDIAN_FO: NIFTY/SENSEX/BANKNIFTY/FINNIFTY options + stock options
  - CRYPTO: BTC/ETH/SOL perps + options via CCXT (Binance, Bybit, OKX)
  - POLYMARKET: prediction markets via official Polymarket SDK

Flow:  Strategy → AssetClass → Exchange

Pure domain — no I/O.
"""

from __future__ import annotations

from enum import Enum


# ── What market ──────────────────────────────────────────────────────────────

class MarketType(str, Enum):
    """Which market an instrument belongs to."""
    INDIAN_FO   = 'indian_fo'      # NSE F&O — NIFTY, SENSEX, BANKNIFTY, FINNIFTY
    CRYPTO      = 'crypto'          # Spot + perps + options via CCXT
    POLYMARKET  = 'polymarket'      # Prediction markets


# ── What kind of asset ───────────────────────────────────────────────────────

class AssetClass(str, Enum):
    """What you're trading — drives sizing, risk, and routing."""
    INDIAN_OPTION       = 'indian_option'       # NIFTY/BNF/FINNIFTY/SENSEX CE/PE
    CRYPTO_SPOT         = 'crypto_spot'         # BTC/ETH/SOL spot
    CRYPTO_PERP         = 'crypto_perp'         # BTC/ETH/SOL perpetual futures
    CRYPTO_FUTURES      = 'crypto_futures'      # BTC/ETH dated futures (quarterly)
    CRYPTO_OPTION       = 'crypto_option'       # BTC/ETH options (Deribit/Bybit/OKX)
    PREDICTION_MARKET   = 'prediction_market'   # Polymarket YES/NO tokens

    @classmethod
    def for_market(
        cls,
        market: MarketType,
        is_option: bool = False,
        is_futures: bool = False,
        is_spot: bool = False,
    ) -> AssetClass:
        """Infer asset class from market type."""
        if market == MarketType.INDIAN_FO:
            return cls.INDIAN_OPTION
        if market == MarketType.CRYPTO:
            if is_option:
                return cls.CRYPTO_OPTION
            if is_futures:
                return cls.CRYPTO_FUTURES
            if is_spot:
                return cls.CRYPTO_SPOT
            return cls.CRYPTO_PERP
        return cls.PREDICTION_MARKET


# ── What currency ────────────────────────────────────────────────────────────

class Currency(str, Enum):
    """Settlement currency."""
    INR  = 'INR'    # Indian Rupee — Indian F&O
    USDC = 'USDC'   # USDC — Polymarket, crypto stablecoins
    USDT = 'USDT'   # Tether — crypto perps
    BTC  = 'BTC'    # Bitcoin — crypto pairs
    ETH  = 'ETH'    # Ether — crypto pairs

    @classmethod
    def for_market(cls, market: MarketType) -> Currency:
        """Default settlement currency for a market."""
        return {
            MarketType.INDIAN_FO:  cls.INR,
            MarketType.CRYPTO:     cls.USDT,
            MarketType.POLYMARKET: cls.USDC,
        }[market]


# ── Which exchange / venue ───────────────────────────────────────────────────

class ExchangeId(str, Enum):
    """Known exchanges / venues.

    Indian F&O: one broker adapter per broker (INDmoney, Dhan, etc.)
    Crypto: ONE CCXTAdapter handles all — just change exchange_name config.
    Polymarket: ONE PolymarketAdapter.
    """
    # Indian F&O brokers
    INDMONEY    = 'indmoney'
    DHAN        = 'dhan'
    # Crypto — all handled by a single CCXTAdapter (exchange_name param)
    BINANCE     = 'binance'
    BYBIT       = 'bybit'
    OKX         = 'okx'
    COINBASE    = 'coinbase'
    DERIBIT     = 'deribit'         # Primary crypto options exchange
    # Prediction
    POLYMARKET  = 'polymarket'
