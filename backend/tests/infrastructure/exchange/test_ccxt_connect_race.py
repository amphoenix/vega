"""Concurrent connect() must build one exchange and never expose an unloaded one."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.infrastructure.exchange.ccxt_adapter import CCXTAdapter, CCXTConfig


class _FakeExchange:
    built: list['_FakeExchange'] = []

    def __init__(self, params):
        self.markets = None
        self.closed = False
        self.fail = False
        _FakeExchange.built.append(self)

    async def load_markets(self):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError('boom')
        self.markets = {'BTC/USDT': {}}

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset():
    _FakeExchange.built = []
    _FakeExchange.fail = False


def _patched():
    return patch('app.infrastructure.exchange.ccxt_adapter._get_ccxt_async',
                 return_value=SimpleNamespace(binance=_FakeExchange))


@pytest.mark.asyncio
async def test_concurrent_connect_builds_one_exchange():
    adapter = CCXTAdapter(CCXTConfig(exchange_name='binance'))
    with _patched():
        await asyncio.gather(*(adapter.connect() for _ in range(5)))
    assert len(_FakeExchange.built) == 1
    assert adapter.is_connected
    assert adapter._exchange.markets


@pytest.mark.asyncio
async def test_failed_load_closes_session_and_stays_disconnected():
    adapter = CCXTAdapter(CCXTConfig(exchange_name='binance'))
    with _patched():
        orig = _FakeExchange.load_markets

        async def failing(self):
            self.fail = True
            await orig(self)

        with patch.object(_FakeExchange, 'load_markets', failing):
            with pytest.raises(RuntimeError):
                await adapter.connect()
    assert not adapter.is_connected
    assert _FakeExchange.built[0].closed
