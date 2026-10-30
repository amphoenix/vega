"""Swing and Scalp must never buy the same contract, and scalp must find SENSEX contracts."""

import threading

import pytest

from app.engines import fo_scanner as fo
from app.engines import order_executor as oe
from app.engines import scalp_scanner as sc

SYM = 'SENSEX-05NOV2026-74500-CE'


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(oe, '_symbol_claims', {})
    monkeypatch.setattr(oe, '_inflight', set())
    monkeypatch.setattr(oe, '_scalp_inflight', set())
    self_now = {'t': 1000.0}
    monkeypatch.setattr(oe, 'clock', lambda: self_now['t'])
    return self_now


class TestClaim:
    def test_first_claim_wins_second_loses(self):
        with oe._lock:
            assert oe._claim_symbol(SYM) is True
            assert oe._claim_symbol(SYM) is False

    def test_other_contracts_are_independent(self):
        with oe._lock:
            assert oe._claim_symbol(SYM) is True
            assert oe._claim_symbol('NIFTY-NOV2026-23100-CE') is True

    def test_claim_expires(self, _clean):
        with oe._lock:
            oe._claim_symbol(SYM)
        _clean['t'] += oe._CLAIM_TTL_SEC + 1
        with oe._lock:
            assert oe._claim_symbol(SYM) is True

    def test_blocked_while_either_engine_has_it_in_flight(self):
        with oe._lock:
            oe._scalp_inflight.add(SYM)
            assert oe._claim_symbol(SYM) is False
            oe._scalp_inflight.clear()
            oe._inflight.add(SYM)
            assert oe._claim_symbol(SYM) is False

    def test_concurrent_swing_and_scalp_only_one_wins(self):
        wins, barrier = [], threading.Barrier(8)

        def attempt():
            barrier.wait()
            with oe._lock:
                wins.append(oe._claim_symbol(SYM))

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert wins.count(True) == 1


class TestContractLookupUsesBaseName:
    """The SENSEX bug: brokers know 'SENSEX', not '^BSESN' — every SENSEX lookup used to fail."""

    class Recorder:
        def __init__(self):
            self.asked = []

        def resolve_option_contract(self, underlying, option_type, strike):
            self.asked.append(underlying)
            return {'trading_symbol': f'{underlying}-X'}

    @pytest.mark.parametrize('ticker,base', [('^BSESN', 'SENSEX'), ('^NSEI', 'NIFTY'), ('^NSEBANK', 'BANKNIFTY')])
    def test_scalp(self, monkeypatch, ticker, base):
        rec = self.Recorder()
        monkeypatch.setattr(sc, 'get_broker', lambda: rec)
        sc._resolve_contract(ticker, 'CE', 100)
        assert rec.asked == [base]

    def test_swing(self, monkeypatch):
        rec = self.Recorder()
        monkeypatch.setattr(fo, 'get_broker', lambda: rec)
        fo._resolve_option_contract('^BSESN', 'PE', 74500)
        assert rec.asked == ['SENSEX']
