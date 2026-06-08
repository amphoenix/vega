"""
Scalp Scanner — momentum-based signal generator for quick in-out trades.

Unlike fo_scanner (multi-timeframe CIO + LLM), the scalp scanner is:
  - Pure technical (NO LLM cost)
  - 1-minute candle based (breakouts + volume spikes)
  - Fixed premium-point SL/T1 (not ATR-based)
  - Max hold timer — auto-exit after N minutes

Signal detection:
  1. Fetch last N 1-min candles for each ticker in the universe
  2. Compute momentum indicators: Donchian breakout, volume spike, RSI extreme
  3. Emit scalp_signal SSE when conditions align
  4. order_executor places entry with trade_mode='scalp'

The scanner runs in its own thread, independent of the CIO pipeline.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime, timedelta
from typing import Optional

from . import broker_utils as bu
from ..utils.logger import get_logger

logger = get_logger('vega.services.scalp_scanner')

# ── Config ────────────────────────────────────────────────────────────────────

def _cfg(key: str, default, typ=str):
    v = os.environ.get(key, str(default))
    try:
        return typ(v)
    except (ValueError, TypeError):
        return default


def scalp_enabled() -> bool:
    return os.environ.get('SCALP_ENABLED', 'true').strip().lower() in ('true', '1', 'yes')


def _scalp_universe() -> list[str]:
    """Same universe as fo_scanner by default."""
    raw = os.environ.get('SCALP_UNIVERSE', '') or os.environ.get('FO_UNIVERSE', '^NSEI,^BSESN')
    return [t.strip() for t in raw.split(',') if t.strip()]


SCALP_SL_PTS        = lambda: _cfg('SCALP_SL_PTS', 8, float)
SCALP_T1_PTS        = lambda: _cfg('SCALP_T1_PTS', 15, float)
# Per-underlying overrides (SENSEX options are more volatile)
SCALP_SL_PTS_SENSEX = lambda: _cfg('SCALP_SL_PTS_SENSEX', 0, float)  # 0 = use global
SCALP_T1_PTS_SENSEX = lambda: _cfg('SCALP_T1_PTS_SENSEX', 0, float)
SCALP_SL_PTS_NIFTY  = lambda: _cfg('SCALP_SL_PTS_NIFTY', 0, float)
SCALP_T1_PTS_NIFTY  = lambda: _cfg('SCALP_T1_PTS_NIFTY', 0, float)
SCALP_MAX_HOLD_MIN  = lambda: _cfg('SCALP_MAX_HOLD_MIN', 10, int)
SCALP_MAX_REENTRIES = lambda: _cfg('SCALP_MAX_REENTRIES', 10, int)
SCALP_DAILY_LOSS    = lambda: _cfg('SCALP_DAILY_LOSS_LIMIT', 500, float)
SCALP_MIN_CONF      = lambda: _cfg('SCALP_MIN_CONFIDENCE', 70, int)
SCALP_VOL_MULT      = lambda: _cfg('SCALP_VOLUME_MULT', 2.0, float)
SCALP_BREAKOUT_BARS = lambda: _cfg('SCALP_BREAKOUT_BARS', 3, int)
SCAN_INTERVAL       = lambda: _cfg('SCALP_SCAN_INTERVAL_SEC', 5, int)

# ── Thread state ──────────────────────────────────────────────────────────────

_scanner_thread: Optional[threading.Thread] = None
_stop_event     = threading.Event()
_manual_trigger = threading.Event()
_wake_event     = threading.Event()
_scan_lock      = threading.Lock()

_state = {
    'running':        False,
    'last_scan':      None,
    'next_scan':      None,
    'signals':        [],
    'latest_by_under': {},
    'stats': {
        'total_signals': 0,
        'wins':          0,
        'losses':        0,
        'avg_hold_sec':  0,
        'daily_pnl':     0.0,
    },
}

# Dedup: track last candle timestamp per ticker to avoid re-signalling same bar
_last_signal_candle: dict = {}  # {ticker: candle_date_str}

# ── SSE plumbing ──────────────────────────────────────────────────────────────

_sse_subscribers: list = []
_sse_lock = threading.Lock()


def subscribe_sse():
    q = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_subscribers.append(q)
        for sig in _state.get('latest_by_under', {}).values():
            try:
                q.put_nowait(json.dumps(
                    {'type': 'scalp_signal', '_replay': True, **sig},
                    default=str,
                ))
            except Exception:
                break
    return q


def unsubscribe_sse(q):
    with _sse_lock:
        try:
            _sse_subscribers.remove(q)
        except ValueError:
            pass


def _broadcast(event: dict):
    msg = json.dumps(event, default=str)
    with _sse_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                _sse_subscribers.remove(q)
            except ValueError:
                pass

# ── Scalp daily state ─────────────────────────────────────────────────────────

_scalp_lock = threading.Lock()
_scalp_ledger: dict[str, dict] = {}   # sym → {entries, sl_exits}
_scalp_daily_pnl: float = 0.0
_scalp_kill_switch: bool = False
_scalp_hold_times: list[float] = []   # seconds per completed scalp


def _load_trading_state() -> None:
    """Load persisted runtime state (bumped limits) from SQLite into os.environ on server start."""
    try:
        from .pnl_store import get_trading_state
        val = get_trading_state('SCALP_DAILY_LOSS_LIMIT')
        if val:
            os.environ['SCALP_DAILY_LOSS_LIMIT'] = val
            logger.info(f"[scalp] Loaded trading_state: SCALP_DAILY_LOSS_LIMIT={val}")
    except Exception as e:
        logger.warning(f"[scalp] Could not load trading state from DB: {e}")


_load_trading_state()


def _persist_state(key: str, value: str) -> None:
    """Persist runtime state to SQLite trading_state table (replaces .env writes)."""
    try:
        from .pnl_store import set_trading_state
        set_trading_state(key, value)
    except Exception as e:
        logger.warning(f"[scalp] Could not persist {key} to DB: {e}")


# Base limit as set in .env (never bumped) — restored every new day
_SCALP_LOSS_LIMIT_BASE: float = _cfg('SCALP_DAILY_LOSS_LIMIT_BASE', 1000, float)


def _restore_scalp_pnl() -> None:
    """On server start: detect new day from last trade date, restore limit if needed,
    then reload today's net scalp P&L from SQLite."""
    global _scalp_daily_pnl, _scalp_kill_switch
    today_str = bu.now_ist().strftime('%Y-%m-%d')

    try:
        from .pnl_store import today_net_by_mode, last_trade_date
        last_date = last_trade_date()
        if last_date and last_date < today_str:
            # Trades exist but last one was before today → new day, restore limit to base
            base = _SCALP_LOSS_LIMIT_BASE
            os.environ['SCALP_DAILY_LOSS_LIMIT'] = str(int(base))
            _persist_state('SCALP_DAILY_LOSS_LIMIT', str(int(base)))
            logger.info(f"[scalp] New day detected (last trade {last_date}) — limit restored to ₹{base:.0f}")
            return  # P&L is 0 (no trades today yet), kill switch off
        # Same day or no trades ever → restore today's net P&L
        restored = today_net_by_mode().get('scalp', 0.0)
        if restored != 0.0:
            _scalp_daily_pnl = restored
            if _scalp_daily_pnl <= -SCALP_DAILY_LOSS():
                _scalp_kill_switch = True
                logger.warning(f"[scalp] Kill switch re-armed on boot: ₹{_scalp_daily_pnl:.2f}")
            logger.info(f"[scalp] Restored daily scalp net P&L: ₹{_scalp_daily_pnl:+.2f}")
    except Exception as _e:
        logger.warning(f"[scalp] Could not restore daily scalp P&L: {_e}")

