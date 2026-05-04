"""
Live (tick-driven) position monitor.

For every open position, subscribes to the IndMoney WebSocket tick stream and
re-evaluates SL / T1 / T2 / trailing-stop / theta-burn on every incoming tick
(typically 1 tick/sec, sub-second when active). Decisions are deterministic
and instant; an LLM commentary loop runs in parallel every 30 s for context.

This sits ON TOP of `position_monitor`'s 1-second safety-net poll. Both write
through the same exit pipeline so there's no double-trade risk: `exit_pending`
flag and per-underlying lock guarantee idempotency.

Public API:
    start()        — begin tick subscriptions for all open positions
    stop()
    sync()         — re-sync subscriptions with current wallet (call after
                     open/close events)
    subscribe_sse() / unsubscribe_sse()  — frontend SSE feed
    last_status()  — latest per-position decision payload (for /api/trade/live-status)
"""
from __future__ import annotations

import json
import os
import queue as _queue
import threading
from datetime import datetime
from typing import Optional

from ..utils.logger import get_logger
from . import broker_utils as bu
from . import greeks as gk
from . import position_monitor as pm

logger = get_logger('phoenixtrade.live_monitor')

# ── State ─────────────────────────────────────────────────────────────────────
_active_subs: dict[str, str] = {}   # pos_key → scrip_code currently subscribed
_state_lock  = threading.Lock()

_status:      dict[str, dict] = {}  # pos_key → latest decision dict
_high_water:  dict[str, float] = {} # pos_key → max premium seen since open

# SSE
_sse_subs: list = []
_sse_lock = threading.Lock()

_running = False


# ── SSE plumbing ──────────────────────────────────────────────────────────────
def subscribe_sse():
    q = _queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_subs.append(q)
    return q


def unsubscribe_sse(q):
    with _sse_lock:
        try: _sse_subs.remove(q)
        except ValueError: pass


def _broadcast(event: dict):
    payload = json.dumps(event, default=str)
    with _sse_lock:
        dead = []
        for q in _sse_subs:
            try: q.put_nowait(payload)
            except _queue.Full: dead.append(q)
        for q in dead:
            try: _sse_subs.remove(q)
            except ValueError: pass


def last_status() -> dict:
    with _state_lock:
        return dict(_status)


# ── Tick handler (HOT PATH — keep this fast) ──────────────────────────────────
def _evaluate_position(pos_key: str, pos: dict, opt_price: float,
                       underlying_price: Optional[float]) -> Optional[dict]:
    """
    Pure function: given a position and a fresh option price, return either:
      * an exit-instruction dict, or
      * a status-update dict (no action), or
      * None if nothing changed.
    Does NOT mutate the wallet — all exits go through pm._do_full_exit /
    _do_partial_exit_ce_pe so locks and idempotency stay centralised.
    """
    avg_entry = pos.get('avg_entry', 0.0)
    if not avg_entry or not opt_price:
        return None

    # Premium-based SL / T1 / T2 (CE/PE only)
    sl_px  = round(avg_entry * 0.50, 2)
    t1_px  = round(avg_entry * 2.00, 2)
    t2_px  = round(avg_entry * 3.00, 2)

    # Trailing stop based on premium high-water-mark, after T1 hit
    hwm = _high_water.get(pos_key, opt_price)
    if opt_price > hwm:
        _high_water[pos_key] = opt_price
        hwm = opt_price
    trail_px = round(hwm * 0.80, 2)   # 20% trail off the peak

    n_ist = bu.now_ist()

    decision: Optional[dict] = None
    if bu.is_force_exit_time():
        decision = {'action': 'EXIT_FULL', 'reason':
                    f"FORCE_EXIT: {n_ist.strftime('%H:%M IST')} ≥ "
                    f"{bu.FORCE_EXIT.strftime('%H:%M')}"}
    elif pm._is_expiry_theta_zone(pos):
        decision = {'action': 'EXIT_FULL',
                    'reason': f"THETA_EXIT: expiry-day after 13:00 IST "
                              f"(premium=₹{opt_price:.2f})"}
    elif opt_price <= sl_px:
        decision = {'action': 'EXIT_FULL',
                    'reason': f"PREMIUM_SL: ₹{opt_price:.2f} ≤ 50% of entry ₹{avg_entry:.2f}"}
    elif opt_price >= t2_px:
        decision = {'action': 'EXIT_FULL',
                    'reason': f"TARGET_2: ₹{opt_price:.2f} = "
                              f"{opt_price/avg_entry:.1f}× entry"}
    elif pos.get('t1_hit') and opt_price <= trail_px:
        decision = {'action': 'EXIT_FULL',
                    'reason': f"TRAIL_SL: ₹{opt_price:.2f} ≤ 20% trail off "
                              f"peak ₹{hwm:.2f}"}
    elif (not pos.get('t1_hit')) and opt_price >= t1_px:
        decision = {'action': 'EXIT_PARTIAL', 'reason': 'TARGET_1: premium 2×'}

    # Greeks snapshot
    g = None
    try:
        if pos.get('strike_price') and underlying_price and pos.get('expiry'):
            exp_d = bu._parse_expiry(pos['expiry'])
            if exp_d:
                dte = max(1, (exp_d - bu.today_ist()).days)
                iv  = gk.implied_vol(underlying_price, float(pos['strike_price']),
                                     dte, opt_price, pos['instrument_type']) \
                       or gk.default_iv(pos.get('underlying',''))
                g = gk.greeks(underlying_price, float(pos['strike_price']),
                              dte, iv, opt_type=pos['instrument_type'])
    except Exception:
        pass

    pnl_pct = ((opt_price - avg_entry) / avg_entry * 100.0) if avg_entry else 0.0
    status = {
        'pos_key':         pos_key,
        'underlying':      pos.get('underlying'),
        'instrument_type': pos.get('instrument_type'),
        'spot':            underlying_price,
        'premium':         round(opt_price, 2),
        'avg_entry':       round(avg_entry, 2),
        'pnl_pct':         round(pnl_pct, 2),
        'high_water':      round(hwm, 2),
        'sl':              sl_px,
        't1':              t1_px,
        't2':              t2_px,
        'trail_sl':        trail_px if pos.get('t1_hit') else None,
        'greeks':          g,
        'decision':        decision,
        'ts':              n_ist.isoformat(),
    }
    return status


