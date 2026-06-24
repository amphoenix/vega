"""
Tracked-position watcher — backend safety net for manually-pinned positions.

Runs server-side, independent of any browser:
  1. Polls broker for current spot every POLL_INTERVAL_S for each underlying.
  2. Reprices every tracked position via Black-Scholes against the new spot.
  3. Classifies status (safe / near_sl / sl_hit / near_t1 / past_t1 / past_t2 / time_exit).
  4. Broadcasts SSE events on status transitions.
  5. 15:00 IST timer fires time_exit for every open position.

Public API:
    sync()                              — re-scan positions, start/stop poller as needed
    subscribe_sse() / unsubscribe_sse() — for /alerts/stream endpoint
    get_status()                        — current per-position status
"""
from __future__ import annotations

import json
import queue as _queue
import threading
from typing import Optional

from ...domain.services import greeks as gk
from ...infrastructure.db import tracked_positions as tp
from ...shared.logger import get_logger
from ...shared.time import datetime, date, now_ist, today_ist, is_market_hours

logger = get_logger('tracked_monitor')

# ── Tunables ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_S   = 1.0
NEAR_SL_PCT       = 0.10
NEAR_T1_PCT       = 0.08
FORCE_EXIT_HOUR   = 15
FORCE_EXIT_MINUTE = 0

ALERT_STATES = {'sl_hit', 'past_t1', 'past_t2', 'near_sl', 'near_t1', 'time_exit'}

# ── State ─────────────────────────────────────────────────────────────────────
_status_lock      = threading.Lock()
_last_status:     dict[str, str] = {}
_last_payload:    dict[str, dict] = {}
_high_water:      dict[str, float] = {}

_subs_lock        = threading.Lock()
_subscribers:     set[_queue.Queue] = set()

_poller_thread:   Optional[threading.Thread] = None
_poller_stop      = threading.Event()

_force_exit_thread: Optional[threading.Thread] = None
_force_exit_stop  = threading.Event()
_force_exit_fired_today = False

_sync_lock = threading.Lock()


# ── SSE plumbing ──────────────────────────────────────────────────────────────
def subscribe_sse() -> _queue.Queue:
    """Frontend opens an SSE connection — returns a Queue to drain."""
    q: _queue.Queue = _queue.Queue(maxsize=200)
    with _status_lock:
        snapshot = list(_last_payload.values())
    for payload in snapshot:
        try: q.put_nowait(payload)
        except _queue.Full: pass
    with _subs_lock:
        _subscribers.add(q)
    return q


def unsubscribe_sse(q: _queue.Queue) -> None:
    with _subs_lock:
        _subscribers.discard(q)


def _broadcast(payload: dict) -> None:
    """Fan-out an event to every connected SSE listener."""
    with _subs_lock:
        dead = set()
        for q in _subscribers:
            try: q.put_nowait(payload)
            except _queue.Full: dead.add(q)
        _subscribers.difference_update(dead)


def get_status() -> dict:
    """Return per-position last-known status (for diagnostic / replay)."""
    with _status_lock:
        return {pid: dict(p) for pid, p in _last_payload.items()}


# ── Pricing & classification ──────────────────────────────────────────────────
def _parse_expiry(s) -> Optional['date']:
    """Parse many expiry formats → datetime.date."""
    if s is None or s == '':
        return None
    s = str(s).strip()
    if 'T' in s:
        s = s.split('T', 1)[0]
    if ' ' in s and ':' in s:
        s = s.split(' ', 1)[0]
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d %b %Y', '%d %B %Y',
                '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y', '%d%b%y', '%d%b%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s.upper(), fmt).date()
        except Exception:
            continue
    return None


def _reprice(ticket: dict, spot: float) -> Optional[float]:
    """BS-reprice an option ticket at a new spot. Returns premium or None."""
    try:
        K = float(ticket.get('strike') or ticket.get('strike_price') or 0)
        if not K or not spot:
            return None
        opt = (ticket.get('option_type') or
               ('CE' if 'CE' in (ticket.get('trading_symbol') or '') else 'PE')).upper()
        exp_d = _parse_expiry(ticket.get('expiry') or '')
        if not exp_d:
            return None
        dte = max(0.5, (exp_d - today_ist()).days)
        iv = float((ticket.get('greeks') or {}).get('iv_used') or 0.18)
        T = dte / 365.0
        return round(gk.bs_price(float(spot), K, T, gk.RISK_FREE_RATE, iv, opt), 2)
    except Exception as e:
        logger.debug(f"_reprice failed: {e}")
        return None


