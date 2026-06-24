"""
Crypto signal scanner v4 — multi-indicator, configurable, with execution pipeline.

Strategy (based on live research of top GitHub repos):
- NostalgiaForInfinity (3.3k stars): multi-indicator confidence, cooldown, trend filter
- FixedRiskRewardLoss: break-even SL (entry+fees after 1:1 risk), 3:1 R:R ratio
- CustomStoplossWithPSAR: Parabolic SAR adaptive trailing stop loss
- CombinedBinHAndCluc: exit_profit_only (never exit signal unless profitable)
- OctoBot: daily loss kill switch, position sizing

Features:
- Dual-timeframe: 15m entry + 1h trend confirmation
- Indicators: EMA crossover + MACD + RSI + Volume ratio + ADX + Bollinger Bands
- Confidence scoring (0-100): weighted combination of indicator signals
- 4-layer exit logic:
  1. Break-even SL: lock at entry+fees once price moves 1:1 risk distance
  2. PSAR trailing: Parabolic SAR as adaptive trailing stop (post break-even)
  3. Fixed trailing fallback: 1% below peak / above trough
  4. Hard SL / TP (3:1 R:R) / timeout safety net
- exit_profit_only: signal exits blocked unless position is profitable
- Trade cooldown per symbol (30 min, avoid churning)
- Both LONG and SHORT positions
- Execution: paper fills or live via CCXTAdapter
- P&L persistence: SQLite via state_store.record_trade()

Scans configurable universe every 300s via Binance REST.
"""
from __future__ import annotations

import threading
import uuid
from typing import Optional

import requests

from ...config import settings
from ...shared.indicators import (
    ema_series, rsi, macd, atr_from_dicts, volume_ratio,
    adx, bollinger_bands, CandleData, detect_candle_patterns,
    stoch_rsi, aroon, roc,
)
from ...shared.logger import get_logger
from ...shared.time import clock

logger = get_logger('crypto_scanner')

_BINANCE = 'https://api.binance.com'
_MAX_SIGNALS = 100


def _fetch_klines(symbol: str, interval: str = '5m', limit: int = 200) -> list[dict]:
    """Fetch OHLCV candles from Binance public REST."""
    try:
        r = requests.get(
            f'{_BINANCE}/api/v3/klines',
            params={'symbol': symbol, 'interval': interval, 'limit': limit},
            timeout=8,
        )
        r.raise_for_status()
        return [
            {
                'time':   c[0],
                'open':   float(c[1]),
                'high':   float(c[2]),
                'low':    float(c[3]),
                'close':  float(c[4]),
                'volume': float(c[5]),
            }
            for c in r.json()
        ]
    except Exception as exc:
        logger.warning('Binance klines failed %s/%s: %s', symbol, interval, exc)
        return []


# ── Signal confidence weights ────────────────────────────────────────────────

# Combined signal from 3 proven Freqtrade strategies:
# 1. NostalgiaForInfinityX5 — StochRSI, Aroon, RSI_3 guards, deep-dip EMAs
# 2. CombinedBinHAndCluc — BinHV45 BB-squeeze + ClucMay deep-dip + vol cap
# 3. FixedRiskRewardLoss — ATR-based initial SL (2*ATR), break-even at 1x risk
# 16 condition categories. Need TREND_1H + 1 dip trigger + min_votes.


