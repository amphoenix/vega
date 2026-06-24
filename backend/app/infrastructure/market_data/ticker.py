"""
Market Data Ticker — placeholder for WebSocket tick stream.

Phase 3: port WS connection from v1's indmoney.py.
Provides callbacks for scanners and position monitor.
"""

from __future__ import annotations

from typing import Callable, Any

from ...shared.logger import get_logger

logger = get_logger('ticker')


class TickerService:
    """Real-time tick feed via WebSocket.

    Phase 3: implement WS connect, subscribe to symbols,
    dispatch ticks to registered callbacks.
    """

    def __init__(self) -> None:
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._connected = False

    def register_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        self._callbacks.append(cb)

    def start(self) -> None:
        logger.info('TickerService: placeholder — not yet connected')
        # Phase 3: establish WS connection

    def stop(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
