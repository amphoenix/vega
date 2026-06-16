"""
Tracked-position watcher — backend safety net for manually-pinned positions.

PURPOSE
-------
The frontend WATCHING list reprices and alerts on every spot tick — but only
while the browser tab is alive. If the user closes their laptop, switches WiFi,
or backgrounds the tab in a way that throttles JS, alerts can be missed and a
stop-loss can blow through ₹15-30k of premium before they notice.

This module runs server-side, independent of any browser, and:

  1. Polls the broker for current spot every POLL_INTERVAL_S (default 3s) for
     each unique underlying that has at least one tracked position.
  2. Reprices every tracked position via Black-Scholes against the new spot.
  3. Classifies a status — safe / near_sl / sl_hit / near_t1 / past_t1 /
     past_t2 / time_exit — using the ticket's own SL/T1/T2 levels.
  4. On a status TRANSITION into an alert state, broadcasts an SSE event to
     subscribed browsers so they can flash the card, beep, and fire a desktop
     notification (which works even when the tab is hidden).
  5. A one-shot 15:00 IST timer fires a 'time_exit' event for every open
     position even on a flat market with no incoming ticks.

The watcher is event-driven from the position list — it auto-starts when the
first position is pinned and auto-stops (clears state) when the last is exited.

Public API
----------
    sync()                  — re-scan positions, start/stop poller as needed
    subscribe_sse() / unsubscribe_sse(q)  — for the /alerts/stream endpoint
    get_status()            — current per-position status (for /alerts/state)
"""
from __future__ import annotations

import json
import queue as _queue
import threading
import time
from datetime import datetime, time as dtime
from typing import Optional

from . import broker_utils as bu
from . import greeks as gk
from . import tracked_positions as tp
from ..utils.logger import get_logger

logger = get_logger('vega.tracked_monitor')

# ── Tunables ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_S   = 1.0      # spot fetch cadence per underlying
NEAR_SL_PCT       = 0.10     # within 10% of SL  → 'near_sl' alert
NEAR_T1_PCT       = 0.08     # within 8% of T1   → 'near_t1' alert
FORCE_EXIT_HOUR   = 15
FORCE_EXIT_MINUTE = 0        # 15:00 IST — frontend uses 15:00, mirror here

ALERT_STATES = {'sl_hit', 'past_t1', 'past_t2', 'near_sl', 'near_t1', 'time_exit'}

# ── State ─────────────────────────────────────────────────────────────────────
_status_lock      = threading.Lock()
_last_status:     dict[str, str] = {}        # position_id → last classified status
_last_payload:    dict[str, dict] = {}       # position_id → last full alert payload
_high_water:      dict[str, float] = {}      # position_id → highest premium seen

_subs_lock        = threading.Lock()
_subscribers:     set[_queue.Queue] = set()  # SSE listeners

_poller_thread:   Optional[threading.Thread] = None
_poller_stop      = threading.Event()

_force_exit_thread: Optional[threading.Thread] = None
_force_exit_stop  = threading.Event()
_force_exit_fired_today = False

_sync_lock = threading.Lock()  # serialises concurrent sync() calls


# ── SSE plumbing ──────────────────────────────────────────────────────────────
def subscribe_sse() -> _queue.Queue:
    """Frontend opens an SSE connection — returns a Queue to drain."""
    q: _queue.Queue = _queue.Queue(maxsize=200)
    # Snapshot BEFORE adding to subscribers so broadcasts that arrive between
    # snapshot and subscribe don't interleave with replay payloads in the wrong order.
    # Any events fired in that gap are re-broadcast within POLL_INTERVAL_S (3s).
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
def _reprice(ticket: dict, spot: float) -> Optional[float]:
    """BS-reprice an option ticket at a new spot. Returns premium or None."""
    try:
        K = float(ticket.get('strike') or ticket.get('strike_price') or 0)
        if not K or not spot:
            return None
        opt = (ticket.get('option_type') or
               ('CE' if 'CE' in (ticket.get('trading_symbol') or '') else 'PE')).upper()
        exp_d = bu._parse_expiry(ticket.get('expiry') or '')
        if not exp_d:
            return None
        dte = max(0.5, (exp_d - bu.today_ist()).days)
        # Lock IV from the ticket so live repricing doesn't introduce noise.
        # Falls back to a sane default (~18% NIFTY-typical) if absent.
        iv = float((ticket.get('greeks') or {}).get('iv_used') or 0.18)
        T = dte / 365.0
        return round(gk.bs_price(float(spot), K, T, gk.RISK_FREE_RATE, iv, opt), 2)
    except Exception as e:
        logger.debug(f"_reprice failed: {e}")
        return None


