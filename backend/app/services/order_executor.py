"""
Order Executor — auto-entry and auto-exit engine for F&O trades.

Entry:
    Called by fo_scanner when a signal has confidence ≥ AUTO_ENTRY_MIN_CONFIDENCE.
    Places a BUY MARKET order via /order/fo, auto-tracks the position, and
    broadcasts the result via SSE (snackbar).

Exit:
    Called by tracked_monitor when a status transitions to sl_hit / past_t1 /
    past_t2 / time_exit.  Places a SELL MARKET order to close the position.
    - sl_hit    → full exit (100% qty)
    - past_t1   → partial exit (50%), trail SL to breakeven
    - past_t2   → full exit
    - time_exit → full exit (15:00 IST)

Strict stop-loss enforcement:
    Entry premium minus a max point limit (configurable per underlying).
    SENSEX: max 50 points | NIFTY: max 15 points.
    If the planner's computed SL is looser, we clamp it tighter.

Re-entry:
    After SL exit, if the signal is still valid (same cycle or next cycle
    still produces conf ≥ threshold), re-entry is attempted up to
    MAX_REENTRIES_PER_DAY per symbol.

Safety:
    All real orders go through /order/fo which is gated by LIVE_TRADING_ENABLED.
    When false, the executor still runs the logic but orders are paper-simulated.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger('phoenixtrade.services.order_executor')

# ── Config helpers ────────────────────────────────────────────────────────────

def _cfg_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _cfg_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def auto_trading_enabled() -> bool:
    """Master toggle for auto-entry. When False, scanner still runs but no orders."""
    return os.environ.get('AUTO_TRADING_ENABLED', 'true').strip().lower() in ('true', '1', 'yes')


def min_confidence() -> int:
    return _cfg_int('AUTO_ENTRY_MIN_CONFIDENCE', 95)


def sl_max_points(underlying: str) -> float:
    """Return the max SL drop in premium points for the given underlying."""
    u = (underlying or '').upper().replace('.NS', '').replace('^', '')
    if 'SENSEX' in u or 'BSESN' in u or 'BANKEX' in u:
        return _cfg_float('SL_MAX_POINTS_SENSEX', 50.0)
    # NIFTY, BANKNIFTY, FINNIFTY — all use the NIFTY limit
    return _cfg_float('SL_MAX_POINTS_NIFTY', 15.0)


def allow_reentry() -> bool:
    return os.environ.get('ALLOW_REENTRY_AFTER_SL', '1').strip() == '1'


def max_reentries() -> int:
    return _cfg_int('MAX_REENTRIES_PER_DAY', 1)


def lots_per_trade() -> int:
    """Number of lots to buy per trade (1 lot = exchange lot size)."""
    return _cfg_int('FO_LOTS_PER_TRADE', 1)


def daily_loss_limit() -> float:
    """Max cumulative realized loss (INR) before all trading stops for the day."""
    return _cfg_float('DAILY_LOSS_LIMIT_INR', 1000.0)


# ── State ─────────────────────────────────────────────────────────────────────
_lock = threading.Lock()

# Track entries placed today to enforce re-entry limits.
# Key: trading_symbol, Value: { 'entries': int, 'sl_exits': int, 'last_entry_id': str }
_daily_ledger: dict[str, dict] = {}

# Positions where we've already fired an exit order (avoid duplicate exits).
# Key: tracked_position_id, Value: exit_type (sl_hit, past_t1, past_t2, time_exit)
_exit_fired: dict[str, str] = {}

# Positions where partial (T1) exit has been done — remaining qty tracked here.
# Key: tracked_position_id
_partial_exited: set[str] = set()

# Daily realized P&L tracker (INR). Negative = loss.
_daily_realized_pnl: float = 0.0

# Kill-switch: set to True when daily loss limit breached. No more trades today.
_daily_kill_switch: bool = False

# Override: user-blocked symbols that should NOT auto-enter this session.
_blocked_symbols: set[str] = set()

# Override: user-forced exit IDs (frontend can push a force-exit).
_force_exit_ids: set[str] = set()


def reset_daily():
    """Called at midnight IST (or on restart) to reset daily state."""
    global _daily_realized_pnl, _daily_kill_switch
    with _lock:
        _daily_ledger.clear()
        _exit_fired.clear()
        _partial_exited.clear()
        _force_exit_ids.clear()
        _daily_realized_pnl = 0.0
        _daily_kill_switch = False
    logger.info("[executor] Daily state reset (P&L zeroed, kill-switch off)")


def block_symbol(symbol: str):
    """User override: prevent auto-entry for this symbol this session."""
    _blocked_symbols.add(symbol.strip().upper())
    logger.info(f"[executor] Blocked auto-entry for {symbol}")


def unblock_symbol(symbol: str):
    _blocked_symbols.discard(symbol.strip().upper())
    logger.info(f"[executor] Unblocked auto-entry for {symbol}")


def force_exit(track_id: str):
    """User override: force-exit a tracked position on next poll cycle."""
    _force_exit_ids.add(track_id)
    logger.info(f"[executor] Force-exit queued for {track_id}")


def is_force_exit_pending(track_id: str) -> bool:
    return track_id in _force_exit_ids


def record_exit_pnl(entry_premium: float, exit_premium: float, qty: int):
    """Record realized P&L from an exit. Triggers kill-switch if limit breached."""
    global _daily_realized_pnl, _daily_kill_switch
    pnl = (exit_premium - entry_premium) * qty
    with _lock:
        _daily_realized_pnl += pnl
        logger.info(f"[executor] Exit P&L: ₹{pnl:+.2f} | "
                    f"Daily total: ₹{_daily_realized_pnl:+.2f} | "
                    f"Limit: -₹{daily_loss_limit():.0f}")
        if _daily_realized_pnl <= -daily_loss_limit():
            _daily_kill_switch = True
            logger.warning(f"[executor] 🛑 DAILY LOSS LIMIT HIT: ₹{_daily_realized_pnl:.2f} "
                           f"exceeds -₹{daily_loss_limit():.0f}. NO MORE TRADES TODAY.")
            try:
                from ..api.indmoney import _order_broadcast
                _order_broadcast({
                    'type': 'order_update', 'severity': 'error',
                    'title': '🛑 DAILY LOSS LIMIT HIT',
                    'status': 'KILL_SWITCH',
                    'message': (f"Total loss ₹{abs(_daily_realized_pnl):.0f} "
                                f"≥ limit ₹{daily_loss_limit():.0f}. "
                                f"All auto-trading stopped for today."),
                    'timestamp': datetime.now().isoformat(),
                })
            except Exception:
                pass


def is_kill_switch_active() -> bool:
    return _daily_kill_switch


def get_daily_pnl() -> float:
    return _daily_realized_pnl


# ── Strict SL enforcement ────────────────────────────────────────────────────

def enforce_strict_sl(ticket: dict) -> dict:
    """
    Clamp the ticket's stop_loss_inr so it never exceeds
    SL_MAX_POINTS from entry premium.

    Mutates and returns the ticket.
    """
    underlying = ticket.get('underlying', '')
    entry = ticket.get('entry', {})
    exit_  = ticket.get('exit', {})
    prem = float(entry.get('expected_premium_inr', 0) or 0)
    if not prem:
        return ticket

    max_pts = sl_max_points(underlying)
    strict_sl = round(prem - max_pts, 2)
    strict_sl = max(strict_sl, 0.05)  # never negative / zero

    current_sl = float(exit_.get('stop_loss_inr', 0) or 0)

    # If current SL is looser (lower) than strict, clamp up
    if current_sl < strict_sl:
        old_sl = current_sl
        exit_['stop_loss_inr'] = strict_sl
        ticket['exit'] = exit_
        logger.info(f"[executor] Strict SL: {ticket.get('trading_symbol')} "
                    f"SL clamped {old_sl:.2f} → {strict_sl:.2f} "
                    f"(entry={prem:.2f}, max_drop={max_pts}pts)")

    return ticket


# ── Auto-entry ────────────────────────────────────────────────────────────────

def try_auto_entry(signal: dict) -> Optional[dict]:
    """
    Called by fo_scanner after a signal passes all gates.
    Returns the tracked record if entry was placed, else None.

    Conditions:
      1. confidence ≥ AUTO_ENTRY_MIN_CONFIDENCE
      2. Signal has a valid ticket with trading_symbol
      3. Symbol not blocked by user override
      4. Re-entry limit not exceeded
      5. Not already tracking this symbol
    """
    # ── Monitor-only mode: scanner runs, signals display, no orders ──
    if not auto_trading_enabled():
        logger.info(f"[executor] MONITOR ONLY — skipping auto-entry for {signal.get('ticker')} "
                    f"(AUTO_TRADING_ENABLED=false)")
        return None

    # ── Kill-switch: daily loss limit breached → no more entries ──
    if _daily_kill_switch:
        logger.warning(f"[executor] BLOCKED {signal.get('ticker')} — "
                       f"daily loss limit hit (₹{_daily_realized_pnl:+.2f})")
        return None

    conf = int(signal.get('confidence', 0))
    threshold = min_confidence()
    ticket = signal.get('ticket')
    sym = (ticket or {}).get('trading_symbol', '').strip().upper()

    if conf < threshold:
        logger.debug(f"[executor] Skip {signal.get('ticker')} — conf {conf}% < {threshold}%")
        return None

    if not ticket or not sym:
        logger.debug(f"[executor] Skip {signal.get('ticker')} — no executable ticket")
        return None

    if sym in _blocked_symbols:
        logger.info(f"[executor] Skip {sym} — blocked by user override")
        return None

    # ── Reject entries when planner says AVOID (late session, theta cliff) ──
    entry_window = (ticket.get('entry') or {}).get('window_ist', '')
    if 'AVOID' in entry_window.upper():
        from ..api.indmoney import _order_broadcast
        logger.info(f"[executor] Skip {sym} — planner says AVOID: {entry_window}")
        _order_broadcast({
            'type': 'order_update', 'severity': 'warning',
            'title': f"Skipped: {ticket.get('display_symbol', sym)}",
            'status': 'WINDOW_REJECT', 'symbol': sym,
            'txn_type': 'BUY',
            'message': f"Entry window: {entry_window}",
            'timestamp': datetime.now().isoformat(),
        })
        return None

    # Check re-entry limits
    with _lock:
        ledger = _daily_ledger.get(sym, {'entries': 0, 'sl_exits': 0})
        if ledger['sl_exits'] > 0 and not allow_reentry():
            logger.info(f"[executor] Skip {sym} — re-entry disabled after SL exit")
            return None
        if ledger['entries'] >= (max_reentries() + 1):  # initial + re-entries
            logger.info(f"[executor] Skip {sym} — max entries ({ledger['entries']}) reached today")
            return None

    # Check not already tracking
    from . import tracked_positions as tp
    existing = tp.list_tracked()
    for rec in existing:
        t = (rec.get('ticket') or {}).get('trading_symbol', '').strip().upper()
        if t == sym:
            logger.info(f"[executor] Skip {sym} — already tracked (id={rec.get('id')})")
            return None

    # Enforce strict SL before entry
    ticket = enforce_strict_sl(ticket)

    # ── Affordability check: if primary ticket too expensive, try alts ────
    _paper = not (os.environ.get('LIVE_TRADING_ENABLED', 'false')
                  .strip().lower() in ('true', '1', 'yes'))
    if _paper:
        _avail = float(os.environ.get('PAPER_CAPITAL_INR', '100000'))
    else:
        try:
            from ..api.indmoney import _ind_available_cash
            _avail = _ind_available_cash() or 0
        except Exception:
            _avail = 0
    _risk_pct = float(os.environ.get('FO_MAX_RISK_PCT', '2.0'))
    _risk_limit = _avail * (_risk_pct / 100.0)
    # Use strict SL for max_loss (enforce_strict_sl already applied above)
    _lot = int(ticket.get('lot_size', 1) or 1)
    _strict_sl_pts = sl_max_points(ticket.get('underlying', ''))
    _max_loss = _strict_sl_pts * _lot  # actual risk with enforced SL

    if _max_loss > _risk_limit and signal.get('cio', {}).get('alt_tickets'):
        logger.info(f"[executor] Primary ticket {sym} max_loss ₹{_max_loss:.0f} > "
                    f"limit ₹{_risk_limit:.0f} — checking alternatives")
        for alt in signal['cio']['alt_tickets']:
            # Alt max_loss with strict SL = sl_max_points × lot_size
            alt_lot = int(alt.get('lot_size', 1) or 1)
            alt_loss = _strict_sl_pts * alt_lot
            if alt_loss <= _risk_limit and alt.get('_full_ticket'):
                alt_ticket = enforce_strict_sl(alt['_full_ticket'])
                alt_sym = alt_ticket.get('trading_symbol', '').strip().upper()
                logger.info(f"[executor] Switching to cheaper alt: {alt_sym} "
                            f"(max_loss ₹{alt_loss:.0f} ≤ ₹{_risk_limit:.0f})")
                ticket = alt_ticket
                sym = alt_sym
                break
        else:
            logger.info(f"[executor] No affordable alternative for {signal.get('ticker')}")
            return None

    # Place the BUY order — qty = lot_size × FO_LOTS_PER_TRADE
    lot_size = int(ticket.get('lot_size', 1) or 1)
    num_lots = lots_per_trade()
    qty = lot_size * num_lots
    trading_symbol = ticket.get('trading_symbol', '')

    # ── Slippage guard: reject if current price moved too far from signal ────
    # Max allowed slippage (default 5%). If price moved more than this from
    # the signal price, the R:R is broken — skip the trade.
    _max_slippage_pct = float(os.environ.get('MAX_ENTRY_SLIPPAGE_PCT', '5.0'))
    signal_price = float(ticket['entry']['expected_premium_inr'])
    try:
        from ..api.indmoney import _ind_ltp
        current_price = _ind_ltp(trading_symbol)
    except Exception:
        current_price = None

    if current_price and signal_price > 0:
        slippage_pct = abs(current_price - signal_price) / signal_price * 100
        if slippage_pct > _max_slippage_pct:
            from ..api.indmoney import _order_broadcast
            logger.warning(f"[executor] SKIPPED {trading_symbol} — price moved "
                           f"₹{signal_price:.2f} → ₹{current_price:.2f} "
                           f"({slippage_pct:.1f}% > {_max_slippage_pct}% max slippage)")
            _order_broadcast({
                'type': 'order_update', 'severity': 'warning',
                'title': f"Skipped: {ticket.get('display_symbol', trading_symbol)}",
                'status': 'SLIPPAGE_REJECT', 'symbol': trading_symbol,
                'txn_type': 'BUY',
                'message': f"Price moved {slippage_pct:.1f}% from signal (₹{signal_price:.0f}→₹{current_price:.0f}). R:R invalid.",
                'timestamp': datetime.now().isoformat(),
            })
            # Clear ticket cache so next scan gets a fresh signal at the new price
            try:
                from .fo_scanner import _ticket_cache
                for k in list(_ticket_cache.keys()):
                    if trading_symbol in str(_ticket_cache[k].get('trading_symbol', '')):
                        del _ticket_cache[k]
            except Exception:
                pass
            return None

    logger.info(f"[executor] AUTO-ENTRY: BUY {trading_symbol} qty={qty} "
                f"({num_lots} lot(s) × {lot_size}) "
                f"conf={conf}% (threshold={threshold}%)")

    from ..api.indmoney import _order_broadcast
    order_result = _place_fo_buy(trading_symbol, qty)

    if order_result and order_result.get('success'):
        # In live mode, use actual market price for entry + SL/T1/T2
        _is_live = os.environ.get('LIVE_TRADING_ENABLED', 'false').strip().lower() in ('true', '1', 'yes')
        if _is_live and current_price and current_price > 0:
            old_entry = ticket['entry']['expected_premium_inr']
            ticket['entry']['expected_premium_inr'] = current_price
            max_pts = sl_max_points(ticket.get('underlying', ''))
            ticket['exit']['stop_loss_inr'] = round(max(current_price - max_pts, 0.05), 2)
            if old_entry > 0:
                ratio = current_price / old_entry
                ticket['exit']['target_1_inr'] = round(ticket['exit']['target_1_inr'] * ratio, 2)
                ticket['exit']['target_2_inr'] = round(ticket['exit']['target_2_inr'] * ratio, 2)
            logger.info(f"[executor] Live fill: entry ₹{old_entry:.2f} → ₹{current_price:.2f}, "
                        f"SL=₹{ticket['exit']['stop_loss_inr']:.2f}")

        # Track the position
        record = tp.add_tracked(ticket, qty=qty,
                                notes=f"Auto-entry conf={conf}% | {signal.get('ticker')}")

        with _lock:
            ledger = _daily_ledger.setdefault(sym, {'entries': 0, 'sl_exits': 0})
            ledger['entries'] += 1
            ledger['last_entry_id'] = record.get('id')

        _order_broadcast({
            'type': 'order_update', 'severity': 'success',
            'title': f"Auto-BUY: {ticket.get('display_symbol', trading_symbol)}",
            'status': 'ENTRY_PLACED', 'symbol': trading_symbol,
            'txn_type': 'BUY', 'qty': qty,
            'message': f"Confidence {conf}% — SL ₹{ticket['exit']['stop_loss_inr']:.2f}",
            'timestamp': datetime.now().isoformat(),
        })
        logger.info(f"[executor] Entry success: {trading_symbol} tracked as {record.get('id')}")
        return record
    else:
        err = (order_result or {}).get('error', 'Unknown error')
        _order_broadcast({
            'type': 'order_update', 'severity': 'error',
            'title': f"Auto-BUY FAILED: {trading_symbol}",
            'status': 'ENTRY_FAILED', 'symbol': trading_symbol,
            'txn_type': 'BUY', 'qty': qty,
            'message': str(err),
            'timestamp': datetime.now().isoformat(),
        })
        logger.error(f"[executor] Entry failed: {trading_symbol} — {err}")
        return None


# ── Auto-exit ─────────────────────────────────────────────────────────────────

def try_auto_exit(track_id: str, status: str, rec: dict,
                  premium: float) -> bool:
    """
    Called by tracked_monitor on status transitions.
    Returns True if an exit order was placed.

    Exit rules:
      - sl_hit    → SELL 100% qty
      - past_t2   → SELL 100% qty
      - past_t1   → SELL 50% qty, trail SL to breakeven
      - time_exit → SELL 100% qty
      - force_exit (user override) → SELL 100% qty
    """
    from ..api.indmoney import _order_broadcast

    # Check for user force-exit override
    is_forced = track_id in _force_exit_ids

    if not is_forced and status not in ('sl_hit', 'past_t1', 'past_t2', 'time_exit', 'thesis_flip'):
        return False

    # Avoid duplicate full exits
    with _lock:
        prev_exit = _exit_fired.get(track_id)
        if prev_exit in ('sl_hit', 'past_t2', 'time_exit', 'force_exit', 'thesis_flip'):
            return False  # already fully exited

        # For T1: only fire once
        if status == 'past_t1' and track_id in _partial_exited:
            return False

    ticket = rec.get('ticket') or {}
    sym = ticket.get('trading_symbol', '')
    total_qty = int(rec.get('qty', 1) or 1)
    lot_size = int(ticket.get('lot_size', 1) or 1)

    if is_forced:
        exit_type = 'force_exit'
        exit_qty = total_qty
    elif status == 'past_t1':
        # Partial exit: 50% qty (rounded to lot size)
        half = max(total_qty // 2, lot_size)
        # Make sure it's a multiple of lot_size
        exit_qty = (half // lot_size) * lot_size
        if exit_qty < lot_size:
            exit_qty = lot_size
        exit_type = 'past_t1'
    else:
        # Full exit for sl_hit, past_t2, time_exit
        exit_qty = total_qty
        exit_type = status

    display = ticket.get('display_symbol', sym)
    logger.info(f"[executor] AUTO-EXIT ({exit_type}): SELL {display} qty={exit_qty} "
                f"premium=₹{premium:.2f}")

    order_result = _place_fo_sell(sym, exit_qty)

    if order_result and order_result.get('success'):
        with _lock:
            if exit_type == 'past_t1':
                _partial_exited.add(track_id)
                _exit_fired[track_id] = 'past_t1'
            else:
                _exit_fired[track_id] = exit_type

            if is_forced:
                _force_exit_ids.discard(track_id)

            # Track SL exits for re-entry limit
            if exit_type == 'sl_hit':
                ledger = _daily_ledger.get(sym.upper(), {'entries': 0, 'sl_exits': 0})
                ledger['sl_exits'] += 1
                _daily_ledger[sym.upper()] = ledger

        # ── Record realized P&L and check daily loss limit ───────────
        entry_prem = float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
        if entry_prem > 0:
            record_exit_pnl(entry_prem, premium, exit_qty)

        severity_map = {
            'sl_hit': 'error',
            'past_t1': 'info',
            'past_t2': 'success',
            'time_exit': 'warning',
            'force_exit': 'warning',
            'thesis_flip': 'warning',
        }
        title_map = {
            'sl_hit': f"🔴 SL EXIT: {display}",
            'past_t1': f"🟢 T1 PARTIAL EXIT: {display}",
            'past_t2': f"🟢 T2 FULL EXIT: {display}",
            'time_exit': f"🚨 TIME EXIT: {display}",
            'force_exit': f"⚡ FORCE EXIT: {display}",
            'thesis_flip': f"🔄 THESIS FLIP EXIT: {display}",
        }

        _order_broadcast({
            'type': 'order_update',
            'severity': severity_map.get(exit_type, 'info'),
            'title': title_map.get(exit_type, f"EXIT: {display}"),
            'status': exit_type.upper(),
            'symbol': sym,
            'txn_type': 'SELL', 'qty': exit_qty,
            'message': f"Premium ₹{premium:.2f} — {exit_type.replace('_', ' ')}",
            'timestamp': datetime.now().isoformat(),
        })

        # For full exits, remove from tracked positions
        if exit_type != 'past_t1':
            from . import tracked_positions as tp
            tp.remove_tracked(track_id, exit_premium=premium, exit_reason=exit_type)
            logger.info(f"[executor] {display} removed from tracked (exit={exit_type})")
            # Clear ticket cache so re-entry gets a fresh price
            try:
                from .fo_scanner import _ticket_cache
                underlying = (ticket.get('underlying') or '').strip()
                opt_type = (ticket.get('option_type') or '').strip().upper()
                _ck = f"{underlying}:{opt_type}"
                if _ck in _ticket_cache:
                    del _ticket_cache[_ck]
                    logger.info(f"[executor] Ticket cache cleared for {_ck} (post-exit)")
            except Exception:
                pass
        else:
            # T1 partial: update remaining qty and trail SL to breakeven
            _trail_sl_to_breakeven(rec, exit_qty)
            logger.info(f"[executor] {display} T1 partial exit done, "
                        f"trailing SL to breakeven, remaining qty")

        return True
    else:
        err = (order_result or {}).get('error', 'Unknown error')
        _order_broadcast({
            'type': 'order_update', 'severity': 'error',
            'title': f"EXIT FAILED: {display}",
            'status': 'EXIT_FAILED', 'symbol': sym,
            'txn_type': 'SELL', 'qty': exit_qty,
            'message': f"{exit_type}: {err}",
            'timestamp': datetime.now().isoformat(),
        })
        logger.error(f"[executor] Exit failed: {sym} ({exit_type}) — {err}")
        return False


# ── Trail SL to breakeven after T1 partial exit ──────────────────────────────

def _trail_sl_to_breakeven(rec: dict, exited_qty: int):
    """After T1 partial exit, update the tracked position:
    - Reduce qty by exited_qty
    - Set SL to entry premium (breakeven)
    """
    from . import tracked_positions as tp
    track_id = rec.get('id')
    total_qty = int(rec.get('qty', 1) or 1)
    remaining = total_qty - exited_qty

    with tp._lock:
        items = tp._read()
        for item in items:
            if item.get('id') == track_id:
                item['qty'] = max(remaining, 0)
                ticket = item.get('ticket', {})
                entry_prem = float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
                if entry_prem > 0:
                    exit_ = ticket.get('exit', {})
                    exit_['stop_loss_inr'] = entry_prem  # breakeven
                    ticket['exit'] = exit_
                    item['ticket'] = ticket
                item['notes'] = (item.get('notes', '') +
                                 f" | T1 exit {exited_qty}qty, SL→breakeven ₹{entry_prem:.2f}")
                break
        tp._write(items)

    logger.info(f"[executor] {track_id} SL trailed to breakeven, remaining qty={remaining}")


# ── Internal order helpers ────────────────────────────────────────────────────

def _place_fo_buy(trading_symbol: str, qty: int) -> dict:
    """Place a BUY MARKET order for an F&O instrument."""
    try:
        from flask import Flask
        from ..api.indmoney import (
            _live_trading_enabled, _connected, _resolve_fo_instrument,
            _fo_scrip_code, _headers, _order_broadcast, BASE_URL,
        )
        import requests as _requests

        if not _connected():
            return {'success': False, 'error': 'INDMONEY_ACCESS_TOKEN not set'}

        if not _live_trading_enabled():
            logger.info(f"[executor] PAPER BUY: {trading_symbol} qty={qty}")
            _order_broadcast({
                'type': 'order_update', 'severity': 'warning',
                'title': f"Paper BUY: {trading_symbol}",
                'status': 'SIMULATED', 'symbol': trading_symbol,
                'txn_type': 'BUY', 'qty': qty,
                'message': 'LIVE_TRADING_ENABLED=false — order not sent',
                'timestamp': datetime.now().isoformat(),
            })
            return {'success': True, 'data': {'order_id': 'PAPER', 'status': 'SIMULATED'}}

        inst = _resolve_fo_instrument(trading_symbol)
        if not inst:
            return {'success': False, 'error': f'Instrument not found: {trading_symbol}'}

        sec_id = (inst.get('SECURITY_ID') or '').strip()
        exch = (inst.get('EXCH') or 'NSE').strip().upper()
        exchange = 'BSE' if exch.startswith('B') else 'NSE'
        algo_id = '9999999999999999' if exchange == 'BSE' else '99999'

        payload = {
            'txn_type': 'BUY',
            'exchange': exchange,
            'segment': 'DERIVATIVE',
            'product': 'MARGIN',
            'order_type': 'MARKET',
            'validity': 'DAY',
            'security_id': sec_id,
            'qty': qty,
            'is_amo': False,
            'algo_id': algo_id,
        }

        r = _requests.post(f'{BASE_URL}/order', headers=_headers(),
                           json=payload, timeout=10)
        return {'success': r.ok, 'data': r.json() if r.ok else r.text}
    except Exception as e:
        logger.error(f"[executor] _place_fo_buy error: {e}")
        return {'success': False, 'error': str(e)}


def _place_fo_sell(trading_symbol: str, qty: int) -> dict:
    """Place a SELL MARKET order for an F&O instrument."""
    try:
        from ..api.indmoney import (
            _live_trading_enabled, _connected, _resolve_fo_instrument,
            _fo_scrip_code, _headers, _order_broadcast, BASE_URL,
        )
        import requests as _requests

        if not _connected():
            return {'success': False, 'error': 'INDMONEY_ACCESS_TOKEN not set'}

        if not _live_trading_enabled():
            logger.info(f"[executor] PAPER SELL: {trading_symbol} qty={qty}")
            _order_broadcast({
                'type': 'order_update', 'severity': 'warning',
                'title': f"Paper SELL: {trading_symbol}",
                'status': 'SIMULATED', 'symbol': trading_symbol,
                'txn_type': 'SELL', 'qty': qty,
                'message': 'LIVE_TRADING_ENABLED=false — order not sent',
                'timestamp': datetime.now().isoformat(),
            })
            return {'success': True, 'data': {'order_id': 'PAPER', 'status': 'SIMULATED'}}

        inst = _resolve_fo_instrument(trading_symbol)
        if not inst:
            return {'success': False, 'error': f'Instrument not found: {trading_symbol}'}

        sec_id = (inst.get('SECURITY_ID') or '').strip()
        exch = (inst.get('EXCH') or 'NSE').strip().upper()
        exchange = 'BSE' if exch.startswith('B') else 'NSE'
        algo_id = '9999999999999999' if exchange == 'BSE' else '99999'

        payload = {
            'txn_type': 'SELL',
            'exchange': exchange,
            'segment': 'DERIVATIVE',
            'product': 'MARGIN',
            'order_type': 'MARKET',
            'validity': 'DAY',
            'security_id': sec_id,
            'qty': qty,
            'is_amo': False,
            'algo_id': algo_id,
        }

        r = _requests.post(f'{BASE_URL}/order', headers=_headers(),
                           json=payload, timeout=10)
        return {'success': r.ok, 'data': r.json() if r.ok else r.text}
    except Exception as e:
        logger.error(f"[executor] _place_fo_sell error: {e}")
        return {'success': False, 'error': str(e)}