def _classify(prem: float, ticket: dict) -> str:
    """Map a current premium to a status bucket using the ticket's own levels.

    For LONG positions (options, futures long): SL below entry, targets above.
    For SHORT positions (forex sell): SL above entry, targets below.
    """
    try:
        ex = ticket.get('exit') or {}
        entry = float((ticket.get('entry') or {}).get('expected_premium_inr') or 0)
        sl = float(ex.get('stop_loss_inr') or 0)
        t1 = float(ex.get('target_1_inr')  or 0)
        t2 = float(ex.get('target_2_inr')  or 0)
        is_short = ticket.get('direction') == 'SHORT'

        if is_short:
            # SHORT: SL above entry (price rising = bad), targets below entry
            if sl and prem >= sl:                       return 'sl_hit'
            if t2 and prem <= t2:                       return 'past_t2'
            if t1 and prem <= t1:                       return 'past_t1'
            if sl and entry > 0:
                sl_dist = sl - entry
                if sl_dist > 0 and (prem - entry) / sl_dist >= 0.8: return 'near_sl'
            if t1 and entry > 0:
                t1_dist = entry - t1
                if t1_dist > 0 and (entry - prem) / t1_dist >= 0.9: return 'near_t1'
            return 'safe'

        # LONG (default): SL below entry, targets above
        if sl and prem <= sl:                       return 'sl_hit'
        if t2 and prem >= t2:                       return 'past_t2'
        if t1 and prem >= t1:                       return 'past_t1'
        if sl:
            sl_is_trailed = entry > 0 and sl > entry
            near_pct = 0.03 if sl_is_trailed else NEAR_SL_PCT
            if prem <= sl * (1.0 + near_pct):       return 'near_sl'
        if t1 and prem >= t1 * (1.0 - NEAR_T1_PCT): return 'near_t1'
        return 'safe'
    except Exception:
        return 'safe'


def _trail_sl_and_targets(rec: dict, prem: float) -> None:
    """Trail SL upward as premium rises. Matches old backend logic."""
    import math
    pid = rec.get('id')
    t = rec.get('ticket') or {}
    ex = t.get('exit') or {}
    entry = float((t.get('entry') or {}).get('expected_premium_inr') or 0)
    if not entry or entry <= 0 or not pid:
        return
    # Trailing SL logic only applies to LONG positions (premium going up = good)
    if t.get('direction') == 'SHORT':
        return

    changed = False

    # ── Sanity-cap T1/T2 ──────────────────────────────────────────────────
    dte = max(1, int(float(t.get('days_to_expiry') or 1)))
    dte_scale = min(1.0, math.sqrt(dte / 7.0))
    t1_max_mult = 1.15 + 0.85 * dte_scale
    t2_max_mult = 1.35 + 2.15 * dte_scale
    if dte <= 2:
        t1_max_mult = min(t1_max_mult, 1.30)
        t2_max_mult = min(t2_max_mult, 1.60)

    current_t1 = float(ex.get('target_1_inr') or 0)
    current_t2 = float(ex.get('target_2_inr') or 0)
    capped_t1 = round(entry * t1_max_mult, 2)
    capped_t2 = round(entry * t2_max_mult, 2)

    if current_t1 > capped_t1:
        ex['target_1_inr'] = capped_t1
        changed = True
    if current_t2 > capped_t2:
        ex['target_2_inr'] = capped_t2
        changed = True
    if ex.get('target_2_inr') and ex.get('target_1_inr'):
        if float(ex['target_2_inr']) <= float(ex['target_1_inr']):
            ex['target_2_inr'] = round(float(ex['target_1_inr']) * 1.40, 2)
            changed = True

    # ── High-water mark + trailing SL ─────────────────────────────────────
    prev_hw = _high_water.get(pid, entry)
    hw = max(prev_hw, prem)
    _high_water[pid] = hw

    is_scalp = t.get('trade_mode') == 'scalp' or bool(t.get('scalp_meta'))
    if is_scalp:
        current_sl = float(ex.get('stop_loss_inr') or 0)
        qty = int(rec.get('qty', 1) or 1)
        brokerage_per_unit = 70.0 / max(qty, 1)
        gain_pts = hw - entry

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

        if gain_pts >= step1_trigger and current_sl < step1_sl:
            ex['stop_loss_inr'] = step1_sl
            changed = True
        if gain_pts >= step2_trigger and current_sl < step2_sl:
            ex['stop_loss_inr'] = step2_sl
            changed = True
        if gain_pts >= trail_trigger:
            trail_sl = round(hw - trail_buffer, 2)
            if trail_sl > current_sl:
                ex['stop_loss_inr'] = trail_sl
                changed = True

    # Swing trailing SL (≥20% above entry)
    gain_pct = (hw - entry) / entry if entry else 0
    if not is_scalp and gain_pct >= 0.20:
        underlying = (t.get('underlying') or '').upper()
        is_sensex = 'BSESN' in underlying or 'SENSEX' in underlying
        trail_drop = 50.0 if is_sensex else 15.0

        current_sl = float(ex.get('stop_loss_inr') or 0)
        new_sl = round(max(current_sl, hw - trail_drop), 2)
        if new_sl > current_sl and new_sl > 0:
            ex['stop_loss_inr'] = new_sl
            changed = True

    if changed:
        t['exit'] = ex
        rec['ticket'] = t
        try:
            with tp._lock:
                items = tp._read()
                for item in items:
                    if item.get('id') == pid:
                        item['ticket'] = t
                        break
                tp._write(items)
        except Exception as e:
            logger.warning(f"[trailing] Failed to persist SL/target update: {e}")
        _broadcast({
            'type': 'tracked_update',
            'id': pid,
            'sl':  ex.get('stop_loss_inr'),
            't1':  ex.get('target_1_inr'),
            't2':  ex.get('target_2_inr'),
            'high_water': hw,
            'timestamp': now_ist().isoformat(),
        })


