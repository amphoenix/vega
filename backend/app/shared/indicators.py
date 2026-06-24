"""
Technical Analysis functions — pure math, no I/O.

Supertrend, ADX, RSI, EMA, MACD, ATR, Bollinger Bands, Donchian.
Ported from v1 ta_utils.py.

All functions work on plain lists/dicts — no pandas required.

FUTURE: when this exceeds ~800 LOC, split into:
    indicators/trend.py
    indicators/momentum.py
    indicators/volatility.py
    indicators/volume.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Trend ────────────────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> Optional[float]:
    """Exponential Moving Average. Returns latest EMA value or None."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    result = sum(values[:period]) / period  # SMA seed
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result


def ema_series(values: list[float], period: int) -> list[float]:
    """Full EMA series (same length as input, leading NaNs replaced by SMA seed)."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    result = [0.0] * (period - 1) + [seed]
    for i in range(period, len(values)):
        result.append(values[i] * k + result[-1] * (1 - k))
    return result


def sma(values: list[float], period: int) -> Optional[float]:
    """Simple Moving Average. Returns latest SMA or None."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


# ── Momentum ─────────────────────────────────────────────────────────────────

def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index. Returns 0-100 or None."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def roc(values: list[float], period: int = 5) -> Optional[float]:
    """Rate of Change (%). Returns None if insufficient data."""
    if len(values) < period + 1:
        return None
    prev = values[-(period + 1)]
    if prev == 0:
        return None
    return (values[-1] - prev) / prev * 100


def stoch_rsi(
    closes: list[float], rsi_period: int = 14,
    stoch_period: int = 14, k_smooth: int = 3, d_smooth: int = 3,
) -> Optional[dict]:
    """Stochastic RSI (NostalgiaForInfinity's primary entry filter).

    Calculates RSI, then applies Stochastic formula on the RSI values,
    then smooths with SMA for %K and %D.

    Returns: {'k': float (0-100), 'd': float (0-100)} or None.
    """
    need = rsi_period + stoch_period + k_smooth + d_smooth + 2
    if len(closes) < need:
        return None

    # Step 1: compute full RSI series
    rsi_vals: list[float] = []
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[:rsi_period]) / rsi_period
    avg_loss = sum(losses[:rsi_period]) / rsi_period

    for i in range(rsi_period, len(gains)):
        avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
        avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rsi_vals.append(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))

    if len(rsi_vals) < stoch_period:
        return None

    # Step 2: Stochastic on RSI values
    stoch_raw: list[float] = []
    for i in range(stoch_period - 1, len(rsi_vals)):
        window = rsi_vals[i - stoch_period + 1:i + 1]
        lo = min(window)
        hi = max(window)
        if hi == lo:
            stoch_raw.append(50.0)
        else:
            stoch_raw.append((rsi_vals[i] - lo) / (hi - lo) * 100.0)

    if len(stoch_raw) < k_smooth:
        return None

    # Step 3: SMA smooth for %K
    k_vals: list[float] = []
    for i in range(k_smooth - 1, len(stoch_raw)):
        k_vals.append(sum(stoch_raw[i - k_smooth + 1:i + 1]) / k_smooth)

    if len(k_vals) < d_smooth:
        return None

    # Step 4: SMA smooth for %D
    d_val = sum(k_vals[-d_smooth:]) / d_smooth

    return {'k': round(k_vals[-1], 2), 'd': round(d_val, 2)}