_restore_scalp_pnl()


def reset_scalp_daily():
    global _scalp_daily_pnl, _scalp_kill_switch, _scalp_hold_times
    with _scalp_lock:
        _scalp_ledger.clear()
        _scalp_daily_pnl = 0.0
        _scalp_kill_switch = False
        _scalp_hold_times = []
    # Restore limit to startup base
    base = _SCALP_LOSS_LIMIT_BASE
    os.environ['SCALP_DAILY_LOSS_LIMIT'] = str(int(base))
    _persist_state('SCALP_DAILY_LOSS_LIMIT', str(int(base)))
    logger.info(f"[scalp] Daily state reset — limit restored to ₹{base:.0f}")


def reset_kill_switch() -> float:
    """Re-enable scalp trading after a kill-switch event without zeroing P&L.
    Also bumps the daily loss limit by ₹500 so repeated resets progressively allow more risk.
    Persists the new limit to SQLite so it survives restarts.
    Returns the new limit."""
    global _scalp_kill_switch
    with _scalp_lock:
        _scalp_kill_switch = False
    current = _cfg('SCALP_DAILY_LOSS_LIMIT', 500, float)
    new_limit = current + 500
    os.environ['SCALP_DAILY_LOSS_LIMIT'] = str(new_limit)
    _persist_state('SCALP_DAILY_LOSS_LIMIT', str(int(new_limit)))
    logger.info(f"[scalp] Kill switch reset — limit bumped ₹{current:.0f} → ₹{new_limit:.0f}")
    return new_limit


