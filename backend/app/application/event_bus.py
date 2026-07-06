"""
Event Bus — simple, synchronous, in-process pub/sub.

Observer pattern. No Kafka, no Redis, no RabbitMQ.
This is a single-user trading system — keep it simple.

Usage:
    bus = EventBus()
    bus.subscribe(SignalGenerated, my_handler)
    bus.publish(SignalGenerated(symbol='NIFTY'))
    # → my_handler(event) is called synchronously
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from ..domain.events.events import DomainEvent
from ..shared.logger import get_logger

logger = get_logger('event_bus')


class EventBus:
    """In-process synchronous event dispatcher."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_type: type, handler: Callable[[DomainEvent], Any]) -> None:
        """Register a handler for a specific event type."""
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)
                logger.debug('Subscribed %s to %s', handler.__name__, event_type.__name__)

    def unsubscribe(self, event_type: type, handler: Callable) -> None:
        """Remove a handler for a specific event type."""
        with self._lock:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    def publish(self, event: DomainEvent) -> None:
        """Dispatch an event to all registered handlers (synchronous).

        Each handler runs in the caller's thread. If a handler raises,
        it is logged and swallowed — other handlers still fire.
        """
        with self._lock:
            handlers = list(self._handlers.get(type(event), []))

        if not handlers:
            logger.debug('No handlers for %s', type(event).__name__)
            return

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    'Handler %s failed for event %s',
                    handler.__name__,
                    type(event).__name__,
                )

    def clear(self) -> None:
        """Remove all subscriptions (used in tests)."""
        with self._lock:
            self._handlers.clear()

    @property
    def handler_count(self) -> int:
        """Total number of registered handlers across all event types."""
        with self._lock:
            return sum(len(h) for h in self._handlers.values())


# ── Module-level singleton ────────────────────────────────────────────────────
event_bus = EventBus()
