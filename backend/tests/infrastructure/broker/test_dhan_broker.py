"""Tests for Dhan broker adapter and BrokerFactory."""

import pytest
from unittest.mock import MagicMock, patch

from app.infrastructure.broker.base import (
    BrokerFactory, register_broker,
    BrokerAdapter, OrderResult, QuoteResult, PositionInfo,
    HoldingInfo, CandleData, InstrumentInfo,
)
from app.infrastructure.broker.dhan_broker import DhanBroker


class TestDhanBrokerStub:
    """Test Dhan broker in stub mode (no SDK / no credentials)."""

    @pytest.fixture
    def broker(self):
        return DhanBroker()  # no credentials → stub mode

    def test_broker_name(self, broker):
        assert broker.broker_name == 'dhan'

    def test_place_order_stub(self, broker):
        result = broker.place_order('NIFTY', 'BUY', 50)
        assert not result.success
        assert result.status == 'REJECTED'

    def test_modify_order_stub(self, broker):
        result = broker.modify_order('fake', qty=50)
        assert not result.success

    def test_cancel_order_stub(self, broker):
        assert broker.cancel_order('fake') is False

    def test_get_ltp_stub(self, broker):
        assert broker.get_ltp('NIFTY') is None

    def test_get_quote_stub(self, broker):
        assert broker.get_quote('NIFTY') is None

    def test_get_positions_stub(self, broker):
        assert broker.get_positions() == []

    def test_get_available_cash_stub(self, broker):
        assert broker.get_available_cash() == 0.0

    def test_get_holdings_stub(self, broker):
        assert broker.get_holdings() == []

    def test_get_order_list_stub(self, broker):
        assert broker.get_order_list() == []

    def test_get_order_status_stub(self, broker):
        assert broker.get_order_status('fake') == {}

    def test_get_candles_stub(self, broker):
        assert broker.get_candles('NIFTY') == []

    def test_subscribe_ticks_stub(self, broker):
        assert broker.subscribe_ticks(['NIFTY']) is False

    def test_search_instruments_stub(self, broker):
        assert broker.search_instruments('NIFTY') == []

    def test_extra_kwargs_ignored(self):
        """Factory may pass extra YAML keys — broker should accept **kwargs."""
        b = DhanBroker(client_id='', access_token='', some_future_field='ignored')
        assert b.broker_name == 'dhan'


class TestDhanBrokerMocked:
    """Test real code paths with mocked dhanhq SDK."""

    def _make_mock_dhan(self):
        mock = MagicMock()
        mock.place_order.return_value = {
            'status': 'success',
            'data': {'orderId': '112233', 'orderStatus': 'TRANSIT'},
        }
        mock.modify_order.return_value = {
            'status': 'success',
            'data': {'orderId': '112233'},
        }
        mock.cancel_order.return_value = {'status': 'success'}
        mock.get_order_by_id.return_value = {
            'data': {'orderId': '112233', 'orderStatus': 'TRADED'},
        }
        mock.quote_data.return_value = {
            'data': {
                'NSE_FNO:12345': {
                    'last_price': 24350.5,
                    'depth': {
                        'buy': [{'price': 24350.0}],
                        'sell': [{'price': 24351.0}],
                    },
                    'volume': 150000,
                    'ohlc': {'open': 24300, 'high': 24400, 'low': 24200, 'close': 24350},
                },
            },
        }
        mock.get_positions.return_value = {
            'data': [
                {'tradingSymbol': 'NIFTY JUN 24000 CE', 'netQty': 50,
                 'averagePrice': 150.0, 'ltp': 180.0,
                 'realizedProfit': 0, 'unrealizedProfit': 1500.0,
                 'securityId': '12345', 'exchangeSegment': 'NSE_FNO',
                 'productType': 'INTRADAY'},
            ],
        }
        mock.get_fund_limits.return_value = {
            'data': {'availabelBalance': 50000.0},
        }
        mock.get_holdings.return_value = {
            'data': [{'tradingSymbol': 'RELIANCE', 'totalQty': 10,
                       'avgCostPrice': 2500.0, 'ltp': 2600.0,
                       'unrealizedProfit': 1000.0, 'securityId': '99'}],
        }
        mock.get_order_list.return_value = {'data': [{'orderId': '112233'}]}
        mock.intraday_minute_data.return_value = {
            'data': {
                'open': [100, 101], 'high': [102, 103],
                'low': [99, 100], 'close': [101, 102],
                'volume': [1000, 1100], 'timestamp': ['2025-06-18 09:15', '2025-06-18 09:20'],
            },
        }
        return mock

    @pytest.fixture
    def broker_with_mock(self):
        broker = DhanBroker(client_id='100001', access_token='fake-token')
        broker._stub_mode = False
        broker._client = self._make_mock_dhan()
        return broker

    def test_place_order(self, broker_with_mock):
        result = broker_with_mock.place_order('NIFTY', 'BUY', 50, security_id='12345')
        assert result.success
        assert result.order_id == '112233'

    def test_modify_order(self, broker_with_mock):
        result = broker_with_mock.modify_order('112233', qty=100, price=155.0)
        assert result.success

    def test_cancel_order(self, broker_with_mock):
        assert broker_with_mock.cancel_order('112233') is True

    def test_get_ltp(self, broker_with_mock):
        ltp = broker_with_mock.get_ltp('NIFTY', security_id='12345')
        assert ltp == 24350.5

    def test_get_quote(self, broker_with_mock):
        q = broker_with_mock.get_quote('NIFTY', security_id='12345')
        assert q.ltp == 24350.5
        assert q.bid == 24350.0
        assert q.ask == 24351.0
        assert q.open == 24300
        assert q.high == 24400

    def test_get_positions(self, broker_with_mock):
        positions = broker_with_mock.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == 'NIFTY JUN 24000 CE'
        assert positions[0].qty == 50
        assert positions[0].pnl == 1500.0
        assert positions[0].exchange == 'NSE_FNO'

    def test_get_available_cash(self, broker_with_mock):
        assert broker_with_mock.get_available_cash() == 50000.0

    def test_get_holdings(self, broker_with_mock):
        holdings = broker_with_mock.get_holdings()
        assert len(holdings) == 1
        assert isinstance(holdings[0], HoldingInfo)
        assert holdings[0].symbol == 'RELIANCE'
        assert holdings[0].qty == 10

    def test_get_order_list(self, broker_with_mock):
        orders = broker_with_mock.get_order_list()
        assert len(orders) == 1

    def test_get_order_status(self, broker_with_mock):
        status = broker_with_mock.get_order_status('112233')
        assert status['orderStatus'] == 'TRADED'

    def test_place_limit_order(self, broker_with_mock):
        result = broker_with_mock.place_order(
            'NIFTY', 'BUY', 50,
            order_type='LIMIT', price=150.0, security_id='12345',
        )
        assert result.success

    def test_get_candles(self, broker_with_mock):
        candles = broker_with_mock.get_candles('NIFTY', interval='5m', days=1, security_id='12345')
        assert len(candles) == 2
        assert isinstance(candles[0], CandleData)
        assert candles[0].open == 100
        assert candles[1].close == 102


