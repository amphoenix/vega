"""
Discord Notification Channel — sends messages via Discord Webhook.

Requires:
    - DISCORD_WEBHOOK_URL: Full webhook URL from Discord channel settings

The channel formats notifications as Discord embeds with color-coded
priority and sends them via the webhook endpoint.

Infrastructure layer — does HTTP I/O.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any

from .base import (
    DeliveryResult, Notification, NotificationChannel,
    NotificationPriority,
)


# Discord embed colors by priority
_PRIORITY_COLORS = {
    NotificationPriority.LOW: 0x95a5a6,       # grey
    NotificationPriority.NORMAL: 0x3498db,     # blue
    NotificationPriority.HIGH: 0xf39c12,       # orange
    NotificationPriority.CRITICAL: 0xe74c3c,   # red
}


@dataclass
class DiscordConfig:
    webhook_url: str = ''
    username: str = 'Vega'
    timeout_seconds: int = 10


class DiscordChannel(NotificationChannel):
    """Sends notifications via Discord Webhook."""

    def __init__(self, config: DiscordConfig | None = None) -> None:
        self._config = config or DiscordConfig()

    @property
    def name(self) -> str:
        return 'discord'

    @property
    def is_configured(self) -> bool:
        return bool(self._config.webhook_url)

    def send(self, notification: Notification) -> DeliveryResult:
        if not self.is_configured:
            return DeliveryResult(
                channel=self.name, success=False,
                error='Discord not configured (missing webhook_url)',
            )

        payload = self._format(notification)

        try:
            self._call_webhook(payload)
            return DeliveryResult(channel=self.name, success=True)
        except Exception as e:
            return DeliveryResult(
                channel=self.name, success=False,
                error=str(e),
            )

    def _format(self, n: Notification) -> dict[str, Any]:
        color = _PRIORITY_COLORS.get(n.priority, 0x95a5a6)

        embed: dict[str, Any] = {
            'title': n.title,
            'description': n.body,
            'color': color,
        }

        if n.metadata:
            embed['fields'] = [
                {'name': k, 'value': str(v), 'inline': True}
                for k, v in n.metadata.items()
            ]

        return {
            'username': self._config.username,
            'embeds': [embed],
        }

    def _call_webhook(self, payload: dict[str, Any]) -> None:
        """POST to Discord webhook. Raises on failure."""
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            self._config.webhook_url, data=data,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
            # Discord returns 204 No Content on success
            if resp.status not in (200, 204):
                raise RuntimeError(f'Discord webhook returned {resp.status}')