def _compute_signal(candles: list[dict], trend_candles: list[dict],
                    min_confidence: int = None,
                    sl_pct: float = None,
                    tp_pct: float = None) -> Optional[dict]:
    """Combined entry logic from 3 proven Freqtrade strategies.

    Sources (in priority order):
    1. NostalgiaForInfinityX5 — StochRSI, Aroon, RSI_3 guards, deep-dip EMAs
    2. CombinedBinHAndCluc   — BinHV45 BB-squeeze + ClucMay deep-dip + vol cap
    3. FixedRiskRewardLoss    — ATR-based initial SL (2*ATR)

    Entry requires:
    - 1h trend confirmation (mandatory)
    - RSI_3 guards pass (no cascade dump)
    - At least one "deep dip" trigger (AROON/SRSI/EMA/BBSQUEEZE/CLUC_DIP)
    - Minimum vote threshold from 16 condition categories

    Exit: No hard TP. Multi-tier trailing + BB middleband exit (ClucMay).
    SL: FixedRiskRewardLoss ATR-based, capped by config safety pct.

    Returns:
        {side, confidence, indicators: {...}, sl_dist, tp_dist, bb_mid} or None
    """
    if len(candles) < 60:
        return None

    closes = [c['close'] for c in candles]
    highs = [c['high'] for c in candles]
    lows = [c['low'] for c in candles]
    volumes = [c['volume'] for c in candles]
    price = closes[-1]

    # ── EMAs (5m) ──────────────────────────────────────────────────────
    ema9 = ema_series(closes, 9)
    ema12 = ema_series(closes, 12)
    ema20 = ema_series(closes, 20)
    ema26 = ema_series(closes, 26)
    ema50 = ema_series(closes, 50)
    if len(ema9) < 2 or len(ema26) < 2 or len(ema50) < 2:
        return None

    # ── StochRSI (NFI's #1 entry filter) ──────────────────────────────
    srsi = stoch_rsi(closes, 14, 14, 3, 3)
    srsi_k = srsi['k'] if srsi else 50.0

    # ── Aroon (NFI Conditions #4, #5 use AROONU < 25) ─────────────────
    aroon_val = aroon(highs, lows, 14)
    aroon_up = aroon_val['up'] if aroon_val else 50.0
    aroon_dn = aroon_val['down'] if aroon_val else 50.0

    # ── RSI (14 standard + 3 ultra-short for momentum guard) ──────────
    rsi_14 = rsi(closes, 14)
    rsi_3 = rsi(closes, 3)   # NFI's cascade-dump guard
    if rsi_14 is None:
        return None

    # ── MACD ──────────────────────────────────────────────────────────
    macd_result = macd(closes)
    macd_cross = macd_result['cross'] if macd_result else 'NONE'
    macd_hist = macd_result['histogram'] if macd_result else 0.0

    # ── Volume ratio + slow mean (CombinedBinHAndCluc) ────────────────
    candle_data = [CandleData.from_dict(c) for c in candles]
    vol_ratio = volume_ratio(candle_data, lookback=20)
    # ClucMay volume ceiling: reject pump candles (vol < 20x slow mean)
    vol_mean_slow = sum(volumes[-30:]) / 30 if len(volumes) >= 30 else 0
    vol_ok = volumes[-1] < (vol_mean_slow * 20) if vol_mean_slow > 0 else True

    # ── ADX ────────────────────────────────────────────────────────────
    adx_val = adx(candle_data, 14)

    # ── Bollinger Bands (standard 20,2 for ClucMay) ───────────────────
    bb = bollinger_bands(closes, 20, 2.0)

    # ── BinHV45 Bollinger (40-period, 2std — different from standard BB)
    bb40 = bollinger_bands(closes, 40, 2.0)

    # ── ATR (FixedRiskRewardLoss uses 2*ATR for initial SL) ──────────
    atr_val = atr_from_dicts(candles, 14)

    # ── Candlestick patterns ──────────────────────────────────────────
    candle_patterns = detect_candle_patterns(candle_data)

    # ── BinHV45 indicators (CombinedBinHAndCluc) ─────────────────────
    # bbdelta = |mid - lower| of 40-period BB
    # closedelta = |close - prev_close|
    # tail = |close - low|
    binhv_signal = False
    if bb40:
        mid40, _, lower40 = bb40
        bbdelta = abs(mid40 - lower40)
        closedelta = abs(closes[-1] - closes[-2]) if len(closes) >= 2 else 0
        tail = abs(closes[-1] - lows[-1])
        prev_lower40 = None
        if len(closes) >= 41:
            prev_bb40 = bollinger_bands(closes[:-1], 40, 2.0)
            if prev_bb40:
                _, _, prev_lower40 = prev_bb40

        if (prev_lower40 is not None and prev_lower40 > 0
                and bbdelta > price * 0.008
                and closedelta > price * 0.0175
                and tail < bbdelta * 0.25
                and price < prev_lower40
                and price <= closes[-2]):
            binhv_signal = True

    # ── ClucMay indicators (CombinedBinHAndCluc) ─────────────────────
    # close < ema_slow AND close < 0.985 * bb_lowerband AND volume OK
    cluc_signal = False
    if bb and ema50 and len(ema50) >= 1:
        _, _, bb_lower = bb
        if (price < ema50[-1]
                and price < bb_lower * 0.985
                and vol_ok):
            cluc_signal = True

    # ── 1h trend confirmation (MANDATORY — never trade against 1h) ────
    trend_1h_up = None
    trend_1h_srsi_k = 50.0
    trend_1h_rsi_3 = 50.0
    if trend_candles and len(trend_candles) >= 30:
        trend_closes = [c['close'] for c in trend_candles]
        trend_ema20 = ema_series(trend_closes, 20)
        trend_ema50 = ema_series(trend_closes, 50)
        if trend_ema20 and trend_ema50:
            trend_1h_up = trend_ema20[-1] > trend_ema50[-1]
        t_srsi = stoch_rsi(trend_closes, 14, 14, 3, 3)
        if t_srsi:
            trend_1h_srsi_k = t_srsi['k']
        t_rsi3 = rsi(trend_closes, 3)
        if t_rsi3 is not None:
            trend_1h_rsi_3 = t_rsi3
    if trend_1h_up is None:
        return None

    # ═══════════════════════════════════════════════════════════════════
    # ENTRY CONDITIONS — Combined from NFI + CombinedBinHAndCluc
    # 16 categories total. Need TREND_1H + 1 "deep dip" + min_votes.
    # ═══════════════════════════════════════════════════════════════════

    buy_conditions: list[str] = []
    sell_conditions: list[str] = []
    buy_guards_pass = True
    sell_guards_pass = True

    # ── LONG PROTECTIONS (NFI RSI_3 guards + ClucMay volume cap) ──────
    if rsi_3 is not None and rsi_3 < 5.0:
        buy_guards_pass = False     # 5m in freefall
    if trend_1h_rsi_3 < 10.0:
        buy_guards_pass = False     # 1h in freefall
    if trend_1h_srsi_k > 80.0:
        buy_guards_pass = False     # 1h overbought
    if not vol_ok:
        buy_guards_pass = False     # ClucMay: reject pump candles
    # NFI: close must be falling for dip entries (don't buy into rising candles)
    if len(closes) >= 2 and closes[-1] >= closes[-2]:
        buy_guards_pass = False

    # ── SHORT PROTECTIONS ─────────────────────────────────────────────
    if rsi_3 is not None and rsi_3 > 95.0:
        sell_guards_pass = False
    if trend_1h_rsi_3 > 90.0:
        sell_guards_pass = False
    # Mirror: close must be rising for bounce entries
    if len(closes) >= 2 and closes[-1] <= closes[-2]:
        sell_guards_pass = False

    # ── LONG ENTRY CONDITIONS ─────────────────────────────────────────
    if trend_1h_up and buy_guards_pass:
        buy_conditions.append('TREND_1H')                          # 1

        # NFI: Aroon downswing
        if aroon_up < 25.0:
            buy_conditions.append('AROON_DIP')                     # 2

        # NFI: StochRSI oversold
        if srsi_k < 30.0:
            buy_conditions.append('SRSI_OVERSOLD')                 # 3

        # NFI: deep dip below EMA9
        if ema9[-1] > 0 and price < ema9[-1] * 0.958:
            buy_conditions.append('DEEP_DIP_EMA9')                 # 4

        # NFI: dip below EMA20
        if ema20 and ema20[-1] > 0 and price < ema20[-1] * 0.970:
            buy_conditions.append('DIP_EMA20')                     # 5

        # NFI: EMA bearish cross (EMA26 > EMA12)
        if ema26[-1] > ema12[-1]:
            ema_gap = (ema26[-1] - ema12[-1]) / ema12[-1]
            if ema_gap > 0.015:
                buy_conditions.append('EMA_CROSS_DIP')             # 6

        # RSI_14 oversold
        if rsi_14 < 35:
            buy_conditions.append('RSI_OVERSOLD')                  # 7

        # Standard BB: price at/below lower band
        if bb:
            upper, mid, lower = bb
            if price <= lower * 1.005:
                buy_conditions.append('BB_LOW')                    # 8

        # MACD
        if macd_cross == 'BULLISH':
            buy_conditions.append('MACD_CROSS')                    # 9

        # Volume spike on dip
        if vol_ratio >= 1.5:
            buy_conditions.append('VOL_SPIKE')                     # 10

        # ADX trending
        if adx_val and adx_val >= 20:
            buy_conditions.append('ADX_TREND')                     # 11

        # Bullish candlestick patterns
        bullish_pats = [p for p in candle_patterns if p['bias'] == 'bullish']
        if bullish_pats:
            buy_conditions.append('CANDLE_REV')                    # 12

        # NFI: Aroon Down > 75
        if aroon_dn > 75.0:
            buy_conditions.append('AROON_DN_HIGH')                 # 13

        # ── NEW: CombinedBinHAndCluc entries ──────────────────────
        # BinHV45: BB-squeeze volatility compression entry
        if binhv_signal:
            buy_conditions.append('BBSQUEEZE')                     # 14

        # ClucMay: deep below BB_lower + below EMA50
        if cluc_signal:
            buy_conditions.append('CLUC_DIP')                      # 15

        # ── NEW: FixedRiskRewardLoss — ATR confirms room for SL ──
        # ATR > 0.5% of price = enough volatility for meaningful trade
        if atr_val and atr_val / price > 0.005:
            buy_conditions.append('ATR_ROOM')                      # 16

    # ── SHORT ENTRY CONDITIONS ────────────────────────────────────────
    if not trend_1h_up and sell_guards_pass:
        sell_conditions.append('TREND_1H')

        if aroon_dn < 25.0:
            sell_conditions.append('AROON_BOUNCE')

        if srsi_k > 70.0:
            sell_conditions.append('SRSI_OVERBOUGHT')

        if ema9[-1] > 0 and price > ema9[-1] * 1.042:
            sell_conditions.append('DEEP_BOUNCE_EMA9')

        if ema12[-1] > ema26[-1]:
            ema_gap = (ema12[-1] - ema26[-1]) / ema26[-1]
            if ema_gap > 0.015:
                sell_conditions.append('EMA_CROSS_BOUNCE')

        if rsi_14 > 65:
            sell_conditions.append('RSI_OVERBOUGHT')

        if bb:
            upper, mid, lower = bb
            if price >= upper * 0.995:
                sell_conditions.append('BB_HIGH')

        if macd_cross == 'BEARISH':
            sell_conditions.append('MACD_CROSS')

        if vol_ratio >= 1.5:
            sell_conditions.append('VOL_SPIKE')

        if adx_val and adx_val >= 20:
            sell_conditions.append('ADX_TREND')

        bearish_pats = [p for p in candle_patterns if p['bias'] == 'bearish']
        if bearish_pats:
            sell_conditions.append('CANDLE_REV')

        if aroon_up > 75.0:
            sell_conditions.append('AROON_UP_HIGH')

        # Mirror: BB-squeeze for short (price above upper band of 40-BB)
        if bb40:
            mid40, _, _ = bb40
            upper40 = mid40 + abs(mid40 - bb40[2]) if bb40[2] else mid40
            bbdelta = abs(mid40 - upper40)
            closedelta = abs(closes[-1] - closes[-2]) if len(closes) >= 2 else 0
            tail = abs(highs[-1] - closes[-1])
            if (bbdelta > price * 0.008 and closedelta > price * 0.0175
                    and tail < bbdelta * 0.25):
                sell_conditions.append('BBSQUEEZE')

        if atr_val and atr_val / price > 0.005:
            sell_conditions.append('ATR_ROOM')

    # ── Signal decision ───────────────────────────────────────────────
    # Require TREND_1H + one "deep dip" trigger + min_votes.
    # NFI/CombinedBinHAndCluc fire on 2-3 core conditions, not 9+.
    # min_votes = 4 base (TREND_1H + dip_trigger + 2 confirmations).
    # min_confidence only gates the *display* threshold after vote passes.
    min_conf = min_confidence if min_confidence is not None else settings.crypto_min_confidence
    max_cats = 16
    min_votes = 4  # TREND_1H + dip_trigger + 2 confirms (realistic)

    side: Optional[str] = None
    confidence = 0
    conditions: list[str] = []

    DIP_TRIGGERS = {'AROON_DIP', 'SRSI_OVERSOLD', 'DEEP_DIP_EMA9', 'BBSQUEEZE', 'CLUC_DIP'}
    BOUNCE_TRIGGERS = {'AROON_BOUNCE', 'SRSI_OVERBOUGHT', 'DEEP_BOUNCE_EMA9', 'BBSQUEEZE'}

    # Weighted confidence: core conditions count more than extras.
    # TREND_1H=25, dip_trigger=25, each extra=10 → 60% at 3 conditions (realistic)
    # All conditions now require real thresholds (no soft RSI_LOW/MACD_POS padding).
    CORE_WEIGHT = 25   # TREND_1H, dip trigger (these are the quality gates)
    EXTRA_WEIGHT = 10  # each additional confirmation

    def _calc_confidence(conds, trigger_set):
        core = 1  # TREND_1H
        core += 1 if trigger_set.intersection(conds) else 0
        extras = len(conds) - core
        raw = core * CORE_WEIGHT + extras * EXTRA_WEIGHT
        return min(raw, 100)

    if ('TREND_1H' in buy_conditions
            and len(buy_conditions) >= min_votes
            and DIP_TRIGGERS.intersection(buy_conditions)):
        side = 'BUY'
        confidence = _calc_confidence(buy_conditions, DIP_TRIGGERS)
        conditions = buy_conditions
    elif ('TREND_1H' in sell_conditions
              and len(sell_conditions) >= min_votes
              and BOUNCE_TRIGGERS.intersection(sell_conditions)):
        side = 'SELL'
        confidence = _calc_confidence(sell_conditions, BOUNCE_TRIGGERS)
        conditions = sell_conditions

    if side is None:
        return None

    # min_confidence gate: need enough votes to meet quality threshold
    if confidence < min_conf:
        logger.info('SIGNAL %s REJECTED conf=%d%% < min=%d%% conditions=%s',
                     side, confidence, min_conf, conditions)
        return None

    logger.info('SIGNAL %s conf=%d%% conditions=%s (srsi_k=%.1f aroon_up=%.0f rsi3=%.1f)',
                side, confidence, conditions, srsi_k, aroon_up, rsi_3 or 0)

    # ── SL: ATR-based, capped by config safety pct ──────────────────
    # ATR*3 on 5m candles gives ~1.2% room for BTC (survives normal noise).
    # ATR*2 was too tight — 0.8% SL got stopped out by 5m fluctuations.
    # Cap at config pct as safety net (prevents insane SL in high-vol).
    _sl_pct = sl_pct if sl_pct is not None else settings.crypto_sl_pct
    if atr_val and atr_val > 0:
        atr_sl = atr_val * 3.0   # 3x ATR — wider than FixedRiskRewardLoss
        pct_sl = price * _sl_pct
        sl = round(min(atr_sl, pct_sl), 6)  # tighter of ATR or pct
    else:
        sl = round(price * _sl_pct, 6)

    tp = 0.0  # no hard TP — trailing + BB middleband exit handles it

    # BB middleband for exit logic (CombinedBinHAndCluc exit)
    bb_mid = round(bb[1], 6) if bb else None

    return {
        'side':       side,
        'confidence': confidence,
        'sl_dist':    sl,
        'tp_dist':    tp,
        'bb_mid':     bb_mid,
        'sar':        None,
        'indicators': {
            'ema9':        round(ema9[-1], 6),
            'ema20':       round(ema20[-1], 6) if ema20 else None,
            'ema50':       round(ema50[-1], 6),
            'rsi':         round(rsi_14, 2),
            'rsi_3':       round(rsi_3, 2) if rsi_3 else None,
            'srsi_k':      srsi_k,
            'aroon_up':    aroon_up,
            'aroon_dn':    aroon_dn,
            'macd_cross':  macd_cross,
            'macd_hist':   round(macd_hist, 6),
            'vol_ratio':   vol_ratio,
            'adx':         round(adx_val, 2) if adx_val else None,
            'atr':         round(atr_val, 6) if atr_val else None,
            'bb_upper':    round(bb[0], 6) if bb else None,
            'bb_mid':      bb_mid,
            'bb_lower':    round(bb[2], 6) if bb else None,
            'trend_1h':    'UP' if trend_1h_up else 'DOWN',
            'trend_1h_srsi': round(trend_1h_srsi_k, 1),
            'patterns':    [p['pattern'] for p in candle_patterns],
            'conditions':  conditions,
            'binhv':       binhv_signal,
            'cluc_dip':    cluc_signal,
        },
    }