def aroon(candles_or_highs, lows_list=None, period: int = 14) -> Optional[dict]:
    """Aroon Up/Down indicator (NFI uses AROONU_14 < 25 for dip entry).

    Can accept either:
    - candles_or_highs: list[CandleData] (uses .high/.low)
    - candles_or_highs: list[float] (highs), lows_list: list[float]

    Returns: {'up': float (0-100), 'down': float (0-100)} or None.
    """
    if lows_list is not None:
        highs = candles_or_highs
        lows = lows_list
    else:
        # Assume list of CandleData-like objects
        highs = [c.high if hasattr(c, 'high') else c['high'] for c in candles_or_highs]
        lows = [c.low if hasattr(c, 'low') else c['low'] for c in candles_or_highs]

    if len(highs) < period + 1 or len(lows) < period + 1:
        return None

    window_h = highs[-(period + 1):]
    window_l = lows[-(period + 1):]

    # Find index of highest high and lowest low in window
    max_idx = 0
    min_idx = 0
    for i in range(1, len(window_h)):
        if window_h[i] >= window_h[max_idx]:
            max_idx = i
        if window_l[i] <= window_l[min_idx]:
            min_idx = i

    # Bars since highest high / lowest low
    bars_since_high = period - max_idx
    bars_since_low = period - min_idx

    aroon_up = ((period - bars_since_high) / period) * 100.0
    aroon_down = ((period - bars_since_low) / period) * 100.0

    return {'up': round(aroon_up, 2), 'down': round(aroon_down, 2)}


# ── Volatility ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class CandleData:
    """Minimal candle representation for indicator functions."""
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    date: str = ''

    @classmethod
    def from_dict(cls, d: dict) -> 'CandleData':
        return cls(
            open=float(d.get('open', 0)),
            high=float(d.get('high', 0)),
            low=float(d.get('low', 0)),
            close=float(d.get('close', 0)),
            volume=float(d.get('volume', 0)),
            date=str(d.get('date', '')),
        )


def atr(candles: list[CandleData], period: int = 14) -> Optional[float]:
    """Average True Range from candle data. Returns latest ATR or None."""
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        c = candles[i]
        pc = candles[i - 1].close
        tr = max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def atr_from_dicts(candles: list[dict], period: int = 14) -> Optional[float]:
    """ATR directly from candle dicts (convenience wrapper)."""
    return atr([CandleData.from_dict(c) for c in candles], period)


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0,
) -> Optional[tuple[float, float, float]]:
    """Bollinger Bands → (upper, middle, lower) or None."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    return (mid + num_std * std, mid, mid - num_std * std)


# ── Channel / Breakout ───────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DonchianChannel:
    """Donchian channel result."""
    high: float
    low: float
    mid: float

    @property
    def width(self) -> float:
        return self.high - self.low


def donchian(candles: list[CandleData], period: int = 20) -> Optional[DonchianChannel]:
    """Donchian Channel from candle data."""
    if len(candles) < period:
        return None
    window = candles[-period:]
    h = max(c.high for c in window)
    l = min(c.low for c in window)
    return DonchianChannel(high=h, low=l, mid=(h + l) / 2)


def donchian_from_dicts(
    candles: list[dict], period: int = 20,
) -> Optional[DonchianChannel]:
    """Donchian channel from candle dicts."""
    return donchian([CandleData.from_dict(c) for c in candles], period)


# ── Volume ───────────────────────────────────────────────────────────────────

def vwap(candles: list[CandleData], session_start: str = '') -> Optional[float]:
    """Volume-Weighted Average Price.

    If session_start is provided (e.g. '2025-06-18 09:15'), only candles
    from that timestamp onward are included (intraday VWAP reset).
    """
    cum_vol = 0.0
    cum_tp_vol = 0.0
    for c in candles:
        if session_start and c.date < session_start:
            continue
        tp = (c.high + c.low + c.close) / 3
        cum_vol += c.volume
        cum_tp_vol += tp * c.volume
    if cum_vol == 0:
        return None
    return round(cum_tp_vol / cum_vol, 2)


def vwap_from_dicts(
    candles: list[dict], session_start: str = '',
) -> Optional[float]:
    """VWAP from candle dicts."""
    return vwap([CandleData.from_dict(c) for c in candles], session_start)


def volume_ratio(candles: list[CandleData], lookback: int = 20) -> float:
    """Current bar volume / average of previous N bars."""
    if len(candles) < 2:
        return 0.0
    prev = candles[-(lookback + 1):-1] if len(candles) > lookback else candles[:-1]
    avg = sum(c.volume for c in prev) / max(len(prev), 1)
    if avg == 0:
        return 0.0
    return round(candles[-1].volume / avg, 2)


# ── ADX ──────────────────────────────────────────────────────────────────────

def adx(candles: list[CandleData], period: int = 14) -> Optional[float]:
    """Average Directional Index (Wilder's method). Delegates to adx_full()."""
    result = adx_full(candles, period)
    return result['adx'] if result else None


def adx_full(
    candles: list[CandleData], period: int = 14,
) -> Optional[dict]:
    """ADX with +DI / -DI breakdown. Returns dict or None.

    Keys: adx, plus_di, minus_di
    """
    if len(candles) < period * 2 + 1:
        return None

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr_list: list[float] = []

    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        up = c.high - p.high
        down = p.low - c.low
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        tr_list.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))

    if len(tr_list) < period:
        return None

    atr_val = sum(tr_list[:period])
    plus_di_sum = sum(plus_dm[:period])
    minus_di_sum = sum(minus_dm[:period])

    dx_values: list[float] = []
    last_plus_di = 0.0
    last_minus_di = 0.0
    for i in range(period, len(tr_list)):
        atr_val = atr_val - (atr_val / period) + tr_list[i]
        plus_di_sum = plus_di_sum - (plus_di_sum / period) + plus_dm[i]
        minus_di_sum = minus_di_sum - (minus_di_sum / period) + minus_dm[i]

        if atr_val == 0:
            continue
        last_plus_di = 100 * plus_di_sum / atr_val
        last_minus_di = 100 * minus_di_sum / atr_val
        di_sum = last_plus_di + last_minus_di
        if di_sum == 0:
            dx_values.append(0)
        else:
            dx_values.append(100 * abs(last_plus_di - last_minus_di) / di_sum)

    if len(dx_values) < period:
        return None
    return {
        'adx': sum(dx_values[-period:]) / period,
        'plus_di': last_plus_di,
        'minus_di': last_minus_di,
    }


