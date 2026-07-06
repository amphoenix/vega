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
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

from ..dependencies import get_broker
from ..shared import time as _mkt
from ..shared.logger import get_logger
from ..shared.time import clock, datetime, timedelta

logger = get_logger('vega.services.scalp_scanner')

# ── Config — all scalp params read from config.py (single source of truth) ────

from ..config import settings as _settings


def scalp_enabled() -> bool:
    return _settings.scalp_enabled


def _scalp_universe() -> list[str]:
    return _settings.scalp_universe_list


# ── Config accessors — read directly from config.py settings ──────────────────
SCALP_SL_PTS        = lambda: _settings.scalp_sl_pts
SCALP_T1_PTS        = lambda: _settings.scalp_t1_pts
SCALP_SL_PTS_SENSEX = lambda: _settings.scalp_sl_pts_sensex
SCALP_T1_PTS_SENSEX = lambda: _settings.scalp_t1_pts_sensex
SCALP_SL_PTS_NIFTY  = lambda: _settings.scalp_sl_pts_nifty
SCALP_T1_PTS_NIFTY  = lambda: _settings.scalp_t1_pts_nifty
SCALP_MAX_HOLD_MIN  = lambda: _settings.scalp_max_hold_min
SCALP_MAX_REENTRIES = lambda: _settings.scalp_max_reentries
SCALP_DAILY_LOSS    = lambda: _scalp_daily_loss_current
SCALP_MIN_CONF      = lambda: _settings.scalp_min_confidence
SCALP_VOL_MULT      = lambda: _settings.scalp_volume_mult
SCALP_BREAKOUT_BARS = lambda: _settings.scalp_breakout_bars
SCALP_OPENING_SKIP_MIN = lambda: _settings.scalp_opening_skip_min
SCALP_MAX_CONCURRENT = lambda: _settings.scalp_max_concurrent
SCALP_ATR_SL_MULT   = lambda: _settings.scalp_atr_sl_mult
SCALP_ATR_T1_MULT   = lambda: _settings.scalp_atr_t1_mult
SCALP_USE_ATR_SL    = lambda: _settings.scalp_use_atr_sl
SCALP_MAX_SPREAD_PCT = lambda: _settings.scalp_max_spread_pct
SCALP_LET_WINNERS_RUN = lambda: _settings.scalp_let_winners_run
SCALP_LOTS_PER_TRADE = lambda: _settings.scalp_max_lots_per_trade
SCALP_REENTRY_COOLDOWN = lambda: _settings.scalp_reentry_cooldown_sec
SCALP_MAX_ENTRIES_PER_DAY = lambda: _settings.scalp_max_entries_per_day
SCALP_ZEROHERO_EXIT_MIN    = lambda: _settings.scalp_zerohero_exit_min
SCALP_ZEROHERO_REENTER_MIN = lambda: _settings.scalp_zerohero_reenter_min
SCALP_ZEROHERO_FINAL_MIN   = lambda: _settings.scalp_zerohero_final_min

# ── Thread state ──────────────────────────────────────────────────────────────

_scanner_thread: threading.Thread | None = None
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
_option_ltp_cache: dict[str, float] = {}   # opt_code.upper() → latest ltp from option WS tick
_last_option_tick_ts: float = 0.0          # clock() of last option WS tick — staleness guard
_WS_STALE_SEC = 12.0                        # ticks older than this → treat WS as dead (poller takes over)
_atm_ws_ltp: dict[str, float] = {}        # trading_symbol → latest ltp from pre-subscribed ATM WS
_atm_subscribed: dict[str, dict] = {}     # ticker → {strike, ce_symbol, pe_symbol, ce_cb, pe_cb}
_atm_bid_ask: dict[str, tuple] = {}       # trading_symbol → (bid, ask) refreshed every 60s


def _cached_prem(ticket: dict) -> float:
    """Return last WS-tick price keyed by scrip code. Falls back to entry premium."""
    opt_code = ticket.get('opt_code', '')
    if opt_code:
        cached = _option_ltp_cache.get(opt_code.strip().upper(), 0.0)
        if cached > 0:
            return cached
    return float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)


def get_scalp_unrealized_pnl() -> float:
    """Sum of unrealized P&L across all open scalp positions using last WS tick price."""
    try:
        from ..infrastructure.db import tracked_positions as _tp
        total = 0.0
        for rec in _tp.list_tracked():
            ticket = rec.get('ticket') or {}
            if ticket.get('trade_mode') != 'scalp':
                continue
            entry_prem = float((ticket.get('entry') or {}).get('expected_premium_inr') or 0)
            current_prem = _cached_prem(ticket)
            if entry_prem > 0 and current_prem > 0:
                qty = int(rec.get('qty') or ticket.get('lot_size') or 1)
                total += (current_prem - entry_prem) * qty
        return total
    except Exception:
        return 0.0


# ── Whipsaw / loss-streak tracking ──────────────────────────────────────────
_recent_exits: list[dict] = []          # [{time, reason, pnl, opt_type}, ...]
_loss_streak_pause_until: dict[str, float] = {'CE': 0.0, 'PE': 0.0}  # per-direction epoch
_last_exit_time: dict = {}              # {ticker: epoch} — cooldown after exit per ticker
_last_status_broadcast: dict = {}       # {ticker+reason: epoch} — throttle status broadcasts
_daily_entry_count: int = 0             # total scalp entries today
_scalp_open_count: int = 0              # live open scalp positions (tick-driven SL gate)

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


def _broadcast_status(ticker: str, reason: str, detail: str = ''):
    """Broadcast a scanner_status event to the trade feed, throttled to once per 30s per ticker+reason."""
    key = f"{ticker}:{reason}"
    now = clock()
    if now - _last_status_broadcast.get(key, 0) < 30:
        return
    _last_status_broadcast[key] = now
    evt = {
        'type': 'scanner_status',
        'ticker': ticker,
        'reason': reason,
        'detail': detail,
        'timestamp': _mkt.now_ist().isoformat(),
    }
    logger.info(f"[scalp] BROADCAST scanner_status: {ticker} {reason} (subs={len(_sse_subscribers)})")
    _broadcast(evt)


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
_scalp_peak_pnl: float = 0.0                      # high-water mark for profit lock
_scalp_kill_switch: bool = False
_zerohero_exited: bool = False                     # True after 14:50 exit, reset on re-enter
_scalp_hold_times: list[float] = []   # seconds per completed scalp
_scalp_pnl_date: str = _mkt.now_ist().strftime('%Y-%m-%d')  # for day rollover detection

# ── DB-backed scalp P&L (single source of truth) ─────────────────────────
_pnl_cache: tuple[float, float] = (0.0, 0.0)  # (value, epoch)
_PNL_CACHE_TTL = 2.0  # seconds

def _get_scalp_daily_pnl() -> float:
    """Return today's net scalp P&L from DB with a short cache."""
    global _pnl_cache
    now = clock()
    if now - _pnl_cache[1] < _PNL_CACHE_TTL:
        return _pnl_cache[0]
    try:
        from ..infrastructure.db.pnl_store import today_net_by_mode
        val = today_net_by_mode().get('scalp', 0.0)
    except Exception:
        val = _pnl_cache[0]  # stale is better than crash
    _pnl_cache = (val, now)
    return val

def _invalidate_pnl_cache() -> None:
    """Force next _get_scalp_daily_pnl() to re-read DB."""
    global _pnl_cache
    _pnl_cache = (_pnl_cache[0], 0.0)


def _persist_state(key: str, value: str) -> None:
    """Persist runtime state to SQLite trading_state table."""
    try:
        from ..infrastructure.db.pnl_store import set_trading_state
        set_trading_state(key, value)
    except Exception as e:
        logger.warning(f"[scalp] Could not persist {key} to DB: {e}")