def record_scalp_pnl(pnl: float, hold_sec: float = 0):
    global _scalp_daily_pnl, _scalp_kill_switch
    with _scalp_lock:
        _scalp_daily_pnl += pnl
        if hold_sec > 0:
            _scalp_hold_times.append(hold_sec)
        kill_just_triggered = False
        if _scalp_daily_pnl <= -SCALP_DAILY_LOSS() and not _scalp_kill_switch:
            _scalp_kill_switch = True
            kill_just_triggered = True
            logger.warning(f"[scalp] KILL SWITCH: daily loss ₹{abs(_scalp_daily_pnl):.0f} "
                           f"≥ limit ₹{SCALP_DAILY_LOSS():.0f}")
    # Update stats in-memory + push to SSE so frontend sees P&L immediately
    _state['stats']['daily_pnl'] = _scalp_daily_pnl
    logger.info(f"[scalp] Recorded P&L ₹{pnl:+.2f} → daily ₹{_scalp_daily_pnl:+.2f}")
    # Force-exit all remaining scalp positions to cap losses
    if kill_just_triggered:
        _force_exit_all_scalp()
    try:
        _broadcast({
            'type': 'scalp_scan_complete',
            'stats': get_scalp_stats(),
            'timestamp': bu.now_ist().isoformat(),
        })
    except Exception:
        pass


def _force_exit_all_scalp():
    """Immediately exit every open scalp position in parallel threads.
    Does NOT queue — each position gets its own thread so all exits fire simultaneously."""
    try:
        from . import tracked_positions as tp
        from .order_executor import try_auto_exit
        from ..api.indmoney import _ind_ltp

        scalp_positions = [
            rec for rec in tp.list_tracked()
            if (rec.get('ticket') or {}).get('trade_mode') == 'scalp'
        ]
        if not scalp_positions:
            return

        def _exit_one(rec):
            ticket = rec.get('ticket') or {}
            sym = ticket.get('trading_symbol', '')
            pid = rec.get('id')
            try:
                # Get live premium; fall back to entry premium so exit always fires
                prem = _ind_ltp(sym) or float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
                logger.warning(f"[scalp] KILL SWITCH → immediate exit {sym} @ ₹{prem:.2f} (id={pid})")
                try_auto_exit(pid, 'force_exit', rec, prem)
            except Exception as e:
                logger.error(f"[scalp] parallel force-exit failed for {sym} ({pid}): {e}")

        threads = [threading.Thread(target=_exit_one, args=(rec,), daemon=True)
                   for rec in scalp_positions]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    except Exception as e:
        logger.error(f"[scalp] _force_exit_all_scalp error: {e}")


def get_scalp_stats() -> dict:
    with _scalp_lock:
        hold_times = list(_scalp_hold_times)
        total_entries = sum(l.get('entries', 0) for l in _scalp_ledger.values())
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0
    return {
        'daily_pnl':      _scalp_daily_pnl,
        'kill_switch':    _scalp_kill_switch,
        'total_signals':  _state['stats']['total_signals'],
        'avg_hold_sec':   round(avg_hold, 1),
        'trades_today':   max(total_entries, len(hold_times)),
    }


# ── Momentum detection (pure technical, no LLM) ─────────────────────────────

