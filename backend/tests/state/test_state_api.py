"""HTTP surface of the server-owned state, plus persistence wiring of runtime toggles."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.state import router
from app.state import settings, signals


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestWatchlistApi:
    def test_empty_then_put_then_get(self, client):
        assert client.get('/api/watchlist').json() == {'success': True, 'data': []}
        body = {'items': [{'symbol': 'tcs', 'name': 'TCS'}, {'symbol': 'infy'}]}
        put = client.put('/api/watchlist', json=body).json()
        assert [i['symbol'] for i in put['data']] == ['TCS', 'INFY']
        assert client.get('/api/watchlist').json()['data'] == put['data']

    def test_second_device_sees_the_same_list(self, client):
        client.put('/api/watchlist', json={'items': [{'symbol': 'A'}]})
        other_device = TestClient(client.app)
        assert other_device.get('/api/watchlist').json()['data'][0]['symbol'] == 'A'

    def test_rejects_malformed_body(self, client):
        assert client.put('/api/watchlist', json={'items': 'nope'}).status_code == 422
        assert client.put('/api/watchlist', json={}).status_code == 422


class TestSignalHistoryApi:
    def test_history_and_filters(self, client):
        signals.record('swing', {'ticker': 'A', 'verdict': 'BUY'})
        signals.record('scalp', {'ticker': 'B', 'direction': 'SELL'})
        everything = client.get('/api/signals/history').json()['data']
        assert [s['ticker'] for s in everything] == ['B', 'A']
        only = client.get('/api/signals/history', params={'segment': 'swing'}).json()['data']
        assert [s['ticker'] for s in only] == ['A']

    def test_validates_params(self, client):
        assert client.get('/api/signals/history', params={'limit': 0}).status_code == 422
        assert client.get('/api/signals/history', params={'limit': 501}).status_code == 422
        assert client.get('/api/signals/history', params={'since_id': -1}).status_code == 422


class TestRuntimeTogglesSurviveRestart:
    def test_scalp_auto_trade_default_then_persisted(self):
        from app.engines import order_executor as oe
        default = oe._settings.scalp_auto_trade
        assert oe.scalp_auto_trading_enabled() is default
        settings.set('scalp_auto_trade', not default)
        settings.clear_cache()                       # simulate a restart
        assert oe.scalp_auto_trading_enabled() is (not default)

    def test_btst_config_persisted_and_reloaded(self):
        from app.engines import btst_scanner as b
        original = dict(b._config)
        try:
            b.update_config({'BTST_MAX_POSITIONS': '7', 'NOT_A_KEY': 1})
            assert b._config['BTST_MAX_POSITIONS'] == 7
            b._config['BTST_MAX_POSITIONS'] = 3       # simulate a fresh process with defaults
            settings.clear_cache()
            b._load_saved_config()
            assert b._config['BTST_MAX_POSITIONS'] == 7
            assert 'NOT_A_KEY' not in settings.get('btst_config')
        finally:
            b._config.clear()
            b._config.update(original)
