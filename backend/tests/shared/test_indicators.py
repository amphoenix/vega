"""
Tests for shared/indicators.py — pure TA functions.
"""

import pytest

from app.shared.indicators import (
    CandleData,
    adx,
    atr,
    atr_from_dicts,
    bollinger_bands,
    donchian,
    donchian_from_dicts,
    ema,
    ema_series,
    roc,
    rsi,
    sma,
    volume_ratio,
    vwap,
    vwap_from_dicts,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _candles(n: int, base: float = 100.0, step: float = 1.0) -> list[CandleData]:
    """Generate N rising candles for testing."""
    result = []
    for i in range(n):
        c = base + i * step
        result.append(CandleData(
            open=c - 0.5,
            high=c + 1.0,
            low=c - 1.0,
            close=c,
            volume=1000 + i * 10,
            date=f'2025-06-18 09:{15 + i:02d}',
        ))
    return result


def _candle_dicts(n: int, base: float = 100.0, step: float = 1.0) -> list[dict]:
    return [
        {'open': base + i - 0.5, 'high': base + i + 1, 'low': base + i - 1,
         'close': base + i * step, 'volume': 1000 + i * 10,
         'date': f'2025-06-18 09:{15 + i:02d}'}
        for i in range(n)
    ]


# ── EMA ──────────────────────────────────────────────────────────────────────

class TestEMA:
    def test_basic(self):
        values = [10.0, 11.0, 12.0, 13.0, 14.0]
        result = ema(values, 3)
        assert result is not None
        assert isinstance(result, float)

    def test_too_short(self):
        assert ema([1.0, 2.0], 5) is None

    def test_exact_period(self):
        values = [10.0, 20.0, 30.0]
        result = ema(values, 3)
        assert result == pytest.approx(20.0)  # SMA of 3 values, no extra

    def test_series_length(self):
        values = list(range(1, 21))
        series = ema_series([float(v) for v in values], 5)
        assert len(series) == len(values)


class TestSMA:
    def test_basic(self):
        assert sma([10.0, 20.0, 30.0], 3) == pytest.approx(20.0)

    def test_too_short(self):
        assert sma([10.0], 3) is None


# ── RSI ──────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_all_gains(self):
        closes = [float(i) for i in range(20)]  # 0,1,2,...19
        result = rsi(closes)
        assert result is not None
        assert result == 100.0

    def test_too_short(self):
        assert rsi([1.0, 2.0, 3.0]) is None

    def test_mixed(self):
        closes = [100, 102, 101, 103, 100, 98, 99, 101, 103, 105,
                  104, 102, 100, 99, 101, 103]
        result = rsi(closes)
        assert result is not None
        assert 0 <= result <= 100


# ── ROC ──────────────────────────────────────────────────────────────────────

class TestROC:
    def test_basic(self):
        values = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 110.0]
        result = roc(values, 5)
        assert result is not None
        assert result == pytest.approx((110 - 101) / 101 * 100)

    def test_too_short(self):
        assert roc([100.0, 110.0], 5) is None

    def test_zero_prev(self):
        values = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        assert roc(values, 5) is None


# ── ATR ──────────────────────────────────────────────────────────────────────

class TestATR:
    def test_basic(self):
        candles = _candles(20)
        result = atr(candles)
        assert result is not None
        assert result > 0

    def test_too_short(self):
        assert atr(_candles(5)) is None

    def test_from_dicts(self):
        result = atr_from_dicts(_candle_dicts(20))
        assert result is not None
        assert result > 0


# ── Bollinger Bands ──────────────────────────────────────────────────────────

class TestBollinger:
    def test_basic(self):
        closes = [float(100 + i) for i in range(25)]
        result = bollinger_bands(closes)
        assert result is not None
        upper, mid, lower = result
        assert upper > mid > lower

    def test_too_short(self):
        assert bollinger_bands([1.0, 2.0]) is None

    def test_constant_values(self):
        closes = [100.0] * 20
        upper, mid, lower = bollinger_bands(closes)
        assert upper == mid == lower == 100.0


# ── Donchian ─────────────────────────────────────────────────────────────────

class TestDonchian:
    def test_basic(self):
        candles = _candles(25)
        result = donchian(candles, 20)
        assert result is not None
        assert result.high >= result.low
        assert result.mid == (result.high + result.low) / 2

    def test_width(self):
        result = donchian(_candles(25), 20)
        assert result.width > 0

    def test_too_short(self):
        assert donchian(_candles(5), 20) is None

    def test_from_dicts(self):
        result = donchian_from_dicts(_candle_dicts(25), 20)
        assert result is not None


# ── VWAP ─────────────────────────────────────────────────────────────────────

class TestVWAP:
    def test_basic(self):
        candles = _candles(10)
        result = vwap(candles)
        assert result is not None
        assert result > 0

    def test_session_filter(self):
        candles = _candles(10)
        # Session starts at 09:20 → first 5 candles (09:15-09:19) excluded
        result = vwap(candles, session_start='2025-06-18 09:20')
        assert result is not None

    def test_zero_volume(self):
        candles = [CandleData(open=100, high=101, low=99, close=100, volume=0)]
        assert vwap(candles) is None

    def test_from_dicts(self):
        result = vwap_from_dicts(_candle_dicts(10))
        assert result is not None


# ── Volume Ratio ─────────────────────────────────────────────────────────────

class TestVolumeRatio:
    def test_basic(self):
        candles = _candles(25)
        result = volume_ratio(candles, 20)
        assert result > 0

    def test_single_candle(self):
        assert volume_ratio(_candles(1)) == 0.0

    def test_spike(self):
        candles = _candles(10)
        # Make last candle have 10x volume
        big = CandleData(open=110, high=112, low=109, close=111, volume=10000)
        candles.append(big)
        result = volume_ratio(candles, 10)
        assert result > 5.0  # should be a big spike


# ── ADX ──────────────────────────────────────────────────────────────────────

class TestADX:
    def test_trending(self):
        # Strong uptrend → high ADX
        candles = _candles(40, base=100, step=2.0)
        result = adx(candles)
        assert result is not None
        assert result > 0

    def test_too_short(self):
        assert adx(_candles(10)) is None


# ── CandleData ───────────────────────────────────────────────────────────────

class TestCandleData:
    def test_from_dict(self):
        d = {'open': 100, 'high': 105, 'low': 98, 'close': 103, 'volume': 5000}
        c = CandleData.from_dict(d)
        assert c.open == 100
        assert c.high == 105
        assert c.close == 103
        assert c.volume == 5000

    def test_from_dict_missing(self):
        c = CandleData.from_dict({})
        assert c.open == 0
        assert c.close == 0
        assert c.volume == 0
