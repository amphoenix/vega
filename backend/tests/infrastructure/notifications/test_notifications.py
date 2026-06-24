"""
Tests for infrastructure/notifications/ — NotificationService, channels.
"""

import time
import pytest

from app.infrastructure.notifications.base import (
    DeliveryResult, Notification, NotificationCategory, NotificationChannel,
    NotificationPriority,
)
from app.infrastructure.notifications.telegram import TelegramChannel, TelegramConfig
from app.infrastructure.notifications.discord import DiscordChannel, DiscordConfig
from app.infrastructure.notifications.service import NotificationService


# ── Fake channel for testing (no real HTTP) ──────────────────────────────────

class FakeChannel(NotificationChannel):
    """In-memory channel for testing."""

    def __init__(self, name: str = 'fake', configured: bool = True, fail: bool = False):
        self._name = name
        self._configured = configured
        self._fail = fail
        self.sent: list[Notification] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_configured(self) -> bool:
        return self._configured

    def send(self, notification: Notification) -> DeliveryResult:
        if self._fail:
            raise RuntimeError('fake send error')
        self.sent.append(notification)
        return DeliveryResult(channel=self._name, success=True)


# ── Notification dataclass ───────────────────────────────────────────────────

class TestNotification:
    def test_basic(self):
        n = Notification(
            title='Trade Opened',
            body='NIFTY 22000 CE',
            category=NotificationCategory.TRADE_OPENED,
        )
        assert n.title == 'Trade Opened'
        assert n.priority == NotificationPriority.NORMAL
        assert not n.is_critical

    def test_critical(self):
        n = Notification(
            title='Kill Switch',
            body='Loss limit hit',
            category=NotificationCategory.KILL_SWITCH,
            priority=NotificationPriority.CRITICAL,
        )
        assert n.is_critical

    def test_to_dict(self):
        n = Notification(
            title='T', body='B',
            category=NotificationCategory.DAILY_SUMMARY,
            metadata={'pnl': -500},
        )
        d = n.to_dict()
        assert d['title'] == 'T'
        assert d['category'] == 'daily_summary'
        assert d['metadata']['pnl'] == -500


# ── Telegram Channel ─────────────────────────────────────────────────────────

class TestTelegramChannel:
    def test_not_configured(self):
        ch = TelegramChannel(TelegramConfig())
        assert not ch.is_configured
        assert ch.name == 'telegram'

    def test_configured(self):
        ch = TelegramChannel(TelegramConfig(bot_token='tok', chat_id='123'))
        assert ch.is_configured

    def test_send_not_configured(self):
        ch = TelegramChannel(TelegramConfig())
        n = Notification(title='T', body='B', category=NotificationCategory.TRADE_OPENED)
        result = ch.send(n)
        assert not result.success
        assert 'not configured' in result.error

    def test_format(self):
        ch = TelegramChannel(TelegramConfig(bot_token='tok', chat_id='123'))
        n = Notification(
            title='Kill Switch', body='Daily loss exceeded',
            category=NotificationCategory.KILL_SWITCH,
            priority=NotificationPriority.CRITICAL,
            metadata={'pnl': -2000},
        )
        text = ch._format(n)
        assert '*Kill Switch*' in text
        assert 'Daily loss exceeded' in text
        assert '`pnl`' in text


# ── Discord Channel ──────────────────────────────────────────────────────────

class TestDiscordChannel:
    def test_not_configured(self):
        ch = DiscordChannel(DiscordConfig())
        assert not ch.is_configured
        assert ch.name == 'discord'

    def test_configured(self):
        ch = DiscordChannel(DiscordConfig(webhook_url='https://discord.com/api/webhooks/x/y'))
        assert ch.is_configured

    def test_send_not_configured(self):
        ch = DiscordChannel(DiscordConfig())
        n = Notification(title='T', body='B', category=NotificationCategory.TRADE_OPENED)
        result = ch.send(n)
        assert not result.success
        assert 'not configured' in result.error

    def test_format(self):
        ch = DiscordChannel(DiscordConfig(webhook_url='https://x'))
        n = Notification(
            title='Trade Closed', body='NIFTY CE +250',
            category=NotificationCategory.TRADE_CLOSED,
            priority=NotificationPriority.NORMAL,
            metadata={'symbol': 'NIFTY', 'pnl': 250},
        )
        payload = ch._format(n)
        assert payload['username'] == 'Vega'
        assert len(payload['embeds']) == 1
        embed = payload['embeds'][0]
        assert embed['title'] == 'Trade Closed'
        assert embed['color'] == 0x3498db  # blue for NORMAL
        assert len(embed['fields']) == 2