# Base limit (never bumped) — restored every new day
_SCALP_LOSS_LIMIT_BASE: float = float(_settings.scalp_daily_loss_limit_base)
# Runtime-mutable current limit (can be bumped on kill-switch reset)
_scalp_daily_loss_current: float = float(_settings.scalp_daily_loss_limit)


def _set_scalp_daily_loss(val: float) -> None:
    """Update runtime scalp daily loss limit and persist."""
    global _scalp_daily_loss_current
    _scalp_daily_loss_current = val
    _persist_state('SCALP_DAILY_LOSS_LIMIT', str(int(val)))


def _restore_scalp_state() -> None:
    """On server start: detect new day from last trade date, restore limit if needed,
    then check kill switch against DB P&L."""
    global _scalp_peak_pnl, _scalp_kill_switch
    today_str = _mkt.now_ist().strftime('%Y-%m-%d')

    try:
        from ..infrastructure.db.pnl_store import get_trading_state, last_trade_date
        last_date = last_trade_date()
        if last_date and last_date < today_str:
            base = _SCALP_LOSS_LIMIT_BASE
            _set_scalp_daily_loss(base)
            _persist_state('SCALP_PEAK_PNL', '0')
            logger.info(f"[scalp] New day detected (last trade {last_date}) — limit restored to ₹{base:.0f}")
            return
        # Read P&L from DB (authoritative)
        restored = _get_scalp_daily_pnl()
        if restored <= -SCALP_DAILY_LOSS():
            _scalp_kill_switch = True
            logger.warning(f"[scalp] Kill switch re-armed on boot: ₹{restored:.2f}")
        logger.info(f"[scalp] DB scalp net P&L: ₹{restored:+.2f}")
        # Restore peak P&L from DB (survives restart)
        peak_str = get_trading_state('SCALP_PEAK_PNL')
        if peak_str:
            _scalp_peak_pnl = max(float(peak_str), restored)
            logger.info(f"[scalp] Restored peak P&L: ₹{_scalp_peak_pnl:+.2f}")
        else:
            _scalp_peak_pnl = max(0.0, restored)
        # Restore loss-streak pauses
        streak_raw = get_trading_state('SCALP_STREAK_PAUSE')
        if streak_raw:
            import json as _j
            _now_ep = clock()
            restored_streak = _j.loads(streak_raw)
            for _ot, _until in restored_streak.items():
                if _until > _now_ep:
                    _loss_streak_pause_until[_ot] = _until
            if any(v > _now_ep for v in restored_streak.values()):
                logger.info(f"[scalp] Restored active loss-streak pauses: {_loss_streak_pause_until}")
        # Restore daily entry count
        entry_str = get_trading_state('SCALP_DAILY_ENTRY_COUNT')
        if entry_str:
            global _daily_entry_count
            _daily_entry_count = int(float(entry_str))
            logger.info(f"[scalp] Restored daily entry count: {_daily_entry_count}")
    except Exception as _e:
        logger.warning(f"[scalp] Could not restore scalp state: {_e}")

_restore_scalp_state()


def _restore_scalp_count() -> None:
    """Restore _scalp_open_count and re-register option WS callbacks for surviving scalp positions.
    Without re-registration, _on_option_tick never fires → tracked_monitor sees scalp_ws_active()=True
    and skips REST reprice → SL detection is completely dead until the next manual entry."""
    global _scalp_open_count
    try:
        from ..infrastructure.db import tracked_positions as _tp
        _broker = get_broker()
        remaining = [
            r for r in _tp.list_tracked()
            if (r.get('ticket') or {}).get('trade_mode') == 'scalp'
        ]
        for rec in remaining:
            ticket = rec.get('ticket') or {}
            trading_symbol = ticket.get('trading_symbol', '')
            if trading_symbol:
                _sec_id = str(ticket.get('security_id', ''))
                def _opt_cb(sym, ltp, tick, _sym=trading_symbol):
                    _on_option_tick(tick, _sym, _sym)
                _broker.subscribe_ticks([trading_symbol], _opt_cb,
                                        exchange='NFO', security_id=_sec_id)
                # Seed tick-level high water mark so trailing SL works from first tick
                pid = rec.get('id')
                entry_prem = float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
                if pid and entry_prem > 0:
                    _tick_high_water[pid] = entry_prem
                logger.info(f"[scalp] Re-registered option WS: {trading_symbol} (sec_id={_sec_id})")
        with _scalp_lock:
            _scalp_open_count = len(remaining)
        if remaining:
            logger.info(f"[scalp] Restored _scalp_open_count={_scalp_open_count}, "
                        f"re-registered {len(remaining)} option WS callbacks")
    except Exception as e:
        logger.warning(f"[scalp] Could not restore scalp count: {e}")


def reset_scalp_daily():
    global _scalp_peak_pnl, _scalp_kill_switch, _scalp_hold_times, _loss_streak_pause_until, _zerohero_exited
    with _scalp_lock:
        _scalp_ledger.clear()
        _scalp_peak_pnl = 0.0
        _scalp_kill_switch = False
        _zerohero_exited = False
        _scalp_hold_times = []
        _recent_exits.clear()
        _loss_streak_pause_until.update({'CE': 0.0, 'PE': 0.0})
        _last_exit_time.clear()
        _adverse_exit_fired.clear()
        _thesis_flip_fired.clear()
    global _daily_entry_count
    _daily_entry_count = 0
    _last_signal_candle.clear()
    _persist_state('SCALP_DAILY_ENTRY_COUNT', '0')
    _persist_state('SCALP_STREAK_PAUSE', __import__('json').dumps({'CE': 0.0, 'PE': 0.0}))
    _persist_state('SCALP_PEAK_PNL', '0')
    # Restore limit to startup base
    base = _SCALP_LOSS_LIMIT_BASE
    _set_scalp_daily_loss(base)
    logger.info(f"[scalp] Daily state reset — limit restored to ₹{base:.0f}")


def reset_kill_switch() -> float:
    """Re-enable scalp trading after a kill-switch event without zeroing P&L.
    Also bumps the daily loss limit by ₹500 so repeated resets progressively allow more risk.
    Persists the new limit to SQLite so it survives restarts.
    Returns the new limit."""
    global _scalp_kill_switch
    with _scalp_lock:
        _scalp_kill_switch = False
    current = SCALP_DAILY_LOSS()
    new_limit = min(current + 500, _SCALP_LOSS_LIMIT_BASE * 3)
    _set_scalp_daily_loss(new_limit)
    logger.info(f"[scalp] Kill switch reset — limit bumped ₹{current:.0f} → ₹{new_limit:.0f}")
    return new_limit


def _check_scalp_day_rollover() -> None:
    """If IST date changed, auto-reset scalp daily state + limits."""
    global _scalp_pnl_date
    today = _mkt.now_ist().strftime('%Y-%m-%d')
    if today != _scalp_pnl_date:
        logger.info(f"[scalp] Day rollover detected ({_scalp_pnl_date} → {today}) — resetting")
        reset_scalp_daily()
        _scalp_pnl_date = today