def _classify(prem: float, ticket: dict) -> str:
    """Map a current premium to a status bucket using the ticket's own levels."""
    try:
        ex = ticket.get('exit') or {}
        entry = float((ticket.get('entry') or {}).get('expected_premium_inr') or 0)
        sl = float(ex.get('stop_loss_inr') or 0)
        t1 = float(ex.get('target_1_inr')  or 0)
        t2 = float(ex.get('target_2_inr')  or 0)

        if sl and prem <= sl:                       return 'sl_hit'
        if t2 and prem >= t2:                       return 'past_t2'
        if t1 and prem >= t1:                       return 'past_t1'
        # NEAR SL: use tighter threshold when SL has been trailed above entry
        # (position is in profit — don't spam NEAR SL on normal pullbacks)
        if sl:
            sl_is_trailed = entry > 0 and sl > entry
            near_pct = 0.03 if sl_is_trailed else NEAR_SL_PCT
            if prem <= sl * (1.0 + near_pct):       return 'near_sl'
        if t1 and prem >= t1 * (1.0 - NEAR_T1_PCT): return 'near_t1'
        return 'safe'
    except Exception:
        return 'safe'


def _trail_sl_and_targets(rec: dict, prem: float) -> None:
    """
    Dynamically trail SL upward as premium rises, and cap unreasonable targets.

    Rules:
    - Sanity-cap T1/T2: T1 ≤ entry × 2.0, T2 ≤ entry × 3.5 (DTE-scaled).
      Fixes legacy positions with absurd targets (e.g. ₹3,854 on a ₹515 option).
    - Track high-water mark for each position.
    - Once premium is ≥20% above entry: trail SL to (high_water − trail_drop).
      trail_drop = sl_max_points for the underlying (15 for NIFTY, 50 for SENSEX).
    - SL only ratchets UP (never down).
    - Changes are persisted to tracked_positions JSON.
    """
    pid = rec.get('id')
    t = rec.get('ticket') or {}
    ex = t.get('exit') or {}
    entry = float((t.get('entry') or {}).get('expected_premium_inr') or 0)
    if not entry or entry <= 0 or not pid:
        return

    changed = False

    # ── Sanity-cap T1/T2 (fixes legacy positions with planner's loose targets) ─
    # Scale max targets by DTE: shorter expiry → tighter caps.
    # 1DTE: T1 ≤ 1.25× (+25%), T2 ≤ 1.55× (+55%)  — realistic for single-session
    # 3DTE: T1 ≤ 1.45×, T2 ≤ 2.15×
    # 7DTE+: T1 ≤ 2.0×, T2 ≤ 3.5×
    import math
    dte = max(1, int(float(t.get('days_to_expiry') or 1)))
    dte_scale = min(1.0, math.sqrt(dte / 7.0))  # 1DTE→0.38, 3DTE→0.65, 7+→1.0
    t1_max_mult = 1.15 + 0.85 * dte_scale   # 1DTE: 1.47→1.47×  3DTE: 1.70×  7DTE: 2.0×
    t2_max_mult = 1.35 + 2.15 * dte_scale   # 1DTE: 2.17→2.17×  3DTE: 2.75×  7DTE: 3.5×
    # Extra tightening for very short DTE (1-2 days)
    if dte <= 2:
        t1_max_mult = min(t1_max_mult, 1.30)  # max +30% for 1-2 DTE
        t2_max_mult = min(t2_max_mult, 1.60)  # max +60% for 1-2 DTE

    current_t1 = float(ex.get('target_1_inr') or 0)
    current_t2 = float(ex.get('target_2_inr') or 0)
    capped_t1 = round(entry * t1_max_mult, 2)
    capped_t2 = round(entry * t2_max_mult, 2)

    if current_t1 > capped_t1:
        logger.info(f"[trailing] {t.get('trading_symbol')} T1 capped: "
                    f"₹{current_t1:.2f} → ₹{capped_t1:.2f} "
                    f"({t1_max_mult:.2f}× entry, {dte}DTE)")
        ex['target_1_inr'] = capped_t1
        changed = True
    if current_t2 > capped_t2:
        logger.info(f"[trailing] {t.get('trading_symbol')} T2 capped: "
                    f"₹{current_t2:.2f} → ₹{capped_t2:.2f} "
                    f"({t2_max_mult:.2f}× entry, {dte}DTE)")
        ex['target_2_inr'] = capped_t2
        changed = True
    # T2 must be above T1
    if ex.get('target_2_inr') and ex.get('target_1_inr'):
        if float(ex['target_2_inr']) <= float(ex['target_1_inr']):
            ex['target_2_inr'] = round(float(ex['target_1_inr']) * 1.40, 2)
            changed = True

    # ── High-water mark + trailing SL ─────────────────────────────────────
    prev_hw = _high_water.get(pid, entry)
    hw = max(prev_hw, prem)
    _high_water[pid] = hw

    # ── Scalp trailing SL — graduated tightening ──────────────────────────
    is_scalp = t.get('trade_mode') == 'scalp' or bool(t.get('scalp_meta'))
    if is_scalp:
        current_sl = float(ex.get('stop_loss_inr') or 0)
        qty = int(rec.get('qty', 1) or 1)
        brokerage_per_unit = 70.0 / max(qty, 1)
        gain_pts = hw - entry

        underlying = (t.get('underlying') or '').upper()
        is_sensex = 'BSESN' in underlying or 'SENSEX' in underlying

        # Graduated SL steps — give the trade room to breathe
        #   SENSEX: +10pts → reduce risk (entry-15), +20pts → breakeven, +25pts → trail HWM-12
        #   NIFTY:  +5pts  → reduce risk (entry-5),  +10pts → breakeven, +12pts → trail HWM-5
        if is_sensex:
            step1_trigger, step1_sl = 10.0, round(entry - 15.0, 2)
            step2_trigger, step2_sl = 20.0, round(entry + brokerage_per_unit, 2)
            trail_trigger, trail_buffer = 25.0, 12.0
        else:
            step1_trigger, step1_sl = 5.0, round(entry - 5.0, 2)
            step2_trigger, step2_sl = 10.0, round(entry + brokerage_per_unit, 2)
            trail_trigger, trail_buffer = 12.0, 5.0

        # Step 1: Reduce risk — tighten SL but keep room for pullback
        if gain_pts >= step1_trigger and current_sl < step1_sl:
            ex['stop_loss_inr'] = step1_sl
            changed = True
            logger.info(f"[trailing] {t.get('trading_symbol')} SCALP REDUCE-RISK: "
                        f"SL ₹{current_sl:.2f} → ₹{step1_sl:.2f} "
                        f"(HWM ₹{hw:.2f}, +{gain_pts:.1f}pts)")

        # Step 2: Breakeven — lock in zero-loss
        if gain_pts >= step2_trigger and current_sl < step2_sl:
            ex['stop_loss_inr'] = step2_sl
            changed = True
            logger.info(f"[trailing] {t.get('trading_symbol')} SCALP BREAKEVEN: "
                        f"SL ₹{current_sl:.2f} → ₹{step2_sl:.2f} "
                        f"(HWM ₹{hw:.2f}, +{gain_pts:.1f}pts)")

        # Step 3: Trail — lock profits at HWM minus buffer
        if gain_pts >= trail_trigger:
            trail_sl = round(hw - trail_buffer, 2)
            if trail_sl > current_sl:
                ex['stop_loss_inr'] = trail_sl
                changed = True
                logger.info(f"[trailing] {t.get('trading_symbol')} SCALP TRAIL: "
                            f"SL ₹{current_sl:.2f} → ₹{trail_sl:.2f} "
                            f"(HWM ₹{hw:.2f}, locking {gain_pts - trail_buffer:.1f}pts)")

    # Only trail once premium has risen ≥20% above entry (swing trades)
    gain_pct = (hw - entry) / entry
    if not is_scalp and gain_pct >= 0.20:
        from .order_executor import sl_max_points
        underlying = t.get('underlying', '')
        trail_drop = sl_max_points(underlying)

        # New SL = high_water minus trail_drop, but never below current SL
        current_sl = float(ex.get('stop_loss_inr') or 0)
        new_sl = round(max(current_sl, hw - trail_drop), 2)

        if new_sl > current_sl and new_sl > 0:
            ex['stop_loss_inr'] = new_sl
            changed = True
            logger.info(f"[trailing] {t.get('trading_symbol')} SL trailed: "
                        f"₹{current_sl:.2f} → ₹{new_sl:.2f} (HWM=₹{hw:.2f})")

    # Persist if changed
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
        # Push updated levels to frontend via SSE (no polling needed)
        _broadcast({
            'type': 'tracked_update',
            'id': pid,
            'sl':  ex.get('stop_loss_inr'),
            't1':  ex.get('target_1_inr'),
            't2':  ex.get('target_2_inr'),
            'high_water': hw,
            'timestamp': bu.now_ist().isoformat(),
        })


