"""
ExchangeAdapter — unified interface for all venues.

Replaces the F&O-only BrokerAdapter with a multi-market abstraction.
Works for Indian F&O, crypto (via CCXT), Polymarket, and US equity.

Inspired by:
  - Hummingbot connector architecture
  - CCXT unified API
  - Freqtrade exchange layer

The old BrokerAdapter in infrastructure/broker/base.py still works
for Indian F&O. This ExchangeAdapter is the new unified layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ...domain.value_objects.instrument import Instrument
from ...domain.value_objects.market import MarketType, Currency, ExchangeId
from ...shared.time import now_ist


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Order:
    """A submitted order on any exchange."""
    order_id: str = ''
    instrument: Instrument | None = None
    side: str = ''                  # 'BUY' or 'SELL'
    qty: float = 0.0               # float for crypto fractional
    order_type: str = 'MARKET'     # MARKET, LIMIT, STOP
    price: float = 0.0             # limit price (0 for market)
    status: str = ''               # PENDING, FILLED, CANCELLED, REJECTED
    fill_price: float = 0.0
    filled_qty: float = 0.0
    fee: float = 0.0
    fee_currency: str = ''
    raw: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ''

    @property
    def is_filled(self) -> bool:
        return self.status == 'FILLED'


@dataclass
class Ticker:
    """Real-time price data for any instrument."""
    symbol: str
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    timestamp: str = ''
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def mid(self) -> float:
        if self.bid and self.ask:
            return round((self.bid + self.ask) / 2, 8)
        return self.last

    @property
    def spread(self) -> float:
        if self.bid and self.ask:
            return round(self.ask - self.bid, 8)
        return 0.0


@dataclass
class Balance:
    """Account balance for a specific currency."""
    currency: str
    free: float = 0.0       # available for trading
    used: float = 0.0       # in open orders / margin
    total: float = 0.0      # free + used


@dataclass
class ExchangePosition:
    """An open position on any exchange."""
    instrument: Instrument | None = None
    symbol: str = ''
    side: str = ''           # 'LONG' or 'SHORT' or '' for spot
    qty: float = 0.0
    avg_entry: float = 0.0
    unrealized_pnl: float = 0.0
    liquidation_price: float = 0.0    # futures only
    raw: dict[str, Any] = field(default_factory=dict)


# ── Abstract base ────────────────────────────────────────────────────────────

class ExchangeAdapter(ABC):
    """Unified interface for all exchange/venue integrations.

    Implementations:
      - CCXTAdapter (wraps CCXT for Binance/Bybit/Deribit/OKX — spot, futures, options)
      - PolymarketAdapter (wraps official Polymarket SDK)

    Paper trading: set TRADING_MODE=paper in .env → adapters connect to
    sandbox/testnet endpoints instead of production. No separate adapter needed.
    """

    @property
    @abstractmethod
    def exchange_id(self) -> ExchangeId:
        """Which exchange this adapter connects to."""
        ...

    @property
    @abstractmethod
    def market_type(self) -> MarketType:
        """Which market type this adapter handles."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the exchange connection is active."""
        ...

    # ── Orders ────────────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(
        self,
        instrument: Instrument,
        side: str,
        qty: float,
        order_type: str = 'MARKET',
        price: float = 0.0,
    ) -> Order:
        """Place an order. Returns filled/pending Order."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled."""
        ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Order:
        """Get order status by ID."""
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: str = '') -> list[Order]:
        """Get all open orders, optionally filtered by symbol."""
        ...

    # ── Market data ───────────────────────────────────────────────────────

    @abstractmethod
    async def get_ticker(self, instrument: Instrument) -> Ticker:
        """Get current price/quote for an instrument."""
        ...

    @abstractmethod
    async def get_orderbook(
        self, instrument: Instrument, depth: int = 10,
    ) -> dict[str, list]:
        """Get order book. Returns {'bids': [...], 'asks': [...]}."""
        ...

    # ── Account ───────────────────────────────────────────────────────────

    @abstractmethod
    async def get_balances(self) -> list[Balance]:
        """Get all currency balances."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[ExchangePosition]:
        """Get all open positions."""
        ...

    # ── Connection lifecycle ──────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Initialize connection (auth, WebSocket, etc.)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Clean shutdown."""
        ...

    # ── Helpers ───────────────────────────────────────────────────────────

    def supports_market(self, market: MarketType) -> bool:
        return self.market_type == market
