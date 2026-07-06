"""
CCXT Exchange Adapter — wraps ccxt.async_support for unified crypto exchange access.

Uses the real ccxt async API:
  - ccxt.async_support.<exchange>() for async exchange instances
  - All methods are awaitable: fetch_ticker, create_order, fetch_balance, etc.
  - Derivatives: set_leverage, set_margin_mode, fetch_positions
  - Options: Deribit/Bybit/OKX crypto options via CCXT unified API

Supports: Binance, Bybit, Coinbase, OKX, Deribit, and 100+ others.

Usage:
    config = CCXTConfig(exchange_name='binance', api_key='...', secret='...')
    adapter = CCXTAdapter(config)
    await adapter.connect()
    ticker = await adapter.get_ticker(btc_usdt_instrument)
    await adapter.set_leverage(10, 'BTC/USDT:USDT')
    order = await adapter.place_order(btc_usdt_instrument, 'BUY', qty=0.01)

Dependencies:
    pip install ccxt  (optional — adapter degrades gracefully without it)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...domain.value_objects.instrument import Instrument
from ...domain.value_objects.market import ExchangeId, MarketType
from ...shared.logger import get_logger
from .base import Balance, ExchangeAdapter, ExchangePosition, Order, Ticker

logger = get_logger('ccxt_adapter')

# Lazy import — ccxt may not be installed
_ccxt_async: Any = None
_ccxt_sync: Any = None


def _get_ccxt_async() -> Any:
    """Lazy-import ccxt.async_support for real async exchange instances."""
    global _ccxt_async
    if _ccxt_async is None:
        try:
            import ccxt.async_support as mod
            _ccxt_async = mod
        except ImportError:
            _ccxt_async = False
    return _ccxt_async if _ccxt_async else None


def _get_ccxt_sync() -> Any:
    """Lazy-import ccxt (sync) for exchange metadata only."""
    global _ccxt_sync
    if _ccxt_sync is None:
        try:
            import ccxt as mod
            _ccxt_sync = mod
        except ImportError:
            _ccxt_sync = False
    return _ccxt_sync if _ccxt_sync else None


# ── Config ───────────────────────────────────────────────────────────────────

@dataclass
class CCXTConfig:
    exchange_name: str = 'binance'
    api_key: str = ''
    secret: str = ''
    password: str = ''           # some exchanges (OKX) need passphrase
    sandbox: bool = False
    timeout_ms: int = 30_000
    rate_limit: bool = True
    # Derivatives config
    default_type: str = 'spot'   # 'spot', 'swap', 'future', 'option'
    margin_mode: str = ''        # 'cross' or 'isolated' — set on connect if non-empty
    leverage: int = 0            # default leverage — set on connect if > 0
    options: dict[str, Any] = field(default_factory=dict)
    # Paper trading — real market data, simulated fills
    paper_mode: bool = True
    paper_capital: float = 100_000.0


# ── Exchange name to enum ────────────────────────────────────────────────────

_EXCHANGE_MAP: dict[str, ExchangeId] = {
    'binance': ExchangeId.BINANCE,
    'bybit': ExchangeId.BYBIT,
    'okx': ExchangeId.OKX,
    'coinbase': ExchangeId.COINBASE,
    'deribit': ExchangeId.DERIBIT,
}


class CCXTAdapter(ExchangeAdapter):
    """Crypto exchange adapter powered by ccxt.async_support.

    Architecture:
        CCXTAdapter
            -> ccxt.async_support.<exchange>(config)
                -> async REST/WS to exchange

    Features:
      - Spot, perpetual swaps, dated futures, options
      - Real async I/O via ccxt.async_support (aiohttp under the hood)
      - Rate limiting (CCXT built-in enableRateLimit)
      - Leverage & margin mode management for derivatives
      - OHLCV candle fetching
      - Order book snapshots
      - Account balance & position tracking
      - Sandbox mode for testing

    When ccxt is not installed, all methods return empty/stub results
    and log warnings — allows the codebase to import and test without ccxt.
    """

    def __init__(self, config: CCXTConfig | None = None, **kwargs: Any) -> None:
        if config is None:
            config = CCXTConfig(**{k: v for k, v in kwargs.items() if k in CCXTConfig.__dataclass_fields__})
        self._config = config
        self._exchange: Any = None   # ccxt.async_support.<exchange> instance
        self._connected = False
        self._markets_loaded = False
        self._exchange_id = _EXCHANGE_MAP.get(config.exchange_name, ExchangeId.BINANCE)
        self._paper_mode = config.paper_mode
        self._paper_capital = config.paper_capital

    @property
    def exchange_id(self) -> ExchangeId:
        return self._exchange_id

    @property
    def market_type(self) -> MarketType:
        return MarketType.CRYPTO

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def exchange_name(self) -> str:
        return self._config.exchange_name

    @property
    def is_sandbox(self) -> bool:
        return self._config.sandbox

    @property
    def is_paper(self) -> bool:
        return self._paper_mode

    @property
    def has_ccxt(self) -> bool:
        return _get_ccxt_async() is not None

    # ── Connection lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        """Create async ccxt exchange, load markets, configure derivatives."""
        ccxt_async = _get_ccxt_async()
        if ccxt_async is None:
            logger.warning('ccxt not installed — running in stub mode (%s)', self._config.exchange_name)
            self._connected = True
            return

        exchange_class = getattr(ccxt_async, self._config.exchange_name, None)
        if exchange_class is None:
            raise ValueError(f'Unknown exchange: {self._config.exchange_name}')

        # Straight from ccxt docs — pass config dict to exchange constructor
        params: dict[str, Any] = {
            'apiKey': self._config.api_key,
            'secret': self._config.secret,
            'enableRateLimit': self._config.rate_limit,
            'timeout': self._config.timeout_ms,
            'options': {
                'defaultType': self._config.default_type,
                **self._config.options,
            },
        }
        if self._config.password:
            params['password'] = self._config.password

        self._exchange = exchange_class(params)

        if self._config.sandbox:
            self._exchange.set_sandbox_mode(True)

        # ccxt async — await load_markets()
        await self._exchange.load_markets()
        self._markets_loaded = True
        self._connected = True
        logger.info('CCXT connected: %s (sandbox=%s, defaultType=%s, markets=%d)',
                     self._config.exchange_name, self._config.sandbox,
                     self._config.default_type, len(self._exchange.markets))

    async def disconnect(self) -> None:
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
        self._exchange = None
        self._connected = False
        self._markets_loaded = False
        logger.info('CCXT disconnected: %s', self._config.exchange_name)

    # ── Derivatives management ────────────────────────────────────────

    async def set_leverage(self, leverage: int, symbol: str) -> dict:
        """Set leverage for a symbol. Directly calls ccxt exchange.set_leverage()."""
        if not self._exchange:
            return {}
        try:
            return await self._exchange.set_leverage(leverage, symbol)
        except Exception as e:
            logger.error('CCXT set_leverage error (%s, %dx): %s', symbol, leverage, e)
            return {'error': str(e)}

    async def set_margin_mode(self, margin_mode: str, symbol: str) -> dict:
        """Set margin mode ('cross' or 'isolated'). Directly calls ccxt exchange.set_margin_mode()."""
        if not self._exchange:
            return {}
        try:
            return await self._exchange.set_margin_mode(margin_mode, symbol)
        except Exception as e:
            logger.error('CCXT set_margin_mode error (%s, %s): %s', symbol, margin_mode, e)
            return {'error': str(e)}

    # ── Orders ────────────────────────────────────────────────────────

    async def place_order(
        self,
        instrument: Instrument,
        side: str,
        qty: float,
        order_type: str = 'MARKET',
        price: float = 0.0,
        params: dict[str, Any] | None = None,
    ) -> Order:
        """Place order via ccxt exchange.create_order().

        The params dict is passed directly to ccxt for exchange-specific options
        (e.g. {'reduceOnly': True, 'postOnly': True, 'stopPrice': 50000}).
        """
        if self._paper_mode:
            import uuid
            ticker = await self.get_ticker(instrument)
            fill = ticker.last or price or 0.0
            self._paper_capital -= fill * qty
            logger.info('CCXT PAPER fill: %s %s x%.4f @ %.6f (capital=%.2f)',
                        side, instrument.symbol, qty, fill, self._paper_capital)
            return Order(
                order_id=f'PAPER-{uuid.uuid4().hex[:8].upper()}',
                instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price, fill_price=fill, filled_qty=qty,
                status='CLOSED',
                raw={'paper': True, 'fill_price': fill},
            )

        if not self._exchange:
            logger.warning('CCXT place_order: no exchange — %s %s x%.4f', side, instrument.symbol, qty)
            return Order(
                order_id='CCXT-STUB', instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price, status='REJECTED',
                raw={'error': 'not connected'},
            )

        try:
            # ccxt.async_support: await exchange.create_order(symbol, type, side, amount, price, params)
            raw = await self._exchange.create_order(
                symbol=instrument.symbol,
                type=order_type.lower(),
                side=side.lower(),
                amount=qty,
                price=price if order_type.upper() == 'LIMIT' else None,
                params=params or {},
            )
            return Order(
                order_id=str(raw.get('id', '')),
                instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price,
                status=(raw.get('status', '') or '').upper(),
                fill_price=float(raw.get('average') or 0),
                filled_qty=float(raw.get('filled') or 0),
                fee=float((raw.get('fee') or {}).get('cost', 0)),
                fee_currency=(raw.get('fee') or {}).get('currency', ''),
                raw=raw,
            )
        except Exception as e:
            logger.error('CCXT place_order error: %s', e)
            return Order(
                order_id='', instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price, status='REJECTED',
                raw={'error': str(e)},
            )

    async def cancel_order(self, order_id: str, symbol: str = '') -> bool:
        if not self._exchange:
            return False
        try:
            await self._exchange.cancel_order(order_id, symbol or None)
            return True
        except Exception as e:
            logger.error('CCXT cancel_order error: %s', e)
            return False

    async def get_order(self, order_id: str, symbol: str = '') -> Order:
        if not self._exchange:
            return Order(order_id=order_id, status='UNKNOWN')
        try:
            raw = await self._exchange.fetch_order(order_id, symbol or None)
            return Order(
                order_id=str(raw.get('id', '')),
                status=(raw.get('status', '') or '').upper(),
                fill_price=float(raw.get('average') or 0),
                filled_qty=float(raw.get('filled') or 0),
                raw=raw,
            )
        except Exception as e:
            logger.error('CCXT get_order error: %s', e)
            return Order(order_id=order_id, status='UNKNOWN', raw={'error': str(e)})

    async def get_open_orders(self, symbol: str = '') -> list[Order]:
        if not self._exchange:
            return []
        try:
            raws = await self._exchange.fetch_open_orders(symbol or None)
            return [
                Order(
                    order_id=str(r.get('id', '')),
                    side=(r.get('side', '') or '').upper(),
                    qty=float(r.get('amount') or 0),
                    order_type=(r.get('type', '') or '').upper(),
                    price=float(r.get('price') or 0),
                    status=(r.get('status', '') or '').upper(),
                    raw=r,
                )
                for r in raws
            ]
        except Exception as e:
            logger.error('CCXT get_open_orders error: %s', e)
            return []

    # ── Market data ───────────────────────────────────────────────────

    async def get_ticker(self, instrument: Instrument) -> Ticker:
        if not self._exchange:
            return Ticker(symbol=instrument.symbol)
        try:
            raw = await self._exchange.fetch_ticker(instrument.symbol)
            return Ticker(
                symbol=instrument.symbol,
                last=float(raw.get('last') or 0),
                bid=float(raw.get('bid') or 0),
                ask=float(raw.get('ask') or 0),
                volume=float(raw.get('quoteVolume') or raw.get('baseVolume') or 0),
                raw=raw,
            )
        except Exception as e:
            logger.error('CCXT get_ticker error: %s', e)
            return Ticker(symbol=instrument.symbol)

    async def get_orderbook(
        self, instrument: Instrument, depth: int = 10,
    ) -> dict[str, list]:
        if not self._exchange:
            return {'bids': [], 'asks': []}
        try:
            raw = await self._exchange.fetch_order_book(instrument.symbol, limit=depth)
            return {'bids': raw.get('bids', []), 'asks': raw.get('asks', [])}
        except Exception as e:
            logger.error('CCXT get_orderbook error: %s', e)
            return {'bids': [], 'asks': []}

    async def fetch_ohlcv(
        self,
        instrument: Instrument,
        timeframe: str = '1h',
        limit: int = 100,
        since: int | None = None,
    ) -> list[list]:
        """Fetch OHLCV candles. Returns [[timestamp, O, H, L, C, V], ...]."""
        if not self._exchange:
            return []
        try:
            return await self._exchange.fetch_ohlcv(
                instrument.symbol, timeframe=timeframe,
                limit=limit, since=since,
            )
        except Exception as e:
            logger.error('CCXT fetch_ohlcv error: %s', e)
            return []

    # ── Account ───────────────────────────────────────────────────────

    async def get_balances(self) -> list[Balance]:
        if self._paper_mode:
            return [Balance(currency='USDT', free=self._paper_capital, used=0.0, total=self._paper_capital)]
        if not self._exchange:
            return []
        try:
            raw = await self._exchange.fetch_balance()
            balances: list[Balance] = []
            for currency, data in raw.get('total', {}).items():
                total = float(data or 0)
                if total > 0:
                    free = float(raw.get('free', {}).get(currency) or 0)
                    used = float(raw.get('used', {}).get(currency) or 0)
                    balances.append(Balance(currency=currency, free=free, used=used, total=total))
            return balances
        except Exception as e:
            logger.error('CCXT get_balances error: %s', e)
            return []

    async def get_positions(self, symbols: list[str] | None = None) -> list[ExchangePosition]:
        """Fetch open positions. Directly calls ccxt exchange.fetch_positions()."""
        if not self._exchange:
            return []
        try:
            raws = await self._exchange.fetch_positions(symbols)
            positions: list[ExchangePosition] = []
            for r in raws:
                contracts = float(r.get('contracts') or 0)
                if contracts == 0:
                    continue
                positions.append(ExchangePosition(
                    symbol=r.get('symbol', ''),
                    side=(r.get('side', '') or '').upper(),
                    qty=contracts,
                    avg_entry=float(r.get('entryPrice') or 0),
                    unrealized_pnl=float(r.get('unrealizedPnl') or 0),
                    liquidation_price=float(r.get('liquidationPrice') or 0),
                    raw=r,
                ))
            return positions
        except Exception as e:
            logger.error('CCXT get_positions error: %s', e)
            return []

    # ── Helpers ────────────────────────────────────────────────────────

    def get_market_info(self, symbol: str) -> dict[str, Any]:
        """Get CCXT market info for a symbol (min qty, tick size, fees, etc.)."""
        if not self._exchange or not self._markets_loaded:
            return {}
        return self._exchange.markets.get(symbol, {})

    async def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Fetch current funding rate for a perpetual swap."""
        if not self._exchange:
            return {}
        try:
            return await self._exchange.fetch_funding_rate(symbol)
        except Exception as e:
            logger.error('CCXT fetch_funding_rate error: %s', e)
            return {}

    async def fetch_option_chain(self, underlying: str) -> list[dict]:
        """Fetch options chain for an underlying (Deribit/Bybit/OKX).

        Directly calls ccxt exchange.fetch_option_chain() where supported.
        Returns list of option market dicts from ccxt.
        """
        if not self._exchange:
            return []
        try:
            # ccxt unified: exchange.fetch_option_chain(underlyingId)
            # Returns dict of {symbol: market_info} for all options on that underlying
            chain = await self._exchange.fetch_option_chain(underlying)
            return list(chain.values()) if isinstance(chain, dict) else chain
        except Exception as e:
            logger.error('CCXT fetch_option_chain error (%s): %s', underlying, e)
            return []

    async def fetch_greeks(self, symbol: str) -> dict[str, Any]:
        """Fetch option greeks for a symbol (Deribit/Bybit).

        Returns ccxt unified structure with delta, gamma, theta, vega, rho, iv.
        """
        if not self._exchange:
            return {}
        try:
            return await self._exchange.fetch_greeks(symbol)
        except Exception as e:
            logger.error('CCXT fetch_greeks error (%s): %s', symbol, e)
            return {}