def _build_payload(rec: dict, prem: float, status: str, spot: float) -> dict:
    """Construct a full SSE event payload matching the frontend contract."""
    t = rec.get('ticket') or {}
    entry = float((t.get('entry') or {}).get('expected_premium_inr') or 0)
    pnl_pct = ((prem - entry) / entry * 100.0) if entry else 0.0
    msg_map = {
        'sl_hit'   : f"🔴 SL HIT — premium ₹{prem:.2f} ≤ SL ₹{(t.get('exit') or {}).get('stop_loss_inr', 0):.2f}. EXIT NOW.",
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
        'timestamp'      : bu.now_ist().isoformat(),
    }


# ── Poller (event-driven would require WS ticks; we poll every 3s as the
# IndStocks WS doesn't reliably push index ticks — same fallback the SSE stream
# uses). 3s × 2 underlyings = 0.7 req/s — negligible. ─────────────────────────
def _poll_once() -> None:
    """One iteration: fetch spots, reprice all positions, emit transitions."""
    from ..api.indmoney import _ind_ltp, _ind_option_ltp

    positions = tp.list_tracked()
    if not positions:
        return

    # Prune stale entries from _last_status/_last_payload for removed positions
    active_ids = {rec.get('id') for rec in positions}
    with _status_lock:
        stale = [pid for pid in _last_status if pid not in active_ids]
        for pid in stale:
            _last_status.pop(pid, None)
            _last_payload.pop(pid, None)
            _high_water.pop(pid, None)

    # Group positions by underlying so we fetch each spot only once.
    by_under: dict[str, list[dict]] = {}
    for rec in positions:
        u = (rec.get('ticket') or {}).get('underlying')
        if u: by_under.setdefault(u, []).append(rec)

    for under, recs in by_under.items():
        spot = _ind_ltp(under)
        if not spot:
            continue
        for rec in recs:
            t    = rec.get('ticket') or {}
            pid  = rec.get('id')
            is_scalp = t.get('trade_mode') == 'scalp' or bool(t.get('scalp_meta'))
            opt_sym  = t.get('trading_symbol') or t.get('display_symbol')

            # Scalp positions with active option WS are monitored tick-by-tick via
            # _on_option_tick — skip the REST LTP poll and SL/T1 classification here
            # to avoid duplicated work and unnecessary broker calls. Force-exit retries
            # and hold-timeout checks still run as safety nets.
            try:
                from .scalp_scanner import scalp_ws_active as _swa
                _skip_reprice = is_scalp and _swa()
            except Exception:
                _skip_reprice = False

            # ── Force-exit check (always runs, even for WS-active scalps) ──────
            try:
                from .order_executor import is_force_exit_pending, try_auto_exit
                if is_force_exit_pending(pid):
                    if _skip_reprice:
                        # Need a premium for the exit — fetch once
                        _fe_prem = None
                        try:
                            _fe_prem = _ind_option_ltp(opt_sym) if opt_sym else None
                        except Exception:
                            pass
                        if _fe_prem is not None:
                            try_auto_exit(pid, 'force_exit', rec, _fe_prem)
                    # Non-skip path handled below
                    continue
            except Exception as _fe:
                logger.warning(f"[tracked_monitor] force-exit check failed: {_fe}")

            # ── Scalp hold-timeout check (always runs, even for WS-active scalps) ──
            if is_scalp and _skip_reprice:
                try:
                    from .order_executor import check_scalp_hold_timeout
                    _sto_prem = None
                    _t = rec.get('ticket') or {}
                    _opt_code = _t.get('opt_code', '')
                    if _opt_code:
                        try:
                            from .scalp_scanner import _option_ltp_cache as _opt_cache
                            _sto_prem = _opt_cache.get(_opt_code.strip().upper())
                        except Exception:
                            pass
                    if _sto_prem is None:
                        _sto_prem = float((_t.get('entry') or {}).get('expected_premium_inr', 0) or 0) or None
                    if _sto_prem is not None:
                        check_scalp_hold_timeout(pid, rec, _sto_prem)
                except Exception as _sh:
                    logger.debug(f"[tracked_monitor] scalp timeout check: {_sh}")

            if _skip_reprice:
                # Scalp with active WS — skip full reprice cycle, continue to next position
                continue

            # ── Full reprice path (swing positions + scalp when WS inactive) ───
            # Prefer live option LTP over BS model — BS can diverge wildly
            # from real market prices, causing false SL/T1 alerts.
            prem = None
            prem_source = 'none'
            if opt_sym:
                try:
                    prem = _ind_option_ltp(opt_sym)
                    if prem is not None:
                        prem_source = 'live_ltp'
                except Exception as e:
                    logger.debug(f"[tracked_monitor] live LTP failed for {opt_sym}: {e}")
            if prem is None:
                prem = _reprice(t, spot)
                if prem is not None:
                    prem_source = 'bs_model'
            if prem is None:
                continue
            # Trail SL upward as premium rises (before classify so SL is current)
            _trail_sl_and_targets(rec, prem)
            new_status = _classify(prem, t)
            logger.debug(f"[tracked_monitor] {opt_sym} prem=₹{prem:.2f} "
                         f"src={prem_source} status={new_status} "
                         f"SL=₹{(t.get('exit') or {}).get('stop_loss_inr', 0)}")
            with _status_lock:
                prev = _last_status.get(pid)
                _last_status[pid] = new_status
                if new_status != prev and new_status in ALERT_STATES:
                    payload = _build_payload(rec, prem, new_status, spot)
                    _last_payload[pid] = payload
                else:
                    payload = None
            # Force-exit (non-skip path)
            try:
                from .order_executor import is_force_exit_pending, try_auto_exit
                if is_force_exit_pending(pid):
                    try_auto_exit(pid, 'force_exit', rec, prem)
                    continue
            except Exception as _fe:
                logger.warning(f"[tracked_monitor] force-exit check failed: {_fe}")

            if payload:
                logger.info(f"[tracked_monitor] {opt_sym} "
                            f"{prev or 'init'} → {new_status} @ ₹{prem:.2f} "
                            f"(src={prem_source})")
                _broadcast(payload)

            # ── Scalp hold-timeout check ───────────────────────────────
            if is_scalp:
                try:
                    from .order_executor import check_scalp_hold_timeout
                    if check_scalp_hold_timeout(pid, rec, prem):
                        continue
                except Exception as _sh:
                    logger.debug(f"[tracked_monitor] scalp timeout check: {_sh}")

            # ── Auto-exit via order executor ──────────────────────────
            # Run on EVERY poll where status is an exit trigger, not just
            # on transitions. This ensures exit retries if the first
            # attempt failed (e.g. network error, paper-mode crash).
            if new_status in ('sl_hit', 'past_t1', 'past_t2', 'time_exit'):
                try:
                    from .order_executor import try_auto_exit
                    try_auto_exit(pid, new_status, rec, prem)
                except Exception as _ex:
                    logger.warning(f"[tracked_monitor] auto-exit failed "
                                   f"for {opt_sym}: {_ex}")

            # ── Retry pending failed exits ──────────────────────────────
            # If a previous exit failed at the broker, keep retrying.
            # Must add to _force_exit_ids so try_auto_exit's is_forced check passes.
            try:
                from .order_executor import _exit_retries
                if pid in _exit_retries:
                    from .order_executor import try_auto_exit, force_exit as _queue_force
                    _queue_force(pid)  # add to _force_exit_ids so is_forced=True
                    logger.info(f"[tracked_monitor] {opt_sym} retrying pending exit (broker failed earlier)")
                    try_auto_exit(pid, 'force_exit', rec, prem)
            except Exception as _re:
                logger.debug(f"[tracked_monitor] exit retry check: {_re}")

    # ── Push live P&L to frontend via SSE (replaces frontend polling) ─────
    try:
        from .order_executor import get_daily_pnl, is_kill_switch_active, daily_loss_limit
        _broadcast({
            'type': 'daily_pnl',
            'realized': round(get_daily_pnl(), 2),
            'kill_switch': is_kill_switch_active(),
            'limit': daily_loss_limit(),
            'timestamp': bu.now_ist().isoformat(),
        })
    except Exception:
        pass


