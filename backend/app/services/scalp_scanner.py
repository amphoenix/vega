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
# Priority: DB (trading_config) → .env → hardcoded default
# This lets the UI update params live without restart.

def _cfg(key: str, default, typ=str):
    # 1. Try DB first (fast — cached in-memory dict)
    db_val = _config_cache.get(key)
    if db_val is not None:
        try:
            return typ(db_val)
        except (ValueError, TypeError):
            pass
    # 2. Fall back to .env
    v = os.environ.get(key, None)
    if v is not None:
        try:
            return typ(v)
        except (ValueError, TypeError):
            pass
    # 3. Hardcoded default
    try:
        return typ(default)
    except (ValueError, TypeError):
        return default


# ── In-memory config cache (loaded from DB on startup, updated on PUT) ────────
_config_cache: dict[str, str] = {}
_config_cache_lock = threading.Lock()

# All scalp config keys with their defaults and types for seeding + validation
SCALP_CONFIG_SCHEMA: dict[str, dict] = {
    'SCALP_SL_PTS':              {'default': 8,    'type': float, 'label': 'SL Points',           'group': 'Trade Levels'},
    'SCALP_T1_PTS':              {'default': 15,   'type': float, 'label': 'T1 Points',           'group': 'Trade Levels'},
    'SCALP_SL_PTS_SENSEX':       {'default': 15,   'type': float, 'label': 'SL Points (SENSEX)',  'group': 'Trade Levels'},
    'SCALP_T1_PTS_SENSEX':       {'default': 25,   'type': float, 'label': 'T1 Points (SENSEX)',  'group': 'Trade Levels'},
    'SCALP_SL_PTS_NIFTY':        {'default': 0,    'type': float, 'label': 'SL Points (NIFTY)',   'group': 'Trade Levels'},
    'SCALP_T1_PTS_NIFTY':        {'default': 0,    'type': float, 'label': 'T1 Points (NIFTY)',   'group': 'Trade Levels'},
    'SCALP_MAX_HOLD_MIN':        {'default': 10,   'type': int,   'label': 'Max Hold (min)',       'group': 'Trade Levels'},
    'SCALP_MAX_REENTRIES':       {'default': 10,   'type': int,   'label': 'Max Re-entries/day',   'group': 'Limits'},
    'SCALP_DAILY_LOSS_LIMIT':    {'default': 1000, 'type': float, 'label': 'Daily Loss Limit (₹)', 'group': 'Risk'},
    'SCALP_DAILY_LOSS_LIMIT_BASE': {'default': 1000, 'type': float, 'label': 'Base Daily Limit (₹)', 'group': 'Risk'},
    'SCALP_MIN_CONFIDENCE':      {'default': 70,   'type': int,   'label': 'Min Confidence (%)',   'group': 'Filters'},
    'SCALP_VOLUME_MULT':         {'default': 2.0,  'type': float, 'label': 'Volume Multiplier',   'group': 'Filters'},
    'SCALP_BREAKOUT_BARS':       {'default': 3,    'type': int,   'label': 'Breakout Bars',        'group': 'Filters'},
    'SCALP_ADX_MIN':             {'default': 12,   'type': float, 'label': 'ADX Minimum',          'group': 'Filters'},
    'SCALP_OPENING_SKIP_MIN':    {'default': 5,    'type': int,   'label': 'Opening Skip (min)',   'group': 'Filters'},
    'SCALP_MAX_CONCURRENT':      {'default': 2,    'type': int,   'label': 'Max Concurrent',       'group': 'Limits'},
    'SCALP_ATR_SL_MULT':         {'default': 1.5,  'type': float, 'label': 'ATR SL Multiplier',   'group': 'Trade Levels'},
    'SCALP_ATR_T1_MULT':         {'default': 2.0,  'type': float, 'label': 'ATR T1 Multiplier',   'group': 'Trade Levels'},
    'SCALP_MAX_SPREAD_PCT':      {'default': 2.0,  'type': float, 'label': 'Max Spread (%)',       'group': 'Filters'},
    'SCALP_REENTRY_COOLDOWN_SEC': {'default': 120, 'type': int,   'label': 'Re-entry Cooldown (s)', 'group': 'Limits'},
    # DISABLED features — code is commented out, hiding from UI
    # 'SCALP_PROFIT_LOCK_DRAWDOWN': {'default': 1000, 'type': float, 'label': 'Profit Lock DD (₹)',  'group': 'Risk'},
    # 'SCALP_MAX_RISK_PER_TRADE':  {'default': 500,  'type': float, 'label': 'Max Risk/Trade (₹)',   'group': 'Risk'},
    # 'SCALP_BREAKEVEN_PROFIT_PTS': {'default': 5,   'type': float, 'label': 'Breakeven After (pts)', 'group': 'Risk'},
    'SCALP_ZEROHERO_EXIT_MIN':   {'default': 50,   'type': int,   'label': 'Zero-Hero Exit (14:MM)', 'group': 'Zero-Hero'},
    'SCALP_ZEROHERO_REENTER_MIN': {'default': 0,   'type': int,   'label': 'Zero-Hero Re-enter (15:MM)', 'group': 'Zero-Hero'},
    'SCALP_ZEROHERO_FINAL_MIN':  {'default': 20,   'type': int,   'label': 'Zero-Hero Final (15:MM)', 'group': 'Zero-Hero'},
}