def _build_payload(rec: dict, prem: float, status: str, spot: float) -> dict:
    """Construct a full SSE event payload matching the frontend contract."""
    t = rec.get('ticket') or {}
    entry = float((t.get('entry') or {}).get('expected_premium_inr') or 0)
    is_short = t.get('direction') == 'SHORT'
    # For SHORT: profit when price drops (entry - prem), loss when price rises
    if is_short:
        pnl_pct = ((entry - prem) / entry * 100.0) if entry else 0.0
    else:
        pnl_pct = ((prem - entry) / entry * 100.0) if entry else 0.0
    sl_val = (t.get('exit') or {}).get('stop_loss_inr', 0)
    if is_short:
        msg_map = {
            'sl_hit'   : f"🔴 SL HIT — price ₹{prem:.2f} ≥ SL ₹{sl_val:.2f}. EXIT NOW.",
            'past_t1'  : f"🟢 T1 HIT — price ₹{prem:.2f} ≤ T1. Exit 50% and trail SL.",
            'past_t2'  : f"🟢 T2 HIT — price ₹{prem:.2f} ≤ T2. Full exit.",
            'near_sl'  : f"🟡 NEAR SL — price ₹{prem:.2f} approaching SL.",
            'near_t1'  : f"🔵 NEAR T1 — price ₹{prem:.2f} approaching T1.",
            'time_exit': f"🚨 FORCE EXIT — past 15:00 IST. Close immediately.",
        }
    else:
        msg_map = {
            'sl_hit'   : f"🔴 SL HIT — premium ₹{prem:.2f} ≤ SL ₹{sl_val:.2f}. EXIT NOW.",
            'past_t1'  : f"🟢 T1 HIT — premium ₹{prem:.2f} ≥ T1. Exit 50% and trail SL to breakeven.",
            'past_t2'  : f"🟢 T2 HIT — premium ₹{prem:.2f} ≥ T2. Full exit.",
            'near_sl'  : f"🟡 NEAR SL — premium ₹{prem:.2f} approaching SL.",
            'near_t1'  : f"🔵 NEAR T1 — premium ₹{prem:.2f} approaching T1.",
            'time_exit': f"🚨 FORCE EXIT — past 15:00 IST. Close immediately.",
        }
    return {
        'type'           : 'tracked_alert',
        'id'             : rec.get('id'),
        'trading_symbol' : t.get('trading_symbol'),
        'display_symbol' : t.get('display_symbol') or t.get('trading_symbol'),
        'underlying'     : t.get('underlying'),
        'status'         : status,
        'message'        : msg_map.get(status, ''),
        'premium'        : prem,
        'entry_premium'  : entry,
        'spot'           : float(spot) if spot else None,
        'pnl_pct'        : round(pnl_pct, 2),
        'sl'             : (t.get('exit') or {}).get('stop_loss_inr'),
        't1'             : (t.get('exit') or {}).get('target_1_inr'),
        't2'             : (t.get('exit') or {}).get('target_2_inr'),
        'timestamp'      : now_ist().isoformat(),
    }


