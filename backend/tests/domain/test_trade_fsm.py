"""Tests for Trade entity FSM."""

import pytest
from datetime import date

from app.domain.entities.trade import Trade, TradeState
from app.domain.exceptions import InvalidStateTransition
from app.domain.value_objects.option_leg import OptionLeg, OptionType


def _leg() -> OptionLeg:
    return OptionLeg(
        trading_symbol='NIFTY26JUN18500CE',
        strike=18500,
        expiry=date(2026, 6, 25),
        option_type=OptionType.CE,
        lot_size=75,
    )


def test_trade_initial_state():
    t = Trade(trade_id='T1', option_leg=_leg(), trade_mode='swing', direction='CE', qty=75)
    assert t.state == TradeState.PENDING
    assert not t.is_open
    assert not t.is_terminal


def test_trade_fill():
    t = Trade(trade_id='T1', option_leg=_leg(), trade_mode='swing', direction='CE', qty=75)
    t.fill(price=120.5, order_id='ORD-001')
    assert t.state == TradeState.OPEN
    assert t.is_open
    assert t.entry_price == 120.5
    assert t.order_id == 'ORD-001'


def test_trade_close_after_fill():
    t = Trade(trade_id='T1', option_leg=_leg(), trade_mode='swing', direction='CE', qty=75)
    t.fill(price=100.0)
    t.close(price=130.0, reason='past_t1')
    assert t.state == TradeState.CLOSED
    assert t.is_terminal
    assert t.realized_gross_pnl == (130.0 - 100.0) * 75


def test_trade_partial_exit():
    t = Trade(trade_id='T1', option_leg=_leg(), trade_mode='swing', direction='CE', qty=75)
    t.fill(price=100.0)
    t.partial_exit(price=200.0, qty=37)
    assert t.state == TradeState.PARTIAL
    assert t.remaining_qty == 38
    t.close(price=250.0, reason='past_t2')
    assert t.state == TradeState.CLOSED
    # partial pnl: (200-100)*37 = 3700
    # remaining pnl: (250-100)*38 = 5700
    assert t.realized_gross_pnl == 3700 + 5700


def test_trade_cancel():
    t = Trade(trade_id='T1', option_leg=_leg(), trade_mode='swing', direction='CE', qty=75)
    t.cancel(reason='signal_expired')
    assert t.state == TradeState.CANCELLED
    assert t.is_terminal


def test_invalid_transition_pending_to_closed():
    t = Trade(trade_id='T1', option_leg=_leg(), trade_mode='swing', direction='CE', qty=75)
    with pytest.raises(InvalidStateTransition):
        t.close(price=100.0)


def test_invalid_transition_closed_to_open():
    t = Trade(trade_id='T1', option_leg=_leg(), trade_mode='swing', direction='CE', qty=75)
    t.fill(price=100.0)
    t.close(price=120.0)
    with pytest.raises(InvalidStateTransition):
        t.fill(price=130.0)
