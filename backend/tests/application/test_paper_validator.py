"""
Tests for application/paper_validator.py — PaperTradingValidator.
"""

import uuid

from app.application.paper_validator import (
    DecisionOutcome,
    PaperDecision,
    PaperTrade,
    PaperTradingSession,
    PaperTradingValidator,
    PaperValidatorConfig,
)
from app.domain.entities.signal import Signal
from app.domain.supervisor.trade_supervisor import SupervisorConfig
from app.infrastructure.broker.base import BrokerAdapter, OrderResult


class _StubBroker(BrokerAdapter):
    """Minimal in-memory broker stub for paper_validator tests."""

    def __init__(self, capital: float = 100_000.0):
        self._capital = capital

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
        return []

    def get_available_cash(self):
        return round(self._capital, 2)

    def get_quote(self, symbol, exchange='NFO', security_id=''):
        return None


def _signal(
    underlying: str = 'NIFTY',
    direction: str = 'CE',
    confidence: float = 85.0,
    spot: float = 100.0,
    trade_mode: str = 'swing',
    symbol: str = '',
) -> Signal:
    return Signal(
        underlying=underlying,
        direction=direction,
        confidence=confidence,
        trade_mode=trade_mode,
        spot=spot,
        symbol=symbol,
    )


# ── PaperTrade ───────────────────────────────────────────────────────────────

class TestPaperTrade:
    def test_open_trade(self):
        sig = _signal()
        t = PaperTrade(track_id='T1', signal=sig, entry_price=100.0, qty=25, symbol='NIFTY-CE')
        assert t.is_open
        assert t.pnl == 0.0

    def test_close_winner_bullish(self):
        sig = _signal(direction='CE')
        t = PaperTrade(track_id='T1', signal=sig, entry_price=100.0, qty=25, symbol='NIFTY-CE')
        t.close(110.0)
        assert not t.is_open
        assert t.pnl == 250.0  # (110-100) * 25

    def test_close_loser_bullish(self):
        sig = _signal(direction='CE')
        t = PaperTrade(track_id='T1', signal=sig, entry_price=100.0, qty=25, symbol='NIFTY-CE')
        t.close(90.0)
        assert t.pnl == -250.0

    def test_close_winner_bearish(self):
        sig = _signal(direction='PE')
        t = PaperTrade(track_id='T1', signal=sig, entry_price=100.0, qty=25, symbol='NIFTY-PE')
        t.close(90.0)
        assert t.pnl == 250.0  # (100-90) * 25

    def test_to_dict(self):
        sig = _signal()
        t = PaperTrade(track_id='T1', signal=sig, entry_price=100.0, qty=25, symbol='X')
        d = t.to_dict()
        assert d['track_id'] == 'T1'
        assert d['is_open']


# ── PaperDecision ────────────────────────────────────────────────────────────

class TestPaperDecision:
    def test_approved_to_dict(self):
        d = PaperDecision(signal=_signal(), outcome=DecisionOutcome.APPROVED, track_id='T1')
        result = d.to_dict()
        assert result['outcome'] == 'approved'
        assert result['track_id'] == 'T1'

    def test_denied_to_dict(self):
        from app.domain.supervisor.trade_supervisor import Denial
        d = PaperDecision(
            signal=_signal(),
            outcome=DecisionOutcome.DENIED,
            denial=Denial(rule='KILL_SWITCH', detail='loss limit'),
        )
        result = d.to_dict()
        assert result['denial_rule'] == 'KILL_SWITCH'


# ── Session ──────────────────────────────────────────────────────────────────