# ── Poller ────────────────────────────────────────────────────────────────────
_ws_subscribed_syms: set[str] = set()  # option symbols we've already subscribed to WS

def _poll_once() -> None:
    """One iteration: fetch spots, reprice all positions, emit transitions."""
    from ...dependencies import get_broker

    broker = get_broker()
    positions = tp.list_tracked()
    if not positions:
        return

    # Prune stale entries
    active_ids = {rec.get('id') for rec in positions}
    with _status_lock:
        stale = [pid for pid in _last_status if pid not in active_ids]
        for pid in stale:
            _last_status.pop(pid, None)
            _last_payload.pop(pid, None)
            _high_water.pop(pid, None)

    # Ensure all tracked option symbols have WS subscriptions for real-time ticks
    for rec in positions:
        t = rec.get('ticket') or {}
        opt_sym = t.get('trading_symbol') or ''
        sec_id = t.get('security_id') or ''
        _is_fx = t.get('trade_mode') == 'forex'
        _ws_exch = t.get('exchange', 'CDS') if _is_fx else 'NFO'
        if opt_sym and opt_sym not in _ws_subscribed_syms:
            _ws_subscribed_syms.add(opt_sym)
            try:
                def _make_ws_cb(sym: str):
                    def _cb(_s, ltp, _tick):
                        try:
                            from ..scalp_scanner import _option_ltp_cache
                            _option_ltp_cache[sym.strip().upper()] = ltp
                        except Exception:
                            pass
                    return _cb
                broker.subscribe_ticks(
                    [opt_sym], exchange=_ws_exch,
                    security_id=sec_id,
                    callback=_make_ws_cb(opt_sym),
                )
                logger.info(f"[tracked_monitor] WS subscribed: {opt_sym} (exchange={_ws_exch})")
            except Exception as _ws_e:
                logger.debug(f"[tracked_monitor] WS subscribe failed: {opt_sym}: {_ws_e}")

    # Group positions by underlying
    by_under: dict[str, list[dict]] = {}
    for rec in positions:
        u = (rec.get('ticket') or {}).get('underlying')
        if u: by_under.setdefault(u, []).append(rec)

    for under, recs in by_under.items():
        # Determine exchange from first ticket in group
        _first_t = (recs[0].get('ticket') or {}) if recs else {}
        _is_forex = _first_t.get('trade_mode') == 'forex'

        # Forex futures: no separate "spot" needed — the futures price IS the price.
        # Skip spot requirement entirely; each position fetches its own futures LTP below.
        if _is_forex:
            spot = None  # not used for forex — premium fetched directly per position
        else:
            _spot_exchange = 'NSE'
            _spot_sec_id = None
            spot = broker.get_ltp(under, exchange=_spot_exchange, security_id=_spot_sec_id)
            # Fallback: scalp scanner candle cache (last close)
            if not spot:
                try:
                    from ..scalp_scanner import _candle_cache
                    _cc = _candle_cache.get(under)
                    if _cc and _cc.get('candles'):
                        spot = float(_cc['candles'][-1].get('close', 0)) or None
                except Exception:
                    pass
            if not spot:
                logger.debug(f"[tracked_monitor] No spot for {under} — skipping {len(recs)} positions")
                continue
        for rec in recs:
            t    = rec.get('ticket') or {}
            pid  = rec.get('id')
            is_scalp = t.get('trade_mode') == 'scalp' or bool(t.get('scalp_meta'))
            is_forex = t.get('trade_mode') == 'forex'
            opt_sym  = t.get('trading_symbol') or t.get('display_symbol')

            # Scalp positions with active option WS are monitored tick-by-tick
            # via _on_option_tick — skip REST reprice to avoid redundant calls.
            # Only timeout and force-exit safety nets run here.
            try:
                from ..scalp_scanner import scalp_ws_active as _swa
                _skip_reprice = is_scalp and _swa()
            except Exception:
                _skip_reprice = False

            # ── Force-exit check (always runs, even for WS-active scalps) ──
            try:
                from ..order_executor import is_force_exit_pending, try_auto_exit
                if is_force_exit_pending(pid):
                    _fe_prem = None
                    # Try WS cache first, then REST, then entry premium
                    try:
                        from ..scalp_scanner import _option_ltp_cache as _opt_cache
                        _fe_prem = _opt_cache.get(opt_sym.strip().upper()) if opt_sym else None
                    except Exception:
                        pass
                    if _fe_prem is None and opt_sym:
                        try:
                            _fe_ex = t.get('exchange', 'NFO') if is_forex else 'NFO'
                            _fe_sec = t.get('security_id') if is_forex else None
                            _fe_prem = broker.get_ltp(opt_sym, exchange=_fe_ex,
                                                      security_id=_fe_sec)
                        except Exception:
                            pass
                    if _fe_prem is None:
                        _fe_prem = float((t.get('entry') or {}).get('expected_premium_inr', 0) or 0) or None
                    if _fe_prem is not None:
                        logger.info(f"[tracked_monitor] Force-exit firing: {opt_sym} @ ₹{_fe_prem:.2f}")
                        try_auto_exit(pid, 'force_exit', rec, _fe_prem)
                    else:
                        logger.warning(f"[tracked_monitor] Force-exit: no premium for {opt_sym}")
                    continue
            except Exception as _fe:
                logger.warning(f"[tracked_monitor] force-exit check failed: {_fe}")

            # ── Scalp hold-timeout check (always runs, even for WS-active) ──
            if is_scalp and _skip_reprice:
                try:
                    from ..order_executor import check_scalp_hold_timeout
                    from ..scalp_scanner import _option_ltp_cache as _opt_cache
                    _sto_prem = _opt_cache.get(opt_sym.strip().upper()) if opt_sym else None
                    if _sto_prem is None:
                        _sto_prem = float((t.get('entry') or {}).get('expected_premium_inr', 0) or 0) or None
                    if _sto_prem is not None:
                        check_scalp_hold_timeout(pid, rec, _sto_prem)
                except Exception as _sh:
                    logger.debug(f"[tracked_monitor] scalp timeout check: {_sh}")

            if _skip_reprice:
                continue  # SL/T1 handled by WS ticks — no REST needed

            # ── Full reprice path (swing positions + scalp when WS inactive) ───
            # Price cascade: WS cache (same as frontend) → REST LTP → BS reprice
            prem = None
            # 1. WS option cache — real-time ticks, same source the frontend uses
            if opt_sym:
                try:
                    from ..scalp_scanner import _option_ltp_cache as _opt_cache
                    prem = _opt_cache.get(opt_sym.strip().upper())
                except Exception:
                    pass
            # 2. REST get_ltp — broker API (Dhan)
            if prem is None and opt_sym:
                try:
                    _prem_exchange = t.get('exchange', 'CUR') if is_forex else 'NFO'
                    _prem_sec_id = t.get('security_id') if is_forex else None
                    prem = broker.get_ltp(opt_sym, exchange=_prem_exchange,
                                         security_id=_prem_sec_id)
                except Exception:
                    pass
            # 2b. For forex: try broker get_quote as secondary REST fallback
            if prem is None and is_forex and opt_sym:
                try:
                    _q = broker.get_quote(opt_sym, exchange=t.get('exchange', 'CUR'),
                                         security_id=t.get('security_id', ''))
                    if _q and _q.ltp:
                        prem = float(_q.ltp)
                except Exception:
                    pass
            # 2c. For forex: Yahoo fallback when broker is down
            if prem is None and is_forex:
                try:
                    from ...api.forex import _extract_ticker as _fx_ticker
                    _pair = t.get('underlying', '')
                    if not _pair.endswith('=X'):
                        _pair = _pair + '=X'
                    _fb = _fx_ticker(_pair)
                    if _fb and _fb.get('last'):
                        prem = float(_fb['last'])
                except Exception:
                    pass
            # 3. Black-Scholes reprice from spot (last resort)
            if prem is None and not is_forex:
                prem = _reprice(t, spot)
            if prem is None:
                logger.debug(f"[tracked_monitor] No premium for {opt_sym} — skipping")
                continue

            # Stamp current broker LTP on ticket so frontend uses futures price
            t['_broker_ltp'] = prem
            _trail_sl_and_targets(rec, prem)
            new_status = _classify(prem, t)

            with _status_lock:
                prev = _last_status.get(pid)
                _last_status[pid] = new_status
                if new_status != prev and new_status in ALERT_STATES:
                    payload = _build_payload(rec, prem, new_status, spot)
                    _last_payload[pid] = payload
                else:
                    payload = None

            if payload:
                logger.info(f"[tracked_monitor] {opt_sym} "
                            f"{prev or 'init'} → {new_status} @ ₹{prem:.2f}")
                _broadcast(payload)

            # ── Scalp hold-timeout check ───────────────────────────────
            if is_scalp:
                try:
                    from ..order_executor import check_scalp_hold_timeout
                    if check_scalp_hold_timeout(pid, rec, prem):
                        continue
                except Exception as _sh:
                    logger.debug(f"[tracked_monitor] scalp timeout check: {_sh}")

            # ── Auto-exit retry on EVERY poll ──────────────────────────
            # Run on every poll where status is an exit trigger, not just
            # on transitions. Ensures retries if first attempt failed.
            if new_status in ('sl_hit', 'past_t1', 'past_t2', 'time_exit'):
                try:
                    from ..order_executor import try_auto_exit
                    try_auto_exit(pid, new_status, rec, prem)
                except Exception as _ex:
                    logger.warning(f"[tracked_monitor] auto-exit failed "
                                   f"for {opt_sym}: {_ex}")


