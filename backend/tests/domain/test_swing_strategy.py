"""
Tests for domain/strategies/swing.py — SwingAIStrategy.
"""

import pytest

from app.domain.strategies.base import StrategyState
from app.domain.strategies.swing import (
    SwingAIStrategy,
    SwingConfig,
    SwingDailyState,
    TechnicalSignal,
)
from app.domain.value_objects.market import MarketType

# ── Helpers ──────────────────────────────────────────────────────────────────

def _strategy(**overrides) -> SwingAIStrategy:
    cfg = SwingConfig(**overrides)
    return SwingAIStrategy(swing_config=cfg)


def _bullish_ta(adx=30, rsi=65, price=22000) -> dict:
    return {
        'supertrend_dir': 1, 'adx': adx, 'rsi': rsi,
        'ema20': price - 50, 'ema50': price - 100,
        'atr': price * 0.01,
        'adx_plus_di': 28, 'adx_minus_di': 15,
        'macd_cross': 'BULLISH', 'macd_hist': 5.0,
        'cpr_type': 'narrow', 'candle_patterns': ['Hammer'],
    }


def _bearish_ta(adx=30, rsi=35, price=22000) -> dict:
    return {
        'supertrend_dir': -1, 'adx': adx, 'rsi': rsi,
        'ema20': price + 50, 'ema50': price + 100,
        'atr': price * 0.01,
        'adx_plus_di': 15, 'adx_minus_di': 28,
        'macd_cross': 'BEARISH', 'macd_hist': -5.0,
        'cpr_type': None, 'candle_patterns': [],
    }


def _tick(ticker='^NSEI', price=22000, ta=None):
    return {'ticker': ticker, 'price': price, 'technicals': ta or _bullish_ta(price=price)}


# ── Construction ─────────────────────────────────────────────────────────────

class TestConstruction:
    def test_defaults(self):
        s = SwingAIStrategy()
        assert s.name == 'SwingAI'
        assert s.state == StrategyState.IDLE
        assert s.config.market_type == MarketType.INDIAN_FO
        assert s.config.trade_mode == 'swing'

    def test_custom_config(self):
        s = _strategy(min_confidence=80, adx_min=20)
        assert s.swing.min_confidence == 80
        assert s.swing.adx_min == 20


# ── Daily State ──────────────────────────────────────────────────────────────

class TestDailyState:
    def test_reset(self):
        state = SwingDailyState()
        state.signal_count = 5
        state.latest_st_dir['^NSEI'] = 1
        state.reset()
        assert state.signal_count == 0
        assert state.latest_st_dir == {}


# ── Technical Pre-filter ─────────────────────────────────────────────────────

