"""
Telegram Notification Channel — sends messages via Telegram Bot API.

Requires:
    - TELEGRAM_BOT_TOKEN: Bot API token from @BotFather
    - TELEGRAM_CHAT_ID: Target chat/group ID

The channel formats notifications as Markdown messages and sends
them via the Bot API sendMessage endpoint.

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


_PRIORITY_EMOJI = {
    NotificationPriority.LOW: '\U0001f4ac',       # speech balloon
    NotificationPriority.NORMAL: '\U0001f4e2',    # loudspeaker
    NotificationPriority.HIGH: '\u26a0\ufe0f',    # warning
    NotificationPriority.CRITICAL: '\U0001f6a8',  # rotating light
}


@dataclass
class TelegramConfig:
    bot_token: str = ''
    chat_id: str = ''
    api_base: str = 'https://api.telegram.org'
    timeout_seconds: int = 10
    parse_mode: str = 'Markdown'


class TelegramChannel(NotificationChannel):
    """Sends notifications via Telegram Bot API."""

    def __init__(self, config: TelegramConfig | None = None) -> None:
        self._config = config or TelegramConfig()

    @property
    def name(self) -> str:
        return 'telegram'

    @property
    def is_configured(self) -> bool:
        return bool(self._config.bot_token and self._config.chat_id)

    def send(self, notification: Notification) -> DeliveryResult:
        if not self.is_configured:
            return DeliveryResult(
                channel=self.name, success=False,
                error='Telegram not configured (missing bot_token or chat_id)',
            )

        text = self._format(notification)

        try:
            response = self._call_api('sendMessage', {
                'chat_id': self._config.chat_id,
                'text': text,
                'parse_mode': self._config.parse_mode,
            })
            return DeliveryResult(
                channel=self.name, success=True,
                response_data=response,
            )
        except Exception as e:
            return DeliveryResult(
                channel=self.name, success=False,
                error=str(e),
            )

    def _format(self, n: Notification) -> str:
        emoji = _PRIORITY_EMOJI.get(n.priority, '')
        lines = [
            f'{emoji} *{n.title}*',
            '',
            n.body,
        ]
        if n.metadata:
            lines.append('')
            for k, v in n.metadata.items():
                lines.append(f'`{k}`: {v}')
        return '\n'.join(lines)

    def _call_api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call Telegram Bot API. Raises on failure."""
        url = f'{self._config.api_base}/bot{self._config.bot_token}/{method}'
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url, data=data,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
            return json.loads(resp.read())
