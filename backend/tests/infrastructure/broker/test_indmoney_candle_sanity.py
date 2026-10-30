"""A candle whose close is outside its own high/low is corrupt and must be dropped."""

from types import SimpleNamespace

import pytest

from app.infrastructure.broker.indmoney_broker import INDMoneyBroker

# real shape seen from the broker: settled bars flat, forming bar close far outside h/l
RAW = [
    {'ts': 1793353980, 'o': 23010, 'h': 23010, 'l': 23010, 'c': 23010, 'v': 10660},
    {'ts': 1793354040, 'o': 23010, 'h': 23010, 'l': 23010, 'c': 23090.9, 'v': 0},
]


@pytest.fixture
def broker(monkeypatch):
    b = INDMoneyBroker(access_token='tok')
    monkeypatch.setattr(b, '_scrip_code', lambda *a, **k: 'NSE_40000001')
    monkeypatch.setattr(
        'app.infrastructure.broker.indmoney_broker.http_get_with_retry',
        lambda *a, **k: SimpleNamespace(ok=True, status_code=200,
                                        json=lambda: {'data': {'NSE_40000001': {'candles': RAW}}}))
    return b


def test_corrupt_forming_bar_is_dropped(broker):
    candles = broker.get_candles('^NSEI', interval='1m', days=1)
    assert [c.close for c in candles] == [23010]


def test_index_price_uses_last_valid_bar(broker, monkeypatch):
    monkeypatch.setattr(broker, '_load_instruments', lambda source='equity': [])
    assert broker.get_ltp('^NSEI') == 23010


def test_resolved_option_contract_carries_lot_size(monkeypatch):
    b = INDMoneyBroker(access_token='tok')
    rows = [{'TRADING_SYMBOL': 'SENSEX-05NOV2026-74500-PE', 'OPTION_TYPE': 'PE', 'EXCH': 'BFO',
             'EXPIRY_DATE': '2999-01-01', 'STRIKE_PRICE': '74500', 'SECURITY_ID': '1', 'LOT_UNITS': '20'}]
    monkeypatch.setattr(b, '_load_instruments', lambda source='equity': rows)
    monkeypatch.setattr(b, 'get_ltp', lambda *a, **k: 100.0)
    c = b.resolve_option_contract('SENSEX', 'PE', 74500)
    assert c['lot_size'] == 20
