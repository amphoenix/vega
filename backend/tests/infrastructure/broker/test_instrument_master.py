"""Instrument-master loading: one shared download, retry on rate limit, no caching of failures."""

import threading
from unittest.mock import patch

import pytest

from app.infrastructure.broker import base as broker_base
from app.infrastructure.broker.groww_broker import GrowwBroker
from app.infrastructure.broker.indmoney_broker import INDMoneyBroker
from app.shared.time import sleep

_CSV = 'TRADING_SYMBOL,OPTION_TYPE\nNIFTY-NOV2026-23000-CE,CE\nNIFTY-NOV2026-23000-PE,PE\n'


class _Resp:
    def __init__(self, status: int = 200, text: str = _CSV):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = text


class _FakeGet:
    """Stands in for requests.get; counts calls, optionally slow, replays scripted statuses."""

    def __init__(self, statuses=(200,), delay: float = 0.0):
        self.statuses = list(statuses)
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self._lock:
            i = self.calls
            self.calls += 1
        if self.delay:
            sleep(self.delay)
        return _Resp(self.statuses[min(i, len(self.statuses) - 1)])


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(broker_base, 'RETRY_DELAYS', (0.0, 0.0, 0.0))


def _run_threads(fn, n=8):
    results = [None] * n

    def work(i):
        results[i] = fn()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class TestIndMoneyInstrumentMaster:
    @pytest.fixture
    def broker(self):
        return INDMoneyBroker(access_token='tok')

    def test_concurrent_callers_share_one_download(self, broker):
        fake = _FakeGet(delay=0.2)
        with patch('app.infrastructure.broker.indmoney_broker._requests.get', fake):
            results = _run_threads(lambda: broker._load_instruments('fno'))
        assert fake.calls == 1
        assert all(len(r) == 2 for r in results)

    def test_cached_after_first_load(self, broker):
        fake = _FakeGet()
        with patch('app.infrastructure.broker.indmoney_broker._requests.get', fake):
            broker._load_instruments('fno')
            broker._load_instruments('fno')
        assert fake.calls == 1

    def test_rate_limit_is_retried_then_succeeds(self, broker):
        fake = _FakeGet(statuses=(429, 429, 200))
        with patch('app.infrastructure.broker.indmoney_broker._requests.get', fake):
            rows = broker._load_instruments('fno')
        assert len(rows) == 2
        assert fake.calls == 3

    def test_persistent_failure_returns_empty_and_is_not_cached(self, broker):
        fake = _FakeGet(statuses=(429,))
        with patch('app.infrastructure.broker.indmoney_broker._requests.get', fake):
            assert broker._load_instruments('fno') == []
            first = fake.calls
            assert first == 1 + len(broker_base.RETRY_DELAYS)
            fake.statuses = [200]
            assert len(broker._load_instruments('fno')) == 2  # retried later, not stuck on []

    def test_non_retryable_error_is_not_retried(self, broker):
        fake = _FakeGet(statuses=(403,))
        with patch('app.infrastructure.broker.indmoney_broker._requests.get', fake):
            assert broker._load_instruments('fno') == []
        assert fake.calls == 1


class TestGrowwInstrumentMaster:
    @pytest.fixture
    def broker(self):
        return GrowwBroker(access_token='tok')

    def test_concurrent_callers_share_one_download(self, broker):
        fake = _FakeGet(delay=0.2)
        with patch('app.infrastructure.broker.groww_broker._requests.get', fake):
            results = _run_threads(lambda: broker.load_instruments('fno'))
        assert fake.calls == 1
        assert all(len(r) == 2 for r in results)

    def test_rate_limit_is_retried_then_succeeds(self, broker):
        fake = _FakeGet(statuses=(429, 200))
        with patch('app.infrastructure.broker.groww_broker._requests.get', fake):
            assert len(broker.load_instruments('fno')) == 2
        assert fake.calls == 2


class TestRetryHelper:
    def test_returns_last_response_when_retries_exhausted(self):
        fake = _FakeGet(statuses=(503,))
        resp = broker_base.http_get_with_retry(fake, 'http://x')
        assert resp.status_code == 503
        assert fake.calls == 1 + len(broker_base.RETRY_DELAYS)

    def test_passes_through_success_immediately(self):
        fake = _FakeGet(statuses=(200,))
        assert broker_base.http_get_with_retry(fake, 'http://x').ok
        assert fake.calls == 1
