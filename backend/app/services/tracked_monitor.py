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

logger = get_logger('phoenixtrade.tracked_monitor')

# ── Tunables ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_S   = 3.0      # spot fetch cadence per underlying
NEAR_SL_PCT       = 0.10     # within 10% of SL  → 'near_sl' alert
NEAR_T1_PCT       = 0.08     # within 8% of T1   → 'near_t1' alert
FORCE_EXIT_HOUR   = 15
FORCE_EXIT_MINUTE = 0        # 15:00 IST — frontend uses 15:00, mirror here

ALERT_STATES = {'sl_hit', 'past_t1', 'past_t2', 'near_sl', 'near_t1', 'time_exit'}

# ── State ─────────────────────────────────────────────────────────────────────
_status_lock      = threading.Lock()
_last_status:     dict[str, str] = {}        # position_id → last classified status
_last_payload:    dict[str, dict] = {}       # position_id → last full alert payload

_subs_lock        = threading.Lock()
_subscribers:     set[_queue.Queue] = set()  # SSE listeners

_poller_thread:   Optional[threading.Thread] = None
_poller_stop      = threading.Event()

_force_exit_thread: Optional[threading.Thread] = None
_force_exit_stop  = threading.Event()
_force_exit_fired_today = False


# ── SSE plumbing ──────────────────────────────────────────────────────────────
def subscribe_sse() -> _queue.Queue:
    """Frontend opens an SSE connection — returns a Queue to drain."""
    q: _queue.Queue = _queue.Queue(maxsize=200)
    with _subs_lock:
        _subscribers.add(q)
    # Replay current statuses so a reconnecting client immediately sees any
    # position already in an alert state (e.g. SL hit while their tab was closed).
    with _status_lock:
        snapshot = list(_last_payload.values())
    for payload in snapshot:
        try: q.put_nowait(payload)
        except _queue.Full: pass
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
        sl = float(ex.get('stop_loss_inr') or 0)
        t1 = float(ex.get('target_1_inr')  or 0)
        t2 = float(ex.get('target_2_inr')  or 0)

        if sl and prem <= sl:                       return 'sl_hit'
        if t2 and prem >= t2:                       return 'past_t2'
        if t1 and prem >= t1:                       return 'past_t1'
        if sl and prem <= sl * (1.0 + NEAR_SL_PCT): return 'near_sl'
        if t1 and prem >= t1 * (1.0 - NEAR_T1_PCT): return 'near_t1'
        return 'safe'
    except Exception:
        return 'safe'


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
        'timestamp'      : datetime.now().isoformat(),
    }


# ── Poller (event-driven would require WS ticks; we poll every 3s as the
# IndStocks WS doesn't reliably push index ticks — same fallback the SSE stream
# uses). 3s × 2 underlyings = 0.7 req/s — negligible. ─────────────────────────
def _poll_once() -> None:
    """One iteration: fetch spots, reprice all positions, emit transitions."""
    from ..api.indmoney import _ind_ltp

    positions = tp.list_tracked()
    if not positions:
        return

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
            prem = _reprice(t, spot)
            if prem is None:
                continue
            new_status = _classify(prem, t)
            pid        = rec.get('id')
            with _status_lock:
                prev = _last_status.get(pid)
                _last_status[pid] = new_status
                if new_status != prev and new_status in ALERT_STATES:
                    payload = _build_payload(rec, prem, new_status, spot)
                    _last_payload[pid] = payload
                else:
                    payload = None
            if payload:
                logger.info(f"[tracked_monitor] {t.get('trading_symbol')} "
                            f"{prev or 'init'} → {new_status} @ ₹{prem:.2f}")
                _broadcast(payload)


def _poll_loop() -> None:
    logger.info(f"tracked_monitor poller started (interval={POLL_INTERVAL_S}s)")
    while not _poller_stop.is_set():
        try:
            if bu.is_market_hours():
                _poll_once()
        except Exception as e:
            logger.warning(f"poll iteration failed: {e}")
        _poller_stop.wait(POLL_INTERVAL_S)
    logger.info("tracked_monitor poller stopped")


def _force_exit_loop() -> None:
    """One-shot daemon: at 15:00 IST emit time_exit for every open position."""
    global _force_exit_fired_today
    while not _force_exit_stop.is_set():
        now_ist = bu.now_ist()
        # Reset the once-per-day flag at midnight
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
    global _poller_thread, _force_exit_thread

    has_positions = bool(tp.list_tracked())

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