def _detect_momentum(candles: list[dict], ticker: str) -> Optional[dict]:
    """
    Analyse the latest 1-min candles for scalp-worthy momentum.

    Returns a signal dict or None.

    Detection rules:
      1. Donchian breakout: close breaks above/below N-bar high/low channel
      2. Volume spike: last bar volume ≥ SCALP_VOLUME_MULT × avg volume
      3. RSI confirmation: RSI > 60 for bullish, < 40 for bearish
      4. Confidence scoring based on alignment of signals
    """
    if not candles or len(candles) < 10:
        return None

    n_bars = SCALP_BREAKOUT_BARS()
    vol_mult = SCALP_VOL_MULT()

    # Extract recent data
    recent = candles[-n_bars:]
    last = candles[-1]

    last_close = float(last.get('close', 0))
    last_high  = float(last.get('high', 0))
    last_low   = float(last.get('low', 0))
    last_vol   = float(last.get('volume', 0))

    if not last_close:
        return None

    # ── Donchian channel ──
    # Use a WIDER lookback (10 bars back, excluding last N) so the channel
    # doesn't chase a trending move bar-by-bar.
    channel_lookback = candles[-(n_bars + 12):-(n_bars)]
    if len(channel_lookback) < 5:
        return None

    channel_high = max(float(c.get('high', 0)) for c in channel_lookback)
    channel_low  = min(float(c.get('low', float('inf'))) for c in channel_lookback)

    # ── Rate-of-change (momentum trigger) ──
    # Catch strong directional moves even when Donchian channel is tracking
    close_5_ago = float(candles[-6].get('close', 0)) if len(candles) > 6 else 0
    roc_5 = ((last_close - close_5_ago) / close_5_ago * 100) if close_5_ago > 0 else 0

    # Volume average (20-bar lookback)
    vol_lookback = candles[-21:-1] if len(candles) > 21 else candles[:-1]
    avg_vol = sum(float(c.get('volume', 0)) for c in vol_lookback) / max(len(vol_lookback), 1)

    # RSI (14-period on closes)
    closes = [float(c.get('close', 0)) for c in candles[-16:]]
    rsi = _fast_rsi(closes, 14)

    # ── Signal detection ──
    breakout_up   = last_close > channel_high
    breakout_down = last_close < channel_low
    vol_spike     = avg_vol > 0 and last_vol >= avg_vol * vol_mult
    is_index      = ticker.startswith('^')

    # Alternative trigger: strong rate-of-change (>0.08% in 5 bars for indices)
    roc_threshold = 0.08 if is_index else 0.12
    roc_up   = roc_5 > roc_threshold
    roc_down = roc_5 < -roc_threshold

    # Signal fires on EITHER Donchian breakout OR strong momentum move
    bull_signal = breakout_up or roc_up
    bear_signal = breakout_down or roc_down

    if not (bull_signal or bear_signal):
        return None

    # Direction — prefer breakout direction; if only ROC, use ROC direction
    if bull_signal and not bear_signal:
        direction = 'BUY'
        inst_type = 'CE'
        rsi_ok = rsi is not None and rsi > 50  # relaxed from 55
    elif bear_signal and not bull_signal:
        direction = 'SELL'
        inst_type = 'PE'
        rsi_ok = rsi is not None and rsi < 50  # relaxed from 45
    else:
        # Conflicting — skip
        return None

    # Confidence scoring
    conf = 55 if is_index else 50
    if breakout_up or breakout_down:
        conf += 10  # Donchian breakout confirmed
    if abs(roc_5) > roc_threshold:
        conf += 10  # Strong momentum move
    if vol_spike:
        conf += 10
    elif is_index:
        conf += 5   # indices don't report volume
    if rsi_ok:
        conf += 10
    # Extra: very strong move
    if abs(roc_5) > roc_threshold * 2:
        conf += 5

    # Consecutive momentum bars
    momentum_count = 0
    for c in recent:
        if direction == 'BUY' and float(c.get('close', 0)) > float(c.get('open', 0)):
            momentum_count += 1
        elif direction == 'SELL' and float(c.get('close', 0)) < float(c.get('open', 0)):
            momentum_count += 1
    if momentum_count >= n_bars:
        conf += 5

    conf = min(conf, 95)

    if conf < SCALP_MIN_CONF():
        logger.debug(f"[scalp] {ticker} conf={conf}% < {SCALP_MIN_CONF()}% "
                     f"(breakout={'UP' if breakout_up else 'DN' if breakout_down else 'NO'} "
                     f"roc={roc_5:+.3f}% rsi={rsi:.1f if rsi else '?'})")
        return None

    return {
        'ticker':          ticker,
        'direction':       direction,
        'instrument_type': inst_type,
        'confidence':      conf,
        'spot':            last_close,
        'channel_high':    channel_high,
        'channel_low':     channel_low,
        'roc_5':           round(roc_5, 4),
        'volume':          last_vol,
        'avg_volume':      round(avg_vol, 0),
        'vol_ratio':       round(last_vol / avg_vol, 2) if avg_vol > 0 else 0,
        'rsi':             round(rsi, 1) if rsi is not None else None,
        'breakout_bars':   n_bars,
        'trade_mode':      'scalp',
        'timestamp':       bu.now_ist().isoformat(),
    }