# ── Supertrend ───────────────────────────────────────────────────────────────

def supertrend(
    candles: list[CandleData], period: int = 10, multiplier: float = 3.0,
) -> Optional[dict]:
    """Supertrend indicator.

    Returns dict with:
      direction: 1 (bullish) or -1 (bearish)
      value: current supertrend level
      upper_band, lower_band: current bands
    """
    if len(candles) < period + 1:
        return None

    # Compute ATR series using simple average (matching v1 ta_utils)
    trs: list[float] = [0.0]
    for i in range(1, len(candles)):
        c = candles[i]
        pc = candles[i - 1].close
        trs.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))

    # Wilder-smoothed ATR
    atr_series = [0.0] * len(candles)
    atr_series[period] = sum(trs[1:period + 1]) / period
    for i in range(period + 1, len(candles)):
        atr_series[i] = (atr_series[i - 1] * (period - 1) + trs[i]) / period

    direction = 1
    upper = [0.0] * len(candles)
    lower = [0.0] * len(candles)

    for i in range(period, len(candles)):
        hl2 = (candles[i].high + candles[i].low) / 2
        basic_upper = hl2 + multiplier * atr_series[i]
        basic_lower = hl2 - multiplier * atr_series[i]

        # Band continuity
        upper[i] = basic_upper if basic_upper < upper[i - 1] or candles[i - 1].close > upper[i - 1] \
            else upper[i - 1]
        lower[i] = basic_lower if basic_lower > lower[i - 1] or candles[i - 1].close < lower[i - 1] \
            else lower[i - 1]

        # Direction flip
        if candles[i].close > upper[i]:
            direction = 1
        elif candles[i].close < lower[i]:
            direction = -1

    return {
        'direction': direction,
        'value': lower[-1] if direction == 1 else upper[-1],
        'upper_band': upper[-1],
        'lower_band': lower[-1],
    }


# ── MACD ─────────────────────────────────────────────────────────────────────