def record_scalp_pnl(pnl: float, hold_sec: float = 0, exit_reason: str = '',
                     underlying: str = '', opt_type: str = ''):
    global _scalp_peak_pnl, _scalp_kill_switch, _loss_streak_pause_until
    _dec_scalp_count()
    _check_scalp_day_rollover()
    # Invalidate cache so next read picks up the new DB row
    _invalidate_pnl_cache()
    # Record exit time for per-ticker re-entry cooldown
    if underlying:
        _last_exit_time[underlying] = clock()
    # Read authoritative P&L from DB
    current_pnl = _get_scalp_daily_pnl()
    with _scalp_lock:
        if hold_sec > 0:
            _scalp_hold_times.append(hold_sec)
        # Update peak P&L (high-water mark) and persist to DB
        if current_pnl > _scalp_peak_pnl:
            _scalp_peak_pnl = current_pnl
            _persist_state('SCALP_PEAK_PNL', str(round(_scalp_peak_pnl, 2)))
        kill_just_triggered = False
        # Kill switch: absolute daily loss limit
        if current_pnl <= -SCALP_DAILY_LOSS() and not _scalp_kill_switch:
            _scalp_kill_switch = True
            kill_just_triggered = True
            logger.warning(f"[scalp] KILL SWITCH: daily loss ₹{abs(current_pnl):.0f} "
                           f"≥ limit ₹{SCALP_DAILY_LOSS():.0f}")
        # ── Loss-streak cooldown: track per direction (CE/PE) ─────────────────
        # CE losses should not block valid PE entries and vice versa.
        now = clock()
        _recent_exits.append({'time': now, 'reason': exit_reason, 'pnl': pnl, 'opt_type': opt_type})
        # Prune exits older than 5 minutes
        cutoff = now - 300
        _recent_exits[:] = [e for e in _recent_exits if e['time'] > cutoff]
        # Count recent losses per direction and pause that direction independently
        for _ot in ('CE', 'PE'):
            dir_losses = sum(
                1 for e in _recent_exits
                if e.get('pnl', 0) < -50 and e.get('opt_type') == _ot
            )
            if dir_losses >= 3 and not _loss_streak_pause_until.get(_ot, 0.0):
                _loss_streak_pause_until[_ot] = now + 300
                logger.warning(f"[scalp] LOSS STREAK {_ot}: {dir_losses} losses in 5min — "
                               f"pausing {_ot} entries for 5min")
                _persist_state('SCALP_STREAK_PAUSE', __import__('json').dumps(_loss_streak_pause_until))
    # Update stats in-memory + push to SSE so frontend sees P&L immediately
    _state['stats']['daily_pnl'] = current_pnl
    logger.info(f"[scalp] Recorded P&L ₹{pnl:+.2f} → daily ₹{current_pnl:+.2f}")
    # Force-exit all remaining scalp positions to cap losses
    if kill_just_triggered:
        _force_exit_all_scalp()
    try:
        _broadcast({
            'type': 'scalp_scan_complete',
            'stats': get_scalp_stats(),
            'timestamp': _mkt.now_ist().isoformat(),
        })
    except Exception:
        pass


def _force_exit_all_scalp():
    """Immediately exit every open scalp position in parallel threads.
    Does NOT queue — each position gets its own thread so all exits fire simultaneously."""
    try:
        from ..infrastructure.db import tracked_positions as tp
        from .order_executor import try_auto_exit
        _broker = get_broker()

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
                prem = _broker.get_ltp(sym) or float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
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


def _inc_scalp_count():
    global _scalp_open_count
    with _scalp_lock:
        _scalp_open_count += 1


def _dec_scalp_count():
    global _scalp_open_count
    with _scalp_lock:
        _scalp_open_count = max(0, _scalp_open_count - 1)


_tick_high_water: dict[str, float] = {}  # pid → highest premium seen (tick-level)

def _on_option_tick(tick: dict, opt_code: str, trading_symbol: str):
    """Option WS tick callback — HFT-speed SL/T1/T2/trail for open scalp positions.
    Runs on EVERY option price tick. No REST calls — pure in-memory."""
    if _stop_event.is_set() or _scalp_open_count == 0:
        return
    ltp = float(tick.get('ltp') or tick.get('last_price') or tick.get('live_price') or 0)
    if not ltp:
        return
    code_upper = opt_code.strip().upper()
    global _last_option_tick_ts
    _last_option_tick_ts = clock()
    _option_ltp_cache[code_upper] = ltp
    try:
        from ..infrastructure.db import tracked_positions as _tp
        from .order_executor import check_scalp_hold_timeout, try_auto_exit
        for rec in _tp.list_tracked():
            t = rec.get('ticket') or {}
            if t.get('opt_code', '').strip().upper() != code_upper:
                continue
            if t.get('trade_mode') != 'scalp':
                continue

            pid = rec.get('id')
            ex = t.get('exit') or {}
            entry = float((t.get('entry') or {}).get('expected_premium_inr', 0) or 0)
            sl = float(ex.get('stop_loss_inr') or 0)
            t1 = float(ex.get('target_1_inr') or 0)
            t2 = float(ex.get('target_2_inr') or 0)

            # ── Graduated trailing SL (tick-level) ─────────────────────
            if entry > 0:
                hw = _tick_high_water.get(pid, entry)
                hw = max(hw, ltp)
                _tick_high_water[pid] = hw
                gain_pts = hw - entry
                qty = int(rec.get('qty', 1) or 1)
                brokerage_per_unit = 70.0 / max(qty, 1)
                underlying = (t.get('underlying') or '').upper()
                is_sensex = 'BSESN' in underlying or 'SENSEX' in underlying

                if is_sensex:
                    step1_trigger, step1_sl = 10.0, round(entry - 15.0, 2)
                    step2_trigger, step2_sl = 20.0, round(entry + brokerage_per_unit, 2)
                    trail_trigger, trail_buffer = 25.0, 12.0
                else:
                    step1_trigger, step1_sl = 5.0, round(entry - 5.0, 2)
                    step2_trigger, step2_sl = 10.0, round(entry + brokerage_per_unit, 2)
                    trail_trigger, trail_buffer = 12.0, 5.0

                new_sl = sl
                if gain_pts >= step1_trigger and new_sl < step1_sl:
                    new_sl = step1_sl
                if gain_pts >= step2_trigger and new_sl < step2_sl:
                    new_sl = step2_sl
                if gain_pts >= trail_trigger:
                    trail_sl = round(hw - trail_buffer, 2)
                    if trail_sl > new_sl:
                        new_sl = trail_sl

                if new_sl > sl:
                    ex['stop_loss_inr'] = new_sl
                    sl = new_sl  # use updated SL for exit check below
                    _tp.upsert(rec)
                    logger.info(f"[scalp] Tick trail: {trading_symbol} SL ₹{sl:.2f}→₹{new_sl:.2f} "
                                f"(HWM ₹{hw:.2f}, gain {gain_pts:.1f}pts)")

            # ── Exit checks ────────────────────────────────────────────
            if sl > 0 and ltp <= sl:
                logger.info(f"[scalp] Tick SL hit: {trading_symbol} ltp={ltp:.2f} <= sl={sl:.2f}")
                _tick_high_water.pop(pid, None)
                try_auto_exit(pid, 'sl_hit', rec, ltp)
            elif t2 > 0 and ltp >= t2:
                logger.info(f"[scalp] Tick T2: {trading_symbol} ltp={ltp:.2f} >= t2={t2:.2f}")
                _tick_high_water.pop(pid, None)
                try_auto_exit(pid, 'past_t2', rec, ltp)
            elif t1 > 0 and ltp >= t1:
                if SCALP_LET_WINNERS_RUN() and entry > 0:
                    # Let winners run: don't exit at T1. Lock in profit by flooring SL to
                    # breakeven+brokerage, then ride toward T2 (graduated trail above keeps
                    # ratcheting SL up on further ticks; SL or T2 becomes the real exit).
                    lock_sl = round(entry + brokerage_per_unit, 2)
                    if lock_sl > sl:
                        ex['stop_loss_inr'] = lock_sl
                        _tp.upsert(rec)
                        logger.info(f"[scalp] T1 reached, letting winner run: {trading_symbol} "
                                    f"ltp={ltp:.2f} >= t1={t1:.2f} — SL locked ₹{sl:.2f}→₹{lock_sl:.2f}")
                    check_scalp_hold_timeout(pid, rec, ltp)
                else:
                    logger.info(f"[scalp] Tick T1: {trading_symbol} ltp={ltp:.2f} >= t1={t1:.2f}")
                    _tick_high_water.pop(pid, None)
                    try_auto_exit(pid, 'past_t1', rec, ltp)
            else:
                # Hold timeout check — runs on every tick, no REST
                check_scalp_hold_timeout(pid, rec, ltp)
    except Exception as e:
        logger.debug(f"[scalp] option tick check error: {e}")