class TestPaperTradingSession:
    def _make_session(self, **config_kw) -> PaperTradingSession:
        config = PaperValidatorConfig(**config_kw)
        validator = PaperTradingValidator(config)
        return validator.create_session('test', broker=_StubBroker(capital=config.initial_capital))

    def test_inject_signal_approved(self):
        session = self._make_session()
        d = session.inject_signal(_signal())
        assert d.outcome == DecisionOutcome.APPROVED
        assert d.track_id.startswith('PT-')
        assert len(session.open_trades) == 1

    def test_inject_multiple_signals(self):
        session = self._make_session()
        d1 = session.inject_signal(_signal(symbol='SYM1'))
        d2 = session.inject_signal(_signal(symbol='SYM2'))
        assert d1.outcome == DecisionOutcome.APPROVED
        assert d2.outcome == DecisionOutcome.APPROVED
        assert len(session.open_trades) == 2

    def test_close_trade(self):
        session = self._make_session()
        d = session.inject_signal(_signal())
        track_id = d.track_id
        ok = session.close_trade(track_id, exit_price=120.0)
        assert ok
        assert len(session.open_trades) == 0
        assert len(session.closed_trades) == 1
        assert session.closed_trades[0].pnl > 0

    def test_close_nonexistent(self):
        session = self._make_session()
        ok = session.close_trade('FAKE', exit_price=100.0)
        assert not ok

    def test_close_all(self):
        session = self._make_session()
        session.inject_signal(_signal(symbol='A'))
        session.inject_signal(_signal(symbol='B'))
        count = session.close_all()
        assert count == 2
        assert len(session.open_trades) == 0

    def test_close_all_with_price_fn(self):
        session = self._make_session()
        session.inject_signal(_signal(spot=100.0, symbol='A'))
        count = session.close_all(exit_price_fn=lambda t: t.entry_price + 10)
        assert count == 1
        assert session.closed_trades[0].pnl > 0

    def test_supervisor_denial_max_trades(self):
        config = PaperValidatorConfig(
            supervisor_config=SupervisorConfig(max_trades_per_day=2),
        )
        validator = PaperTradingValidator(config)
        session = validator.create_session(broker=_StubBroker())

        d1 = session.inject_signal(_signal(symbol='A'))
        d2 = session.inject_signal(_signal(symbol='B'))
        d3 = session.inject_signal(_signal(symbol='C'))

        assert d1.outcome == DecisionOutcome.APPROVED
        assert d2.outcome == DecisionOutcome.APPROVED
        assert d3.outcome == DecisionOutcome.DENIED
        assert d3.denial.rule == 'MAX_TRADES'

    def test_supervisor_kill_switch(self):
        config = PaperValidatorConfig(
            initial_capital=100_000.0,
            supervisor_config=SupervisorConfig(daily_loss_limit=500.0),
        )
        validator = PaperTradingValidator(config)
        session = validator.create_session(broker=_StubBroker())

        # Open and close at a loss big enough to trigger kill switch
        d1 = session.inject_signal(_signal(spot=100.0, symbol='A'))
        session.close_trade(d1.track_id, exit_price=70.0)  # loss = (70-100)*25 = -750

        # Next signal should be denied
        d2 = session.inject_signal(_signal(symbol='B'))
        assert d2.outcome == DecisionOutcome.DENIED
        assert d2.denial.rule == 'KILL_SWITCH'

    def test_supervisor_max_open_trades(self):
        config = PaperValidatorConfig(
            supervisor_config=SupervisorConfig(max_open_trades=1, max_trades_per_day=10),
        )
        validator = PaperTradingValidator(config)
        session = validator.create_session(broker=_StubBroker())

        d1 = session.inject_signal(_signal(symbol='A'))
        d2 = session.inject_signal(_signal(symbol='B'))

        assert d1.outcome == DecisionOutcome.APPROVED
        assert d2.outcome == DecisionOutcome.DENIED
        assert d2.denial.rule == 'MAX_OPEN_TRADES'

    def test_session_hours_denial(self):
        session = self._make_session()
        d = session.inject_signal(_signal(), is_safe_hours=False)
        assert d.outcome == DecisionOutcome.DENIED
        assert d.denial.rule == 'SESSION'

    def test_max_signals_limit(self):
        session = self._make_session(
            max_signals=2,
            supervisor_config=SupervisorConfig(max_trades_per_day=100),
        )
        session.inject_signal(_signal(symbol='A'))
        session.inject_signal(_signal(symbol='B'))
        d3 = session.inject_signal(_signal(symbol='C'))
        assert d3.outcome == DecisionOutcome.ERROR
        assert 'max_signals' in d3.error

    def test_insufficient_capital(self):
        session = self._make_session(initial_capital=1.0)
        d = session.inject_signal(_signal(spot=1000.0))
        assert d.outcome == DecisionOutcome.ERROR
        assert 'capital' in d.error.lower()


