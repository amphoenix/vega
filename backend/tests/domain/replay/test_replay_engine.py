"""
Tests for domain/replay/replay_engine.py — ReplayEngine.
"""


from app.domain.entities.signal import Signal
from app.domain.replay.replay_engine import (
    ReplayConfig,
    ReplayDecision,
    ReplayEngine,
    ReplayResult,
    ReplaySession,
    ReplaySignalRecord,
    ReplayTick,
    SimulatedTrade,
)
from app.domain.strategies.swing import SwingAIStrategy, SwingConfig

# ── Helpers ──────────────────────────────────────────────────────────────────

def _bullish_tick(idx: int, price: float = 22000) -> ReplayTick:
    return ReplayTick(
        timestamp=f'2025-06-10 09:{15 + idx}:00',
        ticker='^NSEI',
        price=price,
        technicals={
            'supertrend_dir': 1, 'adx': 30, 'rsi': 65,
            'ema20': price - 50, 'ema50': price - 100,
            'atr': price * 0.01,
            'adx_plus_di': 28, 'adx_minus_di': 15,
            'macd_cross': 'BULLISH', 'macd_hist': 5.0,
            'cpr_type': 'narrow', 'candle_patterns': ['Hammer'],
        },
    )


def _ranging_tick(idx: int, price: float = 22000) -> ReplayTick:
    return ReplayTick(
        timestamp=f'2025-06-10 09:{15 + idx}:00',
        ticker='^NSEI',
        price=price,
        technicals={
            'supertrend_dir': 1, 'adx': 10, 'rsi': 50,
            'ema20': price, 'ema50': price,
            'atr': price * 0.01,
            'adx_plus_di': 15, 'adx_minus_di': 15,
            'macd_cross': 'NONE', 'macd_hist': 0,
            'cpr_type': None, 'candle_patterns': [],
        },
    )


def _session(ticks=None, strategy=None, date='2025-06-10') -> ReplaySession:
    return ReplaySession(
        date=date,
        ticks=ticks or [_bullish_tick(i) for i in range(5)],
        strategy=strategy or SwingAIStrategy(SwingConfig()),
        label='test',
    )


# ── ReplayTick ───────────────────────────────────────────────────────────────

class TestReplayTick:
    def test_to_strategy_data(self):
        tick = _bullish_tick(0)
        data = tick.to_strategy_data()
        assert data['ticker'] == '^NSEI'
        assert data['price'] == 22000
        assert 'supertrend_dir' in data['technicals']


# ── SimulatedTrade ───────────────────────────────────────────────────────────

class TestSimulatedTrade:
    def test_open_trade(self):
        t = SimulatedTrade(
            trade_id='t1', symbol='^NSEI', direction='BUY',
            entry_price=100, entry_tick=0,
        )
        assert t.is_open
        assert t.gross_pnl == 0

    def test_close_winner(self):
        t = SimulatedTrade(
            trade_id='t1', symbol='^NSEI', direction='BUY',
            entry_price=100, entry_tick=0, qty=10,
        )
        t.close(110, 5, 'target_hit')
        assert not t.is_open
        assert t.gross_pnl == 100.0  # (110-100)*10
        assert t.exit_reason == 'target_hit'

    def test_close_loser(self):
        t = SimulatedTrade(
            trade_id='t1', symbol='^NSEI', direction='BUY',
            entry_price=100, entry_tick=0, qty=10,
        )
        t.close(90, 3, 'sl_hit')
        assert t.gross_pnl == -100.0

    def test_to_dict(self):
        t = SimulatedTrade(
            trade_id='t1', symbol='X', direction='BUY',
            entry_price=100, entry_tick=0,
        )
        d = t.to_dict()
        assert d['trade_id'] == 't1'
        assert 'entry_price' in d


# ── ReplayResult ─────────────────────────────────────────────────────────────

