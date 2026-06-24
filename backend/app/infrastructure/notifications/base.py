"""
Notification Channel ABC — uniform interface for all notification backends.

Channels:
    - Telegram (bot API)
    - Discord (webhook)
    - Email (future)
    - SSE (in-browser, handled separately)

Each channel implements send(). The NotificationService routes events
to the appropriate channels based on configuration.

Infrastructure layer — channels do I/O but are injected via ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NotificationPriority(str, Enum):
    LOW      = 'low'       # daily summary, informational
    NORMAL   = 'normal'    # trade opened/closed
    HIGH     = 'high'      # loss limit, SL hit
    CRITICAL = 'critical'  # kill switch, system error


class NotificationCategory(str, Enum):
    TRADE_OPENED     = 'trade_opened'
    TRADE_CLOSED     = 'trade_closed'
    KILL_SWITCH      = 'kill_switch'
    LOSS_LIMIT       = 'loss_limit'
    DAILY_SUMMARY    = 'daily_summary'
    SYSTEM_ERROR     = 'system_error'
    REGIME_CHANGE    = 'regime_change'
    HEALTH_ALERT     = 'health_alert'


@dataclass(frozen=True, slots=True)
class Notification:
    """A single notification to be delivered."""
    title: str
    body: str
    category: NotificationCategory
    priority: NotificationPriority = NotificationPriority.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_critical(self) -> bool:
        return self.priority == NotificationPriority.CRITICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            'title': self.title,
            'body': self.body,
            'category': self.category.value,
            'priority': self.priority.value,
            'metadata': self.metadata,
        }


@dataclass
class DeliveryResult:
    """Result of a notification delivery attempt."""
    channel: str
    success: bool
    error: str = ''
    response_data: dict[str, Any] = field(default_factory=dict)


class NotificationChannel(ABC):
    """ABC for notification delivery channels."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel name (e.g. 'telegram', 'discord')."""
        ...

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True if the channel has valid credentials/config."""
        ...

    @abstractmethod
    def send(self, notification: Notification) -> DeliveryResult:
        """Deliver a notification. Returns result with success/error."""
        ...
