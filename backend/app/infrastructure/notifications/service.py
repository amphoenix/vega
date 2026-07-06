"""
Notification Service — routes domain events to notification channels.

Central orchestrator: receives Notification objects and fans them out
to all configured channels (Telegram, Discord, etc.).

Features:
    - Multi-channel fanout with per-channel error isolation.
    - Priority filtering (e.g. only send CRITICAL to Telegram).
    - Category filtering (e.g. only send TRADE_CLOSED to Discord).
    - Rate limiting to prevent notification spam.
    - Delivery history for debugging.

The application layer creates this service, registers channels,
and hooks it up to the event bus.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

from ...shared.time import monotonic
from .base import (
    DeliveryResult,
    Notification,
    NotificationCategory,
    NotificationChannel,
    NotificationPriority,
)


@dataclass
class ChannelRule:
    """Routing rule for a notification channel."""
    channel: NotificationChannel
    min_priority: NotificationPriority = NotificationPriority.LOW
    categories: set[NotificationCategory] | None = None  # None = all
    enabled: bool = True


@dataclass
class DeliveryRecord:
    """Record of a delivery attempt for audit."""
    notification: Notification
    results: list[DeliveryResult]
    timestamp: float  # monotonic


class NotificationService:
    """Central notification dispatcher.

    Usage:
        service = NotificationService()
        service.add_channel(telegram_channel, min_priority=NotificationPriority.HIGH)
        service.add_channel(discord_channel)  # receives everything

        service.notify(Notification(
            title='Trade Opened',
            body='NIFTY 22000 CE @ 150',
            category=NotificationCategory.TRADE_OPENED,
        ))
    """

    _PRIORITY_ORDER = {
        NotificationPriority.LOW: 0,
        NotificationPriority.NORMAL: 1,
        NotificationPriority.HIGH: 2,
        NotificationPriority.CRITICAL: 3,
    }

    def __init__(
        self,
        max_history: int = 200,
        rate_limit_seconds: float = 1.0,
    ) -> None:
        self._rules: list[ChannelRule] = []
        self._history: deque[DeliveryRecord] = deque(maxlen=max_history)
        self._lock = threading.Lock()
        self._rate_limit = rate_limit_seconds
        self._last_send: dict[str, float] = {}  # channel_name → monotonic

    # ── Channel Management ───────────────────────────────────────────

    def add_channel(
        self,
        channel: NotificationChannel,
        min_priority: NotificationPriority = NotificationPriority.LOW,
        categories: set[NotificationCategory] | None = None,
        enabled: bool = True,
    ) -> None:
        """Register a notification channel with routing rules."""
        with self._lock:
            self._rules.append(ChannelRule(
                channel=channel,
                min_priority=min_priority,
                categories=categories,
                enabled=enabled,
            ))

    def remove_channel(self, name: str) -> None:
        """Remove a channel by name."""
        with self._lock:
            self._rules = [r for r in self._rules if r.channel.name != name]

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable a channel."""
        with self._lock:
            for rule in self._rules:
                if rule.channel.name == name:
                    rule.enabled = enabled

    # ── Dispatch ─────────────────────────────────────────────────────

    def notify(self, notification: Notification) -> list[DeliveryResult]:
        """Send a notification to all matching channels.

        Returns list of DeliveryResult (one per channel that attempted).
        """
        results: list[DeliveryResult] = []

        with self._lock:
            rules = list(self._rules)

        for rule in rules:
            if not self._should_send(rule, notification):
                continue

            # Rate limiting per channel
            now = monotonic()
            last = self._last_send.get(rule.channel.name, 0)
            if now - last < self._rate_limit:
                results.append(DeliveryResult(
                    channel=rule.channel.name, success=False,
                    error='rate_limited',
                ))
                continue

            try:
                result = rule.channel.send(notification)
            except Exception as e:
                result = DeliveryResult(
                    channel=rule.channel.name, success=False,
                    error=f'channel error: {e}',
                )

            self._last_send[rule.channel.name] = now
            results.append(result)

        # Record delivery
        with self._lock:
            self._history.append(DeliveryRecord(
                notification=notification,
                results=results,
                timestamp=monotonic(),
            ))

        return results

    def _should_send(self, rule: ChannelRule, notification: Notification) -> bool:
        """Check if a notification matches a channel's routing rules."""
        if not rule.enabled:
            return False

        if not rule.channel.is_configured:
            return False

        # Priority filter
        rule_level = self._PRIORITY_ORDER.get(rule.min_priority, 0)
        notif_level = self._PRIORITY_ORDER.get(notification.priority, 0)
        if notif_level < rule_level:
            return False

        # Category filter
        if rule.categories is not None and \
                notification.category not in rule.categories:
            return False

        return True

    # ── Queries ──────────────────────────────────────────────────────

    @property
    def channel_names(self) -> list[str]:
        with self._lock:
            return [r.channel.name for r in self._rules]

    @property
    def configured_channels(self) -> list[str]:
        with self._lock:
            return [r.channel.name for r in self._rules if r.channel.is_configured]

    def recent_deliveries(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent delivery records for debugging."""
        with self._lock:
            records = list(self._history)
        return [
            {
                'title': r.notification.title,
                'category': r.notification.category.value,
                'priority': r.notification.priority.value,
                'results': [
                    {'channel': dr.channel, 'success': dr.success, 'error': dr.error}
                    for dr in r.results
                ],
            }
            for r in records[-limit:]
        ]

    def stats(self) -> dict[str, Any]:
        """Aggregate notification stats."""
        with self._lock:
            records = list(self._history)

        total = len(records)
        successes = sum(
            1 for r in records
            for dr in r.results if dr.success
        )
        failures = sum(
            1 for r in records
            for dr in r.results if not dr.success
        )

        by_category: dict[str, int] = {}
        for r in records:
            cat = r.notification.category.value
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            'total_notifications': total,
            'total_deliveries_success': successes,
            'total_deliveries_failed': failures,
            'by_category': by_category,
            'channels': self.channel_names,
            'configured': self.configured_channels,
        }

    def reset(self) -> None:
        """Clear all state (used in tests)."""
        with self._lock:
            self._rules.clear()
            self._history.clear()
            self._last_send.clear()
