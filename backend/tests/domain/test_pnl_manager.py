"""
Tests for PnL Manager — trade result computation.
"""

import pytest

from app.domain.entities.trade import Trade
from app.domain.services.pnl_manager import compute_trade_result


def _make_closed_trade(
    entry: float = 100.0,
    exit_: float = 120.0,
    qty: int = 50,
    direction: str = 'CE',
    mode: str = 'swing',
    exit_reason: str = 'past_t1',
) -> Trade:
    t = Trade(trade_id='T1', direction=direction, trade_mode=mode, qty=qty)
    t.fill(entry)
    t.close(exit_, reason=exit_reason)
    return t


class TestComputeTradeResult:
    def test_winning_trade(self):
        t = _make_closed_trade(entry=100, exit_=120, qty=50)
        r = compute_trade_result(t, underlying='NIFTY', lot_size=50)

        assert r.trade_id == 'T1'
        assert r.gross_pnl == 1000.0  # (120 - 100) * 50
        assert r.brokerage > 0
        assert r.net_pnl == round(r.gross_pnl - r.brokerage, 2)
        assert r.is_winner
        assert not r.is_loser
        assert r.exit_reason == 'past_t1'

    def test_losing_trade(self):
        t = _make_closed_trade(entry=100, exit_=85, qty=50)
        r = compute_trade_result(t, underlying='NIFTY')

        assert r.gross_pnl == -750.0  # (85 - 100) * 50
        assert r.net_pnl < r.gross_pnl  # brokerage makes it worse
        assert r.is_loser
        assert not r.is_winner

    def test_zero_pnl(self):
        t = _make_closed_trade(entry=100, exit_=100, qty=50)
        r = compute_trade_result(t)

        assert r.gross_pnl == 0.0
        assert r.net_pnl < 0  # brokerage eats into flat trade
        assert r.is_loser

    def test_partial_exit_trade(self):
        t = Trade(trade_id='T2', direction='CE', trade_mode='swing', qty=100)
        t.fill(100.0)
        t.partial_exit(120.0, 50)  # 50 qty at T1
        t.close(130.0, reason='past_t2')  # remaining 50 at T2

        r = compute_trade_result(t, underlying='NIFTY')
        # partial: (120-100)*50 = 1000, remaining: (130-100)*50 = 1500
        assert r.gross_pnl == 2500.0
        assert r.is_winner

    def test_raises_on_open_trade(self):
        t = Trade(trade_id='T3', direction='CE', qty=50)
        t.fill(100.0)
        with pytest.raises(ValueError, match='not closed'):
            compute_trade_result(t)

    def test_raises_on_pending_trade(self):
        t = Trade(trade_id='T4', direction='CE', qty=50)
        with pytest.raises(ValueError, match='not closed'):
            compute_trade_result(t)

    def test_to_dict(self):
        t = _make_closed_trade()
        r = compute_trade_result(t, underlying='NIFTY')
        d = r.to_dict()
        assert d['trade_id'] == 'T1'
        assert 'gross_pnl' in d
        assert 'net_pnl' in d
        assert 'brokerage' in d

    def test_brokerage_breakdown_accessible(self):
        t = _make_closed_trade()
        r = compute_trade_result(t)
        assert r.brokerage_breakdown.flat_brokerage == 40.0
        assert r.brokerage_breakdown.total == r.brokerage