class TestTechnicalPrefilter:
    def test_bullish_signal(self):
        s = _strategy()
        sig = s._technical_prefilter('^NSEI', _bullish_ta(), 22000)
        assert sig is not None
        assert sig.direction == 'BUY'
        assert sig.instrument_type == 'CE'
        assert 50 <= sig.confidence <= 95

    def test_bearish_signal(self):
        s = _strategy()
        sig = s._technical_prefilter('^NSEI', _bearish_ta(), 22000)
        assert sig is not None
        assert sig.direction == 'SELL'
        assert sig.instrument_type == 'PE'

    def test_ranging_skipped(self):
        s = _strategy(adx_min=15)
        ta = _bullish_ta(adx=10)
        assert s._technical_prefilter('^NSEI', ta, 22000) is None

    def test_no_supertrend(self):
        s = _strategy()
        ta = _bullish_ta()
        ta['supertrend_dir'] = None
        assert s._technical_prefilter('^NSEI', ta, 22000) is None

    def test_strong_buy_high_adx(self):
        s = _strategy()
        ta = _bullish_ta(adx=45, rsi=75)
        sig = s._technical_prefilter('^NSEI', ta, 22000)
        assert sig is not None
        assert sig.confidence >= 80
        assert sig.verdict == 'STRONG BUY'

    def test_weak_bull_low_confidence(self):
        s = _strategy()
        ta = {
            'supertrend_dir': 1, 'adx': 16, 'rsi': 50,
            'ema20': 22100, 'ema50': 22200, 'atr': 220,
            'adx_plus_di': 12, 'adx_minus_di': 20,
            'macd_cross': 'BEARISH', 'macd_hist': -3.0,
            'cpr_type': 'wide', 'candle_patterns': [],
        }
        sig = s._technical_prefilter('^NSEI', ta, 22000)
        assert sig is not None
        assert sig.confidence < 60
        assert sig.action == 'WAIT'

    def test_max_confidence_clamped(self):
        s = _strategy()
        ta = _bullish_ta(adx=40, rsi=70, price=22000)
        sig = s._technical_prefilter('^NSEI', ta, 22000)
        assert sig is not None
        assert sig.confidence == 95  # clamped

    def test_correlation_guard_blocks(self):
        s = _strategy()
        s.daily.latest_st_dir['^NSEI'] = -1  # NIFTY bearish
        ta = _bullish_ta()  # SENSEX bullish → blocked
        sig = s._technical_prefilter('^BSESN', ta, 72000)
        assert sig is None

    def test_correlation_guard_allows(self):
        s = _strategy()
        s.daily.latest_st_dir['^NSEI'] = 1
        ta = _bullish_ta()
        sig = s._technical_prefilter('^BSESN', ta, 72000)
        assert sig is not None

    def test_strike_rounding_nifty(self):
        s = _strategy()
        sig = s._technical_prefilter('^NSEI', _bullish_ta(), 22000)
        assert sig.strike % 50 == 0

    def test_strike_rounding_sensex(self):
        s = _strategy()
        s.daily.latest_st_dir['^NSEI'] = 1
        sig = s._technical_prefilter('^BSESN', _bullish_ta(price=72000), 72000)
        assert sig.strike % 100 == 0

    def test_sl_t1_t2_ce(self):
        s = _strategy(sl_pct=2.0, t1_pct=3.0, t2_pct=5.0)
        sig = s._technical_prefilter('^NSEI', _bullish_ta(), 22000)
        assert sig.sl_underlying == pytest.approx(22000 * 0.98, abs=1)
        assert sig.t1_underlying == pytest.approx(22000 * 1.03, abs=1)

    def test_sl_t1_t2_pe(self):
        s = _strategy(sl_pct=2.0, t1_pct=3.0, t2_pct=5.0)
        sig = s._technical_prefilter('^NSEI', _bearish_ta(), 22000)
        assert sig.sl_underlying == pytest.approx(22000 * 1.02, abs=1)
        assert sig.t1_underlying == pytest.approx(22000 * 0.97, abs=1)

    def test_thesis_text(self):
        s = _strategy()
        sig = s._technical_prefilter('^NSEI', _bullish_ta(), 22000)
        assert 'BULLISH' in sig.thesis
        assert 'ADX=' in sig.thesis

    def test_lot_size_nifty(self):
        s = _strategy()
        sig = s._technical_prefilter('^NSEI', _bullish_ta(), 22000)
        assert sig.lot_size == 75


# ── on_tick ──────────────────────────────────────────────────────────────────

class TestOnTick:
    def test_empty_ticker(self):
        s = _strategy()
        result = s.on_tick({'ticker': '', 'price': 22000, 'technicals': {}})
        assert result.signals == []

    def test_zero_price(self):
        s = _strategy()
        result = s.on_tick({'ticker': '^NSEI', 'price': 0, 'technicals': {}})
        assert result.signals == []

    def test_bullish_emits_signal(self):
        s = _strategy()
        result = s.on_tick(_tick())
        assert result.has_signals
        sig = result.signals[0]
        assert sig.direction == 'BUY'
        assert sig.strategy_name == 'SwingAI'
        assert sig.trade_mode == 'swing'

    def test_ranging_no_signal(self):
        s = _strategy()
        result = s.on_tick(_tick(ta=_bullish_ta(adx=10)))
        assert not result.has_signals

    def test_signal_count_increments(self):
        s = _strategy(signal_cooldown_sec=0)
        s.on_tick(_tick())
        s.on_tick(_tick())
        assert s.daily.signal_count == 2

    def test_metadata_contains_technical(self):
        s = _strategy()
        result = s.on_tick(_tick())
        assert 'technical' in result.metadata


# ── LLM Blending (Stage 2) ──────────────────────────────────────────────────