def _make_atm_tick_cb(sym: str):
    def _cb(tick: dict):
        ltp = float(tick.get('ltp') or tick.get('last_price') or tick.get('live_price') or 0)
        if ltp > 0:
            _atm_ws_ltp[sym] = ltp
    return _cb


def _subscribe_atm_options(ticker: str, spot: float) -> None:
    """Pre-subscribe ATM CE + PE to WS so _build_scalp_ticket has ltp without a REST call."""
    try:
        _broker = get_broker()

        base = _mkt.nse_base(ticker)
        strike_interval = 100 if ('SENSEX' in base or 'BANKNIFTY' in base) else 50
        atm_strike = round(spot / strike_interval) * strike_interval

        old = _atm_subscribed.get(ticker, {})
        if old and abs(old.get('strike', 0) - atm_strike) < strike_interval * 0.5:
            return  # same ATM — no resubscription needed

        # Unsubscribe stale ATM callbacks before subscribing new ones
        for key in ('ce', 'pe'):
            old_sym = old.get(f'{key}_symbol')
            old_cb = old.get(f'{key}_cb')
            if old_sym and old_cb:
                try:
                    _broker.unsubscribe_ticks([old_sym])
                except Exception:
                    pass

        entry: dict = {'strike': atm_strike}
        for opt_type in ('CE', 'PE'):
            contract = get_broker().resolve_option_contract(ticker, opt_type, atm_strike)
            if not contract:
                continue
            sym = contract['trading_symbol']
            secid = contract.get('security_id') or ''
            key = opt_type.lower()
            entry[f'{key}_symbol'] = sym
            if contract.get('ltp') and contract['ltp'] > 0:
                _atm_ws_ltp[sym] = contract['ltp']
            cb = _make_atm_tick_cb(sym)
            entry[f'{key}_cb'] = cb
            _broker.subscribe_ticks([sym], lambda s, l, t, _cb=cb: _cb(t),
                                    exchange='NFO', security_id=str(secid))

        _atm_subscribed[ticker] = entry
        logger.info(f"[scalp] ATM pre-sub {ticker} strike={atm_strike} "
                    f"CE={entry.get('ce_symbol','?')} PE={entry.get('pe_symbol','?')}")
    except Exception as e:
        logger.debug(f"[scalp] ATM pre-subscribe failed for {ticker}: {e}")


def _refresh_atm_subscriptions() -> None:
    """Called from candle refresh loop — update ATM WS subscriptions and bid/ask cache."""
    _broker = get_broker()
    for ticker in _scalp_universe():
        cache = _candle_cache.get(ticker)
        candles = (cache or {}).get('candles')
        if not candles:
            continue
        spot = float(candles[-1].get('close') or 0)
        if spot > 0:
            _subscribe_atm_options(ticker, spot)
        # Refresh bid/ask for subscribed ATM CE + PE (one REST call each, runs in 60s background loop)
        entry = _atm_subscribed.get(ticker, {})
        for key in ('ce_symbol', 'pe_symbol'):
            sym = entry.get(key)
            if not sym:
                continue
            try:
                q = _broker.get_quote(sym)
                if q:
                    bid = float(q.get('bid') or 0)
                    ask = float(q.get('ask') or 0)
                    ltp = float(q.get('ltp') or 0)
                    if bid > 0 and ask > 0:
                        _atm_bid_ask[sym] = (bid, ask)
                    if ltp > 0:
                        _atm_ws_ltp[sym] = ltp
            except Exception:
                pass


def _reconcile_on_startup():
    """Remove ghost tracked positions — in JSON but no longer open at broker."""
    try:
        _is_paper = _settings.scalp_mode == 'paper'
        if _is_paper:
            logger.info("[scalp] Reconciliation skipped — paper mode (positions not at broker)")
            return
        _broker = get_broker()
        broker_pos = _broker.get_positions()
        if broker_pos is None:
            logger.info("[scalp] Broker reconciliation skipped — broker unreachable")
            return
        broker_syms = {
            (p.symbol if hasattr(p, 'symbol') else str(p.get('symbol', ''))).strip().upper()
            for p in broker_pos
            if (p.qty if hasattr(p, 'qty') else int(p.get('net_quantity', 0) or 0)) != 0
        }
        from ..infrastructure.db import tracked_positions as _tp
        removed = 0
        for rec in _tp.list_tracked():
            sym = (rec.get('ticket') or {}).get('trading_symbol', '').strip().upper()
            if sym and sym not in broker_syms:
                _tp.remove_tracked(rec['id'], exit_reason='broker_reconciliation')
                logger.warning(f"[scalp] Removed ghost position: {sym}")
                removed += 1
        if removed:
            logger.info(f"[scalp] Reconciliation: removed {removed} ghost position(s)")
        else:
            logger.info("[scalp] Reconciliation: all positions confirmed at broker")
    except Exception as e:
        logger.warning(f"[scalp] Reconciliation error: {e}")


def get_scalp_stats() -> dict:
    _check_scalp_day_rollover()
    with _scalp_lock:
        hold_times = list(_scalp_hold_times)
        total_entries = sum(l.get('entries', 0) for l in _scalp_ledger.values())
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0
    return {
        'daily_pnl':      _get_scalp_daily_pnl(),
        'kill_switch':    _scalp_kill_switch,
        'total_signals':  _state['stats']['total_signals'],
        'avg_hold_sec':   round(avg_hold, 1),
        'trades_today':   max(total_entries, len(hold_times)),
    }


# ── Momentum detection (pure technical, no LLM) ─────────────────────────────

_vix_cache: tuple[float, float] | None = None  # (value, fetched_epoch)
_VIX_CACHE_TTL = 900.0                          # 15 min — VIX is session-level, not tick-level


def _get_cached_vix() -> float | None:
    """Fetch India VIX at most once per 15 minutes. Returns stale value on API failure."""
    global _vix_cache
    now = clock()
    if _vix_cache and _vix_cache[0] > 0 and (now - _vix_cache[1]) < _VIX_CACHE_TTL:
        return _vix_cache[0]
    try:
        v = get_broker().get_ltp('^INDIAVIX')
        logger.debug('[vix] get_ltp(^INDIAVIX) returned: %s', v)
        if v is not None and v > 0:
            _vix_cache = (v, now)
            return v
    except Exception as e:
        logger.debug('[vix] fetch error: %s', e)
    return _vix_cache[0] if _vix_cache and _vix_cache[0] > 0 else None


def scalp_ws_active() -> bool:
    """True only when a scalp position is open AND option ticks are actually fresh.

    Returns False when ticks have gone stale (>_WS_STALE_SEC) so the tracked_monitor
    poller resumes REST-based SL/timeout checks instead of relying on dead WS ticks —
    otherwise a stalled feed would leave positions un-exited until a manual refresh.
    """
    if _scalp_open_count <= 0:
        return False
    return (clock() - _last_option_tick_ts) < _WS_STALE_SEC


