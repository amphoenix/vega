"""
Pure-Python technical indicators — zero external dependencies.
Supertrend, ADX, ATR, Donchian, EMA, RSI, Bollinger, MACD.
Ported from tradingview-mcp indicators_calc.py + extended.
"""
from __future__ import annotations

import math


# ── EMA ───────────────────────────────────────────────────────────────────────
def calc_ema(closes: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) < period:
        return result
    k = 2 / (period + 1)
    sma = sum(closes[:period]) / period
    result[period - 1] = sma
    for i in range(period, len(closes)):
        result[i] = closes[i] * k + result[i - 1] * (1 - k)
    return result


# ── SMA ───────────────────────────────────────────────────────────────────────
def calc_sma(closes: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1: i + 1]) / period
    return result


# ── RSI (Wilder) ──────────────────────────────────────────────────────────────
def calc_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return result
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        result[i] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return result


# ── Bollinger Bands ───────────────────────────────────────────────────────────
def calc_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0) -> dict:
    middle = calc_sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        mean = middle[i]
        std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
        upper[i] = mean + std_mult * std
        lower[i] = mean - std_mult * std
    return {"upper": upper, "middle": middle, "lower": lower}


# ── MACD ──────────────────────────────────────────────────────────────────────
def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    n = len(closes)
    macd_line: list[float | None] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]
    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n
    macd_vals = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    if len(macd_vals) >= signal:
        sig_ema = calc_ema([v for _, v in macd_vals], signal)
        for j, (orig_i, _) in enumerate(macd_vals):
            if sig_ema[j] is not None:
                signal_line[orig_i] = sig_ema[j]
                histogram[orig_i] = macd_line[orig_i] - sig_ema[j]
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


# ── ATR ───────────────────────────────────────────────────────────────────────
def calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    n = len(closes)
    result: list[float | None] = [None] * n
    if n < period + 1:
        return result
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, n)]
    atr = sum(trs[:period]) / period
    result[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i - 1]) / period
        result[i] = atr
    return result