class TestLLMBlending:
    def _tech(self, conf=70) -> TechnicalSignal:
        return TechnicalSignal(
            ticker='^NSEI', direction='BUY', instrument_type='CE',
            confidence=conf, price=22000, verdict='BUY', action='BUY NOW',
            adx=30, rsi=65, supertrend_dir=1,
        )

    def test_agreement_boosts(self):
        s = _strategy()
        result = s.apply_llm_result(self._tech(70), {'final_verdict': 'BUY', 'confidence_to_trade': 75})
        assert result.confidence == 85  # 75 + 10

    def test_disagreement_halves(self):
        s = _strategy()
        result = s.apply_llm_result(self._tech(70), {'final_verdict': 'STRONG SELL', 'confidence_to_trade': 60})
        assert result.confidence == 40  # max(60//2, 40)

    def test_cerebrum_hold_trusts_tech(self):
        s = _strategy()
        result = s.apply_llm_result(self._tech(72), {'final_verdict': 'HOLD', 'confidence_to_trade': 50})
        assert result.confidence == 67  # 72 - 5

    def test_blended_verdict_strong(self):
        s = _strategy()
        result = s.apply_llm_result(self._tech(70), {'final_verdict': 'BUY', 'confidence_to_trade': 85})
        assert result.confidence == 95
        assert result.verdict == 'STRONG BUY'

    def test_low_confidence_wait(self):
        s = _strategy()
        result = s.apply_llm_result(self._tech(55), {'final_verdict': 'STRONG SELL', 'confidence_to_trade': 80})
        assert result.action == 'WAIT'

    def test_preserves_fields(self):
        s = _strategy()
        tech = self._tech(70)
        result = s.apply_llm_result(tech, {'final_verdict': 'BUY', 'confidence_to_trade': 75})
        assert result.ticker == tech.ticker
        assert result.direction == tech.direction
        assert result.adx == tech.adx


# ── Strike & Levels ──────────────────────────────────────────────────────────

class TestStrikeAndLevels:
    def test_ce_levels(self):
        s = _strategy()
        strike, sl, t1, t2 = s._compute_strike_and_levels(22000, '^NSEI', 'CE')
        assert strike > 22000
        assert sl < 22000
        assert t1 > 22000 and t2 > t1

    def test_pe_levels(self):
        s = _strategy()
        strike, sl, t1, t2 = s._compute_strike_and_levels(22000, '^NSEI', 'PE')
        assert strike < 22000
        assert sl > 22000
        assert t1 < 22000 and t2 < t1

    def test_round_strike_nifty(self):
        assert SwingAIStrategy._round_strike(22037, '^NSEI') == 22050

    def test_round_strike_sensex(self):
        assert SwingAIStrategy._round_strike(72060, '^BSESN') == 72100


# ── Lot Size ─────────────────────────────────────────────────────────────────

class TestLotSize:
    def test_nifty(self):
        assert SwingAIStrategy._lot_size('^NSEI') == 75

    def test_sensex(self):
        assert SwingAIStrategy._lot_size('^BSESN') == 20

    def test_banknifty(self):
        assert SwingAIStrategy._lot_size('^NSEBANK') == 30

    def test_unknown(self):
        assert SwingAIStrategy._lot_size('RELIANCE') == 1


# ── Stats ────────────────────────────────────────────────────────────────────

class TestStats:
    def test_basic(self):
        s = _strategy()
        s.daily.signal_count = 3
        s.daily.latest_st_dir['^NSEI'] = 1
        stats = s.stats()
        assert stats['name'] == 'SwingAI'
        assert stats['signal_count'] == 3
        assert stats['latest_directions'] == {'^NSEI': 1}


# ── Get Instruments ──────────────────────────────────────────────────────────

class TestGetInstruments:
    def test_returns_instruments(self):
        s = _strategy()
        instruments = s.get_instruments()
        assert len(instruments) > 0
        assert instruments[0].underlying in ('^NSEI', '^BSESN')


# ── Phase 18: Thesis Flip Detection ──────────────────────────────────────────

