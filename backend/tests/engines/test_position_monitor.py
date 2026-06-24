"""
Tests for Position Monitor — repricing loop and event emission.
"""

import pytest
from app.domain.entities.position import Position, AlertStatus
from app.domain.services.position_store import PositionStore
from app.domain.events.events import AlertStatusChanged, PositionClosed
from app.engines.monitor.position_monitor import PositionMonitor


class FakePriceProvider:
    """Test double — returns preset prices."""
    def __init__(self, prices: dict[str, float] | None = None):
        self.prices = prices or {}

    def get_ltp(self, symbol: str) -> float | None:
        return self.prices.get(symbol)


def _pos(track_id: str = 'T1', symbol: str = 'NIFTY25000CE',
         entry: float = 100.0, sl: float = 85.0,
         t1: float = 130.0, t2: float = 160.0) -> Position:
    return Position(
        track_id=track_id, symbol=symbol, underlying='NIFTY',
        direction='CE', trade_mode='swing', qty=50, lot_size=50,
        entry_price=entry, stop_loss=sl, target_1=t1, target_2=t2,
    )


class TestPositionMonitor:
    def setup_method(self):
        self.store = PositionStore()
        self.prices = FakePriceProvider()
        self.events: list = []
        self.monitor = PositionMonitor(
            store=self.store,
            price_provider=self.prices,
            publish=self.events.append,
        )

    def test_no_positions_noop(self):
        self.monitor.poll_now()
        assert len(self.events) == 0

    def test_safe_zone_no_event(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 105.0
        self.monitor.poll_now()
        # Status stays SAFE → no alert change event
        assert len(self.events) == 0

    def test_near_sl_emits_alert_change(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 88.0  # near SL zone
        self.monitor.poll_now()
        assert len(self.events) == 1
        e = self.events[0]
        assert isinstance(e, AlertStatusChanged)
        assert e.old_status == 'safe'
        assert e.new_status == 'near_sl'

    def test_sl_hit_emits_close(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 80.0  # SL hit
        self.monitor.poll_now()
        # Should emit AlertStatusChanged + PositionClosed
        alert_events = [e for e in self.events if isinstance(e, AlertStatusChanged)]
        close_events = [e for e in self.events if isinstance(e, PositionClosed)]
        assert len(alert_events) == 1
        assert alert_events[0].new_status == 'sl_hit'
        assert len(close_events) == 1
        assert close_events[0].exit_reason == 'sl_hit'

    def test_t1_hit_trails_sl(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 135.0  # past T1
        self.monitor.poll_now()
        p = self.store.get('T1')
        assert p.sl_trailed
        assert p.stop_loss == 100.0  # trailed to entry (breakeven)

    def test_t2_hit_emits_close(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 165.0  # past T2
        self.monitor.poll_now()
        close_events = [e for e in self.events if isinstance(e, PositionClosed)]
        assert len(close_events) == 1
        assert close_events[0].exit_reason == 'past_t2'

    def test_time_exit(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 105.0
        monitor = PositionMonitor(
            store=self.store,
            price_provider=self.prices,
            publish=self.events.append,
            time_exit_checker=lambda: True,
        )
        monitor.poll_now()
        close_events = [e for e in self.events if isinstance(e, PositionClosed)]
        assert len(close_events) == 1
        assert close_events[0].exit_reason == 'time_exit'

    def test_no_price_skips(self):
        self.store.add(_pos())
        # No price set → get_ltp returns None
        self.monitor.poll_now()
        assert len(self.events) == 0

    def test_multiple_positions(self):
        self.store.add(_pos('T1', 'NIFTY25000CE'))
        self.store.add(_pos('T2', 'NIFTY25500CE'))
        self.prices.prices['NIFTY25000CE'] = 88.0   # near SL
        self.prices.prices['NIFTY25500CE'] = 135.0   # past T1
        self.monitor.poll_now()
        # T1 → near_sl alert, T2 → past_t1 alert
        alert_events = [e for e in self.events if isinstance(e, AlertStatusChanged)]
        assert len(alert_events) == 2

    def test_sl_trail_only_once(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 135.0
        self.monitor.poll_now()  # T1 hit, SL trailed
        p = self.store.get('T1')
        assert p.sl_trailed
        assert p.stop_loss == 100.0

        # Second poll — should NOT re-trail
        self.events.clear()
        self.monitor.poll_now()
        # Status stays PAST_T1 → no new alert event
        assert len(self.events) == 0

    def test_start_stop(self):
        """Lifecycle test — start and stop without errors."""
        self.monitor.start()
        assert self.monitor.is_running
        self.monitor.stop()
        assert not self.monitor.is_running

    def test_premium_updated_in_store(self):
        self.store.add(_pos())
        self.prices.prices['NIFTY25000CE'] = 110.0
        self.monitor.poll_now()
        p = self.store.get('T1')
        assert p.current_premium == 110.0