def _detect_momentum(candles: list[dict], ticker: str) -> dict | None:
    """
    4-gate momentum detection for scalp signals.

    GATE 1 → Opening range skip
    GATE 2 → VIX range 12–25 (cached 15 min — no per-tick network call)
    GATE 3 → VWAP directional (hard filter — microstructure, current)
    TRIGGER → Donchian breakout OR ROC-5 > threshold
    QUALITY → Weighted confidence: base + breakout + ROC strength + EMA alignment bonus/penalty
              EMA9/21 is a soft signal (penalty when misaligned, not a hard block)
    """
    if not candles or len(candles) < 25:
        return None

    n_bars = SCALP_BREAKOUT_BARS()

    last = candles[-1]
    last_close = float(last.get('close', 0))
    last_vol   = float(last.get('volume', 0))

    if not last_close:
        return None

    # ── GATE 1: Opening range skip ──────────────────────────────────────────
    skip_min = SCALP_OPENING_SKIP_MIN()
    if skip_min > 0:
        now_ist = _mkt.now_ist()
        market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        if now_ist < market_open + timedelta(minutes=skip_min):
            _broadcast_status(ticker, 'Opening Range', f'First {skip_min}m after open — waiting')
            return None

    # ── GATE 2: VIX range 12–25 (cached — no per-tick network call) ────────────
    _vix = _get_cached_vix()
    if _vix is not None:
        if _vix < 12:
            _broadcast_status(ticker, 'VIX Low', f'VIX {_vix:.1f} — market too flat')
            return None
        if _vix > 25:
            _broadcast_status(ticker, 'VIX High', f'VIX {_vix:.1f} — too volatile')
            return None

    # ── EMA9/21/50: trend detection ──────────────────────────────────────────
    all_closes = [float(c.get('close', 0)) for c in candles]
    ema9  = _fast_ema(all_closes, 9)
    ema21 = _fast_ema(all_closes, 21)
    ema50 = _fast_ema(all_closes, 50)
    ema_direction: str | None = None
    if ema9 is not None and ema21 is not None:
        if ema9 > ema21:
            ema_direction = 'BUY'
        elif ema9 < ema21:
            ema_direction = 'SELL'

    # ── GATE 2.5: Higher-TF trend (HARD — never scalp against the 50min trend) ─
    # EMA50 on 1m ≈ 50-minute trend. Prevents catching micro-bounces in a dump.
    if ema50 is not None and ema21 is not None:
        trend_up   = ema21 > ema50 and last_close > ema50
        trend_down = ema21 < ema50 and last_close < ema50
    else:
        trend_up = trend_down = False

    # ── GATE 3: VWAP directional (hard — microstructure, session-current) ────
    vwap = _fast_vwap(candles)

    # ── TRIGGERS: Donchian breakout OR ROC-5 ────────────────────────────────
    channel_lookback = candles[-(n_bars + 12):-(n_bars)]
    if len(channel_lookback) < 5:
        return None
    channel_high = max(float(c.get('high', 0)) for c in channel_lookback)
    channel_low  = min(float(c.get('low', float('inf'))) for c in channel_lookback)

    close_5_ago = float(candles[-6].get('close', 0)) if len(candles) > 6 else 0
    roc_5 = ((last_close - close_5_ago) / close_5_ago * 100) if close_5_ago > 0 else 0

    is_index      = ticker.startswith('^')
    roc_threshold = 0.20  # uniform — 0.15% on indices fires on noise

    breakout_up   = last_close > channel_high
    breakout_down = last_close < channel_low
    bull_signal   = breakout_up   or (roc_5 >  roc_threshold)
    bear_signal   = breakout_down or (roc_5 < -roc_threshold)

    if not (bull_signal or bear_signal) or (bull_signal and bear_signal):
        return None

    direction = 'BUY' if bull_signal else 'SELL'

    # ── Apply GATE 2.5: trend alignment hard filter ────────────────────────
    if direction == 'BUY' and trend_down:
        logger.debug(f"[scalp] {ticker} BUY blocked — EMA21<EMA50, price below EMA50 (downtrend)")
        _broadcast_status(ticker, 'Trend Block', 'BUY signal but 50m downtrend — skip')
        return None
    if direction == 'SELL' and trend_up:
        logger.debug(f"[scalp] {ticker} SELL blocked — EMA21>EMA50, price above EMA50 (uptrend)")
        _broadcast_status(ticker, 'Trend Block', 'SELL signal but 50m uptrend — skip')
        return None

    # ── Apply GATE 3: VWAP hard filter ───────────────────────────────────────
    if vwap is not None:
        if direction == 'BUY' and last_close < vwap:
            logger.debug(f"[scalp] {ticker} BUY but {last_close:.1f} < VWAP {vwap:.1f} — skip")
            return None
        if direction == 'SELL' and last_close > vwap:
            logger.debug(f"[scalp] {ticker} SELL but {last_close:.1f} > VWAP {vwap:.1f} — skip")
            return None

    # ── CONFIDENCE: weighted sum ─────────────────────────────────────────────
    # Replaces the old binary 85/72 split. Each factor contributes real signal weight.
    conf = 60  # base — above noise floor but below default 70 threshold without corroboration

    # Donchian channel breakout: strongest structural signal
    if breakout_up or breakout_down:
        conf += 12

    # ROC magnitude: stronger momentum → more conviction
    roc_abs = abs(roc_5)
    if roc_abs > roc_threshold * 2:
        conf += 8
    elif roc_abs > roc_threshold:
        conf += 4

    # EMA alignment: soft bonus/penalty (21-min trend context, not a gate)
    if ema_direction is not None:
        if direction == ema_direction:
            conf += 5
        else:
            conf -= 6  # soft penalty — early-trend breakouts remain viable
            logger.debug(f"[scalp] {ticker} {direction} vs EMA {ema_direction} — -6 conf penalty")

    # High VIX penalty: wider spreads make scalping harder above 20
    if _vix is not None and _vix > 20:
        conf -= 5

    conf = max(0, min(100, conf))

    if conf < SCALP_MIN_CONF():
        logger.debug(f"[scalp] {ticker} conf={conf}% < {SCALP_MIN_CONF()}% — skip")
        return None

    atr = _fast_atr(candles, 14)

    # Volume ratio: logged for analytics only — index "volume" is synthetic (constituent aggregate),
    # not futures/options liquidity, so it is not used as a confidence gate.
    vol_lookback = candles[-21:-1] if len(candles) > 21 else candles[:-1]
    avg_vol  = sum(float(c.get('volume', 0)) for c in vol_lookback) / max(len(vol_lookback), 1)
    vol_ratio = round(last_vol / avg_vol, 2) if avg_vol > 0 else 0

    return {
        'ticker':          ticker,
        'direction':       direction,
        'instrument_type': 'CE' if direction == 'BUY' else 'PE',
        'confidence':      conf,
        'spot':            last_close,
        'channel_high':    channel_high,
        'channel_low':     channel_low,
        'roc_5':           round(roc_5, 4),
        'volume':          last_vol,
        'avg_volume':      round(avg_vol, 0),
        'vol_ratio':       vol_ratio,
        'vwap':            vwap,
        'ema9':            round(ema9, 2) if ema9 is not None else None,
        'ema21':           round(ema21, 2) if ema21 is not None else None,
        'atr':             round(atr, 2) if atr is not None else None,
        'breakout_bars':   n_bars,
        'trade_mode':      'scalp',
        'timestamp':       _mkt.now_ist().isoformat(),
        'ema_aligned':     ema_direction == direction if ema_direction else True,
    }