class TestThesisFlip:
    def test_no_flip_without_open_position(self):
        s = _strategy()
        s.daily.latest_st_dir['^NSEI'] = 1
        ta = _bullish_ta()
        ta['supertrend_dir'] = -1  # reversal
        flip = s._check_thesis_flip('^NSEI', ta, 22000)
        assert flip is None  # no open position → no flip

    def test_flip_on_ce_with_bearish_reversal(self):
        s = _strategy()
        s.register_open_position('^NSEI', 'CE')
        s.daily.latest_st_dir['^NSEI'] = 1  # was bullish
        ta = {'supertrend_dir': -1, 'adx': 30}  # flipped bearish
        flip = s._check_thesis_flip('^NSEI', ta, 22000)
        assert flip is not None
        assert flip.old_direction == 'CE'
        assert flip.new_st_dir == -1
        assert 'BEARISH' in flip.reason

    def test_flip_on_pe_with_bullish_reversal(self):
        s = _strategy()
        s.register_open_position('^NSEI', 'PE')
        s.daily.latest_st_dir['^NSEI'] = -1  # was bearish
        ta = {'supertrend_dir': 1, 'adx': 25}
        flip = s._check_thesis_flip('^NSEI', ta, 22000)
        assert flip is not None
        assert flip.old_direction == 'PE'
        assert flip.new_st_dir == 1

    def test_no_flip_same_direction(self):
        s = _strategy()
        s.register_open_position('^NSEI', 'CE')
        s.daily.latest_st_dir['^NSEI'] = 1
        ta = {'supertrend_dir': 1, 'adx': 30}  # same direction
        flip = s._check_thesis_flip('^NSEI', ta, 22000)
        assert flip is None

    def test_flip_confidence_scales_with_adx(self):
        s = _strategy()
        s.register_open_position('^NSEI', 'CE')
        s.daily.latest_st_dir['^NSEI'] = 1
        # High ADX flip
        flip_strong = s._check_thesis_flip('^NSEI', {'supertrend_dir': -1, 'adx': 40}, 22000)
        s.daily.latest_st_dir['^NSEI'] = 1  # reset
        # Low ADX flip
        flip_weak = s._check_thesis_flip('^NSEI', {'supertrend_dir': -1, 'adx': 10}, 22000)
        assert flip_strong.confidence > flip_weak.confidence

    def test_unregister_position(self):
        s = _strategy()
        s.register_open_position('^NSEI', 'CE')
        s.unregister_position('^NSEI')
        s.daily.latest_st_dir['^NSEI'] = 1
        ta = {'supertrend_dir': -1, 'adx': 30}
        flip = s._check_thesis_flip('^NSEI', ta, 22000)
        assert flip is None  # unregistered → no flip

    def test_flip_in_on_tick_metadata(self):
        s = _strategy(signal_cooldown_sec=0)
        s.register_open_position('^NSEI', 'CE')
        s.daily.latest_st_dir['^NSEI'] = 1
        ta = _bearish_ta()
        result = s.on_tick(_tick(ta=ta))
        assert 'thesis_flip' in result.metadata


# ── Phase 18: Signal Cooldown ────────────────────────────────────────────────

class TestSignalCooldown:
    def test_cooldown_blocks_rapid_signals(self):
        s = _strategy(signal_cooldown_sec=600)
        r1 = s.on_tick(_tick())
        assert len(r1.signals) == 1
        # Second signal within 600s → blocked
        r2 = s.on_tick(_tick())
        assert len(r2.signals) == 0

    def test_cooldown_zero_allows_all(self):
        s = _strategy(signal_cooldown_sec=0)
        r1 = s.on_tick(_tick())
        r2 = s.on_tick(_tick())
        assert len(r1.signals) == 1
        assert len(r2.signals) == 1

    def test_cooldown_different_tickers_independent(self):
        s = _strategy(signal_cooldown_sec=600)
        r1 = s.on_tick(_tick(ticker='^NSEI'))
        r2 = s.on_tick(_tick(ticker='^BSESN', ta=_bullish_ta()))
        assert len(r1.signals) == 1
        assert len(r2.signals) == 1  # different ticker → not blocked


# ── Phase 18: VWAP Gate ──────────────────────────────────────────────────────