class TestReplayResult:
    def test_empty_result(self):
        r = ReplayResult(date='2025-06-10')
        assert r.signal_count == 0
        assert r.trade_count == 0
        assert r.win_rate == 0.0
        assert r.total_pnl == 0.0

    def test_summary(self):
        r = ReplayResult(date='2025-06-10', ticks_processed=100)
        s = r.summary()
        assert s['date'] == '2025-06-10'
        assert s['ticks_processed'] == 100

    def test_metrics(self):
        r = ReplayResult(date='d')
        # Add some closed trades
        t1 = SimulatedTrade(trade_id='1', symbol='X', direction='BUY',
                            entry_price=100, entry_tick=0, qty=1)
        t1.close(110, 5, 'target')
        t2 = SimulatedTrade(trade_id='2', symbol='X', direction='BUY',
                            entry_price=100, entry_tick=0, qty=1)
        t2.close(90, 5, 'sl')
        r.trades = [t1, t2]
        assert r.winners == 1
        assert r.losers == 1
        assert r.win_rate == 0.5
        assert r.total_pnl == 0.0  # +10 - 10

    def test_compare_matching(self):
        sig = Signal(underlying='^NSEI', direction='BUY', confidence=80, trade_mode='swing', spot=22000, symbol='^NSEI')
        record = ReplaySignalRecord(
            tick_index=0, timestamp='2025-06-10 09:15:00', signal=sig,
            decision=ReplayDecision.APPROVED,
        )
        r = ReplayResult(date='d', signals=[record])
        actual = [{'timestamp': '2025-06-10 09:15:00', 'symbol': '^NSEI', 'direction': 'BUY'}]
        cmp = r.compare(actual)
        assert cmp['matches'] == 1
        assert cmp['mismatches'] == 0

    def test_compare_mismatch(self):
        sig = Signal(underlying='^NSEI', direction='BUY', confidence=80, trade_mode='swing', spot=22000, symbol='^NSEI')
        record = ReplaySignalRecord(
            tick_index=0, timestamp='2025-06-10 09:15:00', signal=sig,
        )
        r = ReplayResult(date='d', signals=[record])
        actual = [{'timestamp': '2025-06-10 09:15:00', 'symbol': '^NSEI', 'direction': 'SELL'}]
        cmp = r.compare(actual)
        assert cmp['mismatches'] == 1

    def test_compare_replay_only(self):
        sig = Signal(underlying='^NSEI', direction='BUY', confidence=80, trade_mode='swing', spot=22000, symbol='^NSEI')
        record = ReplaySignalRecord(
            tick_index=0, timestamp='2025-06-10 09:15:00', signal=sig,
        )
        r = ReplayResult(date='d', signals=[record])
        cmp = r.compare([])
        assert cmp['replay_only'] == 1

    def test_compare_actual_only(self):
        r = ReplayResult(date='d', signals=[])
        actual = [{'timestamp': '2025-06-10 09:15:00', 'symbol': '^NSEI', 'direction': 'BUY'}]
        cmp = r.compare(actual)
        assert cmp['actual_only'] == 1


# ── ReplayEngine ─────────────────────────────────────────────────────────────