# ── Supertrend ────────────────────────────────────────────────────────────────
def calc_supertrend(highs: list[float], lows: list[float], closes: list[float],
                    atr_period: int = 10, multiplier: float = 3.0) -> dict:
    """
    Returns direction (1=bullish, -1=bearish), upper band, lower band.
    Supertrend is one of the best trend-following indicators for intraday F&O.
    """
    n = len(closes)
    atr = calc_atr(highs, lows, closes, atr_period)
    direction: list[int | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    prev_upper = prev_lower = prev_dir = None

    for i in range(1, n):
        if atr[i] is None:
            continue
        hl2 = (highs[i] + lows[i]) / 2.0
        u = hl2 + multiplier * atr[i]
        l = hl2 - multiplier * atr[i]
        if prev_upper is not None:
            u = min(u, prev_upper) if closes[i - 1] < prev_upper else u
            l = max(l, prev_lower) if closes[i - 1] > prev_lower else l
        upper[i] = u
        lower[i] = l
        if prev_dir is None:
            direction[i] = 1 if closes[i] > u else -1
        elif prev_dir == 1:
            direction[i] = 1 if closes[i] >= l else -1
        else:
            direction[i] = -1 if closes[i] <= u else 1
        prev_upper, prev_lower, prev_dir = u, l, direction[i]

    return {"direction": direction, "upper": upper, "lower": lower}


# ── ADX (Average Directional Index) ──────────────────────────────────────────
def calc_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> dict:
    """
    ADX measures trend strength (not direction).
    ADX > 25  = strong trend (trade with it)
    ADX < 20  = weak/ranging market (avoid trend trades)
    +DI > -DI = bullish; -DI > +DI = bearish
    """
    n = len(closes)
    adx_vals: list[float | None] = [None] * n
    pdi_vals: list[float | None] = [None] * n
    mdi_vals: list[float | None] = [None] * n

    if n < period * 2:
        return {"adx": adx_vals, "+di": pdi_vals, "-di": mdi_vals}

    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        up_move = highs[i] - highs[i - 1]
        dn_move = lows[i - 1] - lows[i]
        plus_dms.append(up_move if up_move > dn_move and up_move > 0 else 0)
        minus_dms.append(dn_move if dn_move > up_move and dn_move > 0 else 0)
        trs.append(tr)

    # Wilder smooth
    atr_w = sum(trs[:period])
    pdm_w = sum(plus_dms[:period])
    mdm_w = sum(minus_dms[:period])

    dx_vals = []
    for i in range(period, len(trs)):
        atr_w = atr_w - atr_w / period + trs[i]
        pdm_w = pdm_w - pdm_w / period + plus_dms[i]
        mdm_w = mdm_w - mdm_w / period + minus_dms[i]
        pdi = 100 * pdm_w / atr_w if atr_w else 0
        mdi = 100 * mdm_w / atr_w if atr_w else 0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else 0
        idx = i + 1  # offset: trs[0] = closes[1]-closes[0]
        pdi_vals[idx] = round(pdi, 2)
        mdi_vals[idx] = round(mdi, 2)
        dx_vals.append(dx)

    # ADX = EMA of DX
    if len(dx_vals) >= period:
        adx = sum(dx_vals[:period]) / period
        base_idx = period * 2
        if base_idx < n:
            adx_vals[base_idx] = round(adx, 2)
        for j in range(period, len(dx_vals)):
            adx = (adx * (period - 1) + dx_vals[j]) / period
            idx = j + period + 1
            if idx < n:
                adx_vals[idx] = round(adx, 2)

    return {"adx": adx_vals, "+di": pdi_vals, "-di": mdi_vals}


# ── Donchian Channel ──────────────────────────────────────────────────────────
def calc_donchian(highs: list[float], lows: list[float], period: int = 20) -> dict:
    n = len(highs)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    middle: list[float | None] = [None] * n
    for i in range(period - 1, n):
        u = max(highs[i - period + 1: i + 1])
        l = min(lows[i - period + 1: i + 1])
        upper[i] = u
        lower[i] = l
        middle[i] = (u + l) / 2
    return {"upper": upper, "lower": lower, "middle": middle}


# ── Bollinger Band rating (from tradingview-mcp) ──────────────────────────────
def bb_rating(close: float, bb_upper: float, bb_middle: float, bb_lower: float) -> tuple[int, str]:
    """
    Returns (rating, signal). Rating -3 to +3.
    +3 = price far above upper band (strong breakout)
    -3 = price far below lower band (strong breakdown)
    """
    half_up = bb_middle + (bb_upper - bb_middle) / 2
    half_dn = bb_middle - (bb_middle - bb_lower) / 2
    if close > bb_upper:
        return 3, "STRONG_BREAKOUT"
    elif close > half_up:
        return 2, "BUY"
    elif close > bb_middle:
        return 1, "WEAK_BUY"
    elif close < bb_lower:
        return -3, "STRONG_BREAKDOWN"
    elif close < half_dn:
        return -2, "SELL"
    elif close < bb_middle:
        return -1, "WEAK_SELL"
    return 0, "NEUTRAL"


# ── Candlestick pattern detection ────────────────────────────────────────────
def detect_patterns(candles: list[dict]) -> list[str]:
    """
    Detect high-probability candlestick patterns on the last 5 bars.
    Returns list of pattern strings for use in agent prompts.
    Focuses on F&O-relevant reversal and continuation patterns.
    """
    if len(candles) < 3:
        return []

    patterns: list[str] = []
    o = [c['open']  for c in candles]
    h = [c['high']  for c in candles]
    l = [c['low']   for c in candles]
    c = [c['close'] for c in candles]
    n = len(c)

    def body(i):   return abs(c[i] - o[i])
    def rng(i):    return max(h[i] - l[i], 0.001)
    def bull(i):   return c[i] > o[i]
    def uw(i):     return h[i] - max(o[i], c[i])
    def lw(i):     return min(o[i], c[i]) - l[i]

    i = n - 1  # current (last) bar

    # ── Single-bar patterns ───────────────────────────────────────────────────
    b, r = body(i), rng(i)
    if b / r < 0.1:
        if uw(i) > 2 * lw(i):
            patterns.append('Gravestone Doji (BEARISH REVERSAL)')
        elif lw(i) > 2 * uw(i):
            patterns.append('Dragonfly Doji (BULLISH REVERSAL)')
        else:
            patterns.append('Doji (INDECISION — watch next bar)')
    elif b / r > 0.85:
        patterns.append(f"{'Bullish' if bull(i) else 'Bearish'} Marubozu ({'STRONG BUY' if bull(i) else 'STRONG SELL'})")
    elif lw(i) > 2 * body(i) and uw(i) < body(i):
        patterns.append(f"{'Hammer (BULLISH REVERSAL)' if not bull(i) else 'Hanging Man (BEARISH WARNING)'}")
    elif uw(i) > 2 * body(i) and lw(i) < body(i):
        patterns.append(f"{'Shooting Star (BEARISH REVERSAL)' if bull(i) else 'Inverted Hammer (BULLISH POTENTIAL)'}")

    # ── Two-bar patterns ──────────────────────────────────────────────────────
    if n >= 2:
        p, pb = i - 1, body(i - 1)
        cb = body(i)
        # Bullish Engulfing
        if not bull(p) and bull(i) and cb > pb and o[i] < c[p] and c[i] > o[p]:
            patterns.append('⭐ Bullish Engulfing (STRONG BUY — high-confidence reversal)')
        # Bearish Engulfing
        if bull(p) and not bull(i) and cb > pb and o[i] > c[p] and c[i] < o[p]:
            patterns.append('⭐ Bearish Engulfing (STRONG SELL — high-confidence reversal)')
        # Bullish Harami
        if not bull(p) and bull(i) and cb < pb and o[i] > c[p] and c[i] < o[p]:
            patterns.append('Bullish Harami (POTENTIAL REVERSAL — needs confirmation)')
        # Bearish Harami
        if bull(p) and not bull(i) and cb < pb and o[i] < c[p] and c[i] > o[p]:
            patterns.append('Bearish Harami (POTENTIAL REVERSAL — needs confirmation)')
        # Tweezer bottom (equal lows, first bear then bull)
        if not bull(p) and bull(i) and abs(l[p] - l[i]) / rng(i) < 0.05:
            patterns.append('Tweezer Bottom (BULLISH REVERSAL at support)')
        # Tweezer top (equal highs, first bull then bear)
        if bull(p) and not bull(i) and abs(h[p] - h[i]) / rng(i) < 0.05:
            patterns.append('Tweezer Top (BEARISH REVERSAL at resistance)')

    # ── Three-bar patterns ────────────────────────────────────────────────────
    if n >= 3:
        i1, i2, i3 = n - 3, n - 2, n - 1
        b1, b2, b3 = body(i1), body(i2), body(i3)
        # Morning Star
        if not bull(i1) and b2 < 0.3 * b1 and bull(i3) and b3 > 0.5 * b1:
            patterns.append('⭐ Morning Star (STRONG BULLISH REVERSAL — buy CE/long FUT)')
        # Evening Star
        if bull(i1) and b2 < 0.3 * b1 and not bull(i3) and b3 > 0.5 * b1:
            patterns.append('⭐ Evening Star (STRONG BEARISH REVERSAL — buy PE/short FUT)')
        # Three White Soldiers
        if all(bull(j) for j in [i1, i2, i3]) and c[i1] < c[i2] < c[i3] and o[i2] > o[i1] and o[i3] > o[i2]:
            patterns.append('Three White Soldiers (STRONG BULLISH CONTINUATION)')
        # Three Black Crows
        if all(not bull(j) for j in [i1, i2, i3]) and c[i1] > c[i2] > c[i3] and o[i2] < o[i1] and o[i3] < o[i2]:
            patterns.append('Three Black Crows (STRONG BEARISH CONTINUATION)')
        # Inside Bar (price compression → breakout imminent)
        if h[i2] < h[i1] and l[i2] > l[i1] and h[i3] < h[i1] and l[i3] > l[i1]:
            patterns.append('Inside Bar Compression (BREAKOUT IMMINENT — watch for direction)')

    return patterns if patterns else ['No significant pattern — neutral price action']


# ── Full indicator block from OHLCV candles ───────────────────────────────────
def compute_all(candles: list[dict]) -> dict:
    """
    Takes list of {open,high,low,close,volume} dicts (oldest first).
    Returns full indicator block for the Technical agent.
    """
    if not candles or len(candles) < 20:
        return {}

    o = [c['open']   for c in candles]
    h = [c['high']   for c in candles]
    l = [c['low']    for c in candles]
    c = [c['close']  for c in candles]
    v = [c.get('volume', 0) for c in candles]

    n = len(c)
    price = c[-1]

    # EMAs
    ema9   = calc_ema(c, 9)
    ema20  = calc_ema(c, 20)
    ema50  = calc_ema(c, 50)
    ema200 = calc_ema(c, 200)

    # RSI
    rsi_vals = calc_rsi(c, 14)

    # ATR
    atr_vals = calc_atr(h, l, c, 14)

    # MACD
    macd = calc_macd(c)

    # Bollinger
    bb = calc_bollinger(c, 20, 2.0)

    # Supertrend
    st = calc_supertrend(h, l, c, 10, 3.0)

    # ADX
    adx = calc_adx(h, l, c, 14)

    # VWAP (session approximation — last 78 bars = ~1 trading day of 5min candles, or use all)
    bars = min(n, 78)
    typ = [(h[i] + l[i] + c[i]) / 3 for i in range(n - bars, n)]
    vol_w = v[n - bars:]
    vwap = sum(t * vv for t, vv in zip(typ, vol_w)) / sum(vol_w) if sum(vol_w) > 0 else price

    # 52-week high/low
    w52h = max(h[-252:]) if n >= 252 else max(h)
    w52l = min(l[-252:]) if n >= 252 else min(l)

    # Volume ratio (last bar vs 20-bar avg)
    avg_vol = sum(v[-21:-1]) / 20 if n >= 21 else (sum(v) / n if n else 1)
    vol_ratio = round(v[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

    # Supertrend current
    st_dir = next((st['direction'][i] for i in range(n - 1, -1, -1) if st['direction'][i] is not None), None)
    st_line = next((st['lower'][i] if st_dir == 1 else st['upper'][i]
                    for i in range(n - 1, -1, -1) if st['direction'][i] is not None), None)

    # ADX current
    adx_val = next((adx['adx'][i] for i in range(n - 1, -1, -1) if adx['adx'][i] is not None), None)
    pdi_val = next((adx['+di'][i] for i in range(n - 1, -1, -1) if adx['+di'][i] is not None), None)
    mdi_val = next((adx['-di'][i] for i in range(n - 1, -1, -1) if adx['-di'][i] is not None), None)

    # BB rating
    bb_r, bb_sig = bb_rating(
        price,
        bb['upper'][-1] or price,
        bb['middle'][-1] or price,
        bb['lower'][-1] or price,
    ) if bb['upper'][-1] else (0, 'NEUTRAL')

    # Golden/Death cross
    cross = 'GOLDEN' if (ema50[-1] and ema200[-1] and ema50[-1] > ema200[-1]) else \
            'DEATH'  if (ema50[-1] and ema200[-1] and ema50[-1] < ema200[-1]) else 'NONE'

    # MACD crossover
    macd_cross = 'NONE'
    if macd['macd'][-1] and macd['signal'][-1]:
        if macd['macd'][-1] > macd['signal'][-1]:
            macd_cross = 'BULLISH'
        elif macd['macd'][-1] < macd['signal'][-1]:
            macd_cross = 'BEARISH'

    # Candlestick patterns on last 5 bars
    patterns = detect_patterns(candles[-5:] if len(candles) >= 5 else candles)

    # CPR (Central Pivot Range) — Zerodha Kite / Groww formula using prev session
    if n >= 2:
        ph, pl, pc = h[-2], l[-2], c[-2]
        cpr_pp = (ph + pl + pc) / 3
        cpr_bc = (ph + pl) / 2
        cpr_tc = 2 * cpr_pp - cpr_bc
        cpr_width_pct = round(abs(cpr_tc - cpr_bc) / cpr_pp * 100, 3)
        cpr_type = 'narrow' if cpr_width_pct < 0.5 else 'wide'
    else:
        cpr_pp = cpr_bc = cpr_tc = None
        cpr_width_pct = None
        cpr_type = None

    return {
        "price":        round(price, 4),
        "rsi":          round(rsi_vals[-1], 2) if rsi_vals[-1] else None,
        "ema9":         round(ema9[-1], 4) if ema9[-1] else None,
        "ema20":        round(ema20[-1], 4) if ema20[-1] else None,
        "ema50":        round(ema50[-1], 4) if ema50[-1] else None,
        "ema200":       round(ema200[-1], 4) if ema200[-1] else None,
        "atr":          round(atr_vals[-1], 4) if atr_vals[-1] else None,
        "vwap":         round(vwap, 4),
        "above_vwap":   price > vwap,
        "vol_ratio":    vol_ratio,
        "bb_upper":     round(bb['upper'][-1], 4) if bb['upper'][-1] else None,
        "bb_middle":    round(bb['middle'][-1], 4) if bb['middle'][-1] else None,
        "bb_lower":     round(bb['lower'][-1], 4) if bb['lower'][-1] else None,
        "bb_rating":    bb_r,
        "bb_signal":    bb_sig,
        "macd":         round(macd['macd'][-1], 4) if macd['macd'][-1] else None,
        "macd_signal":  round(macd['signal'][-1], 4) if macd['signal'][-1] else None,
        "macd_hist":    round(macd['histogram'][-1], 4) if macd['histogram'][-1] else None,
        "macd_cross":   macd_cross,
        "supertrend_dir":  st_dir,   # 1=bullish, -1=bearish
        "supertrend_line": round(st_line, 4) if st_line else None,
        "supertrend_signal": "BUY" if st_dir == 1 else "SELL" if st_dir == -1 else "NEUTRAL",
        "adx":          adx_val,
        "adx_plus_di":  pdi_val,
        "adx_minus_di": mdi_val,
        "adx_trend_strength": "STRONG" if adx_val and adx_val > 25 else "WEAK" if adx_val and adx_val < 20 else "MODERATE",
        "cross":        cross,
        "w52_high":     round(w52h, 4),
        "w52_low":      round(w52l, 4),
        "pct_from_52h": round((price - w52h) / w52h * 100, 2),
        "pct_from_52l": round((price - w52l) / w52l * 100, 2),
        "candle_patterns": patterns,  # list of detected pattern strings
        "cpr_pp":          round(cpr_pp, 4) if cpr_pp else None,
        "cpr_bc":          round(cpr_bc, 4) if cpr_bc else None,
        "cpr_tc":          round(cpr_tc, 4) if cpr_tc else None,
        "cpr_width_pct":   cpr_width_pct,
        "cpr_type":        cpr_type,
    }
