"""
Tests for Alert Classifier — position alert status classification.
"""

from app.domain.entities.position import AlertStatus, Position
from app.domain.services.alert_classifier import classify_alert


def _pos(entry: float = 100.0, sl: float = 85.0,
         t1: float = 130.0, t2: float = 160.0) -> Position:
    return Position(
        track_id='T1', symbol='NIFTY25000CE', underlying='NIFTY',
        direction='CE', trade_mode='swing', qty=50, lot_size=50,
        entry_price=entry, stop_loss=sl, target_1=t1, target_2=t2,
    )


class TestAlertClassification:
    def test_sl_hit(self):
        assert classify_alert(_pos(), 85.0) == AlertStatus.SL_HIT

    def test_sl_below(self):
        assert classify_alert(_pos(), 80.0) == AlertStatus.SL_HIT

    def test_near_sl(self):
        # SL distance = 100 - 85 = 15, 80% = 12 points below entry = 88
        assert classify_alert(_pos(), 88.0) == AlertStatus.NEAR_SL

    def test_safe_zone(self):
        assert classify_alert(_pos(), 105.0) == AlertStatus.SAFE

    def test_at_entry(self):
        assert classify_alert(_pos(), 100.0) == AlertStatus.SAFE

    def test_near_t1(self):
        # T1 distance = 130 - 100 = 30, 90% = 27 above entry = 127
        assert classify_alert(_pos(), 128.0) == AlertStatus.NEAR_T1

    def test_past_t1(self):
        assert classify_alert(_pos(), 135.0) == AlertStatus.PAST_T1

    def test_at_t1(self):
        assert classify_alert(_pos(), 130.0) == AlertStatus.PAST_T1

    def test_near_t2(self):
        # T2 distance from T1 = 160 - 130 = 30, 90% = 27 above T1 = 157
        assert classify_alert(_pos(), 158.0) == AlertStatus.NEAR_T2

    def test_past_t2(self):
        assert classify_alert(_pos(), 165.0) == AlertStatus.PAST_T2

    def test_at_t2(self):
        assert classify_alert(_pos(), 160.0) == AlertStatus.PAST_T2

    def test_tight_levels(self):
        """Tight SL/T1/T2 levels — common for scalping."""
        p = _pos(entry=50.0, sl=42.0, t1=60.0, t2=70.0)
        assert classify_alert(p, 42.0) == AlertStatus.SL_HIT
        assert classify_alert(p, 50.0) == AlertStatus.SAFE
        assert classify_alert(p, 60.0) == AlertStatus.PAST_T1
        assert classify_alert(p, 70.0) == AlertStatus.PAST_T2