def _poll_loop() -> None:
    logger.info(f"tracked_monitor poller started (interval={POLL_INTERVAL_S}s)")
    while not _poller_stop.is_set():
        try:
            if is_market_hours():
                _poll_once()
        except Exception as e:
            logger.warning(f"poll iteration failed: {e}")
        _poller_stop.wait(POLL_INTERVAL_S)
    logger.info("tracked_monitor poller stopped")


def _force_exit_loop() -> None:
    """One-shot daemon: at 15:00 IST emit time_exit for every open position."""
    global _force_exit_fired_today
    while not _force_exit_stop.is_set():
        n = now_ist()
        if n.hour == 0 and _force_exit_fired_today:
            _force_exit_fired_today = False

        if (not _force_exit_fired_today
            and n.hour == FORCE_EXIT_HOUR
            and n.minute >= FORCE_EXIT_MINUTE):
            try:
                from ...dependencies import get_broker
                broker = get_broker()
                positions = tp.list_tracked()
                for rec in positions:
                    t       = rec.get('ticket') or {}
                    u       = t.get('underlying')
                    opt_sym = t.get('trading_symbol') or t.get('display_symbol')
                    spot    = broker.get_ltp(u, exchange='NSE') if u else None
                    prem = None
                    if opt_sym:
                        try:
                            prem = broker.get_ltp(opt_sym, exchange='NFO')
                        except Exception:
                            pass
                    if prem is None:
                        prem = _reprice(t, spot) if spot else None
                    payload = _build_payload(rec, prem or 0.0, 'time_exit', spot or 0.0)
                    pid     = rec.get('id')
                    with _status_lock:
                        _last_status[pid]  = 'time_exit'
                        _last_payload[pid] = payload
                    _broadcast(payload)
                    logger.info(f"[tracked_monitor] FORCE_EXIT 15:00 → {opt_sym}")
                _force_exit_fired_today = True
            except Exception as e:
                logger.warning(f"force-exit broadcast failed: {e}")
        _force_exit_stop.wait(20)