# ── Notification Service ─────────────────────────────────────────────────────

class TestNotificationService:
    def _notif(self, **kw) -> Notification:
        defaults = {
            'title': 'Test',
            'body': 'Body',
            'category': NotificationCategory.TRADE_OPENED,
            'priority': NotificationPriority.NORMAL,
        }
        defaults.update(kw)
        return Notification(**defaults)

    def test_add_channel(self):
        svc = NotificationService()
        svc.add_channel(FakeChannel('a'))
        assert svc.channel_names == ['a']

    def test_remove_channel(self):
        svc = NotificationService()
        svc.add_channel(FakeChannel('a'))
        svc.remove_channel('a')
        assert svc.channel_names == []

    def test_notify_single_channel(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch)
        results = svc.notify(self._notif())
        assert len(results) == 1
        assert results[0].success
        assert len(ch.sent) == 1

    def test_notify_multi_channel(self):
        ch1 = FakeChannel('a')
        ch2 = FakeChannel('b')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch1)
        svc.add_channel(ch2)
        results = svc.notify(self._notif())
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_priority_filter(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch, min_priority=NotificationPriority.HIGH)

        # NORMAL < HIGH → filtered
        results = svc.notify(self._notif(priority=NotificationPriority.NORMAL))
        assert len(results) == 0
        assert len(ch.sent) == 0

        # HIGH >= HIGH → sent
        results = svc.notify(self._notif(priority=NotificationPriority.HIGH))
        assert len(results) == 1
        assert results[0].success

    def test_category_filter(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch, categories={NotificationCategory.KILL_SWITCH})

        # TRADE_OPENED not in filter → skipped
        results = svc.notify(self._notif(category=NotificationCategory.TRADE_OPENED))
        assert len(results) == 0

        # KILL_SWITCH in filter → sent
        results = svc.notify(self._notif(category=NotificationCategory.KILL_SWITCH))
        assert len(results) == 1

    def test_disabled_channel(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch, enabled=False)
        results = svc.notify(self._notif())
        assert len(results) == 0

    def test_set_enabled(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch, enabled=False)
        svc.set_enabled('a', True)
        results = svc.notify(self._notif())
        assert len(results) == 1

    def test_unconfigured_channel_skipped(self):
        ch = FakeChannel('a', configured=False)
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch)
        results = svc.notify(self._notif())
        assert len(results) == 0

    def test_channel_error_isolated(self):
        ch1 = FakeChannel('fail', fail=True)
        ch2 = FakeChannel('ok')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch1)
        svc.add_channel(ch2)
        results = svc.notify(self._notif())
        assert len(results) == 2
        assert not results[0].success
        assert 'channel error' in results[0].error
        assert results[1].success

    def test_rate_limiting(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0.5)
        svc.add_channel(ch)

        r1 = svc.notify(self._notif())
        assert r1[0].success

        r2 = svc.notify(self._notif())
        assert not r2[0].success
        assert r2[0].error == 'rate_limited'

    def test_delivery_history(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch)
        svc.notify(self._notif(title='First'))
        svc.notify(self._notif(title='Second'))
        history = svc.recent_deliveries()
        assert len(history) == 2
        assert history[0]['title'] == 'First'

    def test_stats(self):
        ch = FakeChannel('a')
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(ch)
        svc.notify(self._notif(category=NotificationCategory.TRADE_OPENED))
        svc.notify(self._notif(category=NotificationCategory.KILL_SWITCH))
        s = svc.stats()
        assert s['total_notifications'] == 2
        assert s['total_deliveries_success'] == 2
        assert s['by_category']['trade_opened'] == 1
        assert s['by_category']['kill_switch'] == 1

    def test_configured_channels(self):
        svc = NotificationService()
        svc.add_channel(FakeChannel('yes', configured=True))
        svc.add_channel(FakeChannel('no', configured=False))
        assert svc.configured_channels == ['yes']

    def test_reset(self):
        svc = NotificationService(rate_limit_seconds=0)
        svc.add_channel(FakeChannel('a'))
        svc.notify(self._notif())
        svc.reset()
        assert svc.channel_names == []
        assert svc.recent_deliveries() == []