class TestVWAPGate:
    def test_vwap_boost_bullish_above(self):
        s = _strategy(signal_cooldown_sec=0, vwap_bonus=5, vwap_penalty=5)
        ta = _bullish_ta()
        ta['vwap'] = 21900  # price 22000 > VWAP → boost
        r = s.on_tick(_tick(ta=ta))
        sig_with_vwap = r.metadata['technical']

        s2 = _strategy(signal_cooldown_sec=0, vwap_enabled=False)
        r2 = s2.on_tick(_tick(ta=_bullish_ta()))
        sig_without_vwap = r2.metadata['technical']

        assert sig_with_vwap.confidence >= sig_without_vwap.confidence

    def test_vwap_penalty_bullish_below(self):
        s = _strategy(signal_cooldown_sec=0, vwap_bonus=5, vwap_penalty=5)
        ta = _bullish_ta()
        ta['vwap'] = 22100  # price 22000 < VWAP → penalty
        r = s.on_tick(_tick(ta=ta))
        sig_with_vwap = r.metadata['technical']

        s2 = _strategy(signal_cooldown_sec=0, vwap_enabled=False)
        r2 = s2.on_tick(_tick(ta=_bullish_ta()))
        sig_without_vwap = r2.metadata['technical']

        assert sig_with_vwap.confidence <= sig_without_vwap.confidence

    def test_vwap_disabled_no_effect(self):
        s = _strategy(signal_cooldown_sec=0, vwap_enabled=False)
        ta = _bullish_ta()
        ta['vwap'] = 22100
        r = s.on_tick(_tick(ta=ta))
        assert len(r.signals) == 1  # still signals

    def test_vwap_missing_no_effect(self):
        s = _strategy(signal_cooldown_sec=0, vwap_enabled=True)
        ta = _bullish_ta()  # no 'vwap' key
        r = s.on_tick(_tick(ta=ta))
        assert len(r.signals) == 1


# ── Phase 18: Multi-Timeframe Confluence ─────────────────────────────────────

class TestMTFConfluence:
    def test_all_agree_boosts_confidence(self):
        s = _strategy(signal_cooldown_sec=0, mtf_all_agree_bonus=10)
        ta = _bullish_ta()
        tick = _tick(ta=ta)
        tick['timeframes'] = [
            {'timeframe': '15m', 'supertrend_dir': 1},
            {'timeframe': '1h', 'supertrend_dir': 1},
            {'timeframe': '4h', 'supertrend_dir': 1},
        ]
        r = s.on_tick(tick)
        conf_with_mtf = r.metadata['technical'].confidence

        s2 = _strategy(signal_cooldown_sec=0, mtf_enabled=False)
        r2 = s2.on_tick(_tick(ta=_bullish_ta()))
        conf_without_mtf = r2.metadata['technical'].confidence

        assert conf_with_mtf > conf_without_mtf

    def test_majority_disagree_penalizes(self):
        s = _strategy(signal_cooldown_sec=0, mtf_disagree_penalty=8)
        ta = _bullish_ta()
        tick = _tick(ta=ta)
        tick['timeframes'] = [
            {'timeframe': '15m', 'supertrend_dir': -1},  # disagree
            {'timeframe': '1h', 'supertrend_dir': -1},   # disagree
            {'timeframe': '4h', 'supertrend_dir': 1},    # agree
        ]
        r = s.on_tick(tick)
        conf_disagree = r.metadata['technical'].confidence

        s2 = _strategy(signal_cooldown_sec=0, mtf_enabled=False)
        r2 = s2.on_tick(_tick(ta=_bullish_ta()))
        conf_no_mtf = r2.metadata['technical'].confidence

        assert conf_disagree < conf_no_mtf

    def test_no_timeframes_no_effect(self):
        s = _strategy(signal_cooldown_sec=0)
        r = s.on_tick(_tick())  # no timeframes key
        assert len(r.signals) == 1

    def test_mtf_disabled_no_effect(self):
        s = _strategy(signal_cooldown_sec=0, mtf_enabled=False)
        tick = _tick()
        tick['timeframes'] = [
            {'timeframe': '15m', 'supertrend_dir': -1},
            {'timeframe': '1h', 'supertrend_dir': -1},
        ]
        r1 = s.on_tick(tick)
        s2 = _strategy(signal_cooldown_sec=0, mtf_enabled=False)
        r2 = s2.on_tick(_tick())
        assert r1.metadata['technical'].confidence == r2.metadata['technical'].confidence


# ── Phase 18: RSI Extreme Confidence Decay ───────────────────────────────────