def _load_config_cache() -> None:
    """Load all scalp config from DB into in-memory cache. Seed missing keys."""
    try:
        from .pnl_store import get_trading_state, set_trading_state
        with _config_cache_lock:
            for key, meta in SCALP_CONFIG_SCHEMA.items():
                val = get_trading_state(key)
                if val:
                    _config_cache[key] = val
                else:
                    # Seed from .env or default
                    env_val = os.environ.get(key)
                    seed = env_val if env_val is not None else str(meta['default'])
                    set_trading_state(key, seed)
                    _config_cache[key] = seed
        logger.info(f"[scalp] Config cache loaded: {len(_config_cache)} keys")
    except Exception as e:
        logger.warning(f"[scalp] Could not load config cache: {e}")


def get_all_config() -> dict:
    """Return all scalp config as {key: {value, label, group, type}} for the API/UI."""
    result = {}
    for key, meta in SCALP_CONFIG_SCHEMA.items():
        val = _cfg(key, meta['default'], meta['type'])
        result[key] = {
            'value': val,
            'label': meta['label'],
            'group': meta['group'],
        }
    return result


def update_config(updates: dict) -> dict:
    """Update one or more config keys. Returns the updated config dict."""
    from .pnl_store import set_trading_state
    changed = {}
    with _config_cache_lock:
        for key, value in updates.items():
            if key not in SCALP_CONFIG_SCHEMA:
                continue
            str_val = str(value)
            set_trading_state(key, str_val)
            _config_cache[key] = str_val
            # Also update os.environ so existing code that reads it directly still works
            os.environ[key] = str_val
            changed[key] = value
    if changed:
        logger.info(f"[scalp] Config updated: {changed}")
    return get_all_config()


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
SCALP_DAILY_LOSS    = lambda: _cfg('SCALP_DAILY_LOSS_LIMIT', 1000, float)  # day starts at 1000, bumps +500 on each kill-switch reset
SCALP_MIN_CONF      = lambda: _cfg('SCALP_MIN_CONFIDENCE', 70, int)
SCALP_VOL_MULT      = lambda: _cfg('SCALP_VOLUME_MULT', 2.0, float)
SCALP_BREAKOUT_BARS = lambda: _cfg('SCALP_BREAKOUT_BARS', 3, int)
# SCAN_INTERVAL removed — tick-driven scanner, no polling

# ── New filter configs ─────────────────────────────────────────────────────
SCALP_ADX_MIN       = lambda: _cfg('SCALP_ADX_MIN', 12, float)       # ADX below this = no trend, skip (12 for 1-min bars; 20 is for daily)
SCALP_OPENING_SKIP_MIN = lambda: _cfg('SCALP_OPENING_SKIP_MIN', 5, int) # skip first N min after open
SCALP_MAX_CONCURRENT = lambda: _cfg('SCALP_MAX_CONCURRENT', 2, int)  # max simultaneous scalp positions
SCALP_ATR_SL_MULT   = lambda: _cfg('SCALP_ATR_SL_MULT', 1.5, float) # SL = ATR × this multiplier
SCALP_ATR_T1_MULT   = lambda: _cfg('SCALP_ATR_T1_MULT', 2.0, float) # T1 = ATR × this multiplier
SCALP_USE_ATR_SL    = lambda: os.environ.get('SCALP_USE_ATR_SL', 'true').strip().lower() in ('true', '1', 'yes')
SCALP_MAX_SPREAD_PCT = lambda: _cfg('SCALP_MAX_SPREAD_PCT', 2.0, float)  # max bid-ask spread % of premium

# ── Profit lock & risk sizing ─────────────────────────────────────────────
SCALP_PROFIT_LOCK_DRAWDOWN = lambda: _cfg('SCALP_PROFIT_LOCK_DRAWDOWN', 1000, float)  # max drawdown from peak P&L before kill
SCALP_MAX_RISK_PER_TRADE   = lambda: _cfg('SCALP_MAX_RISK_PER_TRADE', 500, float)     # max ₹ risk per single trade
SCALP_BREAKEVEN_PROFIT_PTS = lambda: _cfg('SCALP_BREAKEVEN_PROFIT_PTS', 5, float)     # move SL to breakeven after N pts profit

# ── Zero-hero 3 PM window ────────────────────────────────────────────────
SCALP_ZEROHERO_EXIT_MIN    = lambda: _cfg('SCALP_ZEROHERO_EXIT_MIN', 50, int)         # exit all at 14:MM (default 14:50)
SCALP_ZEROHERO_REENTER_MIN = lambda: _cfg('SCALP_ZEROHERO_REENTER_MIN', 0, int)       # re-enter at 15:MM (default 15:00)
SCALP_ZEROHERO_FINAL_MIN   = lambda: _cfg('SCALP_ZEROHERO_FINAL_MIN', 20, int)        # final exit at 15:MM (default 15:20)

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

# ── Whipsaw / loss-streak tracking ──────────────────────────────────────────
_recent_exits: list[dict] = []          # [{time: float, reason: str}, ...]
_loss_streak_pause_until: float = 0.0   # epoch — no new scalps until this time
_last_exit_time: dict = {}              # {ticker: epoch} — cooldown after exit per ticker
SCALP_REENTRY_COOLDOWN_SEC = lambda: _cfg('SCALP_REENTRY_COOLDOWN_SEC', 120, int)  # wait N sec before re-entering same ticker

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
_scalp_peak_pnl: float = 0.0                      # high-water mark for profit lock
_scalp_kill_switch: bool = False
_zerohero_exited: bool = False                     # True after 14:50 exit, reset on re-enter
_scalp_hold_times: list[float] = []   # seconds per completed scalp
_scalp_pnl_date: str = bu.now_ist().strftime('%Y-%m-%d')  # for day rollover detection


