"""Tests for brokerage calculation."""

from app.domain.services.brokerage_calc import calc_brokerage, total_brokerage


def test_brokerage_basic():
    b = calc_brokerage(entry_premium=100, exit_premium=120, qty=75)
    assert b.flat_brokerage == 40.0
    assert b.total > 40.0  # flat + taxes
    assert b.stt > 0
    assert b.exchange_txn > 0
    assert b.gst > 0


def test_brokerage_total_shorthand():
    total = total_brokerage(100, 120, 75)
    b = calc_brokerage(100, 120, 75)
    assert total == b.total


def test_brokerage_zero_exit():
    """Expired worthless — exit premium is 0."""
    b = calc_brokerage(entry_premium=50, exit_premium=0.05, qty=75)
    assert b.total > 0
    assert b.stt >= 0  # STT rounds to 0 for tiny exit premiums