# ── Lifecycle ─────────────────────────────────────────────────────────────────
def sync() -> None:
    """
    Idempotent: ensures poller + force-exit threads running iff positions exist.
    """
    with _sync_lock:
        _sync_inner()


def _sync_inner() -> None:
    global _poller_thread, _force_exit_thread

    positions = tp.list_tracked()
    has_positions = bool(positions)

    if has_positions:
        active_ids = {rec.get('id') for rec in positions}
        with _status_lock:
            stale = [pid for pid in _last_status if pid not in active_ids]
            for pid in stale:
                _last_status.pop(pid, None)
                _last_payload.pop(pid, None)
                _high_water.pop(pid, None)

    if has_positions:
        if _poller_thread is None or not _poller_thread.is_alive():
            _poller_stop.clear()
            _poller_thread = threading.Thread(target=_poll_loop, daemon=True,
                                              name='tracked_monitor.poller')
            _poller_thread.start()
        if _force_exit_thread is None or not _force_exit_thread.is_alive():
            _force_exit_stop.clear()
            _force_exit_thread = threading.Thread(target=_force_exit_loop, daemon=True,
                                                  name='tracked_monitor.force_exit')
            _force_exit_thread.start()
    else:
        with _status_lock:
            _last_status.clear()
            _last_payload.clear()
            _high_water.clear()