class TestBrokerFactory:
    def test_create_dhan_new_yaml_format(self):
        """Factory creates DhanBroker using new config-driven YAML format."""
        with patch('app.infrastructure.config.config_service.load_yaml', return_value={
            'active_broker': 'dhan',
            'brokers': {
                'dhan': {
                    'class': 'app.infrastructure.broker.dhan_broker.DhanBroker',
                    'env_keys': {'DHAN_CLIENT_ID': 'client_id', 'DHAN_ACCESS_TOKEN': 'access_token'},
                    'defaults': {'product_type': 'INTRADAY'},
                },
            },
        }):
            broker = BrokerFactory.create('dhan')
        assert broker.broker_name == 'dhan'

    def test_create_dhan_legacy_yaml_format(self):
        """Factory still works with old flat YAML format (backward compat)."""
        with patch('app.infrastructure.config.config_service.load_yaml', return_value={
            'active_broker': 'dhan',
            'brokers': {'dhan': {'product_type': 'INTRADAY'}},
        }):
            broker = BrokerFactory.create('dhan')
        assert broker.broker_name == 'dhan'

    def test_create_uses_active_broker(self):
        """Factory uses active_broker from YAML when no name passed."""
        with patch('app.infrastructure.config.config_service.load_yaml', return_value={
            'active_broker': 'dhan',
            'brokers': {
                'dhan': {
                    'class': 'app.infrastructure.broker.dhan_broker.DhanBroker',
                    'env_keys': {},
                    'defaults': {},
                },
            },
        }):
            broker = BrokerFactory.create()
        assert broker.broker_name == 'dhan'

    def test_unknown_broker_raises(self):
        with patch('app.infrastructure.config.config_service.load_yaml', return_value={'brokers': {}}):
            with pytest.raises(ValueError, match="Unknown broker 'nonexistent'"):
                BrokerFactory.create('nonexistent')

    def test_register_custom_broker(self):
        register_broker('zerodha', 'app.infrastructure.broker.dhan_broker', 'DhanBroker')
        from app.infrastructure.broker.base import _BROKER_REGISTRY
        assert 'zerodha' in _BROKER_REGISTRY

    def test_is_paper_mode(self):
        from unittest.mock import patch, PropertyMock
        from app.config import Settings
        with patch('app.config.settings', Settings(trading_mode='paper')):
            assert BrokerFactory.is_paper_mode()
        with patch('app.config.settings', Settings(trading_mode='live')):
            assert not BrokerFactory.is_paper_mode()

    def test_env_keys_resolve(self):
        """env_keys mapping correctly resolves env vars to constructor kwargs."""
        with patch('app.infrastructure.config.config_service.load_yaml', return_value={
            'active_broker': 'dhan',
            'brokers': {
                'dhan': {
                    'class': 'app.infrastructure.broker.dhan_broker.DhanBroker',
                    'env_keys': {'DHAN_CLIENT_ID': 'client_id', 'DHAN_ACCESS_TOKEN': 'access_token'},
                    'defaults': {'product_type': 'CNC'},
                },
            },
        }), patch.dict('os.environ', {'DHAN_CLIENT_ID': '100001', 'DHAN_ACCESS_TOKEN': 'tok123'}):
            broker = BrokerFactory.create('dhan')
        assert broker.broker_name == 'dhan'
        assert broker._client_id == '100001'
        assert broker._default_product == 'CNC'