def _fast_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Quick RSI from a list of close prices."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ── 1-min candle fetcher ─────────────────────────────────────────────────────

def _fetch_1min_candles(ticker: str, bars: int = 30) -> list[dict]:
    """
    Fetch 1-minute candles via the proven _ind_candles() helper in indmoney.py.
    Returns list of {date, open, high, low, close, volume} dicts, oldest→newest.
    """
    try:
        from ..api.indmoney import _ind_candles
        candles = _ind_candles(ticker, interval='1m', days=1)
        if not candles:
            return []
        return candles[-bars:]
    except Exception as e:
        logger.warning(f"[scalp] 1-min candle fetch error for {ticker}: {e}")
        return []


# ── Scalp ticket builder ─────────────────────────────────────────────────────

def _build_scalp_ticket(signal: dict, ticker: str) -> Optional[dict]:
    """
    Build a scalp trade ticket with fixed-point SL/T1.
    Resolves the nearest ATM option contract and sets levels.
    """
    try:
        from .fo_scanner import _nse_base, _resolve_option_contract
        from ..api.indmoney import _ind_option_ltp, _display_symbol

        opt_type = signal['instrument_type']
        spot = signal['spot']

        # Pick nearest ATM strike
        base = _nse_base(ticker)

        # Round spot to nearest strike interval
        if 'SENSEX' in base.upper() or 'BSESN' in base.upper():
            strike_interval = 100
        elif 'BANKNIFTY' in base.upper():
            strike_interval = 100
        else:
            strike_interval = 50  # NIFTY

        atm_strike = round(spot / strike_interval) * strike_interval

        contract = _resolve_option_contract(ticker, opt_type, atm_strike)
        if not contract:
            logger.debug(f"[scalp] No contract found for {ticker} {opt_type} {atm_strike}")
            return None

        # Get current premium
        trading_symbol = contract.get('trading_symbol') or contract.get('symbol', '')
        premium = contract.get('ltp')
        if not premium or premium <= 0:
            premium = _ind_option_ltp(trading_symbol)
        if not premium or premium <= 0:
            logger.debug(f"[scalp] No premium for {trading_symbol}")
            return None

        # Fixed SL/T1 in premium points — per-underlying overrides
        is_sensex = 'SENSEX' in base.upper() or 'BSESN' in base.upper()
        if is_sensex:
            sl_pts = SCALP_SL_PTS_SENSEX() or SCALP_SL_PTS()
            t1_pts = SCALP_T1_PTS_SENSEX() or SCALP_T1_PTS()
        else:
            sl_pts = SCALP_SL_PTS_NIFTY() or SCALP_SL_PTS()
            t1_pts = SCALP_T1_PTS_NIFTY() or SCALP_T1_PTS()

        sl  = round(max(premium - sl_pts, 0.05), 2)
        t1  = round(premium + t1_pts, 2)
        t2  = round(premium + t1_pts * 1.8, 2)  # T2 = 1.8× T1 distance

        # Lot size from broker_utils
        lot_size = bu.underlying_lot_size(base) or (20 if 'SENSEX' in base.upper() else 75)

        display = _display_symbol(trading_symbol) or trading_symbol

        return {
            'trading_symbol':  trading_symbol,
            'display_symbol':  display,
            'underlying':      ticker,
            'option_type':     opt_type,
            'strike':          atm_strike,
            'strike_price':    atm_strike,
            'expiry':          contract.get('expiry_s', ''),
            'lot_size':        lot_size,
            'trade_mode':      'scalp',
            'entry': {
                'expected_premium_inr': premium,
                'window_ist':           'SCALP — momentum breakout',
            },
            'exit': {
                'stop_loss_inr':    sl,
                'stop_loss_points': sl_pts,
                'target_1_inr':     t1,
                'target_2_inr':     t2,
            },
            'scalp_meta': {
                'max_hold_min':    SCALP_MAX_HOLD_MIN(),
                'entered_at':      bu.now_ist().isoformat(),
                'direction':       signal['direction'],
                'channel_high':    signal.get('channel_high'),
                'channel_low':     signal.get('channel_low'),
                'vol_ratio':       signal.get('vol_ratio'),
                'rsi':             signal.get('rsi'),
            },
        }

    except Exception as e:
        logger.warning(f"[scalp] Ticket build failed for {ticker}: {e}")
        return None


