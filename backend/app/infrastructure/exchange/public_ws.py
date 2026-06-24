"""
PublicStreamManager — Binance and Bybit public WebSocket streams.

Architecture:
    WS daemon thread
        → loop.call_soon_threadsafe(q.put_nowait, data)
        → asyncio.Queue  (one per SSE client)
        → EventSourceResponse generator

No API keys required. Threads are started on first subscribe and reused
across all SSE clients for the same symbol. Set loop once from main.py
lifespan via set_loop(asyncio.get_running_loop()).
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from ...shared.logger import get_logger

logger = get_logger('public_ws')

_BINANCE_WS   = 'wss://stream.binance.com:9443/ws'
_BINANCE_FUTS = 'wss://fstream.binance.com/ws'    # futures mark-price / funding
_BYBIT_LINEAR = 'wss://stream.bybit.com/v5/public/linear'


class PublicStreamManager:
    """
    Manages public WebSocket subscriptions for Binance and Bybit.

    One daemon thread per unique stream channel, N subscriber queues per channel.
    Thread safety via threading.Lock. Asyncio bridge via loop.call_soon_threadsafe.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._running: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Call once from main.py lifespan with asyncio.get_running_loop()."""
        self._loop = loop

    # ── Subscribe / unsubscribe ───────────────────────────────────────────────

    def subscribe(self, channel: str) -> asyncio.Queue:
        """Create and register a queue for a channel. Call from async context."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.setdefault(channel, []).append(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(channel, [])
            try:
                subs.remove(q)
            except ValueError:
                pass

    # ── Internal broadcast (called from WS daemon threads) ───────────────────

    def _broadcast(self, channel: str, data: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            queues = list(self._subscribers.get(channel, []))
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, data)
            except Exception:
                pass

    # ── Binance ───────────────────────────────────────────────────────────────

    def start_binance_ticker(self, symbol: str) -> None:
        """24hr mini-ticker: real LTP, bid, ask, volume, change_pct."""
        sym = symbol.lower()
        self._start_binance(f'{sym}@ticker', f'binance:{sym}:ticker', _BINANCE_WS)

    def start_binance_kline(self, symbol: str, interval: str = '1m') -> None:
        sym = symbol.lower()
        self._start_binance(f'{sym}@kline_{interval}', f'binance:{sym}:kline_{interval}', _BINANCE_WS)

    def start_binance_depth(self, symbol: str) -> None:
        """Partial book depth @100ms — top 20 bid/ask levels."""
        sym = symbol.lower()
        self._start_binance(f'{sym}@depth20@100ms', f'binance:{sym}:depth', _BINANCE_WS)

    def start_binance_futures_ticker(self, symbol: str) -> None:
        """Futures mark-price + funding rate stream."""
        sym = symbol.lower()
        self._start_binance(f'{sym}@ticker', f'binance_fut:{sym}:ticker', _BINANCE_FUTS)

    def _start_binance(self, stream: str, channel: str, base: str) -> None:
        with self._lock:
            if self._running.get(channel):
                return
            self._running[channel] = True

        url = f'{base}/{stream}'

        def _thread() -> None:
            try:
                import websocket

                def on_message(ws: Any, msg: str) -> None:
                    try:
                        self._broadcast(channel, json.loads(msg))
                    except Exception:
                        pass

                def on_error(ws: Any, err: Any) -> None:
                    logger.warning('Binance WS [%s] error: %s', channel, err)

                def on_close(ws: Any, *_: Any) -> None:
                    logger.info('Binance WS [%s] closed', channel)
                    with self._lock:
                        self._running[channel] = False

                websocket.WebSocketApp(
                    url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                ).run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                logger.error('Binance WS [%s] thread fatal: %s', channel, exc)
                with self._lock:
                    self._running[channel] = False

        threading.Thread(target=_thread, name=f'bws:{channel}', daemon=True).start()
        logger.info('Binance WS started: %s', url)

    # ── Bybit ─────────────────────────────────────────────────────────────────

    def start_bybit_ticker(self, symbol: str) -> None:
        """Linear perpetual ticker: LTP, OI, funding rate."""
        sym = symbol.upper()
        self._start_bybit([f'tickers.{sym}'], f'bybit:{sym}:ticker')

    def start_bybit_orderbook(self, symbol: str, depth: int = 25) -> None:
        sym = symbol.upper()
        self._start_bybit([f'orderbook.{depth}.{sym}'], f'bybit:{sym}:orderbook')

    def start_bybit_trades(self, symbol: str) -> None:
        sym = symbol.upper()
        self._start_bybit([f'publicTrade.{sym}'], f'bybit:{sym}:trades')

    def _start_bybit(self, topics: list[str], channel: str) -> None:
        with self._lock:
            if self._running.get(channel):
                return
            self._running[channel] = True

        sub_msg = json.dumps({'op': 'subscribe', 'args': topics})

        def _thread() -> None:
            try:
                import websocket

                def on_open(ws: Any) -> None:
                    ws.send(sub_msg)
                    logger.info('Bybit WS subscribed: %s', topics)

                def on_message(ws: Any, msg: str) -> None:
                    try:
                        data = json.loads(msg)
                        if data.get('topic'):
                            self._broadcast(channel, data)
                    except Exception:
                        pass

                def on_error(ws: Any, err: Any) -> None:
                    logger.warning('Bybit WS [%s] error: %s', channel, err)

                def on_close(ws: Any, *_: Any) -> None:
                    logger.info('Bybit WS [%s] closed', channel)
                    with self._lock:
                        self._running[channel] = False

                websocket.WebSocketApp(
                    _BYBIT_LINEAR,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                ).run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                logger.error('Bybit WS [%s] thread fatal: %s', channel, exc)
                with self._lock:
                    self._running[channel] = False

        threading.Thread(target=_thread, name=f'byws:{channel}', daemon=True).start()
        logger.info('Bybit WS started for topics: %s', topics)


# ── Module-level singleton ────────────────────────────────────────────────────

_mgr: PublicStreamManager | None = None


def get_stream_manager() -> PublicStreamManager:
    global _mgr
    if _mgr is None:
        _mgr = PublicStreamManager()
    return _mgr
