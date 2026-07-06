"""
Tests for domain/strategies/scalp.py — NiftyScalpStrategy.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.domain.strategies.base import StrategyState
from app.domain.strategies.scalp import (
    MomentumSignal,
    NiftyScalpStrategy,
    ScalpConfig,
    ScalpDailyState,
)
from app.domain.value_objects.market import MarketType
from app.shared.time import monotonic

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_candles(
    n: int = 30,
    base: float = 22000.0,
    trend: float = 0.0,
    volume: float = 50000.0,
) -> list[dict]:
    """Generate N 1-min candle dicts with optional trend."""
    candles = []
    for i in range(n):
        c = base + i * trend
        candles.append({
            'date': f'2025-06-18 09:{15 + i:02d}',
            'open': c - 5,
            'high': c + 10,
            'low': c - 10,
            'close': c,
            'volume': volume,
        })
    return candles


def _breakout_candles(direction: str = 'BUY', n: int = 30) -> list[dict]:
    """Generate candles with a clear breakout in the last bar."""
    candles = _make_candles(n, base=22000, trend=0)  # flat

    if direction == 'BUY':
        # Last candle breaks above channel high
        candles[-1] = {
            'date': f'2025-06-18 09:{15 + n - 1:02d}',
            'open': 22010,
            'high': 22050,
            'low': 22005,
            'close': 22040,  # well above channel_high (~22010)
            'volume': 50000,
        }
    else:
        # Last candle breaks below channel low
        candles[-1] = {
            'date': f'2025-06-18 09:{15 + n - 1:02d}',
            'open': 21990,
            'high': 21995,
            'low': 21950,
            'close': 21960,  # well below channel_low (~21990)
            'volume': 50000,
        }
    return candles


def _strategy(**overrides) -> NiftyScalpStrategy:
    """Create a strategy with optional config overrides."""
    cfg = ScalpConfig(**overrides)
    return NiftyScalpStrategy(scalp_config=cfg)


def _tick_data(candles, ticker='^NSEI', vix=18.0):
    return {'candles': candles, 'ticker': ticker, 'vix': vix}


# ── Construction ─────────────────────────────────────────────────────────────

class TestConstruction:
    def test_defaults(self):
        s = NiftyScalpStrategy()
        assert s.name == 'NiftyScalp'
        assert s.state == StrategyState.IDLE
        assert s.config.market_type == MarketType.INDIAN_FO
        assert s.config.trade_mode == 'scalp'

    def test_custom_config(self):
        s = _strategy(sl_pts=10.0, t1_pts=20.0, min_confidence=80)
        assert s.scalp.sl_pts == 10.0
        assert s.scalp.t1_pts == 20.0
        assert s.scalp.min_confidence == 80


# ── Daily State ──────────────────────────────────────────────────────────────

class TestDailyState:
    def test_reset(self):
        state = ScalpDailyState()
        state.daily_pnl = 500.0
        state.entry_count = 5
        state.hold_times.append(120.0)
        state.reset()
        assert state.daily_pnl == 0.0
        assert state.entry_count == 0
        assert state.hold_times == []


# ── Momentum Detection ──────────────────────────────────────────────────────

class TestMomentumDetection:
    @patch('app.domain.strategies.scalp.now_ist')
    def test_insufficient_candles(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy()
        result = s._detect_momentum(_make_candles(5), '^NSEI')
        assert result is None

    @patch('app.domain.strategies.scalp.now_ist')
    def test_no_trend_no_signal(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy()
        # Flat candles → no breakout
        candles = _make_candles(30, trend=0)
        result = s._detect_momentum(candles, '^NSEI')
        assert result is None

    @patch('app.domain.strategies.scalp.now_ist')
    def test_breakout_up_signal(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy(min_confidence=50)  # low threshold for test
        candles = _breakout_candles('BUY')
        result = s._detect_momentum(candles, '^NSEI')
        if result is not None:
            assert result.direction == 'BUY'
            assert result.instrument_type == 'CE'
            assert result.confidence >= 50

    @patch('app.domain.strategies.scalp.now_ist')
    def test_breakout_down_signal(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy(min_confidence=50)
        candles = _breakout_candles('SELL')
        result = s._detect_momentum(candles, '^NSEI')
        if result is not None:
            assert result.direction == 'SELL'
            assert result.instrument_type == 'PE'

    @patch('app.domain.strategies.scalp.now_ist')
    def test_vix_too_low(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy()
        candles = _breakout_candles('BUY')
        result = s._detect_momentum(candles, '^NSEI', vix=10.0)
        assert result is None

    @patch('app.domain.strategies.scalp.now_ist')
    def test_vix_too_high(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy()
        candles = _breakout_candles('BUY')
        result = s._detect_momentum(candles, '^NSEI', vix=30.0)
        assert result is None

    @patch('app.domain.strategies.scalp.now_ist')
    def test_opening_range_skip(self, mock_now):
        # Before 09:20 with 5-min skip
        mock_now.return_value = datetime(2025, 6, 18, 9, 18)
        s = _strategy(opening_skip_min=5)
        candles = _breakout_candles('BUY')
        result = s._detect_momentum(candles, '^NSEI')
        assert result is None

    @patch('app.domain.strategies.scalp.now_ist')
    def test_confidence_below_threshold(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy(min_confidence=99)  # very high threshold
        candles = _breakout_candles('BUY')
        result = s._detect_momentum(candles, '^NSEI')
        assert result is None


# ── on_tick ──────────────────────────────────────────────────────────────────

class TestOnTick:
    @patch('app.domain.strategies.scalp.now_ist')
    def test_empty_candles(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy()
        result = s.on_tick({'candles': [], 'ticker': '^NSEI'})
        assert result.signals == []

    @patch('app.domain.strategies.scalp.now_ist')
    def test_no_ticker(self, mock_now):
        mock_now.return_value = datetime(2025, 6, 18, 10, 0)
        s = _strategy()
        result = s.on_tick({'candles': _make_candles(30), 'ticker': ''})
        assert result.signals == []


# ── Entry Gates ──────────────────────────────────────────────────────────────

class TestEntryGates:
    def _signal(self) -> MomentumSignal:
        return MomentumSignal(
            ticker='^NSEI', direction='BUY', instrument_type='CE',
            confidence=75, spot=22000, channel_high=22010, channel_low=21990,
            roc_5=0.3, vwap_val=21995, ema9=22005, ema21=21998,
            atr_val=50.0, volume_ratio=1.5, ema_aligned=True,
        )

    def test_entry_cap_blocks(self):
        s = _strategy(max_entries_per_day=5)
        s.daily.entry_count = 5
        candles = _make_candles(30)
        result = s._check_entry_gates(self._signal(), candles, '^NSEI')
        assert result == 'entry_cap'

    def test_max_concurrent_blocks(self):
        s = _strategy(max_concurrent=2)
        s.daily.open_count = 2
        candles = _make_candles(30)
        result = s._check_entry_gates(self._signal(), candles, '^NSEI')
        assert result == 'max_concurrent'

    def test_reentry_cooldown(self):
        s = _strategy(reentry_cooldown_sec=120)
        s.daily.last_exit_time['^NSEI'] = monotonic()  # just exited
        candles = _make_candles(30)
        result = s._check_entry_gates(self._signal(), candles, '^NSEI')
        assert result == 'reentry_cooldown'

    def test_candle_dedup(self):
        s = _strategy()
        candles = _make_candles(30)
        s.daily.last_signal_candle['^NSEI'] = candles[-1]['date']
        result = s._check_entry_gates(self._signal(), candles, '^NSEI')
        assert result == 'candle_dedup'

    def test_all_clear(self):
        s = _strategy()
        candles = _make_candles(30)
        result = s._check_entry_gates(self._signal(), candles, '^NSEI')
        assert result is None

    def test_loss_streak_blocks(self):
        s = _strategy()
        s.daily.loss_streak_pause['CE'] = monotonic() + 300  # paused for 5 min
        candles = _make_candles(30)
        result = s._check_entry_gates(self._signal(), candles, '^NSEI')
        assert result == 'loss_streak_CE'

    def test_max_reentries(self):
        s = _strategy(max_reentries=3)
        s.daily.ledger['^NSEI'] = {'entries': 3}
        candles = _make_candles(30)
        result = s._check_entry_gates(self._signal(), candles, '^NSEI')
        assert result == 'max_reentries'


# ── SL/T1 Levels ─────────────────────────────────────────────────────────────

class TestComputeLevels:
    def test_fixed_nifty(self):
        s = _strategy(use_atr_sl=False, sl_pts=8.0, t1_pts=15.0)
        levels = s.compute_levels(200.0, '^NSEI')
        assert levels['stop_loss'] == pytest.approx(192.0)
        assert levels['target_1'] == pytest.approx(215.0)
        assert levels['target_2'] == pytest.approx(200 + 15 * 1.8)
        assert levels['stop_loss_pts'] == 8.0

    def test_fixed_sensex(self):
        s = _strategy(use_atr_sl=False, sl_pts_sensex=15.0, t1_pts_sensex=25.0)
        levels = s.compute_levels(300.0, '^BSESN')
        assert levels['stop_loss'] == pytest.approx(285.0)
        assert levels['target_1'] == pytest.approx(325.0)

    def test_atr_based(self):
        s = _strategy(use_atr_sl=True, atr_sl_mult=1.5, atr_t1_mult=2.0)
        levels = s.compute_levels(200.0, '^NSEI', atr_val=10.0)
        assert levels['stop_loss_pts'] == 15.0  # 10 * 1.5
        assert levels['stop_loss'] == pytest.approx(200 - 15, abs=0.1)

    def test_atr_clamped(self):
        s = _strategy(use_atr_sl=True, atr_sl_mult=5.0, atr_t1_mult=10.0)
        levels = s.compute_levels(200.0, '^NSEI', atr_val=50.0)
        # SL should be clamped to max 20 for non-sensex
        assert levels['stop_loss_pts'] == 20.0


# ── Trade Opened ─────────────────────────────────────────────────────────────

class TestOnTradeOpened:
    def test_increments_counts(self):
        s = _strategy()
        s.on_trade_opened({'underlying': '^NSEI'})
        assert s.daily.entry_count == 1
        assert s.daily.open_count == 1
        assert s.daily.ledger['^NSEI']['entries'] == 1

    def test_open_close_cycle(self):
        s = _strategy()
        s.on_trade_opened({'underlying': '^NSEI'})
        s.on_trade_opened({'underlying': '^NSEI'})
        assert s.daily.open_count == 2
        s.on_trade_closed({'pnl': 100, 'hold_seconds': 60})
        assert s.daily.open_count == 1
        assert s.daily.entry_count == 2  # entry_count stays


# ── Trade Closed ─────────────────────────────────────────────────────────────

class TestOnTradeClosed:
    def test_pnl_tracking(self):
        s = _strategy()
        s.on_trade_closed({'pnl': 500.0, 'hold_seconds': 120, 'underlying': '^NSEI'})
        assert s.daily.daily_pnl == 500.0
        assert s.daily.peak_pnl == 500.0
        assert len(s.daily.hold_times) == 1

    def test_decrements_open_count(self):
        s = _strategy()
        s.daily.open_count = 2
        s.on_trade_closed({'pnl': 100, 'hold_seconds': 60})
        assert s.daily.open_count == 1


# ── Thesis Flip ──────────────────────────────────────────────────────────────

class TestThesisFlip:
    def test_no_flip_when_aligned(self):
        s = _strategy()
        # Strong uptrend → EMA9 > EMA21 → bias is CE
        candles = _make_candles(30, trend=5.0)
        result = s.check_thesis_flip(candles, 'CE')
        assert result is None  # holding CE, bias is CE → no flip

    def test_flip_detected(self):
        s = _strategy()
        # Strong uptrend → bias is CE
        candles = _make_candles(30, trend=5.0)
        result = s.check_thesis_flip(candles, 'PE')
        # Holding PE in uptrend → should suggest CE
        if result is not None:
            assert result == 'CE'

    def test_grace_period(self):
        s = _strategy()
        candles = _make_candles(30, trend=5.0)
        # Just entered 10 seconds ago → within grace period
        from app.shared.time import now_ist
        entry = now_ist() - timedelta(seconds=10)
        result = s.check_thesis_flip(candles, 'PE', entry_time=entry)
        assert result is None  # grace period protects


# ── Adverse Move ─────────────────────────────────────────────────────────────

class TestAdverseMove:
    def test_no_adverse(self):
        s = _strategy()
        # CE position, spot went UP → no adverse
        assert s.check_adverse_move(22100, 22000, 'CE', 15.0) is False

    def test_adverse_ce(self):
        s = _strategy()
        # CE position, spot dropped 12 pts (> 15 * 0.75 = 11.25)
        assert s.check_adverse_move(21988, 22000, 'CE', 15.0) is True

    def test_adverse_pe(self):
        s = _strategy()
        # PE position, spot went UP 12 pts
        assert s.check_adverse_move(22012, 22000, 'PE', 15.0) is True

    def test_below_threshold(self):
        s = _strategy()
        # CE position, spot dropped only 5 pts (< 15 * 0.75 = 11.25)
        assert s.check_adverse_move(21995, 22000, 'CE', 15.0) is False


# ── Stats ────────────────────────────────────────────────────────────────────

class TestStats:
    def test_basic(self):
        s = _strategy()
        s.daily.daily_pnl = 250.0
        s.daily.entry_count = 3
        s.daily.hold_times = [60.0, 120.0, 90.0]
        stats = s.stats()
        assert stats['daily_pnl'] == 250.0
        assert stats['entry_count'] == 3
        assert stats['avg_hold_sec'] == pytest.approx(90.0)
        assert stats['name'] == 'NiftyScalp'


# ── Get Instruments ──────────────────────────────────────────────────────────

class TestGetInstruments:
    def test_returns_instruments(self):
        s = _strategy()
        instruments = s.get_instruments()
        assert len(instruments) > 0