# ── Scan cycle ────────────────────────────────────────────────────────────────

def _run_scan_cycle():
    """One scalp scan cycle — fetch 1-min candles, detect momentum, emit signals."""
    universe = _scalp_universe()

    with _scan_lock:
        _state['last_scan'] = bu.now_ist().isoformat()
        _state['signals'] = []

    _broadcast({
        'type': 'scalp_scan_start',
        'universe': universe,
        'timestamp': bu.now_ist().isoformat(),
    })

    for ticker in universe:
        if _stop_event.is_set():
            break

        try:
            candles = _fetch_1min_candles(ticker, bars=30)
            if not candles:
                logger.info(f"[scalp] {ticker}: no candles returned")
                continue

            # Quick diagnostics
            last = candles[-1]
            lc = float(last.get('close', 0))
            n = SCALP_BREAKOUT_BARS()
            ch = candles[-(n + 12):-(n)] if len(candles) > (n + 12) else []
            c5 = float(candles[-6].get('close', 0)) if len(candles) > 6 else 0
            roc = ((lc - c5) / c5 * 100) if c5 > 0 else 0
            ch_hi = max(float(c.get('high', 0)) for c in ch) if ch else 0
            ch_lo = min(float(c.get('low', float('inf'))) for c in ch) if ch else 0
            logger.info(f"[scalp] {ticker}: close={lc:.1f} ch_hi={ch_hi:.1f} ch_lo={ch_lo:.1f} "
                        f"roc5={roc:+.3f}% candles={len(candles)} "
                        f"bk_up={lc>ch_hi} roc_up={roc>0.08}")

            signal = _detect_momentum(candles, ticker)
            if not signal:
                continue

            # Check scalp kill switch first — do NOT mark candle as seen if skipping,
            # so those candles can still fire after the kill switch is reset.
            if _scalp_kill_switch:
                logger.info(f"[scalp] Kill switch active — skipping {ticker}")
                continue

            # Dedup: skip if same candle already triggered a signal
            last_candle_ts = candles[-1].get('date', '')
            if _last_signal_candle.get(ticker) == last_candle_ts:
                continue
            _last_signal_candle[ticker] = last_candle_ts

            # Check re-entry limits
            sym_key = ticker.upper()
            with _scalp_lock:
                ledger = _scalp_ledger.get(sym_key, {'entries': 0, 'sl_exits': 0})
                if ledger['entries'] >= SCALP_MAX_REENTRIES():
                    logger.info(f"[scalp] Max re-entries reached for {ticker}")
                    continue

            # Build scalp ticket
            ticket = _build_scalp_ticket(signal, ticker)
            if not ticket:
                continue

            signal['ticket'] = ticket

            # Cache latest signal per underlying and increment counter atomically
            from .fo_scanner import _nse_base
            base = _nse_base(ticker)
            with _scan_lock:
                _state['stats']['total_signals'] += 1
                _state['signals'].append(signal)
                _state['latest_by_under'][base] = signal

            # Broadcast scalp signal
            _broadcast({
                'type': 'scalp_signal',
                **signal,
            })

            logger.info(f"[scalp] SIGNAL: {ticker} {signal['direction']} "
                        f"{signal['instrument_type']} conf={signal['confidence']}% "
                        f"vol_ratio={signal.get('vol_ratio', 0)}")

            # Attempt auto-entry if auto-trading enabled
            try:
                from .order_executor import try_scalp_entry, auto_trading_enabled
                ate = auto_trading_enabled()
                logger.info(f"[scalp] Attempting auto-entry for {ticker} "
                            f"(auto_trading_enabled={ate}, "
                            f"trading_symbol={ticket.get('trading_symbol')})")
                result = try_scalp_entry(signal)
                if result:
                    with _scalp_lock:
                        ledger = _scalp_ledger.setdefault(sym_key, {'entries': 0, 'sl_exits': 0})
                        ledger['entries'] += 1
                    logger.info(f"[scalp] Auto-entry placed for {ticker} → {result.get('id')}")
                else:
                    logger.info(f"[scalp] Auto-entry returned None for {ticker}")
            except Exception as e:
                logger.warning(f"[scalp] Auto-entry failed for {ticker}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"[scalp] Scan error for {ticker}: {e}")

    _broadcast({
        'type': 'scalp_scan_complete',
        'signals_count': len(_state['signals']),
        'stats': get_scalp_stats(),
        'timestamp': bu.now_ist().isoformat(),
    })


# ── Scanner loop ──────────────────────────────────────────────────────────────

def _scanner_loop():
    logger.info(f"[scalp] Scanner started (interval={SCAN_INTERVAL()}s, "
                f"universe={_scalp_universe()})")

    with _scan_lock:
        _state['running'] = True

    while not _stop_event.is_set():
        manual = _manual_trigger.is_set()
        if manual or bu.is_market_hours():
            if manual:
                _manual_trigger.clear()
            try:
                _run_scan_cycle()
            except Exception as e:
                logger.error(f"[scalp] Scan cycle error: {e}", exc_info=True)
            with _scan_lock:
                _state['next_scan'] = bu.now_ist().isoformat()
            _wake_event.clear()
            _wake_event.wait(SCAN_INTERVAL())
        else:
            secs_to_open = bu.seconds_until_market_open()
            sleep_for = min(max(secs_to_open, 1), 1800)
            _wake_event.clear()
            _wake_event.wait(sleep_for)

    with _scan_lock:
        _state['running'] = False
    logger.info("[scalp] Scanner stopped")


# ── Public API ────────────────────────────────────────────────────────────────

def start():
    global _scanner_thread
    if not scalp_enabled():
        logger.info("[scalp] Scalp mode disabled (SCALP_ENABLED=false)")
        return
    if _scanner_thread and _scanner_thread.is_alive():
        logger.info("[scalp] Scanner already running")
        return
    _stop_event.clear()
    _scanner_thread = threading.Thread(
        target=_scanner_loop, name='ScalpScanner', daemon=True)
    _scanner_thread.start()
    logger.info("[scalp] Scanner thread started")


def stop():
    _stop_event.set()
    _wake_event.set()
    if _scanner_thread:
        _scanner_thread.join(timeout=10)
    logger.info("[scalp] Scanner stopped")


def trigger_now():
    _manual_trigger.set()
    if _scanner_thread and _scanner_thread.is_alive():
        _wake_event.set()


def get_state() -> dict:
    with _scan_lock:
        s = dict(_state)
    s['interval_seconds'] = SCAN_INTERVAL()
    s['universe']         = _scalp_universe()
    s['enabled']          = scalp_enabled()
    s['config'] = {
        'sl_pts':        SCALP_SL_PTS(),
        't1_pts':        SCALP_T1_PTS(),
        'max_hold_min':  SCALP_MAX_HOLD_MIN(),
        'max_reentries': SCALP_MAX_REENTRIES(),
        'daily_loss':    SCALP_DAILY_LOSS(),
        'min_conf':      SCALP_MIN_CONF(),
        'vol_mult':      SCALP_VOL_MULT(),
    }
    s['stats'] = get_scalp_stats()
    return s
