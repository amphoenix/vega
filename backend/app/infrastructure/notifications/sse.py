"""
SSE Manager — single Server-Sent Events broadcast hub.

All SSE streams (scanner events, position updates, alerts)
go through this one manager. Frontend subscribes by channel.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from typing import Any

from ...shared.logger import get_logger

logger = get_logger('sse')


class SSEManager:
    """Thread-safe SSE broadcaster with named channels."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, channel: str) -> asyncio.Queue:
        """Create a new subscription queue for a channel."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._queues[channel].append(q)
        logger.debug('SSE subscribe: %s (total %d)', channel, len(self._queues[channel]))
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        """Remove a subscription queue."""
        with self._lock:
            try:
                self._queues[channel].remove(q)
            except ValueError:
                pass

    def broadcast(self, channel: str, event: str, data: dict[str, Any]) -> None:
        """Send an event to all subscribers of a channel (thread-safe)."""
        payload = json.dumps({'event': event, **data})
        with self._lock:
            queues = list(self._queues.get(channel, []))

        dead: list[asyncio.Queue] = []
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)

        # Prune full/dead queues
        if dead:
            with self._lock:
                for q in dead:
                    try:
                        self._queues[channel].remove(q)
                    except ValueError:
                        pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(qs) for qs in self._queues.values())
