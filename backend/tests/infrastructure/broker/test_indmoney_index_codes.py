"""IndMoney index codes come from its own index master, not from guessed numbers."""

import pytest

from app.infrastructure.broker.indmoney_broker import INDMoneyBroker

MASTER = [
    {'EXCH': 'NSE', 'SEGMENT': 'NIFTY 50', 'SECURITY_ID': '40000001'},
    {'EXCH': 'NSE', 'SEGMENT': 'BANK NIFTY', 'SECURITY_ID': '40000003'},
    {'EXCH': 'BSE', 'SEGMENT': 'SENSEX', 'SECURITY_ID': '40000006'},
    {'EXCH': 'NSE', 'SEGMENT': 'Nifty Midcap 50', 'SECURITY_ID': '40000017'},
    {'EXCH': 'NSE', 'SEGMENT': 'Nifty Financial', 'SECURITY_ID': '40000100'},
    {'EXCH': 'NSE', 'SEGMENT': 'India VIX', 'SECURITY_ID': '40000107'},
    {'EXCH': 'NSE', 'SEGMENT': 'NIFTY PHARMA', 'SECURITY_ID': '40000005'},
    {'EXCH': 'NSE', 'SEGMENT': 'NIFTY 100', 'SECURITY_ID': '40000002'},
]


@pytest.fixture
def broker(monkeypatch):
    b = INDMoneyBroker(access_token='tok')
    monkeypatch.setattr(b, '_load_instruments', lambda source='equity': MASTER if source == 'index' else [])
    return b


@pytest.mark.parametrize('ticker,code', [
    ('^INDIAVIX', 'NSE_40000107'), ('INDIAVIX', 'NSE_40000107'),
    ('^NSEI', 'NSE_40000001'), ('NIFTY', 'NSE_40000001'),
    ('^NSEBANK', 'NSE_40000003'), ('BANKNIFTY', 'NSE_40000003'),
    ('^BSESN', 'BSE_40000006'), ('SENSEX', 'BSE_40000006'),
    ('^NSEMDCP50', 'NSE_40000017'),
])
def test_resolved_by_name_from_the_master(broker, ticker, code):
    assert broker._scrip_code(ticker) == code


def test_the_old_wrong_codes_are_gone(broker):
    # these used to be hardcoded and pointed at NIFTY 100 / BANK NIFTY / NIFTY PHARMA
    assert broker._scrip_code('^NSEBANK') != 'NSE_40000002'
    assert broker._scrip_code('^CNXFIN') != 'NSE_40000003'
    assert broker._scrip_code('^CNXFIN') != 'NSE_40000100'     # IndStocks' "Nifty Financial" is a different index
    assert broker._scrip_code('^NSEMDCP50') != 'NSE_40000005'


def test_unlisted_index_is_not_guessed(broker):
    # Nifty Financial Services is not in IndStocks' index master: no code, so callers use the data fallback
    assert broker._index_code('^CNXFIN') is None
    assert broker._index_code('FINNIFTY') is None


def test_case_insensitive(broker):
    assert broker._scrip_code('^indiavix') == 'NSE_40000107'


def test_follows_the_master_if_ids_change(monkeypatch):
    b = INDMoneyBroker(access_token='tok')
    changed = [{'EXCH': 'NSE', 'SEGMENT': 'India VIX', 'SECURITY_ID': '99999999'}]
    monkeypatch.setattr(b, '_load_instruments', lambda source='equity': changed if source == 'index' else [])
    assert b._scrip_code('^INDIAVIX') == 'NSE_99999999'


def test_master_unavailable_falls_back_only_for_verified_indices(monkeypatch):
    b = INDMoneyBroker(access_token='tok')
    monkeypatch.setattr(b, '_load_instruments', lambda source='equity': [])
    assert b._scrip_code('^NSEI') == 'NSE_40000001'
    assert b._scrip_code('^BSESN') == 'BSE_40000006'
    assert b._index_code('^INDIAVIX') is None        # never guess a code we cannot verify
    assert b._index_code('^NSEBANK') is None


def test_non_index_ticker_is_not_treated_as_index(broker):
    assert broker._index_code('RELIANCE.NS') is None


class TestIndexPriceStaysWithTheBroker:
    class Candle:
        def __init__(self, close):
            self.close = close

    @pytest.fixture
    def idx_broker(self, broker, monkeypatch):
        monkeypatch.setattr('app.infrastructure.broker.indmoney_broker._requests.get', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no REST')))
        return broker

    def test_uses_own_latest_candle_when_no_tick_is_cached(self, idx_broker, monkeypatch):
        monkeypatch.setattr(idx_broker, 'get_candles', lambda s, interval='5m', days=5, **k: [self.Candle(15.40), self.Candle(15.46)])
        yahoo_called = []
        monkeypatch.setattr('yfinance.Ticker', lambda s: yahoo_called.append(s) or (_ for _ in ()).throw(AssertionError('Yahoo used')))
        assert idx_broker.get_ltp('^INDIAVIX') == 15.46
        assert yahoo_called == []

    def test_falls_back_to_yahoo_only_when_broker_has_nothing(self, idx_broker, monkeypatch):
        monkeypatch.setattr(idx_broker, 'get_candles', lambda *a, **k: [])
        class FI:
            last_price = 15.5
        class T:
            def __init__(self, s): self.fast_info = FI()
        monkeypatch.setattr('yfinance.Ticker', T)
        assert idx_broker.get_ltp('^INDIAVIX') == 15.5