def _on_underlying_tick(pos_key: str, pos: dict, opt_secid: str):
    """
    Returns a closure that fires on every underlying-spot tick (for FUT or as
    fallback for CE/PE when the option-leg WebSocket isn't available).
    """
    def _handler(tick: dict):
        try:
            spot = float(tick.get('ltp') or 0)
            if not spot:
                return
            # Pull current option price from broker tick cache (option subscription)
            opt_px = pm._live_option_premium(pos_key) or pos.get('avg_entry', 0)
            _process_decision(pos_key, pos, opt_px, spot)
        except Exception as e:
            logger.warning(f"on_underlying_tick({pos_key}) error: {e}")
    return _handler


def _on_option_tick(pos_key: str, pos: dict):
    def _handler(tick: dict):
        try:
            opt_px = float(tick.get('ltp') or 0)
            if not opt_px:
                return
            spot = pm._live_price(pos.get('underlying') or '')
            _process_decision(pos_key, pos, opt_px, spot)
        except Exception as e:
            logger.warning(f"on_option_tick({pos_key}) error: {e}")
    return _handler


def _process_decision(pos_key: str, pos: dict, opt_px: float,
                      spot: Optional[float]):
    status = _evaluate_position(pos_key, pos, opt_px, spot)
    if not status:
        return

    # Save status
    with _state_lock:
        _status[pos_key] = status

    _broadcast({'type': 'tick_update', **status})

    decision = status.get('decision')
    if not decision:
        return

    # Re-load fresh wallet state to honour any concurrent change
    wallet = pm._load_wallet()
    fresh  = (wallet.get('positions') or {}).get(pos_key)
    if not fresh or fresh.get('exit_pending'):
        return

    live_mode = bu.is_live_mode()
    if decision['action'] == 'EXIT_PARTIAL':
        ok = pm._do_partial_exit_ce_pe(
            wallet, pos_key, fresh, opt_px, spot or 0,
            fresh.get('lot_size', 1), fresh.get('qty', 1), live_mode)
        if ok:
            pm._save_wallet(wallet)
            _broadcast({'type': 'exit_partial', 'pos_key': pos_key,
                        'reason': decision['reason'], 'price': opt_px})
            sync()    # T1 hit means qty changed — refresh subs
    elif decision['action'] == 'EXIT_FULL':
        ok = pm._do_full_exit(
            wallet, pos_key, fresh, opt_px, spot or 0,
            decision['reason'], live_mode, option_premium=opt_px)
        if ok:
            pm._save_wallet(wallet)
            _broadcast({'type': 'exit_full', 'pos_key': pos_key,
                        'reason': decision['reason'], 'price': opt_px})
            sync()    # position closed — drop its subs


# ── Subscription management ───────────────────────────────────────────────────
def sync() -> None:
    """
    Compare currently-open positions against active tick subscriptions; add
    new subs for fresh positions, drop subs for closed ones.
    """
    if not _running:
        return
    try:
        from ..api.indmoney import (
            register_tick_callback, unregister_tick_callback,
        )
    except Exception as e:
        logger.warning(f"live_monitor: indmoney import failed: {e}")
        return

    wallet = pm._load_wallet()
    open_keys = set((wallet.get('positions') or {}).keys())

    with _state_lock:
        prev_keys = set(_active_subs.keys())

    # New positions
    for pos_key in open_keys - prev_keys:
        pos = wallet['positions'][pos_key]
        if pos.get('instrument_type') not in ('CE', 'PE', 'FUT'):
            continue
        sec_id   = pos.get('security_id') or ''
        und_tick = pos.get('underlying') or ''
        try:
            if sec_id:
                # Subscribe to option leg first (premium ticks)
                register_tick_callback(sec_id, _on_option_tick(pos_key, pos))
                with _state_lock:
                    _active_subs[pos_key] = sec_id
                logger.info(f"live_monitor: subscribed {pos_key} (sec_id={sec_id})")
            elif und_tick:
                # Fall back to underlying spot ticks
                register_tick_callback(und_tick,
                                       _on_underlying_tick(pos_key, pos, ''))
                with _state_lock:
                    _active_subs[pos_key] = und_tick
                logger.info(f"live_monitor: subscribed {pos_key} via underlying {und_tick}")
        except Exception as e:
            logger.warning(f"live_monitor: subscribe({pos_key}) failed: {e}")

    # Closed positions
    for pos_key in prev_keys - open_keys:
        with _state_lock:
            code = _active_subs.pop(pos_key, None)
        try:
            if code:
                unregister_tick_callback(code)
            _high_water.pop(pos_key, None)
            _status.pop(pos_key, None)
        except Exception:
            pass


# ── Public lifecycle ──────────────────────────────────────────────────────────
def start():
    global _running
    if _running:
        return
    _running = True
    sync()
    logger.info("live_monitor started")


def stop():
    global _running
    _running = False
    try:
        from ..api.indmoney import unregister_tick_callback
        with _state_lock:
            codes = list(set(_active_subs.values()))
            _active_subs.clear()
        for c in codes:
            try: unregister_tick_callback(c)
            except Exception: pass
    except Exception:
        pass
    logger.info("live_monitor stopped")