_funds_last_push: float = 0.0
_FUNDS_PUSH_INTERVAL: float = 60.0  # push funds update every 60s

def _push_funds_update() -> None:
    """Periodically broadcast available cash so the frontend stays in sync."""
    global _funds_last_push
    now = time.time()
    if now - _funds_last_push < _FUNDS_PUSH_INTERVAL:
        return
    _funds_last_push = now
    try:
        from ..api.indmoney import _ind_available_cash
        cash = _ind_available_cash()
        _broadcast({
            'type': 'funds_update',
            'available_cash': cash,
            'timestamp': bu.now_ist().isoformat(),
        })
    except Exception as e:
        logger.debug(f"funds_update broadcast failed: {e}")


def _poll_loop() -> None:
    logger.info(f"tracked_monitor poller started (interval={POLL_INTERVAL_S}s)")
    while not _poller_stop.is_set():
        try:
            _push_funds_update()
            if bu.is_market_hours():
                _poll_once()
        except Exception as e:
            logger.warning(f"poll iteration failed: {e}")
        _poller_stop.wait(POLL_INTERVAL_S)
    logger.info("tracked_monitor poller stopped")


def _force_exit_loop() -> None:
    """One-shot daemon: at 15:00 IST emit time_exit for every open position."""
    global _force_exit_fired_today
    _midnight_reset_fired = False
    while not _force_exit_stop.is_set():
        now_ist = bu.now_ist()
        # Auto daily reset at midnight — zeroes P&L, restores loss limits to base
        if now_ist.hour == 0 and not _midnight_reset_fired:
            try:
                from .order_executor import reset_daily
                from .scalp_scanner import reset_scalp_daily
                reset_daily()
                reset_scalp_daily()
                logger.info("[monitor] Midnight daily reset fired")
            except Exception as _e:
                logger.warning(f"[monitor] Midnight reset failed: {_e}")
            _midnight_reset_fired = True
        elif now_ist.hour != 0:
            _midnight_reset_fired = False

        if now_ist.hour == 0 and _force_exit_fired_today:
            _force_exit_fired_today = False

        if (not _force_exit_fired_today
            and now_ist.hour == FORCE_EXIT_HOUR
            and now_ist.minute >= FORCE_EXIT_MINUTE):
            try:
                positions = tp.list_tracked()
                from ..api.indmoney import _ind_ltp
                for rec in positions:
                    t    = rec.get('ticket') or {}
                    u    = t.get('underlying')
                    spot = _ind_ltp(u) if u else None
                    prem = _reprice(t, spot) if spot else None
                    payload = _build_payload(rec, prem or 0.0, 'time_exit', spot or 0.0)
                    pid     = rec.get('id')
                    with _status_lock:
                        _last_status[pid]  = 'time_exit'
                        _last_payload[pid] = payload
                    _broadcast(payload)
                    logger.info(f"[tracked_monitor] FORCE_EXIT 15:00 → {t.get('trading_symbol')}")
                    # Auto-exit order for 15:00 force close
                    try:
                        from .order_executor import try_auto_exit
                        try_auto_exit(pid, 'time_exit', rec, prem or 0.0)
                    except Exception as _te:
                        logger.warning(f"[tracked_monitor] 15:00 auto-exit failed: {_te}")
                _force_exit_fired_today = True
            except Exception as e:
                logger.warning(f"force-exit broadcast failed: {e}")
        _force_exit_stop.wait(20)   # check every 20s — precision is enough for 15:00


# ── Lifecycle (called from tracked_positions.add/remove and app boot) ────────
def sync() -> None:
    """
    Idempotent: ensures the poller + force-exit threads are running iff there
    is at least one tracked position. Call after every add/remove.
    """
    with _sync_lock:
        _sync_inner()


def _sync_inner() -> None:
    global _poller_thread, _force_exit_thread

    positions = tp.list_tracked()
    has_positions = bool(positions)

    # Always prune stale IDs (position was removed but others remain)
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
        # No active positions — let the threads idle out. We don't kill them
        # since sync() is called frequently; they'll exit on next stop signal.
        with _status_lock:
            _last_status.clear()
            _last_payload.clear()
            _high_water.clear()
