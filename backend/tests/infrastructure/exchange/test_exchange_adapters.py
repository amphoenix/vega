"""
Tests for CCXT adapter and Polymarket adapter.

Tests run in stub mode (no SDKs installed) by default.
Mock-based tests exercise the real code paths with mocked SDK objects.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.value_objects.instrument import Instrument, polymarket_instrument
from app.domain.value_objects.market import AssetClass, Currency, ExchangeId, MarketType
from app.infrastructure.exchange.base import Order, Ticker
from app.infrastructure.exchange.ccxt_adapter import CCXTAdapter, CCXTConfig
from app.infrastructure.exchange.polymarket_adapter import PolymarketAdapter, PolymarketConfig


def _btc_instrument() -> Instrument:
    return Instrument(
        symbol='BTC/USDT',
        market=MarketType.CRYPTO,
        exchange=ExchangeId.BINANCE,
        asset_class=AssetClass.CRYPTO_SPOT,
        base_currency='BTC',
        quote_currency='USDT',
        settle_currency=Currency.USDT,
    )


def _eth_instrument() -> Instrument:
    return Instrument(
        symbol='ETH/USDT',
        market=MarketType.CRYPTO,
        exchange=ExchangeId.BINANCE,
        asset_class=AssetClass.CRYPTO_SPOT,
        base_currency='ETH',
        quote_currency='USDT',
        settle_currency=Currency.USDT,
    )


# ── CCXTConfig ───────────────────────────────────────────────────────────────

class TestCCXTConfig:
    def test_defaults(self):
        c = CCXTConfig()
        assert c.exchange_name == 'binance'
        assert c.sandbox is True
        assert c.rate_limit is True
        assert c.default_type == 'spot'
        assert c.margin_mode == ''
        assert c.leverage == 0

    def test_custom(self):
        c = CCXTConfig(exchange_name='bybit', api_key='k', secret='s', sandbox=False)
        assert c.exchange_name == 'bybit'
        assert c.api_key == 'k'

    def test_derivatives_config(self):
        c = CCXTConfig(
            exchange_name='binance',
            default_type='swap',
            margin_mode='cross',
            leverage=10,
        )
        assert c.default_type == 'swap'
        assert c.margin_mode == 'cross'
        assert c.leverage == 10

    def test_deribit_options_config(self):
        c = CCXTConfig(exchange_name='deribit', default_type='option')
        assert c.default_type == 'option'


# ── CCXTAdapter (stub mode — no ccxt installed) ─────────────────────────────

class TestCCXTAdapterStub:
    @pytest.fixture
    def adapter(self):
        return CCXTAdapter(config=CCXTConfig(exchange_name='binance'))

    @pytest.mark.asyncio
    async def test_connect_stub(self, adapter):
        await adapter.connect()
        assert adapter.is_connected

    @pytest.mark.asyncio
    async def test_disconnect(self, adapter):
        await adapter.connect()
        await adapter.disconnect()
        assert not adapter.is_connected

    def test_exchange_id(self, adapter):
        assert adapter.exchange_id == ExchangeId.BINANCE

    def test_market_type(self, adapter):
        assert adapter.market_type == MarketType.CRYPTO

    def test_exchange_name(self, adapter):
        assert adapter.exchange_name == 'binance'

    def test_is_sandbox(self, adapter):
        assert adapter.is_sandbox is True

    @pytest.mark.asyncio
    async def test_place_order_stub_returns_rejected(self, adapter):
        await adapter.connect()
        order = await adapter.place_order(_btc_instrument(), 'BUY', 0.01)
        # Without ccxt installed, returns stub REJECTED
        assert order.status == 'REJECTED'
        assert order.instrument.symbol == 'BTC/USDT'

    @pytest.mark.asyncio
    async def test_get_ticker_stub_empty(self, adapter):
        await adapter.connect()
        ticker = await adapter.get_ticker(_btc_instrument())
        assert ticker.symbol == 'BTC/USDT'
        assert ticker.last == 0.0

    @pytest.mark.asyncio
    async def test_cancel_order_stub(self, adapter):
        await adapter.connect()
        result = await adapter.cancel_order('fake-id')
        assert result is False

    @pytest.mark.asyncio
    async def test_get_order_stub(self, adapter):
        await adapter.connect()
        order = await adapter.get_order('fake-id')
        assert order.status == 'UNKNOWN'

    @pytest.mark.asyncio
    async def test_get_open_orders_stub(self, adapter):
        await adapter.connect()
        orders = await adapter.get_open_orders()
        assert orders == []

    @pytest.mark.asyncio
    async def test_get_orderbook_stub(self, adapter):
        await adapter.connect()
        ob = await adapter.get_orderbook(_btc_instrument())
        assert ob == {'bids': [], 'asks': []}

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_stub(self, adapter):
        await adapter.connect()
        candles = await adapter.fetch_ohlcv(_btc_instrument())
        assert candles == []

    @pytest.mark.asyncio
    async def test_get_balances_stub(self, adapter):
        await adapter.connect()
        balances = await adapter.get_balances()
        assert balances == []

    @pytest.mark.asyncio
    async def test_get_positions_stub(self, adapter):
        await adapter.connect()
        positions = await adapter.get_positions()
        assert positions == []

    def test_get_market_info_stub(self, adapter):
        info = adapter.get_market_info('BTC/USDT')
        assert info == {}

    def test_legacy_kwargs_compat(self):
        a = CCXTAdapter(exchange_name='okx', sandbox=False)
        assert a.exchange_id == ExchangeId.OKX
        assert a.is_sandbox is False

    def test_bybit_exchange_id(self):
        a = CCXTAdapter(config=CCXTConfig(exchange_name='bybit'))
        assert a.exchange_id == ExchangeId.BYBIT

    def test_deribit_exchange_id(self):
        a = CCXTAdapter(config=CCXTConfig(exchange_name='deribit'))
        assert a.exchange_id == ExchangeId.DERIBIT

    def test_coinbase_exchange_id(self):
        a = CCXTAdapter(config=CCXTConfig(exchange_name='coinbase'))
        assert a.exchange_id == ExchangeId.COINBASE

    @pytest.mark.asyncio
    async def test_set_leverage_stub(self, adapter):
        await adapter.connect()
        result = await adapter.set_leverage(10, 'BTC/USDT:USDT')
        assert result == {}

    @pytest.mark.asyncio
    async def test_set_margin_mode_stub(self, adapter):
        await adapter.connect()
        result = await adapter.set_margin_mode('cross', 'BTC/USDT:USDT')
        assert result == {}

    @pytest.mark.asyncio
    async def test_fetch_funding_rate_stub(self, adapter):
        await adapter.connect()
        result = await adapter.fetch_funding_rate('BTC/USDT:USDT')
        assert result == {}

    @pytest.mark.asyncio
    async def test_fetch_option_chain_stub(self, adapter):
        await adapter.connect()
        result = await adapter.fetch_option_chain('BTC')
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_greeks_stub(self, adapter):
        await adapter.connect()
        result = await adapter.fetch_greeks('BTC/USD:BTC-250627-100000-C')
        assert result == {}

    @pytest.mark.asyncio
    async def test_place_order_with_params(self, adapter):
        await adapter.connect()
        order = await adapter.place_order(
            _btc_instrument(), 'BUY', 0.01,
            order_type='LIMIT', price=50000.0,
            params={'reduceOnly': True},
        )
        assert order.status == 'REJECTED'  # stub mode


# ── CCXTAdapter (mocked ccxt) ─────────────────────────────────────────────

class TestCCXTAdapterMocked:
    """Test real code paths with mocked ccxt.async_support exchange."""

    def _make_mock_exchange(self):
        """Create a mock that behaves like ccxt.async_support.<exchange>."""
        mock_exchange = MagicMock()
        mock_exchange.load_markets = AsyncMock(return_value={})
        mock_exchange.markets = {'BTC/USDT': {'symbol': 'BTC/USDT', 'base': 'BTC'}}
        mock_exchange.close = AsyncMock()
        mock_exchange.set_sandbox_mode = MagicMock()

        mock_exchange.fetch_ticker = AsyncMock(return_value={
            'symbol': 'BTC/USDT', 'last': 67000.0,
            'bid': 66990.0, 'ask': 67010.0, 'quoteVolume': 1_000_000.0,
        })
        mock_exchange.create_order = AsyncMock(return_value={
            'id': 'ord-123', 'status': 'closed', 'average': 67000.0,
            'filled': 0.01, 'fee': {'cost': 0.067, 'currency': 'BNB'},
        })
        mock_exchange.cancel_order = AsyncMock(return_value={'id': 'ord-123'})
        mock_exchange.fetch_order = AsyncMock(return_value={
            'id': 'ord-123', 'status': 'closed', 'average': 67000.0, 'filled': 0.01,
        })
        mock_exchange.fetch_open_orders = AsyncMock(return_value=[
            {'id': 'ord-456', 'side': 'buy', 'amount': 0.05, 'type': 'limit', 'price': 66000.0, 'status': 'open'},
        ])
        mock_exchange.fetch_balance = AsyncMock(return_value={
            'total': {'USDT': 5000.0, 'BTC': 0.1},
            'free': {'USDT': 4000.0, 'BTC': 0.1},
            'used': {'USDT': 1000.0, 'BTC': 0.0},
        })
        mock_exchange.fetch_positions = AsyncMock(return_value=[
            {'symbol': 'BTC/USDT:USDT', 'side': 'long', 'contracts': 0.5,
             'entryPrice': 65000.0, 'unrealizedPnl': 1000.0, 'liquidationPrice': 60000.0},
        ])
        mock_exchange.fetch_order_book = AsyncMock(return_value={
            'bids': [[66990.0, 1.5]], 'asks': [[67010.0, 2.0]],
        })
        mock_exchange.fetch_ohlcv = AsyncMock(return_value=[
            [1718000000000, 66000, 67500, 65500, 67000, 5000],
        ])
        mock_exchange.set_leverage = AsyncMock(return_value={'leverage': 10})
        mock_exchange.set_margin_mode = AsyncMock(return_value={'marginMode': 'cross'})
        mock_exchange.fetch_funding_rate = AsyncMock(return_value={
            'symbol': 'BTC/USDT:USDT', 'fundingRate': 0.0001,
        })
        return mock_exchange

    @pytest.fixture
    def adapter_with_mock(self):
        mock_exchange = self._make_mock_exchange()
        # Mock the ccxt.async_support module
        mock_ccxt_async = MagicMock()
        mock_ccxt_async.binance = MagicMock(return_value=mock_exchange)

        adapter = CCXTAdapter(config=CCXTConfig(exchange_name='binance'))
        return adapter, mock_exchange, mock_ccxt_async

    @pytest.mark.asyncio
    async def test_connect_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        assert adapter.is_connected
        assert adapter._markets_loaded
        mock_exchange.load_markets.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_ticker_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        ticker = await adapter.get_ticker(_btc_instrument())
        assert ticker.last == 67000.0
        assert ticker.bid == 66990.0
        assert ticker.ask == 67010.0

    @pytest.mark.asyncio
    async def test_place_order_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        order = await adapter.place_order(_btc_instrument(), 'BUY', 0.01)
        assert order.order_id == 'ord-123'
        assert order.status == 'CLOSED'
        assert order.fill_price == 67000.0
        assert order.fee == pytest.approx(0.067)
        mock_exchange.create_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_order_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        assert await adapter.cancel_order('ord-123', 'BTC/USDT') is True
        mock_exchange.cancel_order.assert_awaited_once_with('ord-123', 'BTC/USDT')

    @pytest.mark.asyncio
    async def test_fetch_positions_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        positions = await adapter.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == 'BTC/USDT:USDT'
        assert positions[0].side == 'LONG'
        assert positions[0].qty == 0.5
        assert positions[0].unrealized_pnl == 1000.0

    @pytest.mark.asyncio
    async def test_set_leverage_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        result = await adapter.set_leverage(10, 'BTC/USDT:USDT')
        assert result == {'leverage': 10}
        mock_exchange.set_leverage.assert_awaited_once_with(10, 'BTC/USDT:USDT')

    @pytest.mark.asyncio
    async def test_set_margin_mode_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        result = await adapter.set_margin_mode('cross', 'BTC/USDT:USDT')
        assert result == {'marginMode': 'cross'}

    @pytest.mark.asyncio
    async def test_get_balances_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        balances = await adapter.get_balances()
        assert len(balances) == 2
        usdt = next(b for b in balances if b.currency == 'USDT')
        assert usdt.total == 5000.0
        assert usdt.free == 4000.0

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        candles = await adapter.fetch_ohlcv(_btc_instrument(), timeframe='1h', limit=1)
        assert len(candles) == 1
        assert candles[0][4] == 67000  # close

    @pytest.mark.asyncio
    async def test_disconnect_real_path(self, adapter_with_mock):
        adapter, mock_exchange, mock_ccxt_async = adapter_with_mock
        with patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async', return_value=mock_ccxt_async):
            await adapter.connect()
        await adapter.disconnect()
        assert not adapter.is_connected
        mock_exchange.close.assert_awaited_once()


# ── PolymarketConfig ─────────────────────────────────────────────────────────

class TestPolymarketConfig:
    def test_defaults(self):
        c = PolymarketConfig()
        assert c.private_key == ''
        assert c.wallet == ''
        assert c.chain_id == 137

    def test_custom(self):
        c = PolymarketConfig(private_key='0xabc', wallet='0xdef', api_key='k')
        assert c.private_key == '0xabc'
        assert c.wallet == '0xdef'
        assert c.api_key == 'k'


# ── PolymarketAdapter (stub mode — no SDK installed) ─────────────────────────

def _poly_instrument() -> Instrument:
    return polymarket_instrument(
        condition_id='0x1234567890abcdef',
        question='Will BTC hit 100k by 2025?',
        outcome='YES',
    )


class TestPolymarketAdapterStub:
    @pytest.fixture
    def adapter(self):
        return PolymarketAdapter(config=PolymarketConfig())

    @pytest.mark.asyncio
    async def test_connect_stub(self, adapter):
        await adapter.connect()
        assert adapter.is_connected

    @pytest.mark.asyncio
    async def test_disconnect(self, adapter):
        await adapter.connect()
        await adapter.disconnect()
        assert not adapter.is_connected

    def test_exchange_id(self, adapter):
        assert adapter.exchange_id == ExchangeId.POLYMARKET

    def test_market_type(self, adapter):
        assert adapter.market_type == MarketType.POLYMARKET

    @pytest.mark.asyncio
    async def test_place_order_stub_rejected(self, adapter):
        await adapter.connect()
        order = await adapter.place_order(_poly_instrument(), 'BUY', 100, price=0.55)
        assert order.status == 'REJECTED'

    @pytest.mark.asyncio
    async def test_cancel_order_stub(self, adapter):
        await adapter.connect()
        assert await adapter.cancel_order('fake-id') is False

    @pytest.mark.asyncio
    async def test_cancel_all_stub(self, adapter):
        await adapter.connect()
        assert await adapter.cancel_all() is False

    @pytest.mark.asyncio
    async def test_get_order_stub(self, adapter):
        await adapter.connect()
        order = await adapter.get_order('fake-id')
        assert order.status == 'UNKNOWN'

    @pytest.mark.asyncio
    async def test_get_open_orders_stub(self, adapter):
        await adapter.connect()
        orders = await adapter.get_open_orders()
        assert orders == []

    @pytest.mark.asyncio
    async def test_get_ticker_stub(self, adapter):
        await adapter.connect()
        ticker = await adapter.get_ticker(_poly_instrument())
        assert ticker.symbol == '0x1234567890abcdef'
        assert ticker.last == 0.0

    @pytest.mark.asyncio
    async def test_get_orderbook_stub(self, adapter):
        await adapter.connect()
        ob = await adapter.get_orderbook(_poly_instrument())
        assert ob == {'bids': [], 'asks': []}

    @pytest.mark.asyncio
    async def test_get_balances_stub(self, adapter):
        await adapter.connect()
        assert await adapter.get_balances() == []

    @pytest.mark.asyncio
    async def test_get_positions_stub(self, adapter):
        await adapter.connect()
        assert await adapter.get_positions() == []

    @pytest.mark.asyncio
    async def test_get_market_stub(self, adapter):
        await adapter.connect()
        assert await adapter.get_market(market_id='0xabc') == {}

    @pytest.mark.asyncio
    async def test_get_price_stub(self, adapter):
        await adapter.connect()
        assert await adapter.get_price('0xabc') == 0.0

    @pytest.mark.asyncio
    async def test_get_spread_stub(self, adapter):
        await adapter.connect()
        assert await adapter.get_spread('0xabc') == 0.0

    @pytest.mark.asyncio
    async def test_get_markets_stub(self, adapter):
        await adapter.connect()
        assert await adapter.get_markets(query='trump') == []

    @pytest.mark.asyncio
    async def test_get_market_info_stub(self, adapter):
        await adapter.connect()
        assert await adapter.get_market_info('0xabc') == {}

    def test_legacy_kwargs_compat(self):
        a = PolymarketAdapter(private_key='0xabc', wallet='0xdef')
        assert a._config.private_key == '0xabc'


# ── PolymarketAdapter (mocked SDK) ───────────────────────────────────────────

class TestPolymarketAdapterMocked:
    """Test real code paths with mocked polymarket SDK."""

    def _make_mock_client(self):
        client = MagicMock()
        # place_limit_order returns an object with id and status
        order_resp = MagicMock()
        order_resp.id = 'poly-ord-1'
        order_resp.order_id = 'poly-ord-1'
        order_resp.status = 'LIVE'
        client.place_limit_order = MagicMock(return_value=order_resp)
        client.place_market_order = MagicMock(return_value=order_resp)

        client.cancel_order = MagicMock()
        client.cancel_all = MagicMock()

        # get_order
        open_order = MagicMock()
        open_order.id = 'poly-ord-1'
        open_order.side = 'BUY'
        open_order.original_size = 100
        open_order.price = 0.55
        open_order.status = 'LIVE'
        open_order.size_matched = 50
        client.get_order = MagicMock(return_value=open_order)

        # list_open_orders returns a paginator (iterable of pages)
        client.list_open_orders = MagicMock(return_value=[[open_order]])

        # get_balance_allowance
        ba = MagicMock()
        ba.balance = 1500.0
        client.get_balance_allowance = MagicMock(return_value=ba)

        # list_positions
        pos = MagicMock()
        pos.market = '0xabc'
        pos.condition_id = '0xabc'
        pos.outcome = 'YES'
        pos.size = 200.0
        pos.avg_price = 0.45
        pos.pnl = 20.0
        client.list_positions = MagicMock(return_value=[[pos]])

        # get_order_book
        bid = MagicMock()
        bid.price = 0.54
        bid.size = 500
        ask = MagicMock()
        ask.price = 0.56
        ask.size = 300
        book = MagicMock()
        book.bids = [bid]
        book.asks = [ask]
        client.get_order_book = MagicMock(return_value=book)

        # get_last_trade_price
        ltp = MagicMock()
        ltp.price = 0.55
        client.get_last_trade_price = MagicMock(return_value=ltp)

        # get_price
        client.get_price = MagicMock(return_value=0.55)

        # get_spread
        client.get_spread = MagicMock(return_value=0.02)

        # get_market
        market_obj = MagicMock()
        market_obj.question = 'Will BTC hit 100k?'
        market_obj.condition_id = '0xabc'
        client.get_market = MagicMock(return_value=market_obj)

        client.close = MagicMock()

        return client

    @pytest.fixture
    def adapter_with_mock(self):
        adapter = PolymarketAdapter(config=PolymarketConfig(private_key='0xfake'))
        mock_client = self._make_mock_client()
        adapter._client = mock_client
        adapter._connected = True
        return adapter, mock_client

    @pytest.mark.asyncio
    async def test_place_limit_order(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        order = await adapter.place_order(_poly_instrument(), 'BUY', 100, order_type='LIMIT', price=0.55)
        assert order.order_id == 'poly-ord-1'
        assert order.status == 'LIVE'
        mock_client.place_limit_order.assert_called_once_with(
            token_id='0x1234567890abcdef', price=0.55, size=100, side='BUY',
        )

    @pytest.mark.asyncio
    async def test_place_market_order_buy(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        order = await adapter.place_order(_poly_instrument(), 'BUY', 50, order_type='MARKET')
        assert order.order_id == 'poly-ord-1'
        mock_client.place_market_order.assert_called_once_with(
            token_id='0x1234567890abcdef', side='BUY', amount=50,
        )

    @pytest.mark.asyncio
    async def test_place_market_order_sell(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        await adapter.place_order(_poly_instrument(), 'SELL', 50, order_type='MARKET')
        mock_client.place_market_order.assert_called_once_with(
            token_id='0x1234567890abcdef', side='SELL', shares=50,
        )

    @pytest.mark.asyncio
    async def test_cancel_order(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        assert await adapter.cancel_order('poly-ord-1') is True
        mock_client.cancel_order.assert_called_once_with(order_id='poly-ord-1')

    @pytest.mark.asyncio
    async def test_cancel_all(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        assert await adapter.cancel_all() is True
        mock_client.cancel_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_order(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        order = await adapter.get_order('poly-ord-1')
        assert order.order_id == 'poly-ord-1'
        assert order.side == 'BUY'
        assert order.qty == 100
        assert order.price == 0.55
        assert order.filled_qty == 50

    @pytest.mark.asyncio
    async def test_get_open_orders(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        orders = await adapter.get_open_orders()
        assert len(orders) == 1
        assert orders[0].order_id == 'poly-ord-1'
        assert orders[0].status == 'OPEN'

    @pytest.mark.asyncio
    async def test_get_ticker(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        ticker = await adapter.get_ticker(_poly_instrument())
        assert ticker.last == 0.55
        assert ticker.bid == 0.54
        assert ticker.ask == 0.56

    @pytest.mark.asyncio
    async def test_get_orderbook(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        ob = await adapter.get_orderbook(_poly_instrument())
        assert len(ob['bids']) == 1
        assert ob['bids'][0] == [0.54, 500]
        assert ob['asks'][0] == [0.56, 300]

    @pytest.mark.asyncio
    async def test_get_balances(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        balances = await adapter.get_balances()
        assert len(balances) == 1
        assert balances[0].currency == 'USDC'
        assert balances[0].total == 1500.0

    @pytest.mark.asyncio
    async def test_get_positions(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        positions = await adapter.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == '0xabc'
        assert positions[0].side == 'YES'
        assert positions[0].qty == 200.0
        assert positions[0].avg_entry == 0.45

    @pytest.mark.asyncio
    async def test_get_market(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        market = await adapter.get_market(market_id='0xabc')
        assert 'question' in market
        mock_client.get_market.assert_called_once_with(id='0xabc')

    @pytest.mark.asyncio
    async def test_get_market_by_slug(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        await adapter.get_market(slug='btc-100k')
        mock_client.get_market.assert_called_once_with(slug='btc-100k')

    @pytest.mark.asyncio
    async def test_get_price(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        price = await adapter.get_price('0xabc', side='BUY')
        assert price == 0.55
        mock_client.get_price.assert_called_once_with(token_id='0xabc', side='BUY')

    @pytest.mark.asyncio
    async def test_get_spread(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        spread = await adapter.get_spread('0xabc')
        assert spread == 0.02

    @pytest.mark.asyncio
    async def test_disconnect(self, adapter_with_mock):
        adapter, mock_client = adapter_with_mock
        await adapter.disconnect()
        assert not adapter.is_connected
        mock_client.close.assert_called_once()


# ── Order / Ticker data classes ──────────────────────────────────────────────

class TestOrderDataClass:
    def test_is_filled(self):
        assert Order(status='FILLED').is_filled
        assert not Order(status='REJECTED').is_filled


class TestTickerDataClass:
    def test_mid(self):
        t = Ticker(symbol='X', bid=99.0, ask=101.0)
        assert t.mid == 100.0

    def test_mid_fallback(self):
        t = Ticker(symbol='X', last=50.0)
        assert t.mid == 50.0

    def test_spread(self):
        t = Ticker(symbol='X', bid=99.0, ask=101.0)
        assert t.spread == 2.0

    def test_spread_zero(self):
        t = Ticker(symbol='X', last=50.0)
        assert t.spread == 0.0