def _fast_atr(candles: list[dict], period: int = 14) -> float | None:
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




def _fast_ema(values: list[float], period: int) -> float | None:
    """Exponential Moving Average of a list of values."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period  # SMA seed
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _fast_vwap(candles: list[dict]) -> float | None:
    """VWAP from today's session candles (09:15 IST onwards) only.
    Filters pre-market and previous-day bars so VWAP resets each session.
    Note: index volume (^NSEI/^BSESN) is synthetic constituent aggregate —
    VWAP direction is used as a microstructure proxy, not absolute liquidity."""
    today = _mkt.now_ist().strftime('%Y-%m-%d')
    session_start = f"{today} 09:15"
    cum_vol = 0.0
    cum_tp_vol = 0.0
    for c in candles:
        d = str(c.get('date', ''))
        if d < session_start:
            continue
        tp = (float(c.get('high', 0)) + float(c.get('low', 0)) + float(c.get('close', 0))) / 3
        vol = float(c.get('volume', 0))
        cum_vol += vol
        cum_tp_vol += tp * vol
    if cum_vol == 0:
        return None
    return round(cum_tp_vol / cum_vol, 2)




# ── 1-min candle fetcher ─────────────────────────────────────────────────────

def _fetch_1min_candles(ticker: str, bars: int = 120) -> list[dict]:
    """
    Fetch 1-minute candles via the proven _ind_candles() helper in indmoney.py.
    Returns list of {date, open, high, low, close, volume} dicts, oldest→newest.
    """
    try:
        candles = get_broker().get_candles(ticker, interval='1m', days=1)
        candles = [{'date': c.date, 'open': c.open, 'high': c.high, 'low': c.low,
                    'close': c.close, 'volume': c.volume} for c in candles] if candles else []
        if not candles:
            return []
        return candles[-bars:]
    except Exception as e:
        logger.warning(f"[scalp] 1-min candle fetch error for {ticker}: {e}")
        return []


# ── Scalp ticket builder ─────────────────────────────────────────────────────

def _build_scalp_ticket(signal: dict, ticker: str) -> dict | None:
    """
    Build a scalp trade ticket with fixed-point SL/T1.
    Resolves the nearest ATM option contract and sets levels.
    """
    try:
        _broker = get_broker()

        opt_type = signal['instrument_type']
        spot = signal['spot']

        # Pick nearest ATM strike
        base = _mkt.nse_base(ticker)

        # Round spot to nearest strike interval
        if 'SENSEX' in base.upper() or 'BSESN' in base.upper() or 'BANKNIFTY' in base.upper():
            strike_interval = 100
        else:
            strike_interval = 50  # NIFTY

        atm_strike = round(spot / strike_interval) * strike_interval

        # Zero-hero 3PM window: use 1-strike OTM (cheaper, higher-gamma lottery) instead
        # of ATM. CE → strike above spot, PE → strike below spot.
        strike = atm_strike
        if _check_zerohero_window() == 'zerohero':
            strike = atm_strike + strike_interval if opt_type == 'CE' else atm_strike - strike_interval
            logger.info(f"[scalp] Zero-hero OTM strike for {ticker} {opt_type}: {strike} (ATM {atm_strike})")

        contract = _broker.resolve_option_contract(ticker, opt_type, strike)
        if not contract:
            logger.debug(f"[scalp] No contract found for {ticker} {opt_type} {atm_strike}")
            return None

        # Get current premium — WS cache first (no REST), REST only as fallback
        trading_symbol = contract.get('trading_symbol') or contract.get('symbol', '')
        premium = _atm_ws_ltp.get(trading_symbol) or contract.get('ltp')
        if not premium or premium <= 0:
            premium = _broker.get_ltp(trading_symbol)
        if not premium or premium <= 0:
            logger.debug(f"[scalp] No premium for {trading_symbol}")
            return None

        # ── Spread / liquidity check ──
        # Use _atm_bid_ask cache (populated every 60s in candle refresh loop) — no REST on hot path.
        # Falls back to _ind_option_quote only when cache is cold (first 60s after start).
        try:
            cached_ba = _atm_bid_ask.get(trading_symbol)
            if cached_ba:
                best_bid, best_ask = cached_ba
            else:
                q = _broker.get_quote(trading_symbol)
                best_bid = float((q or {}).get('bid') or 0)
                best_ask = float((q or {}).get('ask') or 0)
                if best_bid > 0 and best_ask > 0:
                    _atm_bid_ask[trading_symbol] = (best_bid, best_ask)
            if best_bid > 0 and best_ask > 0:
                spread_pct = (best_ask - best_bid) / premium * 100
                max_spread = SCALP_MAX_SPREAD_PCT()
                if spread_pct > max_spread:
                    logger.info(f"[scalp] {trading_symbol} spread {spread_pct:.1f}% > {max_spread}% — skipping (bid={best_bid:.2f} ask={best_ask:.2f})")
                    return None
        except Exception:
            pass

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

        _default_lot = 20 if 'SENSEX' in base.upper() else (30 if 'BANKNIFTY' in base.upper() else 65)
        lot_size = _broker.underlying_lot_size(base) or _default_lot
        if not (1 <= lot_size <= 900):
            logger.warning(f"[scalp] Lot size {lot_size} out of range for {base} — using default {_default_lot}")
            lot_size = _default_lot

        display = contract.get('display_symbol') or trading_symbol

        return {
            'trading_symbol':  trading_symbol,
            'display_symbol':  display,
            'underlying':      ticker,
            'option_type':     opt_type,
            'strike':          strike,
            'strike_price':    strike,
            'expiry':          contract.get('expiry_s', ''),
            'security_id':     contract.get('security_id', ''),
            'lot_size':        lot_size,
            'trade_mode':      'scalp',
            'entry': {
                'expected_premium_inr': premium,
                'index_price':          spot,
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
                'entered_at':      _mkt.now_ist().isoformat(),
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
    """Fetch candles with 5s cache. Live tick overlay on last candle gives real-time signal."""
    cache = _candle_cache.get(ticker)
    now = clock()
    if cache and now - cache.get('fetched_at', 0) < 5:
        return cache.get('candles')
    candles = _fetch_1min_candles(ticker)
    if candles:
        _candle_cache[ticker] = {'candles': candles, 'fetched_at': now}
        return candles
    return cache.get('candles') if cache else None


def _check_zerohero_window() -> str:
    """Check zero-hero 3PM window. Returns: 'normal', 'exit', 'cooldown', 'zerohero', 'final_exit'."""
    global _zerohero_exited
    now = _mkt.now_ist()
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

    try:
        # Day rollover
        _check_scalp_day_rollover()

        # Loss-streak cooldown — direction-specific (CE pause ≠ PE pause)
        _opt_type = signal.get('instrument_type', '')  # 'CE' or 'PE'
        now_epoch = clock()
        if _opt_type:
            _streak_until = _loss_streak_pause_until.get(_opt_type, 0.0)
            if _streak_until and now_epoch < _streak_until:
                _broadcast_status(ticker, 'Loss Streak',
                                  f'3+ {_opt_type} losses in 5min — cooling off {_opt_type}')
                return
            elif _streak_until and now_epoch >= _streak_until:
                _loss_streak_pause_until[_opt_type] = 0.0
                logger.info(f"[scalp] Loss-streak cooldown expired for {_opt_type} — resuming")

        # Kill switch
        if _scalp_kill_switch:
            _broadcast_status(ticker, 'Kill Switch', 'Daily loss limit hit — all entries blocked')
            return

        # Per-ticker re-entry cooldown
        last_exit = _last_exit_time.get(ticker, 0)
        cooldown = SCALP_REENTRY_COOLDOWN()
        remaining = int(cooldown - (clock() - last_exit)) if last_exit else 0
        if last_exit and clock() - last_exit < cooldown:
            _broadcast_status(ticker, 'Cooldown', f'Just exited — waiting {remaining}s before re-entering')
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
        from ..infrastructure.db import tracked_positions as tp
        existing = tp.list_tracked()
        scalp_count = sum(1 for r in existing
                          if (r.get('ticket') or {}).get('trade_mode') == 'scalp')
        if scalp_count >= SCALP_MAX_CONCURRENT():
            _broadcast_status(ticker, 'Slots Full', f'{scalp_count} scalp positions open — max reached')
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
        base = _mkt.nse_base(ticker)
        with _scan_lock:
            _state['stats']['total_signals'] += 1
            _state['signals'].append(signal)
            if len(_state['signals']) > 200:
                _state['signals'] = _state['signals'][-200:]
            _state['latest_by_under'][base] = signal

        _broadcast({'type': 'scalp_signal', **signal})
        logger.info(f"[scalp] SIGNAL: {ticker} {signal['direction']} "
                    f"{signal['instrument_type']} conf={signal['confidence']}% "
                    f"vol_ratio={signal.get('vol_ratio', 0)}")

        # Thesis flip: exit opposite scalp positions on direction change
        try:
            from .order_executor import try_auto_exit
            _broker = get_broker()
            new_opt = signal.get('instrument_type', '')
            opposite_opt = 'PE' if new_opt == 'CE' else 'CE'
            for rec in tp.list_tracked():
                t = rec.get('ticket') or {}
                if t.get('trade_mode') != 'scalp':
                    continue
                if t.get('underlying', '') == ticker and t.get('option_type', '').upper() == opposite_opt:
                    opt_sym = t.get('trading_symbol', '')
                    prem = _broker.get_ltp(opt_sym) or 0
                    logger.warning(f"[scalp] THESIS FLIP: {ticker} was {opposite_opt}, "
                                   f"now {new_opt} — auto-exiting {opt_sym}")
                    try_auto_exit(rec['id'], 'thesis_flip', rec, prem)
        except Exception as e:
            logger.warning(f"[scalp] Thesis flip check failed: {e}")

        # Auto-entry
        try:
            from .order_executor import try_scalp_entry
            result = try_scalp_entry(signal)
            if result:
                global _daily_entry_count
                with _scalp_lock:
                    _daily_entry_count += 1
                    ledger = _scalp_ledger.setdefault(sym_key, {'entries': 0, 'sl_exits': 0})
                    ledger['entries'] += 1
                _persist_state('SCALP_DAILY_ENTRY_COUNT', str(_daily_entry_count))
                logger.info(f"[scalp] Auto-entry placed for {ticker} → {result.get('id')} (day entry #{_daily_entry_count})")
        except Exception as e:
            logger.warning(f"[scalp] Auto-entry failed for {ticker}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"[scalp] Signal processing error for {ticker}: {e}")
    finally:
        with _tick_lock:
            _tick_processing[ticker] = False


_adverse_exit_fired: set[str] = set()  # track_id → already exited by adverse check


def _check_adverse_exit(candles: list[dict], ticker: str, positions: list | None = None):
    """On every tick, check if market conditions have deteriorated (whipsaw/chop)
    for this ticker. If so, force-exit any open scalp position immediately
    instead of waiting for SL to be hit."""
    try:
        from ..infrastructure.db import tracked_positions as tp
        from .order_executor import try_auto_exit

        # Find open scalp positions for this ticker
        scalp_positions = [
            rec for rec in (positions if positions is not None else tp.list_tracked())
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

        # Check if underlying has moved against the position.
        # CE profits when index rises; PE profits when index falls.
        # If index reverses past entry level → thesis is broken → exit.
        current_ltp = float(candles[-1].get('close', 0)) if candles else 0
        for rec in scalp_positions:
            ticket = rec.get('ticket') or {}
            held_opt = ticket.get('option_type', '').upper()
            entry_index = float(ticket.get('entry_index_price') or
                                ticket.get('entry', {}).get('index_price') or 0)
            if not entry_index or not current_ltp or not held_opt:
                continue
            # 30s grace — same as thesis_flip, prevents immediate exit on new entries
            entered = rec.get('entered_at') or rec.get('created_at') or (ticket.get('scalp_meta') or {}).get('entered_at', '')
            if entered:
                try:
                    entry_time = datetime.fromisoformat(entered)
                    _now = _mkt.now_ist()
                    if entry_time.tzinfo is None:
                        _now = _now.replace(tzinfo=None)
                    if (_now - entry_time).total_seconds() < 30:
                        continue
                except Exception:
                    pass
            # How far has the index moved against us?
            if held_opt == 'CE':
                # CE needs index to go UP. If index drops below entry → adverse.
                adverse_pts = entry_index - current_ltp
            else:
                # PE needs index to go DOWN. If index rises above entry → adverse.
                adverse_pts = current_ltp - entry_index
            if adverse_pts > 0:
                # Index has moved against position. Exit if > half the SL points.
                sl_pts = float(ticket.get('exit', {}).get('stop_loss_points') or 15)
                threshold = sl_pts * 0.75
                if adverse_pts >= threshold:
                    sym = ticket.get('trading_symbol', '')
                    pid = rec.get('id')
                    prem = _cached_prem(ticket)
                    if prem <= 0:
                        logger.warning(f"[scalp] ADVERSE MOVE: {sym} — no premium available, skipping exit")
                        continue
                    logger.warning(f"[scalp] ADVERSE MOVE: {ticker} index moved {adverse_pts:.1f} pts "
                                   f"against {held_opt} (entry={entry_index:.1f} now={current_ltp:.1f}, "
                                   f"threshold={threshold:.1f}) — exiting {sym} @ ₹{prem:.2f}")
                    _adverse_exit_fired.add(pid)
                    try_auto_exit(pid, 'adverse_move', rec, prem)

        if flip_count < 7:
            return  # not choppy enough to warrant exit

        # Whipsaw detected with open positions — force exit all
        for rec in scalp_positions:
            ticket = rec.get('ticket') or {}
            sym = ticket.get('trading_symbol', '')
            pid = rec.get('id')
            prem = _cached_prem(ticket)
            if prem <= 0:
                logger.warning(f"[scalp] WHIPSAW EXIT: {sym} — no premium available, skipping exit")
                continue
            logger.warning(f"[scalp] ADVERSE EXIT: {ticker} whipsaw ({flip_count} flips) "
                           f"— force-exiting {sym} @ ₹{prem:.2f} (id={pid})")
            _adverse_exit_fired.add(pid)
            try_auto_exit(pid, 'thesis_flip', rec, prem)
    except Exception as e:
        logger.warning(f"[scalp] adverse exit check failed: {e}")


_thesis_flip_fired: set = set()  # track IDs already flipped (prevent re-fire)

def _check_thesis_flip(candles: list[dict], ticker: str, _ltp: float, positions: list | None = None):
    """Check if EMA direction contradicts held scalp position — exit if so.
    Runs on EVERY tick, independent of signal generation.
    Uses the same _fast_ema() as _detect_momentum for consistency."""
    try:
        from ..infrastructure.db import tracked_positions as tp
        from .order_executor import try_auto_exit
        items = positions if positions is not None else tp.list_tracked()
        scalp_positions = [
            r for r in items
            if (r.get('ticket') or {}).get('trade_mode') == 'scalp'
            and (r.get('ticket') or {}).get('underlying', '') == ticker
            and r.get('id') not in _thesis_flip_fired
        ]
        if not scalp_positions:
            return

        # Use the same EMA function and input as _detect_momentum
        all_closes = [float(c.get('close', 0)) for c in candles]
        ema9 = _fast_ema(all_closes, 9)
        ema21 = _fast_ema(all_closes, 21)
        if ema9 is None or ema21 is None:
            return

        # Require meaningful EMA separation before declaring a flip.
        # Tiny crossovers (< 3 pts on NIFTY) are noise, not a real trend change.
        ema_diff = abs(ema9 - ema21)
        min_flip_pts = 3.0  # minimum EMA gap to confirm flip
        if ema_diff < min_flip_pts:
            return  # too close to call — not a confirmed flip

        # EMA9 > EMA21 → bullish (CE), EMA9 < EMA21 → bearish (PE)
        current_bias = 'CE' if ema9 > ema21 else 'PE'

        for rec in scalp_positions:
            ticket = rec.get('ticket') or {}
            held_opt = ticket.get('option_type', '').upper()
            if not held_opt or held_opt == current_bias:
                continue  # same direction, no flip

            # Don't thesis-flip within first 30s of entry — let the trade breathe
            entered = rec.get('entered_at') or rec.get('created_at') or ''
            if entered:
                try:
                    entry_time = datetime.fromisoformat(entered)
                    now = _mkt.now_ist()
                    # Make both naive or both aware for comparison
                    if entry_time.tzinfo is None:
                        now = now.replace(tzinfo=None)
                    hold_secs = (now - entry_time).total_seconds()
                    if hold_secs < 30:
                        continue  # too soon — skip thesis flip
                except Exception as e:
                    logger.debug(f"[scalp] thesis flip hold-time check error: {e}")
                    continue  # if we can't verify hold time, DON'T flip

            sym = ticket.get('trading_symbol', '')
            pid = rec.get('id')
            prem = _cached_prem(ticket)
            if prem <= 0:
                logger.warning(f"[scalp] THESIS FLIP: {sym} — no premium available, skipping exit")
                continue
            logger.warning(f"[scalp] THESIS FLIP: {ticker} EMA bias now {current_bias} "
                           f"(ema9={ema9:.1f} vs ema21={ema21:.1f}, gap={ema_diff:.1f}), "
                           f"held {held_opt} — auto-exiting {sym} @ ₹{prem:.2f}")
            _thesis_flip_fired.add(pid)
            try_auto_exit(pid, 'thesis_flip', rec, prem)
    except Exception as e:
        logger.debug(f"[scalp] thesis flip check: {e}")


def _on_tick(tick: dict, ticker: str):
    """Live tick callback — instant breakout detection. Called from WS thread.
    MUST stay fast — no REST calls here. All blocking I/O goes to pool threads."""
    if _stop_event.is_set():
        return
    if not _mkt.is_market_hours():
        return

    ltp = float(tick.get('ltp') or 0)
    if not ltp:
        return

    # Use cached candles only — never trigger REST from WS callback thread.
    # Cache is populated by _candle_refresh_loop (60s) and pool threads.
    cache = _candle_cache.get(ticker)
    candles = cache.get('candles') if cache else None
    if not candles or len(candles) < 10:
        return

    # Update last candle with live tick for real-time detection (shallow copy — only mutate last bar)
    live_candles = candles[:]
    live_candles[-1] = dict(candles[-1])
    live_candles[-1]['close'] = ltp
    if ltp > float(live_candles[-1].get('high', 0)):
        live_candles[-1]['high'] = ltp
    if ltp < float(live_candles[-1].get('low', float('inf'))):
        live_candles[-1]['low'] = ltp

    # Read positions once — passed to both checks to avoid 2 separate lock acquisitions
    try:
        from ..infrastructure.db import tracked_positions as _tp_mod
        _tick_positions = _tp_mod.list_tracked() if _scalp_open_count > 0 else []
    except Exception:
        _tick_positions = None

    # ── Adverse-condition exit: detect whipsaw/chop on EVERY tick
    _check_adverse_exit(live_candles, ticker, _tick_positions)

    # ── Thesis flip: if EMA direction contradicts held position, exit
    _check_thesis_flip(live_candles, ticker, ltp, _tick_positions)

    # Gate: read + set _tick_processing in ONE lock acquisition to close the
    # window where two ticks both see False and both submit to the pool.
    with _tick_lock:
        if _tick_processing.get(ticker):
            return
        _tick_processing[ticker] = True

    # Run signal detection with live-updated candles
    try:
        signal = _detect_momentum(live_candles, ticker)
    except Exception as e:
        logger.error(f"[scalp] _detect_momentum crash for {ticker}: {e}")
        signal = None
    if not signal:
        with _tick_lock:
            _tick_processing[ticker] = False
        return

    _signal_pool.submit(_process_signal, ticker, signal, live_candles)


# ── Candle refresh loop (runs once per minute as backup) ─────────────────────

def _candle_refresh_loop():
    """Periodically refresh candles for all tickers. Ensures indicators stay fresh
    even if ticks are sparse. Also handles zero-hero window exits."""
    logger.info("[scalp] Candle refresh loop started")
    while not _stop_event.is_set():
        try:
            if _mkt.is_market_hours():
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
                    except Exception as _rc_e:
                        logger.debug(f"[scalp] Candle refresh failed {ticker}: {_rc_e}")

                # Update ATM option WS subscriptions using fresh spot prices from candle cache
                try:
                    _refresh_atm_subscriptions()
                except Exception:
                    pass

                with _scan_lock:
                    _state['last_scan'] = _mkt.now_ist().isoformat()

                _broadcast({
                    'type': 'scalp_scan_complete',
                    'stats': get_scalp_stats(),
                    'timestamp': _mkt.now_ist().isoformat(),
                })
        except Exception as _loop_e:
            logger.error(f"[scalp] Candle refresh loop error: {_loop_e}", exc_info=True)
        _stop_event.wait(60)


_candle_thread: threading.Thread | None = None
_registered_callbacks: dict[str, object] = {}  # ticker → callback fn
_signal_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='ScalpSignal')


def _register_tick_callbacks():
    """Register live tick callbacks for all universe tickers."""
    _broker = get_broker()
    for ticker in _scalp_universe():
        if ticker in _registered_callbacks:
            continue
        cb = lambda sym, ltp, tick, t=ticker: _on_tick(tick, t)
        _broker.subscribe_ticks([ticker], cb)
        _registered_callbacks[ticker] = cb
        logger.info(f"[scalp] Registered tick callback for {ticker}")


def _unregister_tick_callbacks():
    """Unregister all tick callbacks."""
    _broker = get_broker()
    for ticker in list(_registered_callbacks.keys()):
        try:
            _broker.unsubscribe_ticks([ticker])
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
                                    _signal_pool.submit(_process_signal, ticker, signal, candles)
                except Exception as e:
                    logger.error(f"[scalp] Manual scan error for {ticker}: {e}")

        if not _mkt.is_market_hours():
            secs_to_open = _mkt.seconds_until_market_open()
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
    _reconcile_on_startup()  # sync — must complete before tick callbacks registered (prevents ghost-position exits)
    _restore_scalp_count()   # restore WS SL monitoring for positions that survived restart
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
    s['config'] = {
        'sl_pts':       SCALP_SL_PTS(),
        't1_pts':       SCALP_T1_PTS(),
        'max_hold_min': SCALP_MAX_HOLD_MIN(),
        'daily_loss':   SCALP_DAILY_LOSS(),
        'lots':         SCALP_LOTS_PER_TRADE(),
        'live_trading': _settings.scalp_mode == 'live',
    }
    s['stats'] = get_scalp_stats()
    s['peak_pnl'] = _scalp_peak_pnl
    return s
