"""
Integration Tests — full trade lifecycle ⭐⭐⭐⭐

Wires Trade FSM + Supervisor + Risk Guards + Broker together.
No mocks. Tests the complete path from signal → risk check → order → fill → close.
Uses a minimal stub broker (not the real adapter) purely for testing the pipeline.
"""

import uuid
from datetime import date

import pytest

from app.domain.entities.signal import Signal
from app.domain.entities.trade import Trade, TradeState
from app.domain.risk.budget_guard import check_budget
from app.domain.risk.exposure_guard import ExposureGuard
from app.domain.risk.position_sizer import compute_size
from app.domain.safety.kill_switch import KillSwitchConfig, KillSwitchEngine
from app.domain.services.brokerage_calc import calc_brokerage
from app.domain.supervisor.trade_supervisor import SupervisorConfig, TradeSupervisor
from app.domain.value_objects.option_leg import OptionLeg, OptionType
from app.infrastructure.broker.base import BrokerAdapter, OrderResult, PositionInfo


class _StubBroker(BrokerAdapter):
    """Minimal in-memory broker stub for integration tests only."""

    def __init__(self, capital: float = 100_000.0):
        self._capital = capital
        self._positions: dict[str, PositionInfo] = {}

    @property
    def broker_name(self) -> str:
        return '_StubBroker'

    def place_order(self, symbol, side, qty, order_type='MARKET', price=0.0,
                    exchange='NFO', security_id=''):
        oid = f'STUB-{uuid.uuid4().hex[:8].upper()}'
        fill = price if price > 0 else 100.0
        if side.upper() == 'BUY':
            cost = fill * qty
            if cost > self._capital:
                return OrderResult(success=False, message='Insufficient capital')
            self._capital -= cost
        else:
            self._capital += fill * qty
        return OrderResult(success=True, order_id=oid, fill_price=fill, status='FILLED')

    def get_ltp(self, symbol, exchange='NFO', security_id=''):
        return None

    def get_positions(self):
        return list(self._positions.values())

    def get_available_cash(self):
        return round(self._capital, 2)

    def get_quote(self, symbol, exchange='NFO', security_id=''):
        return None


def _leg(strike=18500, option_type=OptionType.CE):
    return OptionLeg(
        trading_symbol=f'NIFTY26JUN{strike}{option_type.value}',
        strike=strike,
        expiry=date(2026, 6, 25),
        option_type=option_type,
        lot_size=75,
    )


def _signal(direction='CE', confidence=80.0):
    return Signal(
        underlying='NIFTY',
        direction=direction,
        confidence=confidence,
        trade_mode='swing',
        spot=18550.0,
    )


@pytest.fixture
def kill_switch():
    return KillSwitchEngine(KillSwitchConfig(
        daily_loss_soft=2500, daily_loss_hard=5000,
        loss_streak_soft=10, loss_streak_hard=20,
    ))


@pytest.fixture
def supervisor(kill_switch):
    return TradeSupervisor(SupervisorConfig(
        daily_loss_limit=2500,
        max_trades_per_day=10,
        max_consecutive_losses=3,
    ), kill_switch=kill_switch)


@pytest.fixture
def exposure():
    return ExposureGuard(max_directional_lots=5)


@pytest.fixture
def broker():
    return _StubBroker(capital=100_000.0)