class TestRSIDecay:
    def test_bullish_overbought_reduces_confidence(self):
        s = _strategy(signal_cooldown_sec=0, rsi_overbought=75.0, rsi_extreme_penalty=10)
        ta = _bullish_ta(rsi=85)
        r = s.on_tick(_tick(ta=ta))
        conf_high_rsi = r.metadata['technical'].confidence

        s2 = _strategy(signal_cooldown_sec=0, rsi_overbought=75.0, rsi_extreme_penalty=10)
        ta2 = _bullish_ta(rsi=60)
        r2 = s2.on_tick(_tick(ta=ta2))
        conf_normal_rsi = r2.metadata['technical'].confidence

        assert conf_high_rsi < conf_normal_rsi

    def test_bearish_oversold_reduces_confidence(self):
        s = _strategy(signal_cooldown_sec=0, rsi_oversold=25.0, rsi_extreme_penalty=15)
        ta = _bearish_ta(rsi=10)  # deeply oversold
        r = s.on_tick(_tick(ta=ta))
        conf_low_rsi = r.metadata['technical'].confidence

        s2 = _strategy(signal_cooldown_sec=0, rsi_oversold=25.0, rsi_extreme_penalty=15)
        ta2 = _bearish_ta(rsi=40)
        r2 = s2.on_tick(_tick(ta=ta2))
        conf_normal_rsi = r2.metadata['technical'].confidence

        assert conf_low_rsi < conf_normal_rsi

    def test_rsi_in_normal_range_no_penalty(self):
        s = _strategy(signal_cooldown_sec=0)
        ta = _bullish_ta(rsi=60)
        r = s.on_tick(_tick(ta=ta))
        # No penalty should apply
        assert len(r.signals) == 1


# ── Phase 18: LLM Prompt Builder ─────────────────────────────────────────────

class TestLLMPromptBuilder:
    def test_prompt_contains_key_sections(self):
        s = _strategy()
        sig = TechnicalSignal(
            ticker='^NSEI', direction='BUY', instrument_type='CE',
            confidence=78, price=22000.0, verdict='BUY', action='BUY NOW',
            adx=30, rsi=62, supertrend_dir=1, ema20=21950, ema50=21900,
            plus_di=28, minus_di=15, macd_cross='BULLISH', macd_hist=5.0,
            atr=220.0, confirms=2, contradicts=0,
            strike=22350, sl_underlying=21560, t1_underlying=22660,
            t2_underlying=23100, estimated_premium=132.0, lot_size=75,
            thesis='Supertrend BULLISH, ADX=30',
        )
        prompt = s.build_llm_prompt(sig)
        assert 'TRADE DECISION REQUEST' in prompt
        assert '^NSEI' in prompt
        assert 'BULLISH' in prompt
        assert 'ADX: 30.0' in prompt
        assert 'RSI: 62.0' in prompt
        assert 'final_verdict' in prompt  # JSON template

    def test_prompt_bearish(self):
        s = _strategy()
        sig = TechnicalSignal(
            ticker='^NSEI', direction='SELL', instrument_type='PE',
            confidence=72, price=22000.0, verdict='SELL', action='SELL NOW',
            adx=25, rsi=35, supertrend_dir=-1, ema20=22050, ema50=22100,
            plus_di=15, minus_di=28, macd_cross='BEARISH', macd_hist=-3.0,
            atr=200.0, confirms=2, contradicts=0,
        )
        prompt = s.build_llm_prompt(sig)
        assert 'BEARISH' in prompt
        assert 'DOWN' in prompt  # Supertrend DOWN


# ── Phase 18: Position Registration ──────────────────────────────────────────

class TestPositionRegistration:
    def test_register_and_unregister(self):
        s = _strategy()
        s.register_open_position('^NSEI', 'CE')
        assert s.daily.open_directions['^NSEI'] == 'CE'
        s.unregister_position('^NSEI')
        assert '^NSEI' not in s.daily.open_directions

    def test_unregister_nonexistent_is_noop(self):
        s = _strategy()
        s.unregister_position('^NSEI')  # should not raise

    def test_reset_clears_positions(self):
        s = _strategy()
        s.register_open_position('^NSEI', 'CE')
        s.daily.reset()
        assert len(s.daily.open_directions) == 0