# Load config cache from DB on import (seeds missing keys on first boot)
_load_config_cache()

# For new-day detection: reset bumped daily limit back to base
def _reset_config_on_new_day() -> None:
    """If new day, reset SCALP_DAILY_LOSS_LIMIT in cache back to base value."""
    try:
        from .pnl_store import last_trade_date
        today_str = bu.now_ist().strftime('%Y-%m-%d')
        last_date = last_trade_date()
        if last_date and last_date < today_str:
            base = str(int(_cfg('SCALP_DAILY_LOSS_LIMIT_BASE', 1000, float)))
            with _config_cache_lock:
                _config_cache['SCALP_DAILY_LOSS_LIMIT'] = base
            os.environ['SCALP_DAILY_LOSS_LIMIT'] = base
            logger.info(f"[scalp] New day (last trade {last_date}) — daily limit reset to ₹{base}")
    except Exception as e:
        logger.warning(f"[scalp] Could not check new-day reset: {e}")

_reset_config_on_new_day()


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
    global _scalp_daily_pnl, _scalp_peak_pnl, _scalp_kill_switch
    today_str = bu.now_ist().strftime('%Y-%m-%d')

    try:
        from .pnl_store import today_net_by_mode, last_trade_date, get_trading_state
        last_date = last_trade_date()
        if last_date and last_date < today_str:
            # Trades exist but last one was before today → new day, restore limit to base
            base = _SCALP_LOSS_LIMIT_BASE
            os.environ['SCALP_DAILY_LOSS_LIMIT'] = str(int(base))
            _persist_state('SCALP_DAILY_LOSS_LIMIT', str(int(base)))
            _persist_state('SCALP_PEAK_PNL', '0')  # reset peak for new day
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
        # Restore peak P&L from DB (survives restart)
        peak_str = get_trading_state('SCALP_PEAK_PNL')
        if peak_str:
            _scalp_peak_pnl = max(float(peak_str), _scalp_daily_pnl)
            logger.info(f"[scalp] Restored peak P&L: ₹{_scalp_peak_pnl:+.2f}")
        else:
            _scalp_peak_pnl = max(0.0, _scalp_daily_pnl)
    except Exception as _e:
        logger.warning(f"[scalp] Could not restore daily scalp P&L: {_e}")

_restore_scalp_pnl()


def reset_scalp_daily():
    global _scalp_daily_pnl, _scalp_peak_pnl, _scalp_kill_switch, _scalp_hold_times, _loss_streak_pause_until, _zerohero_exited
    with _scalp_lock:
        _scalp_ledger.clear()
        _scalp_daily_pnl = 0.0
        _scalp_peak_pnl = 0.0
        _scalp_kill_switch = False
        _zerohero_exited = False
        _scalp_hold_times = []
        _recent_exits.clear()
        _loss_streak_pause_until = 0.0
        _last_exit_time.clear()
        _adverse_exit_fired.clear()
    _persist_state('SCALP_PEAK_PNL', '0')
    # Restore limit to startup base
    base = _SCALP_LOSS_LIMIT_BASE
    base_str = str(int(base))
    os.environ['SCALP_DAILY_LOSS_LIMIT'] = base_str
    _persist_state('SCALP_DAILY_LOSS_LIMIT', base_str)
    with _config_cache_lock:
        _config_cache['SCALP_DAILY_LOSS_LIMIT'] = base_str
    logger.info(f"[scalp] Daily state reset — limit restored to ₹{base:.0f}")


def reset_kill_switch() -> float:
    """Re-enable scalp trading after a kill-switch event without zeroing P&L.
    Also bumps the daily loss limit by ₹500 so repeated resets progressively allow more risk.
    Persists the new limit to SQLite so it survives restarts.
    Returns the new limit."""
    global _scalp_kill_switch
    with _scalp_lock:
        _scalp_kill_switch = False
    current = _cfg('SCALP_DAILY_LOSS_LIMIT', 1000, float)
    new_limit = current + 500
    new_str = str(int(new_limit))
    os.environ['SCALP_DAILY_LOSS_LIMIT'] = new_str
    _persist_state('SCALP_DAILY_LOSS_LIMIT', new_str)
    with _config_cache_lock:
        _config_cache['SCALP_DAILY_LOSS_LIMIT'] = new_str
    logger.info(f"[scalp] Kill switch reset — limit bumped ₹{current:.0f} → ₹{new_limit:.0f}")
    return new_limit


def _check_scalp_day_rollover() -> None:
    """If IST date changed, auto-reset scalp daily state + limits."""
    global _scalp_pnl_date
    today = bu.now_ist().strftime('%Y-%m-%d')
    if today != _scalp_pnl_date:
        logger.info(f"[scalp] Day rollover detected ({_scalp_pnl_date} → {today}) — resetting")
        reset_scalp_daily()
        _scalp_pnl_date = today