def macd(
    closes: list[float],
    fast: int = 12, slow: int = 26, signal_period: int = 9,
) -> Optional[dict]:
    """MACD → {macd_line, signal_line, histogram, cross}.

    cross: 'BULLISH' | 'BEARISH' | 'NONE'
    """
    if len(closes) < slow + signal_period:
        return None

    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    if not fast_ema or not slow_ema:
        return None

    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    # Signal line = EMA of MACD line (use only valid portion)
    valid_macd = macd_line[slow - 1:]  # skip leading zeros
    if len(valid_macd) < signal_period:
        return None
    sig_ema = ema_series(valid_macd, signal_period)
    if not sig_ema:
        return None

    hist = valid_macd[-1] - sig_ema[-1]

    # Cross detection: compare last 2 bars
    cross = 'NONE'
    if len(valid_macd) >= 2 and len(sig_ema) >= 2:
        prev_diff = valid_macd[-2] - sig_ema[-2]
        curr_diff = valid_macd[-1] - sig_ema[-1]
        if prev_diff <= 0 < curr_diff:
            cross = 'BULLISH'
        elif prev_diff >= 0 > curr_diff:
            cross = 'BEARISH'

    return {
        'macd_line': valid_macd[-1],
        'signal_line': sig_ema[-1],
        'histogram': hist,
        'cross': cross,
    }


# ── Candlestick patterns ─────────────────────────────────────────────────────

def detect_candle_patterns(candles: list[CandleData]) -> list[dict]:
    """Detect Japanese candlestick patterns on the last few candles.

    Returns list of {'pattern': str, 'bias': 'bullish'|'bearish'|'neutral', 'weight': int}.
    Weight is 1-3: 1=weak single-candle, 2=moderate, 3=strong multi-candle.
    """
    if len(candles) < 5:
        return []

    patterns: list[dict] = []
    c = candles[-1]
    p = candles[-2]
    pp = candles[-3]

    body = abs(c.close - c.open)
    rng = c.high - c.low
    if rng == 0:
        return patterns

    body_pct = body / rng
    upper_wick = c.high - max(c.open, c.close)
    lower_wick = min(c.open, c.close) - c.low
    is_bull = c.close > c.open
    is_bear = c.close < c.open

    prev_body = abs(p.close - p.open)
    prev_rng = p.high - p.low

    # ── Single candle patterns ────────────────────────────────────────────

    # Doji — tiny body
    if body_pct < 0.1:
        patterns.append({'pattern': 'Doji', 'bias': 'neutral', 'weight': 1})

    # Hammer / Hanging Man — small body at top, long lower shadow
    if (lower_wick >= 2 * body and upper_wick <= body * 0.3 and body_pct < 0.4):
        # Hammer after downtrend = bullish
        if candles[-3].close > candles[-2].close > c.low:
            patterns.append({'pattern': 'Hammer', 'bias': 'bullish', 'weight': 2})
        else:
            patterns.append({'pattern': 'Hanging Man', 'bias': 'bearish', 'weight': 1})

    # Inverted Hammer / Shooting Star — small body at bottom, long upper shadow
    if (upper_wick >= 2 * body and lower_wick <= body * 0.3 and body_pct < 0.4):
        if candles[-3].close > candles[-2].close:
            patterns.append({'pattern': 'Inverted Hammer', 'bias': 'bullish', 'weight': 1})
        else:
            patterns.append({'pattern': 'Shooting Star', 'bias': 'bearish', 'weight': 2})

    # Marubozu — strong body, tiny wicks
    if body_pct >= 0.85:
        if is_bull:
            patterns.append({'pattern': 'Bullish Marubozu', 'bias': 'bullish', 'weight': 2})
        else:
            patterns.append({'pattern': 'Bearish Marubozu', 'bias': 'bearish', 'weight': 2})

    # ── Two candle patterns ───────────────────────────────────────────────

    # Bullish Engulfing
    if (p.close < p.open and is_bull
            and c.open <= p.close and c.close >= p.open
            and body > prev_body):
        patterns.append({'pattern': 'Bullish Engulfing', 'bias': 'bullish', 'weight': 3})

    # Bearish Engulfing
    if (p.close > p.open and is_bear
            and c.open >= p.close and c.close <= p.open
            and body > prev_body):
        patterns.append({'pattern': 'Bearish Engulfing', 'bias': 'bearish', 'weight': 3})

    # Piercing Line — bearish prev, bullish curr opens below prev low, closes above mid
    prev_mid = (p.open + p.close) / 2
    if (p.close < p.open and is_bull
            and c.open < p.close and c.close > prev_mid and c.close < p.open):
        patterns.append({'pattern': 'Piercing Line', 'bias': 'bullish', 'weight': 2})

    # Dark Cloud Cover
    if (p.close > p.open and is_bear
            and c.open > p.close and c.close < prev_mid and c.close > p.open):
        patterns.append({'pattern': 'Dark Cloud Cover', 'bias': 'bearish', 'weight': 2})

    # Tweezer Bottom — similar lows after downtrend
    if (abs(c.low - p.low) / rng < 0.03 and is_bull and p.close < p.open):
        patterns.append({'pattern': 'Tweezer Bottom', 'bias': 'bullish', 'weight': 2})

    # Tweezer Top — similar highs after uptrend
    if (abs(c.high - p.high) / rng < 0.03 and is_bear and p.close > p.open):
        patterns.append({'pattern': 'Tweezer Top', 'bias': 'bearish', 'weight': 2})

    # ── Three candle patterns ─────────────────────────────────────────────

    pp_body = abs(pp.close - pp.open)
    pp_rng = pp.high - pp.low

    # Morning Star — bearish, small-body, bullish
    if (pp.close < pp.open                                   # bearish first
            and abs(p.close - p.open) / max(prev_rng, 1e-9) < 0.3  # small middle
            and is_bull                                        # bullish third
            and c.close > (pp.open + pp.close) / 2):           # closes above mid of first
        patterns.append({'pattern': 'Morning Star', 'bias': 'bullish', 'weight': 3})

    # Evening Star — bullish, small-body, bearish
    if (pp.close > pp.open
            and abs(p.close - p.open) / max(prev_rng, 1e-9) < 0.3
            and is_bear
            and c.close < (pp.open + pp.close) / 2):
        patterns.append({'pattern': 'Evening Star', 'bias': 'bearish', 'weight': 3})

    # Three White Soldiers
    if (pp.close > pp.open and p.close > p.open and is_bull
            and p.close > pp.close and c.close > p.close
            and pp_body / max(pp_rng, 1e-9) > 0.5
            and prev_body / max(prev_rng, 1e-9) > 0.5
            and body_pct > 0.5):
        patterns.append({'pattern': 'Three White Soldiers', 'bias': 'bullish', 'weight': 3})

    # Three Black Crows
    if (pp.close < pp.open and p.close < p.open and is_bear
            and p.close < pp.close and c.close < p.close
            and pp_body / max(pp_rng, 1e-9) > 0.5
            and prev_body / max(prev_rng, 1e-9) > 0.5
            and body_pct > 0.5):
        patterns.append({'pattern': 'Three Black Crows', 'bias': 'bearish', 'weight': 3})

    return patterns