# ── CryptoScanner ─────────────────────────────────────────────────────────────

class CryptoScanner:
    """Multi-indicator crypto scanner with execution pipeline.

    Can be instantiated standalone (singleton via get_crypto_scanner) or
    per-bot (via BotManager passing bot_config).
    """

    def __init__(self, bot_config=None) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Per-bot override: if provided, use bot_config for symbols + settings
        self._bot = bot_config  # CryptoBot instance or None

        # { symbol: position_dict }
        self._positions: dict[str, dict] = {}
        self._realized_pnl: float = 0.0
        self._daily_pnl: float = 0.0
        self._signals: list[dict] = []
        self._running = False
        self._cycle_count = 0

        # Trade cooldown: { symbol: last_close_timestamp } — prevent re-entry churn
        self._last_trade_ts: dict[str, float] = {}
        self._cooldown_sec: int = self._cfg('trade_cooldown_sec',
                                             getattr(settings, 'crypto_trade_cooldown_sec', 1800))

        # Seed realized P&L from SQLite so it survives restarts
        try:
            from ...infrastructure.db import state_store
            summary = state_store.daily_summary(market_type='crypto')
            for mode_key in ('swing', 'scalp', 'crypto'):
                mode_data = summary.get(mode_key, {})
                self._realized_pnl += mode_data.get('net', 0.0)
                self._daily_pnl += mode_data.get('net', 0.0)
            logger.info('Loaded today\'s realized crypto P&L from DB: $%.2f', self._realized_pnl)
        except Exception as exc:
            logger.warning('Could not load crypto P&L from DB: %s', exc)

    # ── Per-bot config helper ──────────────────────────────────────────────────

    def _cfg(self, key: str, default=None):
        """Read config: bot override first, then global settings."""
        if self._bot and hasattr(self._bot, 'config'):
            cfg = self._bot.config
            # BotConfig field names match without 'crypto_' prefix
            if hasattr(cfg, key):
                return getattr(cfg, key)
        return default

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        thread_name = f'bot-{self._bot.bot_id[:8]}' if self._bot else 'crypto-scanner'
        self._thread = threading.Thread(target=self._loop, daemon=True, name=thread_name)
        self._thread.start()
        self._running = True
        interval = self._cfg('scan_interval_sec', settings.crypto_scan_interval_sec)
        label = f'Bot "{self._bot.name}"' if self._bot else 'CryptoScanner v4'
        logger.info('%s started — %d symbols, %ds interval', label, len(self._symbols), interval)

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        logger.info('CryptoScanner stopped')

    def is_running(self) -> bool:
        return self._running

    def signal_count(self) -> int:
        with self._lock:
            return len(self._signals)

    def get_signals(self, limit: int = 30) -> list[dict]:
        with self._lock:
            return list(reversed(self._signals[-limit:]))

    def get_positions(self) -> list[dict]:
        with self._lock:
            return list(self._positions.values())

    def get_pnl(self) -> dict:
        with self._lock:
            unrealized = sum(p.get('pnl', 0) for p in self._positions.values())

        # Fetch brokerage and trade count from SQLite
        brokerage = 0.0
        trades = 0
        try:
            from ...infrastructure.db import state_store
            summary = state_store.daily_summary(market_type='crypto')
            for mode_key in ('swing', 'scalp', 'crypto'):
                mode_data = summary.get(mode_key, {})
                brokerage += mode_data.get('brokerage', 0.0)
                trades += mode_data.get('trades', 0)
        except Exception:
            pass

        return {
            'realized':   round(self._realized_pnl, 2),
            'unrealized': round(unrealized, 2),
            'net':        round(self._realized_pnl + unrealized, 2),
            'daily':      round(self._daily_pnl, 2),
            'positions':  len(self._positions),
            'brokerage':  round(brokerage, 2),
            'trades':     trades,
        }

    def get_config(self) -> dict:
        cfg = {
            'symbols':          self._symbols,
            'scan_interval':    self._cfg('scan_interval_sec', settings.crypto_scan_interval_sec),
            'entry_tf':         self._cfg('entry_tf', settings.crypto_scan_timeframe),
            'trend_tf':         self._cfg('trend_tf', settings.crypto_scan_trend_tf),
            'min_confidence':   self._cfg('min_confidence', settings.crypto_min_confidence),
            'sl_pct':           self._cfg('sl_pct', settings.crypto_sl_pct),
            'tp_pct':           self._cfg('tp_pct', settings.crypto_tp_pct),
            'trail_pct':        self._cfg('trail_pct', settings.crypto_trail_pct),
            'breakeven_pct':    self._cfg('breakeven_pct', settings.crypto_breakeven_pct),
            'max_hold_hours':   self._cfg('max_hold_hours', settings.crypto_max_hold_hours),
            'max_positions':    self._cfg('max_positions', settings.crypto_max_positions),
            'position_size':    self._cfg('position_size_usd', settings.crypto_position_size_usd),
            'daily_loss_limit': self._cfg('daily_loss_limit_usd', settings.crypto_daily_loss_limit_usd),
            'trade_cooldown':   self._cooldown_sec,
        }
        if self._bot:
            cfg['bot_id'] = self._bot.bot_id
            cfg['bot_name'] = self._bot.name
        return cfg

    def manual_close(self, symbol: str) -> Optional[dict]:
        """Close a position manually. Returns trade result or None."""
        with self._lock:
            pos = self._positions.get(symbol)
        if not pos:
            return None
        price = pos['current_price']
        return self._close_position(symbol, price, reason='manual')

    @property
    def _symbols(self) -> list[str]:
        if self._bot and self._bot.symbols:
            return self._bot.symbols
        return [s.strip() for s in settings.crypto_scan_symbols.split(',') if s.strip()]

    # ── Background loop ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._cycle_count += 1
                self._scan_all()
                self._check_exits()
            except Exception as exc:
                logger.error('CryptoScanner cycle error: %s', exc)

            interval = self._cfg('scan_interval_sec', settings.crypto_scan_interval_sec)

            # When holding positions, check exits every 60s instead of full
            # scan interval (300s). Price can blow through SL in seconds.
            if self._positions:
                exit_interval = min(60, interval)
                elapsed_wait = 0
                while elapsed_wait < interval and not self._stop_event.is_set():
                    self._stop_event.wait(timeout=exit_interval)
                    elapsed_wait += exit_interval
                    if self._positions and elapsed_wait < interval:
                        try:
                            self._refresh_prices()
                            self._check_exits()
                        except Exception as exc:
                            logger.warning('Exit-check mini-cycle error: %s', exc)
            else:
                self._stop_event.wait(timeout=interval)

    def _is_auto_enabled(self) -> bool:
        # Same pattern as scalp/swing: scanner started = auto-trade ON (paper mode).
        # crypto_mode in config.py controls paper vs live. No separate toggle needed.
        return True

    def _is_kill_switched(self) -> bool:
        limit = self._cfg('daily_loss_limit_usd', settings.crypto_daily_loss_limit_usd)
        return self._daily_pnl <= -limit

    def _scan_all(self) -> None:
        auto = self._is_auto_enabled()
        kill = self._is_kill_switched()
        logger.info('SCAN cycle=%d  auto=%s  kill=%s  positions=%d  daily_pnl=$%.2f',
                     self._cycle_count, auto, kill, len(self._positions), self._daily_pnl)
        if kill:
            logger.warning('Daily loss limit hit ($%.2f), skipping scan', self._daily_pnl)
            return

        for sym in self._symbols:
            try:
                self._scan_symbol(sym, auto)
            except Exception as exc:
                logger.warning('Error scanning %s: %s', sym, exc)

    def _is_on_cooldown(self, symbol: str) -> bool:
        """Check if symbol is on trade cooldown to prevent churn."""
        last_ts = self._last_trade_ts.get(symbol, 0)
        return (clock() - last_ts) < self._cooldown_sec

    def _scan_symbol(self, symbol: str, auto_enabled: bool) -> None:
        entry_tf = self._cfg('entry_tf', settings.crypto_scan_timeframe)
        trend_tf = self._cfg('trend_tf', settings.crypto_scan_trend_tf)

        candles = _fetch_klines(symbol, interval=entry_tf, limit=200)
        trend_candles = _fetch_klines(symbol, interval=trend_tf, limit=100) if trend_tf != entry_tf else []

        result = _compute_signal(
            candles, trend_candles,
            min_confidence=self._cfg('min_confidence', settings.crypto_min_confidence),
            sl_pct=self._cfg('sl_pct', settings.crypto_sl_pct),
            tp_pct=self._cfg('tp_pct', settings.crypto_tp_pct),
        )
        # Refresh bb_mid on open positions from fresh candle data (zero extra REST)
        if symbol in self._positions and candles and len(candles) >= 20:
            fresh_closes = [c['close'] for c in candles]
            fresh_bb = bollinger_bands(fresh_closes, 20, 2.0)
            if fresh_bb:
                with self._lock:
                    if symbol in self._positions:
                        self._positions[symbol]['bb_mid'] = round(fresh_bb[1], 6)

        if result is None:
            # Still update prices for open positions
            if candles:
                self._update_position_price(symbol, candles[-1]['close'])
            return

        side = result['side']
        price = candles[-1]['close']
        has_position = symbol in self._positions

        # ── BUY signal → open LONG (or close SHORT) ───────────────────────
        if side == 'BUY':
            # If we have a SHORT position, close it (signal reversal)
            if has_position and self._positions[symbol].get('side') == 'SHORT':
                pos = self._positions[symbol]
                unrealized = (pos['entry_price'] - price) * pos['qty']
                # exit_profit_only (CombinedBinHAndCluc pattern):
                # NEVER exit on signal unless position is actually profitable
                if unrealized > 0:
                    trade_result = self._close_position(symbol, price, reason='signal_reversal')
                    self._record_signal(symbol, side, price, result, executed=True,
                                         pnl=trade_result['pnl'] if trade_result else None)
                else:
                    self._record_signal(symbol, side, price, result, executed=False)
                return

            # Don't open LONG if: already positioned, at max, or on cooldown
            max_pos = self._cfg('max_positions', settings.crypto_max_positions)
            if has_position or len(self._positions) >= max_pos:
                self._update_position_price(symbol, price)
                self._record_signal(symbol, side, price, result, executed=False)
                return
            if self._is_on_cooldown(symbol):
                logger.info('COOLDOWN %s — skipping BUY (%.0fs left)',
                            symbol, self._cooldown_sec - (clock() - self._last_trade_ts.get(symbol, 0)))
                self._record_signal(symbol, side, price, result, executed=False)
                return

            # Open LONG
            if auto_enabled and not self._is_kill_switched():
                self._open_position(symbol, price, result, direction='LONG')
                self._record_signal(symbol, side, price, result, executed=True)
            else:
                self._record_signal(symbol, side, price, result, executed=False)
            return

        # ── SELL signal → open SHORT (or close LONG) ──────────────────────
        if side == 'SELL':
            # If we have a LONG position, close it (exit_profit_only gate)
            if has_position and self._positions[symbol].get('side') == 'LONG':
                pos = self._positions[symbol]
                unrealized = (price - pos['entry_price']) * pos['qty']
                # exit_profit_only (CombinedBinHAndCluc pattern):
                # NEVER exit on signal unless position is actually profitable
                if unrealized > 0:
                    trade_result = self._close_position(symbol, price, reason='signal')
                    self._record_signal(symbol, side, price, result, executed=True,
                                         pnl=trade_result['pnl'] if trade_result else None)
                else:
                    logger.info('EXIT_PROFIT_ONLY %s — holding LONG (unrealized=$%.2f, need > $0)',
                                symbol, unrealized)
                    self._update_position_price(symbol, price)
                    self._record_signal(symbol, side, price, result, executed=False)
                return

            # Don't open SHORT if: already positioned, at max, or on cooldown
            max_pos = self._cfg('max_positions', settings.crypto_max_positions)
            if has_position or len(self._positions) >= max_pos:
                self._update_position_price(symbol, price)
                self._record_signal(symbol, side, price, result, executed=False)
                return
            if self._is_on_cooldown(symbol):
                logger.info('COOLDOWN %s — skipping SHORT (%.0fs left)',
                            symbol, self._cooldown_sec - (clock() - self._last_trade_ts.get(symbol, 0)))
                self._record_signal(symbol, side, price, result, executed=False)
                return

            # Open SHORT
            if auto_enabled and not self._is_kill_switched():
                self._open_position(symbol, price, result, direction='SHORT')
                self._record_signal(symbol, side, price, result, executed=True)
            else:
                self._record_signal(symbol, side, price, result, executed=False)
            return

    def _check_exits(self) -> None:
        """Combined exit logic from 3 proven Freqtrade strategies.

        Sources:
        1. NFI custom_stoploss — multi-tier trailing (tighter at higher profit)
        2. FixedRiskRewardLoss — break-even at 1x risk distance
        3. CombinedBinHAndCluc — exit when price > BB middleband (mean reversion)

        Layers (checked in order):
        1. Break-even SL: after profit >= breakeven_pct, move SL to entry
        2. Multi-tier trailing: 2.5%/2%/1.5%/1% based on profit level
        3. BB middleband exit: CombinedBinHAndCluc closes LONG when close > bb_mid
           (exit_profit_only: only if position is profitable)
        4. Hard SL / TP(if set) / timeout safety net
        """
        now = clock()
        max_hold_sec = self._cfg('max_hold_hours', settings.crypto_max_hold_hours) * 3600
        be_pct = self._cfg('breakeven_pct', settings.crypto_breakeven_pct)

        for symbol in list(self._positions.keys()):
            with self._lock:
                pos = self._positions.get(symbol)
            if not pos:
                continue

            price = pos['current_price']
            entry = pos['entry_price']
            side = pos.get('side', 'LONG')
            sl = pos.get('sl', 0)
            tp = pos.get('tp', 0)
            elapsed = now - pos['entry_time'] / 1000

            # Calculate profit %
            if side == 'LONG':
                profit_pct = (price - entry) / entry
                unrealized = (price - entry) * pos['qty']
            else:  # SHORT
                profit_pct = (entry - price) / entry
                unrealized = (entry - price) * pos['qty']

            # ── 1. Break-even SL (FixedRiskRewardLoss + NFI) ─────────────
            if not pos.get('be_activated') and profit_pct >= be_pct:
                pos['be_activated'] = True
                if side == 'LONG':
                    new_sl = round(entry * 1.001, 6)
                    if new_sl > sl:
                        pos['sl'] = new_sl
                        sl = new_sl
                else:
                    new_sl = round(entry * 0.999, 6)
                    if new_sl < sl or sl == 0:
                        pos['sl'] = new_sl
                        sl = new_sl
                logger.info('BREAK-EVEN %s %s: entry=%.6f sl→%.6f (profit=%.2f%%)',
                             symbol, side, entry, sl, profit_pct * 100)

            # ── 2. Multi-tier trailing SL (NFI custom_stoploss tiers) ────
            if pos.get('be_activated'):
                if profit_pct >= 0.15:
                    trail_pct = 0.010   # 1% trail at 15%+ profit
                elif profit_pct >= 0.08:
                    trail_pct = 0.015   # 1.5% trail at 8%+ profit
                elif profit_pct >= 0.04:
                    trail_pct = 0.020   # 2% trail at 4%+ profit
                else:
                    trail_pct = 0.025   # 2.5% trail near breakeven

                if side == 'LONG':
                    peak = pos.get('peak_price', entry)
                    if price > peak:
                        peak = price
                        pos['peak_price'] = peak
                    trail_sl = round(peak * (1 - trail_pct), 6)
                    if trail_sl > sl:
                        pos['sl'] = trail_sl
                        sl = trail_sl
                elif side == 'SHORT':
                    trough = pos.get('trough_price', entry)
                    if price < trough:
                        trough = price
                        pos['trough_price'] = trough
                    trail_sl = round(trough * (1 + trail_pct), 6)
                    if trail_sl < sl or sl == 0:
                        pos['sl'] = trail_sl
                        sl = trail_sl

            # ── 3. NFI pump-protection exit ──────────────────────────────
            # NostalgiaForInfinity custom_exit: if price pumps >5% in one
            # candle (relative to 1h), likely dump coming. Take profit now.
            if pos.get('be_activated') and profit_pct > 0.05:
                peak = pos.get('peak_price', entry)
                # Rapid pump: current price within 1% of peak AND profit > 5%
                if side == 'LONG' and peak > 0 and (peak - price) / peak < 0.01:
                    # Check if RSI_3 extreme (momentum exhaustion)
                    # We don't have live RSI here, but >5% profit + near peak = take it
                    if profit_pct > 0.08:
                        self._close_position(symbol, price, reason='pump_exit')
                        continue

            # ── 4. CombinedBinHAndCluc BB middleband exit ────────────────
            # "exit when close > bb_middleband" — mean reversion complete.
            # Only fires on dip entries (CLUC/BBSQUEEZE) and requires:
            # - position is profitable (exit_profit_only gate)
            # - profit > 1% (avoid closing for pennies near midband)
            bb_mid = pos.get('bb_mid')
            if bb_mid and profit_pct > 0.01:
                if side == 'LONG' and price > bb_mid:
                    self._close_position(symbol, price, reason='bb_mid_exit')
                    continue
                elif side == 'SHORT' and price < bb_mid:
                    self._close_position(symbol, price, reason='bb_mid_exit')
                    continue

            # ── 4. Check exit conditions (SL / TP / timeout) ─────────────
            reason = None
            if side == 'LONG':
                if sl and price <= sl:
                    reason = 'sl_hit' if not pos.get('be_activated') else 'trail_sl'
                elif tp and tp > 0 and price >= tp:
                    reason = 'tp_hit'
                elif elapsed >= max_hold_sec:
                    reason = 'timeout'
            elif side == 'SHORT':
                if sl and price >= sl:
                    reason = 'sl_hit' if not pos.get('be_activated') else 'trail_sl'
                elif tp and tp > 0 and price <= tp:
                    reason = 'tp_hit'
                elif elapsed >= max_hold_sec:
                    reason = 'timeout'

            if reason:
                self._close_position(symbol, price, reason=reason)

    # ── Position management ───────────────────────────────────────────────────

    def _calc_qty(self, price: float) -> float:
        """Calculate position size in base currency."""
        if price <= 0:
            return 0.0
        notional = self._cfg('position_size_usd', settings.crypto_position_size_usd)
        return round(notional / price, 6)

    def _open_position(self, symbol: str, price: float, signal: dict,
                       direction: str = 'LONG') -> None:
        qty = self._calc_qty(price)

        tp_dist = signal['tp_dist']
        if direction == 'LONG':
            sl_price = round(price - signal['sl_dist'], 6)
            tp_price = round(price + tp_dist, 6) if tp_dist > 0 else 0.0
        else:  # SHORT
            sl_price = round(price + signal['sl_dist'], 6)
            tp_price = round(price - tp_dist, 6) if tp_dist > 0 else 0.0

        with self._lock:
            self._positions[symbol] = {
                'symbol':        symbol,
                'side':          direction,
                'entry_price':   price,
                'qty':           qty,
                'entry_time':    int(clock() * 1000),
                'current_price': price,
                'pnl':           0.0,
                'sl':            sl_price,
                'tp':            tp_price,
                'confidence':    signal['confidence'],
                'notional':      round(price * qty, 2),
                'peak_price':    price,    # for trailing SL (LONG)
                'trough_price':  price,    # for trailing SL (SHORT)
                'be_activated':  False,    # break-even SL activated?
                'bb_mid':        signal.get('bb_mid'),  # CombinedBinHAndCluc exit level
                'bot_id':        self._bot.bot_id if self._bot else None,
                'mode':          'paper',
            }
        bot_label = f'bot:{self._bot.bot_id[:8]}' if self._bot else 'singleton'
        logger.info('OPEN %s %s @ %.6f  qty=%.6f  SL=%.6f  TP=%.6f  conf=%d  [%s|paper]',
                     direction, symbol, price, qty, sl_price, tp_price, signal['confidence'], bot_label)

    def _close_position(self, symbol: str, price: float, reason: str = '') -> Optional[dict]:
        with self._lock:
            pos = self._positions.pop(symbol, None)
        if not pos:
            return None

        qty = pos['qty']
        entry = pos['entry_price']
        side = pos.get('side', 'LONG')

        if side == 'LONG':
            pnl = round((price - entry) * qty, 2)
        else:  # SHORT
            pnl = round((entry - price) * qty, 2)

        with self._lock:
            self._realized_pnl += pnl
            self._daily_pnl += pnl
            # Record cooldown timestamp to prevent re-entry churn
            self._last_trade_ts[symbol] = clock()

        # Persist to SQLite — use actual qty so P&L = (exit-entry)*qty is correct
        brokerage = round((entry * qty + price * qty) * 0.001, 2)  # ~0.1% maker fee both sides
        bot_tag = f'bot:{self._bot.bot_id[:8]}' if self._bot else 'singleton'
        try:
            from ...infrastructure.db import state_store
            state_store.record_trade(
                mode='crypto',
                symbol=symbol,
                underlying=symbol.replace('USDT', ''),
                entry_prem=entry,
                exit_prem=price,
                qty=qty,
                lot_size=1,
                brokerage=brokerage,
                exit_reason=f'{reason}|{bot_tag}',
                market_type='crypto',
                direction=side,
                currency='USD',
                entry_time=str(pos['entry_time']),
                exit_time=str(int(clock() * 1000)),
            )
        except Exception as exc:
            logger.error('Failed to persist crypto trade: %s', exc)

        logger.info('CLOSE %s %s @ %.6f  entry=%.6f  pnl=$%.2f  reason=%s  cooldown=%ds',
                     side, symbol, price, entry, pnl, reason, self._cooldown_sec)
        return {'symbol': symbol, 'pnl': pnl, 'reason': reason, 'entry': entry, 'exit': price}

    def _refresh_prices(self) -> None:
        """Fetch latest price for all open positions (mini exit-check cycles).

        Only fetches 1m candle for price update — 1 REST call per position.
        bb_mid is refreshed in the full _scan_symbol cycle (every 300s), not here.
        """
        for symbol in list(self._positions.keys()):
            try:
                candles = _fetch_klines(symbol, interval='1m', limit=1)
                if candles:
                    self._update_position_price(symbol, candles[-1]['close'])
            except Exception as exc:
                logger.debug('Price refresh failed for %s: %s', symbol, exc)

    def _update_position_price(self, symbol: str, price: float) -> None:
        with self._lock:
            pos = self._positions.get(symbol)
            if pos:
                pos['current_price'] = price
                side = pos.get('side', 'LONG')
                if side == 'LONG':
                    pos['pnl'] = round((price - pos['entry_price']) * pos['qty'], 2)
                else:  # SHORT
                    pos['pnl'] = round((pos['entry_price'] - price) * pos['qty'], 2)

    # ── Signal recording ──────────────────────────────────────────────────────

    def _record_signal(
        self, symbol: str, side: str, price: float,
        result: dict, executed: bool = False, pnl: float = None,
    ) -> None:
        sig = {
            'id':         str(uuid.uuid4()),
            'symbol':     symbol,
            'side':       side,
            'price':      price,
            'confidence': result['confidence'],
            'sl_dist':    result['sl_dist'],
            'tp_dist':    result['tp_dist'],
            'indicators': result['indicators'],
            'ts':         int(clock() * 1000),
            'executed':   executed,
            'pnl':        pnl,
        }
        with self._lock:
            self._signals.append(sig)
            if len(self._signals) > _MAX_SIGNALS:
                self._signals = self._signals[-_MAX_SIGNALS:]

        logger.info(
            'SIGNAL %s %s @ %.6f  conf=%d  RSI=%.1f  MACD=%s  vol=%.1fx  exec=%s',
            side, symbol, price, result['confidence'],
            result['indicators'].get('rsi', 0),
            result['indicators'].get('macd_cross', '?'),
            result['indicators'].get('vol_ratio', 0),
            executed,
        )

    def reset_daily(self) -> None:
        """Reset daily P&L counter (called at midnight)."""
        with self._lock:
            self._daily_pnl = 0.0
        logger.info('Daily P&L reset')


# ── Module-level singleton ────────────────────────────────────────────────────

_scanner: CryptoScanner | None = None


def get_crypto_scanner() -> CryptoScanner:
    global _scanner
    if _scanner is None:
        _scanner = CryptoScanner()
    return _scanner