def record_scalp_pnl(pnl: float, hold_sec: float = 0, exit_reason: str = '', underlying: str = ''):
    global _scalp_daily_pnl, _scalp_peak_pnl, _scalp_kill_switch, _loss_streak_pause_until
    _check_scalp_day_rollover()
    # Record exit time for per-ticker re-entry cooldown
    if underlying:
        import time as _t
        _last_exit_time[underlying] = _t.time()
    with _scalp_lock:
        _scalp_daily_pnl += pnl
        if hold_sec > 0:
            _scalp_hold_times.append(hold_sec)
        # Update peak P&L (high-water mark) and persist to DB
        if _scalp_daily_pnl > _scalp_peak_pnl:
            _scalp_peak_pnl = _scalp_daily_pnl
            _persist_state('SCALP_PEAK_PNL', str(round(_scalp_peak_pnl, 2)))
        kill_just_triggered = False
        # Kill switch #1: absolute daily loss limit
        if _scalp_daily_pnl <= -SCALP_DAILY_LOSS() and not _scalp_kill_switch:
            _scalp_kill_switch = True
            kill_just_triggered = True
            logger.warning(f"[scalp] KILL SWITCH (absolute): daily loss ₹{abs(_scalp_daily_pnl):.0f} "
                           f"≥ limit ₹{SCALP_DAILY_LOSS():.0f}")
        # Kill switch #2: profit lock — DISABLED (can stop trading too early on good days)
        # drawdown = _scalp_peak_pnl - _scalp_daily_pnl
        # lock_threshold = SCALP_PROFIT_LOCK_DRAWDOWN()
        # if drawdown >= lock_threshold and _scalp_peak_pnl > 0 and not _scalp_kill_switch:
        #     _scalp_kill_switch = True
        #     kill_just_triggered = True
        #     logger.warning(f"[scalp] KILL SWITCH (profit lock): gave back ₹{drawdown:.0f} "
        #                    f"from peak ₹{_scalp_peak_pnl:.0f} (now ₹{_scalp_daily_pnl:.0f}), "
        #                    f"lock threshold ₹{lock_threshold:.0f}")

        # ── Loss-streak cooldown: if 3+ losses in last 5 min, pause 5 min ──
        import time as _time
        now = _time.time()
        _recent_exits.append({'time': now, 'reason': exit_reason, 'pnl': pnl})
        # Prune exits older than 5 minutes
        cutoff = now - 300
        _recent_exits[:] = [e for e in _recent_exits if e['time'] > cutoff]
        # Count recent losses (sl_hit, thesis_flip, force_exit with negative pnl)
        recent_losses = sum(1 for e in _recent_exits if e.get('pnl', 0) < -50)
        if recent_losses >= 3 and not _loss_streak_pause_until:
            _loss_streak_pause_until = now + 300  # 5 min cooldown
            logger.warning(f"[scalp] LOSS STREAK: {recent_losses} losses in 5 min — "
                           f"pausing scalp entries for 5 min")
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
    all_closes = [float(c.get('close', 0)) for c in candles]
    closes = all_closes[-16:]
    rsi = _fast_rsi(closes, 14)

    # ── Opening range filter ──
    # Skip first N minutes after market open (09:15 IST) — pure chaos
    skip_min = SCALP_OPENING_SKIP_MIN()
    if skip_min > 0:
        now_ist = bu.now_ist()
        market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        if now_ist < market_open + timedelta(minutes=skip_min):
            logger.debug(f"[scalp] {ticker} OPENING RANGE: first {skip_min}m — skipping")
            return None

    # ── ADX trend filter ──
    # ADX < threshold means no clear trend, scalping into chop
    adx = _fast_adx(candles, 14)
    adx_min = SCALP_ADX_MIN()
    if adx is not None and adx < adx_min:
        logger.debug(f"[scalp] {ticker} ADX={adx:.1f} < {adx_min} — no trend, skipping")
        return None

    # ── VWAP filter ──
    # Only take longs above VWAP, shorts below VWAP
    vwap = _fast_vwap(candles)

    # ── EMA alignment (9/21) ──
    ema9 = _fast_ema(all_closes, 9)
    ema21 = _fast_ema(all_closes, 21)

    # ── Bollinger squeeze detection ──
    bb_squeeze = _bollinger_squeeze(candles, 20, 0.5)

    # ── ATR for dynamic SL ──
    atr = _fast_atr(candles, 14)

    # ── Whipsaw / choppiness filter ──
    # Count direction flips in recent bars. If the market is just zig-zagging,
    # every breakout is a fake-out. Skip if too many flips.
    flip_count = 0
    flip_lookback = candles[-10:] if len(candles) >= 10 else candles
    for i in range(1, len(flip_lookback)):
        prev_dir = float(flip_lookback[i-1].get('close', 0)) - float(flip_lookback[i-1].get('open', 0))
        curr_dir = float(flip_lookback[i].get('close', 0)) - float(flip_lookback[i].get('open', 0))
        if prev_dir * curr_dir < 0:  # sign change = direction flip
            flip_count += 1
    if flip_count >= 7:  # 7+ flips in 10 bars = pure chop
        logger.debug(f"[scalp] {ticker} WHIPSAW: {flip_count} flips in 10 bars — skipping")
        return None

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

    # ── VWAP directional filter ──
    # Longs only above VWAP, shorts only below VWAP
    if vwap is not None:
        if direction == 'BUY' and last_close < vwap:
            logger.debug(f"[scalp] {ticker} BUY signal but close {last_close:.1f} < VWAP {vwap:.1f} — skipping")
            return None
        if direction == 'SELL' and last_close > vwap:
            logger.debug(f"[scalp] {ticker} SELL signal but close {last_close:.1f} > VWAP {vwap:.1f} — skipping")
            return None

    # ── EMA alignment filter ──
    # For BUY: EMA9 > EMA21 (short-term trend is up)
    # For SELL: EMA9 < EMA21 (short-term trend is down)
    ema_aligned = False
    if ema9 is not None and ema21 is not None:
        if direction == 'BUY' and ema9 > ema21:
            ema_aligned = True
        elif direction == 'SELL' and ema9 < ema21:
            ema_aligned = True
        elif not (breakout_up or breakout_down):
            # EMA misaligned AND no Donchian breakout — weak signal, skip
            logger.debug(f"[scalp] {ticker} EMA misaligned (9={ema9:.1f} vs 21={ema21:.1f}) + no breakout — skipping")
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
    # VWAP alignment bonus
    if vwap is not None:
        if (direction == 'BUY' and last_close > vwap) or \
           (direction == 'SELL' and last_close < vwap):
            conf += 5
    # EMA alignment bonus
    if ema_aligned:
        conf += 5
    # Bollinger squeeze bonus — breakout after squeeze is high-confidence
    if bb_squeeze:
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
        'vwap':            vwap,
        'adx':             round(adx, 1) if adx is not None else None,
        'ema9':            round(ema9, 2) if ema9 is not None else None,
        'ema21':           round(ema21, 2) if ema21 is not None else None,
        'atr':             round(atr, 2) if atr is not None else None,
        'bb_squeeze':      bb_squeeze,
        'ema_aligned':     ema_aligned,
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