# ── Report ───────────────────────────────────────────────────────────────────

class TestPaperSessionReport:
    def _run_session(self, signals: list[Signal], close_prices: dict[str, float] | None = None):
        config = PaperValidatorConfig(
            supervisor_config=SupervisorConfig(max_trades_per_day=20, max_open_trades=20),
        )
        validator = PaperTradingValidator(config)
        session = validator.create_session(broker=_StubBroker())
        track_ids = {}

        for sig in signals:
            d = session.inject_signal(sig)
            if d.outcome == DecisionOutcome.APPROVED:
                track_ids[sig.symbol or sig.underlying] = d.track_id

        if close_prices:
            for key, price in close_prices.items():
                tid = track_ids.get(key)
                if tid:
                    session.close_trade(tid, price)

        return session.finalize()

    def test_empty_report(self):
        validator = PaperTradingValidator()
        session = validator.create_session(broker=_StubBroker())
        report = session.finalize()
        assert report.total_signals == 0
        assert report.total_pnl == 0
        assert report.win_rate == 0.0

    def test_summary(self):
        report = self._run_session(
            [_signal(symbol='A'), _signal(symbol='B')],
            {'A': 110.0, 'B': 90.0},
        )
        s = report.summary()
        assert s['total_signals'] == 2
        assert s['approved'] == 2
        assert s['trades_total'] == 2

    def test_win_rate(self):
        report = self._run_session(
            [_signal(symbol='W1', spot=100.0), _signal(symbol='L1', spot=100.0)],
            {'W1': 110.0, 'L1': 90.0},
        )
        assert report.winning_trades == 1
        assert report.losing_trades == 1
        assert report.win_rate == 50.0

    def test_denial_reasons(self):
        config = PaperValidatorConfig(
            supervisor_config=SupervisorConfig(max_trades_per_day=1),
        )
        validator = PaperTradingValidator(config)
        session = validator.create_session(broker=_StubBroker())
        session.inject_signal(_signal(symbol='A'))
        session.inject_signal(_signal(symbol='B'))
        report = session.finalize()
        assert 'MAX_TRADES' in report.denial_reasons

    def test_validate_no_failures(self):
        report = self._run_session(
            [_signal(symbol='A', spot=100.0)],
            {'A': 110.0},
        )
        failures = report.validate()
        assert failures == []

    def test_validate_no_duplicate_track_ids(self):
        report = self._run_session(
            [_signal(symbol='A'), _signal(symbol='B')],
            {'A': 110.0, 'B': 110.0},
        )
        failures = report.validate()
        assert failures == []

    def test_finalize_closes_open_trades(self):
        validator = PaperTradingValidator()
        session = validator.create_session(broker=_StubBroker())
        session.inject_signal(_signal(symbol='X'))
        assert len(session.open_trades) == 1
        report = session.finalize()
        assert report.open_trades == 0

    def test_pnl_total(self):
        report = self._run_session(
            [_signal(symbol='W', spot=100.0), _signal(symbol='L', spot=100.0)],
            {'W': 120.0, 'L': 80.0},
        )
        # W: (120 - ~100.1) * 25 ≈ +497.5, L: (80 - ~100.1) * 25 ≈ -502.5
        assert isinstance(report.total_pnl, float)


# ── Validator Factory ────────────────────────────────────────────────────────

class TestPaperTradingValidator:
    def test_create_session(self):
        v = PaperTradingValidator()
        s = v.create_session('s1', broker=_StubBroker())
        assert s.session_id == 's1'

    def test_create_session_auto_id(self):
        v = PaperTradingValidator()
        s = v.create_session(broker=_StubBroker())
        assert s.session_id.startswith('paper-')

    def test_sessions_isolated(self):
        v = PaperTradingValidator()
        s1 = v.create_session(broker=_StubBroker())
        s2 = v.create_session(broker=_StubBroker())
        s1.inject_signal(_signal(symbol='A'))
        assert len(s1.open_trades) == 1
        assert len(s2.open_trades) == 0