class TestReplayEngine:
    def test_basic_run(self):
        engine = ReplayEngine()
        result = engine.run(_session())
        assert result.ticks_processed == 5
        assert result.date == '2025-06-10'
        assert result.elapsed_ms > 0

    def test_no_strategy_returns_error(self):
        engine = ReplayEngine()
        session = ReplaySession(date='d', ticks=[_bullish_tick(0)])
        result = engine.run(session)
        assert len(result.errors) == 1
        assert 'No strategy' in result.errors[0]

    def test_bullish_ticks_produce_signals(self):
        engine = ReplayEngine()
        result = engine.run(_session())
        assert result.signal_count > 0
        assert result.signals[0].signal.direction == 'BUY'

    def test_ranging_ticks_no_signals(self):
        engine = ReplayEngine()
        ticks = [_ranging_tick(i) for i in range(5)]
        result = engine.run(_session(ticks=ticks))
        assert result.signal_count == 0

    def test_auto_approve_without_gate(self):
        engine = ReplayEngine(ReplayConfig(apply_gates=True))
        result = engine.run(_session())
        # No gate_fn → auto-approve
        for s in result.signals:
            assert s.decision == ReplayDecision.APPROVED

    def test_gate_rejects(self):
        engine = ReplayEngine(ReplayConfig(apply_gates=True))
        gate = lambda sig: (False, 'kill_switch_active')
        result = engine.run(_session(), gate_fn=gate)
        for s in result.signals:
            assert s.decision == ReplayDecision.REJECTED
            assert s.rejection_reason == 'kill_switch_active'
        # No trades opened on rejection
        assert result.trade_count == 0

    def test_gate_approves(self):
        engine = ReplayEngine(ReplayConfig(apply_gates=True))
        gate = lambda sig: (True, '')
        result = engine.run(_session(), gate_fn=gate)
        assert result.approved_count > 0
        assert result.trade_count > 0

    def test_gates_disabled(self):
        engine = ReplayEngine(ReplayConfig(apply_gates=False))
        result = engine.run(_session())
        for s in result.signals:
            assert s.decision == ReplayDecision.SKIPPED

    def test_max_ticks_respected(self):
        engine = ReplayEngine(ReplayConfig(max_ticks=2))
        ticks = [_bullish_tick(i) for i in range(10)]
        result = engine.run(_session(ticks=ticks))
        assert result.ticks_processed == 2

    def test_verbose_tick_log(self):
        engine = ReplayEngine(ReplayConfig(verbose=True))
        result = engine.run(_session())
        assert len(result.tick_log) == 5

    def test_open_trades_closed_at_session_end(self):
        # Use ticks with stable price so no SL/T1 triggers
        engine = ReplayEngine()
        ticks = [_bullish_tick(i, price=22000) for i in range(3)]
        result = engine.run(_session(ticks=ticks))
        # All trades should be closed at session end
        assert result.open_trades == 0
        for t in result.trades:
            assert t.exit_reason in ('session_end', 'sl_hit', 'target_hit')

    def test_on_signal_callback(self):
        captured = []
        engine = ReplayEngine()
        result = engine.run(_session(), on_signal=lambda r: captured.append(r))
        assert len(captured) == result.signal_count

    def test_on_progress_callback(self):
        progress = []
        engine = ReplayEngine(ReplayConfig(batch_size=2))
        ticks = [_bullish_tick(i) for i in range(6)]
        engine.run(_session(ticks=ticks), on_progress=lambda p, t: progress.append((p, t)))
        assert len(progress) > 0

    def test_simulated_exit_sl(self):
        """Price drops 2%+ → SL hit."""
        engine = ReplayEngine()
        ticks = [
            _bullish_tick(0, price=22000),
            _bullish_tick(1, price=21500),  # -2.3%
        ]
        result = engine.run(_session(ticks=ticks))
        sl_trades = [t for t in result.trades if t.exit_reason == 'sl_hit']
        assert len(sl_trades) >= 1

    def test_simulated_exit_target(self):
        """Price rises 3%+ → target hit."""
        engine = ReplayEngine()
        ticks = [
            _bullish_tick(0, price=22000),
            _bullish_tick(1, price=22700),  # +3.2%
        ]
        result = engine.run(_session(ticks=ticks))
        target_trades = [t for t in result.trades if t.exit_reason == 'target_hit']
        assert len(target_trades) >= 1

    def test_gate_error_rejects(self):
        def bad_gate(sig):
            raise RuntimeError('gate crashed')
        engine = ReplayEngine(ReplayConfig(apply_gates=True))
        result = engine.run(_session(), gate_fn=bad_gate)
        for s in result.signals:
            assert s.decision == ReplayDecision.REJECTED
            assert 'gate error' in s.rejection_reason

    def test_strategy_error_recorded(self):
        """Strategy that throws on on_tick should be caught and logged."""
        class BadStrategy(SwingAIStrategy):
            def on_tick(self, data):
                raise ValueError('boom')

        engine = ReplayEngine()
        result = engine.run(_session(strategy=BadStrategy()))
        assert len(result.errors) > 0
        assert 'boom' in result.errors[0]

    def test_multiple_sessions_isolated(self):
        engine = ReplayEngine()
        r1 = engine.run(_session(date='2025-06-10'))
        r2 = engine.run(_session(date='2025-06-11'))
        assert r1.date != r2.date
        assert r1.ticks_processed == r2.ticks_processed


# ── ReplaySignalRecord ───────────────────────────────────────────────────────

class TestReplaySignalRecord:
    def test_to_dict(self):
        sig = Signal(underlying='^NSEI', direction='BUY', confidence=80, trade_mode='swing', spot=22000, symbol='^NSEI')
        r = ReplaySignalRecord(
            tick_index=0, timestamp='t0', signal=sig,
            decision=ReplayDecision.APPROVED,
        )
        d = r.to_dict()
        assert d['decision'] == 'approved'
        assert d['confidence'] == 80