def _fast_atr(candles: list[dict], period: int = 14) -> Optional[float]:
    """Average True Range from candle dicts."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i].get('high', 0))
        l = float(candles[i].get('low', 0))
        pc = float(candles[i - 1].get('close', 0))
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs[-period:]) / period if len(trs) >= period else None


def _fast_adx(candles: list[dict], period: int = 14) -> Optional[float]:
    """Simplified ADX (Average Directional Index) from candle dicts."""
    if len(candles) < period + 2:
        return None
    plus_dm_list, minus_dm_list, tr_list = [], [], []
    for i in range(1, len(candles)):
        h = float(candles[i].get('high', 0))
        l = float(candles[i].get('low', 0))
        ph = float(candles[i - 1].get('high', 0))
        pl = float(candles[i - 1].get('low', 0))
        pc = float(candles[i - 1].get('close', 0))
        plus_dm = max(h - ph, 0) if (h - ph) > (pl - l) else 0
        minus_dm = max(pl - l, 0) if (pl - l) > (h - ph) else 0
        tr = max(h - l, abs(h - pc), abs(l - pc))
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    # Simple averages for the last 'period' bars
    atr = sum(tr_list[-period:]) / period
    if atr == 0:
        return 0.0
    plus_di = (sum(plus_dm_list[-period:]) / period) / atr * 100
    minus_di = (sum(minus_dm_list[-period:]) / period) / atr * 100
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0.0
    dx = abs(plus_di - minus_di) / di_sum * 100
    return round(dx, 2)


def _fast_ema(values: list[float], period: int) -> Optional[float]:
    """Exponential Moving Average of a list of values."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period  # SMA seed
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _fast_vwap(candles: list[dict]) -> Optional[float]:
    """VWAP (Volume Weighted Average Price) from candle dicts.
    Uses typical price = (H + L + C) / 3, weighted by volume."""
    cum_vol = 0.0
    cum_tp_vol = 0.0
    for c in candles:
        tp = (float(c.get('high', 0)) + float(c.get('low', 0)) + float(c.get('close', 0))) / 3
        vol = float(c.get('volume', 0))
        cum_vol += vol
        cum_tp_vol += tp * vol
    if cum_vol == 0:
        return None
    return round(cum_tp_vol / cum_vol, 2)


def _bollinger_squeeze(candles: list[dict], period: int = 20, squeeze_threshold: float = 0.5) -> bool:
    """Returns True if Bollinger Band width is contracting (squeeze), indicating
    a potential explosive move. A squeeze followed by expansion = real breakout."""
    if len(candles) < period + 5:
        return False
    closes = [float(c.get('close', 0)) for c in candles]
    # Current bandwidth
    recent = closes[-period:]
    sma = sum(recent) / period
    std = (sum((x - sma) ** 2 for x in recent) / period) ** 0.5
    bw_now = (std / sma * 100) if sma > 0 else 0
    # Previous bandwidth (5 bars ago)
    prev = closes[-(period + 5):-5]
    sma_p = sum(prev) / period
    std_p = (sum((x - sma_p) ** 2 for x in prev) / period) ** 0.5
    bw_prev = (std_p / sma_p * 100) if sma_p > 0 else 0
    # Squeeze = bandwidth was contracting, now expanding
    return bw_prev < squeeze_threshold and bw_now > bw_prev


# ── 1-min candle fetcher ─────────────────────────────────────────────────────