# ── Parabolic SAR ────────────────────────────────────────────────────────────

def parabolic_sar(
    candles: list[CandleData],
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
) -> Optional[float]:
    """Compute Parabolic SAR and return latest value.

    Standard Welles Wilder algorithm used by TA-Lib / Freqtrade.
    Returns the SAR value for the most recent candle, or None if not enough data.
    """
    if len(candles) < 3:
        return None

    # Initialise with first two candles
    is_long = candles[1].close > candles[0].close
    af = af_start
    if is_long:
        sar = candles[0].low
        ep = candles[1].high
    else:
        sar = candles[0].high
        ep = candles[1].low

    for i in range(2, len(candles)):
        c = candles[i]
        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)

        if is_long:
            # SAR cannot be above prior two lows
            sar = min(sar, candles[i - 1].low, candles[i - 2].low)
            if c.low < sar:
                # Flip to short
                is_long = False
                sar = ep
                ep = c.low
                af = af_start
            else:
                if c.high > ep:
                    ep = c.high
                    af = min(af + af_step, af_max)
        else:
            # SAR cannot be below prior two highs
            sar = max(sar, candles[i - 1].high, candles[i - 2].high)
            if c.high > sar:
                # Flip to long
                is_long = True
                sar = ep
                ep = c.high
                af = af_start
            else:
                if c.low < ep:
                    ep = c.low
                    af = min(af + af_step, af_max)

    return round(sar, 8)
