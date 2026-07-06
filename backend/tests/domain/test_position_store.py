"""
Tests for Position Store — thread-safe in-memory position container.
"""

import threading

import pytest

from app.domain.entities.position import AlertStatus, Position
from app.domain.services.position_store import PositionStore


def _pos(track_id: str = 'T1', symbol: str = 'NIFTY25000CE',
         underlying: str = 'NIFTY', mode: str = 'swing') -> Position:
    return Position(
        track_id=track_id,
        symbol=symbol,
        underlying=underlying,
        direction='CE',
        trade_mode=mode,
        qty=50,
        lot_size=50,
        entry_price=100.0,
        stop_loss=85.0,
        target_1=130.0,
        target_2=160.0,
    )


class TestBasicOps:
    def test_add_and_get(self):
        store = PositionStore()
        store.add(_pos())
        assert len(store) == 1
        assert store.get('T1') is not None
        assert 'T1' in store

    def test_add_duplicate_raises(self):
        store = PositionStore()
        store.add(_pos())
        with pytest.raises(ValueError, match='already tracked'):
            store.add(_pos())

    def test_remove(self):
        store = PositionStore()
        store.add(_pos())
        removed = store.remove('T1')
        assert removed is not None
        assert len(store) == 0
        assert store.remove('T1') is None  # idempotent

    def test_clear(self):
        store = PositionStore()
        store.add(_pos('T1'))
        store.add(_pos('T2'))
        store.clear()
        assert len(store) == 0


class TestUpdates:
    def test_update_premium(self):
        store = PositionStore()
        store.add(_pos())
        p = store.update_premium('T1', 110.0, spot=25100.0)
        assert p.current_premium == 110.0
        assert p.current_spot == 25100.0

    def test_update_premium_missing(self):
        store = PositionStore()
        assert store.update_premium('NOPE', 110.0) is None

    def test_update_alert(self):
        store = PositionStore()
        store.add(_pos())
        old = store.update_alert('T1', AlertStatus.NEAR_T1)
        assert old == AlertStatus.SAFE  # was SAFE, changed to NEAR_T1

    def test_update_alert_no_change(self):
        store = PositionStore()
        store.add(_pos())
        old = store.update_alert('T1', AlertStatus.SAFE)
        assert old is None  # same status

    def test_trail_sl(self):
        store = PositionStore()
        store.add(_pos())
        store.trail_sl('T1', 100.0)
        p = store.get('T1')
        assert p.stop_loss == 100.0
        assert p.sl_trailed

    def test_mark_partial_exit(self):
        store = PositionStore()
        store.add(_pos())
        store.mark_partial_exit('T1')
        assert store.get('T1').partial_exited


class TestQueries:
    def test_all(self):
        store = PositionStore()
        store.add(_pos('T1'))
        store.add(_pos('T2'))
        assert len(store.all()) == 2

    def test_by_underlying(self):
        store = PositionStore()
        store.add(_pos('T1', underlying='NIFTY'))
        store.add(_pos('T2', underlying='SENSEX'))
        store.add(_pos('T3', underlying='NIFTY'))
        assert len(store.by_underlying('NIFTY')) == 2
        assert len(store.by_underlying('SENSEX')) == 1

    def test_by_mode(self):
        store = PositionStore()
        store.add(_pos('T1', mode='swing'))
        store.add(_pos('T2', mode='scalp'))
        assert len(store.by_mode('swing')) == 1
        assert len(store.by_mode('scalp')) == 1

    def test_total_unrealized_pnl(self):
        store = PositionStore()
        p1 = _pos('T1')
        p1.current_premium = 110.0  # +10 * 50 = +500
        store.add(p1)
        p2 = _pos('T2')
        p2.current_premium = 90.0   # -10 * 50 = -500
        store.add(p2)
        assert store.total_unrealized_pnl == 0.0

    def test_to_list(self):
        store = PositionStore()
        store.add(_pos())
        lst = store.to_list()
        assert len(lst) == 1
        assert lst[0]['track_id'] == 'T1'


class TestThreadSafety:
    def test_concurrent_adds(self):
        store = PositionStore()
        errors = []

        def adder(i: int):
            try:
                store.add(_pos(f'T{i}'))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=adder, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(store) == 100