def _fetch_1min_candles(ticker: str, bars: int = 40) -> list[dict]:
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

        # ── Spread / liquidity check ──
        # Wide bid-ask spread = bad fills, slippage eats the profit
        try:
            from ..api.indmoney import _ind_option_depth
            depth = _ind_option_depth(trading_symbol)
            if depth:
                best_bid = float(depth.get('bid', 0) or 0)
                best_ask = float(depth.get('ask', 0) or 0)
                if best_bid > 0 and best_ask > 0:
                    spread_pct = (best_ask - best_bid) / premium * 100
                    max_spread = SCALP_MAX_SPREAD_PCT()
                    if spread_pct > max_spread:
                        logger.info(f"[scalp] {trading_symbol} spread {spread_pct:.1f}% > {max_spread}% — skipping (bid={best_bid} ask={best_ask})")
                        return None
        except Exception:
            pass  # depth API may not exist — skip check gracefully

        # SL/T1 — ATR-based dynamic or fixed-point, per config
        is_sensex = 'SENSEX' in base.upper() or 'BSESN' in base.upper()
        atr_val = signal.get('atr')

        if SCALP_USE_ATR_SL() and atr_val and atr_val > 0:
            # ATR-based dynamic SL adapts to current volatility
            sl_pts = round(atr_val * SCALP_ATR_SL_MULT(), 2)
            t1_pts = round(atr_val * SCALP_ATR_T1_MULT(), 2)
            # Clamp SL to reasonable scalp bounds (min 3, max 20 for NIFTY; max 30 for SENSEX)
            max_sl = 30 if is_sensex else 20
            sl_pts = max(3, min(sl_pts, max_sl))
            t1_pts = max(sl_pts * 1.2, min(t1_pts, max_sl * 2))
            logger.info(f"[scalp] ATR-based SL={sl_pts:.1f} T1={t1_pts:.1f} (ATR={atr_val:.2f})")
        else:
            # Fixed SL/T1 in premium points — per-underlying overrides
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
                'vwap':            signal.get('vwap'),
                'adx':             signal.get('adx'),
                'ema9':            signal.get('ema9'),
                'ema21':           signal.get('ema21'),
                'atr':             signal.get('atr'),
                'bb_squeeze':      signal.get('bb_squeeze'),
                'atr_sl':          SCALP_USE_ATR_SL(),
            },
        }

    except Exception as e:
        logger.warning(f"[scalp] Ticket build failed for {ticker}: {e}")
        return None


# ── Tick-driven scanner ───────────────────────────────────────────────────────
# Instead of polling every 5s, register live tick callbacks for each ticker in
# the universe. Candles are refreshed once per minute (on bar close). Each live
# tick updates the latest bar's close and checks for breakout instantly.

_candle_cache: dict[str, dict] = {}       # ticker → {candles: [...], fetched_at: float}
_tick_processing: dict[str, bool] = {}    # ticker → True if a signal thread is running
_tick_lock = threading.Lock()

def _refresh_candles(ticker: str) -> list[dict] | None:
    """Fetch candles and cache for 60s. Returns cached candles or None."""
    import time as _t
    cache = _candle_cache.get(ticker)
    now = _t.time()
    if cache and now - cache.get('fetched_at', 0) < 60:
        return cache.get('candles')
    candles = _fetch_1min_candles(ticker, bars=40)
    if candles:
        _candle_cache[ticker] = {'candles': candles, 'fetched_at': now}
        return candles
    return cache.get('candles') if cache else None


def _check_zerohero_window() -> str:
    """Check zero-hero 3PM window. Returns: 'normal', 'exit', 'cooldown', 'zerohero', 'final_exit'."""
    global _zerohero_exited
    now = bu.now_ist()
    h, m = now.hour, now.minute

    exit_min = SCALP_ZEROHERO_EXIT_MIN()        # e.g., 50 → 14:50
    reenter_min = SCALP_ZEROHERO_REENTER_MIN()   # e.g., 0  → 15:00
    final_min = SCALP_ZEROHERO_FINAL_MIN()       # e.g., 20 → 15:20

    # 14:MM exit window
    if h == 14 and m >= exit_min:
        if not _zerohero_exited:
            _zerohero_exited = True
            return 'exit'
        return 'cooldown'
    # 15:00 to 15:MM(reenter) → cooldown (no entries yet)
    if h == 15 and m < reenter_min:
        return 'cooldown'
    # 15:MM(reenter) to 15:MM(final) → zero-hero entry window
    if h == 15 and reenter_min <= m < final_min:
        _zerohero_exited = False  # allow re-entry
        return 'zerohero'
    # 15:MM(final)+ → final exit
    if h == 15 and m >= final_min:
        return 'final_exit'
    return 'normal'


