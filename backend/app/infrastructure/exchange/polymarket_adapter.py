"""
Polymarket Adapter — wraps official polymarket-client Python SDK.

Uses the real polymarket SDK (pip install polymarket-client):
  - polymarket.SecureClient.create(private_key=...) for authenticated ops
  - polymarket.PublicClient() for read-only market data
  - SecureClient.place_limit_order / place_market_order for trading
  - SecureClient.list_open_orders / cancel_order for order management
  - SecureClient.list_positions / get_balance_allowance for account
  - SecureClient.get_order_book / get_price / get_last_trade_price for data
  - SecureClient.get_market for market discovery

Usage:
    config = PolymarketConfig(private_key='0x...', wallet='0x...')
    adapter = PolymarketAdapter(config)
    await adapter.connect()
    ticker = await adapter.get_ticker(trump_2028_instrument)
    order = await adapter.place_order(trump_2028_instrument, 'BUY', qty=100, price=0.55)

Dependencies:
    pip install polymarket-client  (official Polymarket SDK)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.value_objects.instrument import Instrument
from ...domain.value_objects.market import ExchangeId, MarketType
from ...shared.logger import get_logger
from .base import Balance, ExchangeAdapter, ExchangePosition, Order, Ticker

logger = get_logger('polymarket')


# ── Config ───────────────────────────────────────────────────────────────────

@dataclass
class PolymarketConfig:
    private_key: str = ''            # EVM private key for signing
    wallet: str = ''                 # wallet address (defaults to signer's deposit wallet)
    api_key: str = ''                # optional API key creds
    api_secret: str = ''
    api_passphrase: str = ''
    funder: str = ''                 # deprecated — use wallet
    chain_id: int = 137              # Polygon mainnet (not used by new SDK, kept for compat)
    # Paper trading — real market data, simulated fills
    paper_mode: bool = True
    paper_capital: float = 100_000.0


class PolymarketAdapter(ExchangeAdapter):
    """Polymarket prediction market adapter — uses real polymarket-client SDK.

    Architecture:
        PolymarketAdapter
            ↓
        polymarket.SecureClient (authenticated) / polymarket.PublicClient (read-only)
            ↓
        Polymarket CLOB API + Gamma API

    Features:
      - Market discovery (get_market, search)
      - Buy/sell YES/NO outcome tokens (limit + market orders)
      - Order book access (get_order_book, get_price, get_spread)
      - Position tracking (list_positions)
      - USDC balance management (get_balance_allowance)
      - Order management (list_open_orders, cancel_order, cancel_all)

    When polymarket-client is not installed, all methods return empty/stub results
    and log warnings — allows the codebase to import and test without the SDK.
    """

    def __init__(self, config: PolymarketConfig | None = None, **kwargs: Any) -> None:
        if config is None:
            config = PolymarketConfig(**{k: v for k, v in kwargs.items() if k in PolymarketConfig.__dataclass_fields__})
        self._config = config
        self._client: Any = None        # polymarket.SecureClient instance
        self._public_client: Any = None  # polymarket.PublicClient instance
        self._connected = False
        self._paper_mode = config.paper_mode
        self._paper_capital = config.paper_capital

    @property
    def exchange_id(self) -> ExchangeId:
        return ExchangeId.POLYMARKET

    @property
    def market_type(self) -> MarketType:
        return MarketType.POLYMARKET

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def has_sdk(self) -> bool:
        """Check if polymarket-client is installed."""
        try:
            import polymarket  # noqa: F401
            return True
        except ImportError:
            return False

    # ── Connection lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        """Create SecureClient via polymarket.SecureClient.create().

        Straight from the SDK README:
            from polymarket import SecureClient, PRODUCTION, ApiKeyCreds
            client = SecureClient.create(
                private_key="0x...",
                environment=PRODUCTION,
            )
        """
        try:
            from polymarket import PRODUCTION, ApiKeyCreds, PublicClient, SecureClient

            # Build creds if provided
            creds: ApiKeyCreds | None = None
            if self._config.api_key and self._config.api_secret and self._config.api_passphrase:
                creds = ApiKeyCreds(
                    api_key=self._config.api_key,
                    api_secret=self._config.api_secret,
                    api_passphrase=self._config.api_passphrase,
                )

            if self._config.private_key:
                # Authenticated client — trading + data
                self._client = SecureClient.create(
                    private_key=self._config.private_key,
                    wallet=self._config.wallet or None,
                    environment=PRODUCTION,
                    credentials=creds,
                )
                self._connected = True
                logger.info('Polymarket SecureClient connected (wallet=%s)',
                            self._config.wallet or 'auto')
            else:
                # Read-only client — market data only
                self._public_client = PublicClient(environment=PRODUCTION)
                self._connected = True
                logger.info('Polymarket PublicClient connected (read-only)')

        except ImportError:
            logger.warning('polymarket-client not installed — running in stub mode. '
                          'pip install polymarket-client')
            self._connected = True  # allow stub usage
        except Exception as e:
            logger.error('Polymarket connection failed: %s', e)
            self._connected = False

    async def disconnect(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        if self._public_client:
            try:
                self._public_client.close()
            except Exception:
                pass
        self._client = None
        self._public_client = None
        self._connected = False
        logger.info('Polymarket disconnected')

    # ── Orders ────────────────────────────────────────────────────────

    async def place_order(
        self,
        instrument: Instrument,
        side: str,
        qty: float,
        order_type: str = 'LIMIT',
        price: float = 0.0,
    ) -> Order:
        """Place order via SecureClient.place_limit_order() or place_market_order().

        Directly from the SDK:
            resp = client.place_limit_order(
                token_id="...",
                price=0.55,
                size=100,
                side="BUY",
            )
            resp = client.place_market_order(
                token_id="...",
                side="BUY",
                amount=50,  # USDC spend for BUY
            )
        """
        token_id = instrument.condition_id or instrument.symbol

        if self._paper_mode:
            import uuid
            fill = await self.get_price(token_id, side) or price or 0.0
            self._paper_capital -= fill * qty
            logger.info('Polymarket PAPER fill: %s %s x%.0f @ %.4f (capital=%.2f)',
                        side, instrument.symbol, qty, fill, self._paper_capital)
            return Order(
                order_id=f'PAPER-{uuid.uuid4().hex[:8].upper()}',
                instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price, fill_price=fill, filled_qty=qty,
                status='FILLED',
                raw={'paper': True, 'fill_price': fill},
            )

        if not self._client:
            logger.warning('Polymarket place_order: no client — %s %s x%.0f', side, instrument.symbol, qty)
            return Order(
                order_id='POLY-STUB', instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price, status='REJECTED',
                raw={'error': 'not connected or read-only'},
            )


        try:
            if order_type.upper() == 'MARKET':
                # Market order: BUY uses amount (USDC spend), SELL uses shares
                if side.upper() == 'BUY':
                    raw = self._client.place_market_order(
                        token_id=token_id,
                        side='BUY',
                        amount=qty,   # USDC to spend
                    )
                else:
                    raw = self._client.place_market_order(
                        token_id=token_id,
                        side='SELL',
                        shares=qty,   # shares to sell
                    )
            else:
                # Limit order
                raw = self._client.place_limit_order(
                    token_id=token_id,
                    price=price,
                    size=qty,
                    side=side.upper(),
                )

            # OrderResponse has: id, status, order_id, etc.
            raw_dict = raw.__dict__ if hasattr(raw, '__dict__') else {'raw': str(raw)}
            order_id = str(getattr(raw, 'id', '') or getattr(raw, 'order_id', '') or '')
            status = str(getattr(raw, 'status', 'PENDING') or 'PENDING').upper()

            return Order(
                order_id=order_id,
                instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price,
                status=status,
                raw=raw_dict,
            )
        except Exception as e:
            logger.error('Polymarket place_order error: %s', e)
            return Order(
                order_id='', instrument=instrument,
                side=side, qty=qty, order_type=order_type,
                price=price, status='REJECTED',
                raw={'error': str(e)},
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel via SecureClient.cancel_order(order_id=...)."""
        if not self._client:
            return False
        try:
            self._client.cancel_order(order_id=order_id)
            return True
        except Exception as e:
            logger.error('Polymarket cancel_order error: %s', e)
            return False

    async def cancel_all(self) -> bool:
        """Cancel all open orders via SecureClient.cancel_all()."""
        if not self._client:
            return False
        try:
            self._client.cancel_all()
            return True
        except Exception as e:
            logger.error('Polymarket cancel_all error: %s', e)
            return False

    async def get_order(self, order_id: str) -> Order:
        """Get order via SecureClient.get_order(order_id=...)."""
        if not self._client:
            return Order(order_id=order_id, status='UNKNOWN')
        try:
            raw = self._client.get_order(order_id=order_id)
            return Order(
                order_id=str(getattr(raw, 'id', order_id)),
                side=str(getattr(raw, 'side', '')).upper(),
                qty=float(getattr(raw, 'original_size', 0) or getattr(raw, 'size', 0) or 0),
                price=float(getattr(raw, 'price', 0) or 0),
                status=str(getattr(raw, 'status', 'UNKNOWN')).upper(),
                filled_qty=float(getattr(raw, 'size_matched', 0) or 0),
                raw=raw.__dict__ if hasattr(raw, '__dict__') else {},
            )
        except Exception as e:
            logger.error('Polymarket get_order error: %s', e)
            return Order(order_id=order_id, status='UNKNOWN', raw={'error': str(e)})

    async def get_open_orders(self, symbol: str = '') -> list[Order]:
        """List open orders via SecureClient.list_open_orders()."""
        if not self._client:
            return []
        try:
            paginator = self._client.list_open_orders(
                market=symbol if symbol else None,
            )
            orders: list[Order] = []
            for page in paginator:
                for o in page:
                    orders.append(Order(
                        order_id=str(getattr(o, 'id', '')),
                        side=str(getattr(o, 'side', '')).upper(),
                        qty=float(getattr(o, 'original_size', 0) or getattr(o, 'size', 0) or 0),
                        price=float(getattr(o, 'price', 0) or 0),
                        status='OPEN',
                        raw=o.__dict__ if hasattr(o, '__dict__') else {},
                    ))
            return orders
        except Exception as e:
            logger.error('Polymarket get_open_orders error: %s', e)
            return []

    # ── Market data ───────────────────────────────────────────────────

    async def get_ticker(self, instrument: Instrument) -> Ticker:
        """Get market price via SecureClient.get_last_trade_price() + get_order_book()."""
        client = self._client or self._public_client
        if not client:
            return Ticker(symbol=instrument.symbol)

        token_id = instrument.condition_id or instrument.symbol

        try:
            # get_order_book returns OrderBook with bids/asks
            book = client.get_order_book(token_id=token_id)

            # Extract best bid/ask from the order book
            best_bid = 0.0
            best_ask = 0.0
            if hasattr(book, 'bids') and book.bids:
                best_bid = float(book.bids[0].price) if hasattr(book.bids[0], 'price') else float(book.bids[0][0])
            if hasattr(book, 'asks') and book.asks:
                best_ask = float(book.asks[0].price) if hasattr(book.asks[0], 'price') else float(book.asks[0][0])

            # Get last trade price
            last = 0.0
            try:
                ltp = client.get_last_trade_price(token_id=token_id)
                last = float(getattr(ltp, 'price', 0) or 0)
            except Exception:
                last = (best_bid + best_ask) / 2 if best_bid and best_ask else best_bid or best_ask

            return Ticker(
                symbol=instrument.symbol,
                last=last,
                bid=best_bid,
                ask=best_ask,
                raw={'book': book.__dict__ if hasattr(book, '__dict__') else {}},
            )
        except Exception as e:
            logger.error('Polymarket get_ticker error: %s', e)
            return Ticker(symbol=instrument.symbol)

    async def get_orderbook(
        self, instrument: Instrument, depth: int = 10,
    ) -> dict[str, list]:
        """Get order book via SecureClient.get_order_book(token_id=...)."""
        client = self._client or self._public_client
        if not client:
            return {'bids': [], 'asks': []}

        token_id = instrument.condition_id or instrument.symbol

        try:
            book = client.get_order_book(token_id=token_id)

            def _parse_levels(levels: Any) -> list[list]:
                result = []
                if not levels:
                    return result
                for lvl in levels:
                    if hasattr(lvl, 'price') and hasattr(lvl, 'size'):
                        result.append([float(lvl.price), float(lvl.size)])
                    elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                        result.append([float(lvl[0]), float(lvl[1])])
                return result[:depth]

            return {
                'bids': _parse_levels(getattr(book, 'bids', [])),
                'asks': _parse_levels(getattr(book, 'asks', [])),
            }
        except Exception as e:
            logger.error('Polymarket get_orderbook error: %s', e)
            return {'bids': [], 'asks': []}

    # ── Account ───────────────────────────────────────────────────────

    async def get_balances(self) -> list[Balance]:
        """Get USDC balance via SecureClient.get_balance_allowance(asset_type=...).

        From the SDK:
            ba = client.get_balance_allowance(asset_type="COLLATERAL")
            # ba.balance = USDC balance
        """
        if self._paper_mode:
            return [Balance(currency='USDC', free=self._paper_capital, used=0.0, total=self._paper_capital)]
        if not self._client:
            return []
        try:
            ba = self._client.get_balance_allowance(asset_type='COLLATERAL')
            balance_val = float(getattr(ba, 'balance', 0) or 0)
            if balance_val > 0:
                return [Balance(currency='USDC', free=balance_val, used=0.0, total=balance_val)]
            return []
        except Exception as e:
            logger.error('Polymarket get_balances error: %s', e)
            return []

    async def get_positions(self) -> list[ExchangePosition]:
        """Get open positions via SecureClient.list_positions().

        From the SDK:
            paginator = client.list_positions()
            for page in paginator:
                for position in page:
                    print(position.market, position.size, position.avg_price)
        """
        if not self._client:
            return []
        try:
            paginator = self._client.list_positions()
            positions: list[ExchangePosition] = []
            for page in paginator:
                for pos in page:
                    size = float(getattr(pos, 'size', 0) or 0)
                    if size == 0:
                        continue
                    positions.append(ExchangePosition(
                        symbol=str(getattr(pos, 'market', '') or getattr(pos, 'condition_id', '')),
                        side=str(getattr(pos, 'outcome', 'YES')).upper(),
                        qty=size,
                        avg_entry=float(getattr(pos, 'avg_price', 0) or 0),
                        unrealized_pnl=float(getattr(pos, 'pnl', 0) or getattr(pos, 'cur_val_cents', 0) or 0),
                        raw=pos.__dict__ if hasattr(pos, '__dict__') else {},
                    ))
            return positions
        except Exception as e:
            logger.error('Polymarket get_positions error: %s', e)
            return []

    # ── Polymarket-specific methods ───────────────────────────────────

    async def get_market(self, market_id: str = '', slug: str = '', url: str = '') -> dict:
        """Get market info via SecureClient.get_market(id=...) or get_market(slug=...).

        From the SDK:
            market = client.get_market(id="0x...")
            market = client.get_market(slug="will-trump-win")
        """
        client = self._client or self._public_client
        if not client:
            return {}
        try:
            kwargs: dict[str, str] = {}
            if market_id:
                kwargs['id'] = market_id
            elif slug:
                kwargs['slug'] = slug
            elif url:
                kwargs['url'] = url
            else:
                return {}
            market = client.get_market(**kwargs)
            return market.__dict__ if hasattr(market, '__dict__') else {}
        except Exception as e:
            logger.error('Polymarket get_market error: %s', e)
            return {}

    async def get_price(self, token_id: str, side: str = 'BUY') -> float:
        """Get executable price via SecureClient.get_price(token_id=..., side=...).

        From the SDK:
            price = client.get_price(token_id="...", side="BUY")
        """
        client = self._client or self._public_client
        if not client:
            return 0.0
        try:
            price = client.get_price(token_id=token_id, side=side.upper())
            return float(price)
        except Exception as e:
            logger.error('Polymarket get_price error: %s', e)
            return 0.0

    async def get_spread(self, token_id: str) -> float:
        """Get bid-ask spread via SecureClient.get_spread(token_id=...)."""
        client = self._client or self._public_client
        if not client:
            return 0.0
        try:
            spread = client.get_spread(token_id=token_id)
            return float(spread)
        except Exception as e:
            logger.error('Polymarket get_spread error: %s', e)
            return 0.0

    # Legacy compat aliases
    async def get_markets(self, query: str = '', limit: int = 20) -> list[dict]:
        """Search markets — delegates to get_market with slug."""
        if query:
            result = await self.get_market(slug=query)
            return [result] if result else []
        return []

    async def get_market_info(self, condition_id: str) -> dict:
        """Get market info by condition_id — delegates to get_market(id=...)."""
        return await self.get_market(market_id=condition_id)