class TestTradeLifecycle:
    """End-to-end: signal → risk → trade → fill → close → P&L."""

    def test_full_winning_trade(self, supervisor, exposure, broker, kill_switch):
        sig = _signal()
        leg = _leg()

        # 1. Supervisor check
        denial = supervisor.check_permission('NIFTY', sig.trade_mode)
        assert denial is None

        # 2. Budget check
        bv = check_budget(2500, 0, sl_max_points=15, qty=75, brokerage_estimate=40)
        assert bv.allowed

        # 3. Position sizing
        size = compute_size(100_000, 20, 15, 75, 120, desired_lots=2)
        assert size.lots >= 1
        qty = size.lots * 75

        # 4. Create trade
        trade = Trade(
            trade_id='T1', option_leg=leg,
            direction='CE', trade_mode='swing',
            qty=qty, stop_loss=105, target_1=135, target_2=150,
        )
        assert trade.state == TradeState.PENDING

        # 5. Place order via paper broker
        result = broker.place_order(leg.trading_symbol, 'BUY', qty=qty, price=120.0)
        assert result.success

        # 6. Fill trade
        trade.fill(result.fill_price, result.order_id)
        assert trade.state == TradeState.OPEN
        supervisor.record_trade_opened('T1', 'NIFTY')
        exposure.add('CE', lots=size.lots)

        # 7. Close at target
        exit_result = broker.place_order(leg.trading_symbol, 'SELL', qty=qty, price=140.0)
        assert exit_result.success
        trade.close(140.0, reason='T2_HIT')
        assert trade.state == TradeState.CLOSED
        assert trade.realized_gross_pnl > 0

        # 8. Calculate brokerage
        brk = calc_brokerage(120.0, 140.0, qty)
        assert brk.total > 0

        # 9. Record in supervisor + kill switch
        net_pnl = trade.realized_gross_pnl - brk.total
        kill_switch.record_pnl(net_pnl)
        supervisor.record_trade_closed('T1', pnl=net_pnl, underlying='NIFTY')
        exposure.remove('CE', lots=size.lots)

        assert supervisor.daily_pnl > 0
        assert not supervisor.is_kill_switch_active

    def test_full_losing_trade_hits_sl(self, supervisor, exposure, broker, kill_switch):
        leg = _leg()

        denial = supervisor.check_permission('NIFTY', 'swing')
        assert denial is None

        trade = Trade(
            trade_id='T2', option_leg=leg,
            direction='CE', trade_mode='swing',
            qty=75, stop_loss=105, target_1=135, target_2=150,
        )

        result = broker.place_order(leg.trading_symbol, 'BUY', qty=75, price=120.0)
        trade.fill(result.fill_price, result.order_id)
        supervisor.record_trade_opened('T2', 'NIFTY')
        exposure.add('CE', lots=1)

        # SL hit
        broker.place_order(leg.trading_symbol, 'SELL', qty=75, price=105.0)
        trade.close(105.0, reason='SL_HIT')
        assert trade.realized_gross_pnl < 0

        brk = calc_brokerage(120.0, 105.0, 75)
        net_pnl = trade.realized_gross_pnl - brk.total
        kill_switch.record_pnl(net_pnl)
        supervisor.record_trade_closed('T2', pnl=net_pnl, was_sl=True, underlying='NIFTY')
        exposure.remove('CE', lots=1)

        assert supervisor.daily_pnl < 0

    def test_three_consecutive_losses_block_next(self, supervisor, broker, kill_switch):
        """After 3 SL hits, supervisor blocks the 4th trade."""
        for i in range(3):
            tid = f'LOSS-{i}'
            supervisor.record_trade_opened(tid, 'NIFTY')
            kill_switch.record_pnl(-200)
            supervisor.record_trade_closed(tid, pnl=-200, was_sl=True, underlying='NIFTY')

        denial = supervisor.check_permission('NIFTY', 'swing')
        assert denial is not None
        assert denial.rule == 'LOSS_STREAK'

    def test_partial_exit_then_close(self, supervisor, exposure, broker):
        """T1 hit → partial exit 50% → SL trails to breakeven → close."""
        leg = _leg()
        trade = Trade(
            trade_id='T3', option_leg=leg,
            direction='CE', trade_mode='swing',
            qty=150, stop_loss=105, target_1=135, target_2=150,
        )
        broker.place_order(leg.trading_symbol, 'BUY', qty=150, price=120.0)
        trade.fill(120.0, 'PAPER-001')
        supervisor.record_trade_opened('T3', 'NIFTY')
        exposure.add('CE', lots=2)

        # T1 hit — partial exit 50%
        trade.partial_exit(135.0, qty=75)
        assert trade.state == TradeState.PARTIAL
        broker.place_order(leg.trading_symbol, 'SELL', qty=75, price=135.0)

        # Close remaining at breakeven (trailed SL)
        trade.close(120.0, reason='SL_TRAILED')
        broker.place_order(leg.trading_symbol, 'SELL', qty=75, price=120.0)
        assert trade.state == TradeState.CLOSED

        # Partial exit profit + breakeven on rest
        # 75 * (135 - 120) = 1125 on partial, 75 * (120 - 120) = 0 on rest
        assert trade.realized_gross_pnl == 1125.0

    def test_cancelled_trade_doesnt_count_as_loss(self, supervisor, broker):
        trade = Trade(
            trade_id='T4', option_leg=_leg(),
            direction='CE', trade_mode='swing',
            qty=75, stop_loss=105, target_1=135, target_2=150,
        )
        trade.cancel(reason='ORDER_REJECTED')
        assert trade.state == TradeState.CANCELLED
        # Don't record in supervisor — cancelled trades shouldn't affect P&L
        assert supervisor.daily_pnl == 0.0


class TestExposureIntegration:
    """Exposure guard wired with actual trades."""

    def test_exposure_blocks_overconcentration(self, exposure, supervisor, broker):
        """Can't go all-in on one direction."""
        for i in range(5):
            exposure.add('CE', lots=1)
        v = exposure.check('CE', lots=1)
        assert not v.allowed
        assert v.reason  # explains why blocked

    def test_opposite_direction_balances(self, exposure):
        exposure.add('CE', lots=3)
        exposure.add('PE', lots=2)
        v = exposure.check('CE', lots=1)
        # net = 3-2 = 1, adding 1 CE = 2, still under 5
        assert v.allowed


class TestBrokerageAccuracy:
    """Verify brokerage matches expected fee structure."""

    def test_typical_intraday(self):
        brk = calc_brokerage(120.0, 140.0, 75)
        assert brk.flat_brokerage == 40.0
        assert brk.exchange_txn > 0
        assert brk.gst > 0
        assert brk.total > 40  # always more than flat fee

    def test_zero_profit_trade(self):
        brk = calc_brokerage(120.0, 120.0, 75)
        # Still has brokerage from flat fee + exchange txn on entry side
        assert brk.total > 0

    def test_large_qty_scales(self):
        brk_small = calc_brokerage(120.0, 140.0, 75)
        brk_large = calc_brokerage(120.0, 140.0, 150)
        assert brk_large.total > brk_small.total