def _process_signal(ticker: str, signal: dict, candles: list[dict]):
    """Process a confirmed signal — filters, ticket building, entry. Runs in a thread."""
    global _loss_streak_pause_until
    import time as _time

    try:
        # Day rollover
        _check_scalp_day_rollover()

        # Loss-streak cooldown
        if _loss_streak_pause_until and _time.time() < _loss_streak_pause_until:
            return
        elif _loss_streak_pause_until and _time.time() >= _loss_streak_pause_until:
            _loss_streak_pause_until = 0.0
            logger.info(f"[scalp] Loss-streak cooldown expired — resuming")

        # Kill switch
        if _scalp_kill_switch:
            return

        # Per-ticker re-entry cooldown
        last_exit = _last_exit_time.get(ticker, 0)
        cooldown = SCALP_REENTRY_COOLDOWN_SEC()
        if last_exit and _time.time() - last_exit < cooldown:
            return

        # Zero-hero window check
        zh_state = _check_zerohero_window()
        if zh_state in ('exit', 'final_exit'):
            _force_exit_all_scalp()
            return
        if zh_state == 'cooldown':
            return

        # Dedup by candle timestamp
        last_candle_ts = candles[-1].get('date', '')
        if _last_signal_candle.get(ticker) == last_candle_ts:
            return
        _last_signal_candle[ticker] = last_candle_ts

        # Max concurrent positions
        from . import tracked_positions as tp
        existing = tp.list_tracked()
        scalp_count = sum(1 for r in existing
                          if (r.get('ticket') or {}).get('trade_mode') == 'scalp')
        if scalp_count >= SCALP_MAX_CONCURRENT():
            return

        # Re-entry limits
        sym_key = ticker.upper()
        with _scalp_lock:
            ledger = _scalp_ledger.get(sym_key, {'entries': 0, 'sl_exits': 0})
            if ledger['entries'] >= SCALP_MAX_REENTRIES():
                return

        # Build ticket
        ticket = _build_scalp_ticket(signal, ticker)
        if not ticket:
            return
        signal['ticket'] = ticket

        # Cache signal
        from .fo_scanner import _nse_base
        base = _nse_base(ticker)
        with _scan_lock:
            _state['stats']['total_signals'] += 1
            _state['signals'].append(signal)
            _state['latest_by_under'][base] = signal

        _broadcast({'type': 'scalp_signal', **signal})
        logger.info(f"[scalp] SIGNAL: {ticker} {signal['direction']} "
                    f"{signal['instrument_type']} conf={signal['confidence']}% "
                    f"vol_ratio={signal.get('vol_ratio', 0)}")

        # Thesis flip: exit opposite scalp positions on direction change
        try:
            from .order_executor import try_auto_exit
            from ..api.indmoney import _ind_ltp
            new_opt = signal.get('instrument_type', '')
            opposite_opt = 'PE' if new_opt == 'CE' else 'CE'
            for rec in tp.list_tracked():
                t = rec.get('ticket') or {}
                if t.get('trade_mode') != 'scalp':
                    continue
                if t.get('underlying', '') == ticker and t.get('option_type', '').upper() == opposite_opt:
                    opt_sym = t.get('trading_symbol', '')
                    prem = _ind_ltp(opt_sym) or 0
                    logger.warning(f"[scalp] THESIS FLIP: {ticker} was {opposite_opt}, "
                                   f"now {new_opt} — auto-exiting {opt_sym}")
                    try_auto_exit(rec['id'], 'thesis_flip', rec, prem)
        except Exception as e:
            logger.warning(f"[scalp] Thesis flip check failed: {e}")

        # Auto-entry
        try:
            from .order_executor import try_scalp_entry, auto_trading_enabled
            result = try_scalp_entry(signal)
            if result:
                with _scalp_lock:
                    ledger = _scalp_ledger.setdefault(sym_key, {'entries': 0, 'sl_exits': 0})
                    ledger['entries'] += 1
                logger.info(f"[scalp] Auto-entry placed for {ticker} → {result.get('id')}")
        except Exception as e:
            logger.warning(f"[scalp] Auto-entry failed for {ticker}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"[scalp] Signal processing error for {ticker}: {e}")
    finally:
        with _tick_lock:
            _tick_processing[ticker] = False


_adverse_exit_fired: set[str] = set()  # track_id → already exited by adverse check


def _check_adverse_exit(candles: list[dict], ticker: str):
    """On every tick, check if market conditions have deteriorated (whipsaw/chop)
    for this ticker. If so, force-exit any open scalp position immediately
    instead of waiting for SL to be hit."""
    try:
        from . import tracked_positions as tp
        from .order_executor import try_auto_exit
        from ..api.indmoney import _ind_ltp

        # Find open scalp positions for this ticker
        scalp_positions = [
            rec for rec in tp.list_tracked()
            if (rec.get('ticket') or {}).get('trade_mode') == 'scalp'
            and (rec.get('ticket') or {}).get('underlying', '') == ticker
            and rec.get('id') not in _adverse_exit_fired
        ]
        if not scalp_positions:
            return

        # Count direction flips in last 10 bars (same logic as whipsaw filter)
        flip_count = 0
        flip_lookback = candles[-10:] if len(candles) >= 10 else candles
        for i in range(1, len(flip_lookback)):
            prev_dir = float(flip_lookback[i-1].get('close', 0)) - float(flip_lookback[i-1].get('open', 0))
            curr_dir = float(flip_lookback[i].get('close', 0)) - float(flip_lookback[i].get('open', 0))
            if prev_dir * curr_dir < 0:
                flip_count += 1

        if flip_count < 7:
            return  # not choppy enough to warrant exit

        # Whipsaw detected with open positions — force exit all
        for rec in scalp_positions:
            ticket = rec.get('ticket') or {}
            sym = ticket.get('trading_symbol', '')
            pid = rec.get('id')
            prem = _ind_ltp(sym) or float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
            logger.warning(f"[scalp] ADVERSE EXIT: {ticker} whipsaw ({flip_count} flips) "
                           f"— force-exiting {sym} @ ₹{prem:.2f} (id={pid})")
            _adverse_exit_fired.add(pid)
            try_auto_exit(pid, 'thesis_flip', rec, prem)
    except Exception as e:
        logger.warning(f"[scalp] adverse exit check failed: {e}")


def _on_tick(tick: dict, ticker: str):
    """Live tick callback — instant breakout detection. Called from WS thread."""
    if _stop_event.is_set():
        return
    if not bu.is_market_hours():
        return

    ltp = float(tick.get('ltp') or 0)
    if not ltp:
        return

    # Don't run two signal threads for the same ticker simultaneously
    with _tick_lock:
        if _tick_processing.get(ticker):
            return

    # Refresh candles if needed (60s cache)
    candles = _refresh_candles(ticker)
    if not candles or len(candles) < 10:
        return

    # Update last candle with live tick for real-time detection
    import copy
    live_candles = copy.deepcopy(candles)
    live_candles[-1]['close'] = ltp
    if ltp > float(live_candles[-1].get('high', 0)):
        live_candles[-1]['high'] = ltp
    if ltp < float(live_candles[-1].get('low', float('inf'))):
        live_candles[-1]['low'] = ltp

    # ── Adverse-condition exit: detect whipsaw/chop on EVERY tick and
    #    force-exit any open scalp position for this ticker immediately.
    #    This prevents bleeding to SL in choppy markets.
    _check_adverse_exit(live_candles, ticker)

    # Run signal detection with live-updated candles
    signal = _detect_momentum(live_candles, ticker)
    if not signal:
        return

    # Mark as processing and spawn thread for heavy work
    with _tick_lock:
        _tick_processing[ticker] = True

    threading.Thread(
        target=_process_signal,
        args=(ticker, signal, live_candles),
        daemon=True,
        name=f'ScalpSignal-{ticker}',
    ).start()


# ── Candle refresh loop (runs once per minute as backup) ─────────────────────

def _candle_refresh_loop():
    """Periodically refresh candles for all tickers. Ensures indicators stay fresh
    even if ticks are sparse. Also handles zero-hero window exits."""
    import time as _t
    while not _stop_event.is_set():
        if bu.is_market_hours():
            # Zero-hero window: auto-exit at 14:50
            zh = _check_zerohero_window()
            if zh in ('exit', 'final_exit'):
                logger.info(f"[scalp] Zero-hero {zh}: exiting all scalp positions")
                _force_exit_all_scalp()

            for ticker in _scalp_universe():
                if _stop_event.is_set():
                    break
                try:
                    _refresh_candles(ticker)
                except Exception:
                    pass

            with _scan_lock:
                _state['last_scan'] = bu.now_ist().isoformat()

            _broadcast({
                'type': 'scalp_scan_complete',
                'stats': get_scalp_stats(),
                'timestamp': bu.now_ist().isoformat(),
            })
        _stop_event.wait(60)


_candle_thread: Optional[threading.Thread] = None
_registered_callbacks: dict[str, object] = {}  # ticker → callback fn


def _register_tick_callbacks():
    """Register live tick callbacks for all universe tickers."""
    from ..api.indmoney import register_tick_callback
    for ticker in _scalp_universe():
        if ticker in _registered_callbacks:
            continue
        cb = lambda tick, t=ticker: _on_tick(tick, t)
        register_tick_callback(ticker, cb)
        _registered_callbacks[ticker] = cb
        logger.info(f"[scalp] Registered tick callback for {ticker}")


def _unregister_tick_callbacks():
    """Unregister all tick callbacks."""
    from ..api.indmoney import unregister_tick_callback
    for ticker, cb in _registered_callbacks.items():
        try:
            unregister_tick_callback(ticker, cb)
        except Exception:
            pass
    _registered_callbacks.clear()


# ── Scanner loop (fallback + candle refresh) ─────────────────────────────────

def _scanner_loop():
    logger.info(f"[scalp] Scanner started (tick-driven + 60s candle refresh, "
                f"universe={_scalp_universe()})")

    with _scan_lock:
        _state['running'] = True

    # Register live tick callbacks for instant breakout detection
    try:
        _register_tick_callbacks()
    except Exception as e:
        logger.warning(f"[scalp] Failed to register tick callbacks: {e}")

    # Start candle refresh thread
    global _candle_thread
    _candle_thread = threading.Thread(
        target=_candle_refresh_loop, name='ScalpCandleRefresh', daemon=True)
    _candle_thread.start()

    # Keep the main scanner thread alive; handle manual triggers
    while not _stop_event.is_set():
        manual = _manual_trigger.is_set()
        if manual:
            _manual_trigger.clear()
            # Force-refresh candles and run signal check for all tickers
            for ticker in _scalp_universe():
                try:
                    _candle_cache.pop(ticker, None)  # force refresh
                    candles = _refresh_candles(ticker)
                    if candles and len(candles) >= 10:
                        signal = _detect_momentum(candles, ticker)
                        if signal:
                            with _tick_lock:
                                if not _tick_processing.get(ticker):
                                    _tick_processing[ticker] = True
                                    threading.Thread(
                                        target=_process_signal,
                                        args=(ticker, signal, candles),
                                        daemon=True).start()
                except Exception as e:
                    logger.error(f"[scalp] Manual scan error for {ticker}: {e}")

        if not bu.is_market_hours():
            secs_to_open = bu.seconds_until_market_open()
            sleep_for = min(max(secs_to_open, 1), 1800)
            _wake_event.clear()
            _wake_event.wait(sleep_for)
        else:
            _wake_event.clear()
            _wake_event.wait(30)  # wake every 30s to check manual trigger / market hours

    with _scan_lock:
        _state['running'] = False
    _unregister_tick_callbacks()
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
    s['interval_seconds'] = 0  # tick-driven, no polling interval
    s['universe']         = _scalp_universe()
    s['enabled']          = scalp_enabled()
    # Flat config for display strip (backward compat with frontend)
    all_cfg = get_all_config()
    s['config'] = {meta['label'].lower().replace(' ', '_').replace('(', '').replace(')', '').replace('₹', ''): meta['value']
                   for meta in all_cfg.values()}
    # Also include short keys the frontend stats strip expects
    s['config']['sl_pts']       = SCALP_SL_PTS()
    s['config']['t1_pts']       = SCALP_T1_PTS()
    s['config']['max_hold_min'] = SCALP_MAX_HOLD_MIN()
    s['config']['daily_loss']   = SCALP_DAILY_LOSS()
    s['params'] = all_cfg  # full schema for settings drawer
    s['stats'] = get_scalp_stats()
    s['peak_pnl'] = _scalp_peak_pnl
    return s
