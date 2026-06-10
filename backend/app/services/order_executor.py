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

from . import broker_utils as bu
from ..utils.logger import get_logger

logger = get_logger('vega.services.order_executor')

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
    """Master toggle for swing auto-entry. When False, scanner still runs but no orders."""
    return os.environ.get('AUTO_TRADING_ENABLED', 'true').strip().lower() in ('true', '1', 'yes')


def scalp_auto_trading_enabled() -> bool:
    """Separate toggle for scalp auto-entry. Falls back to AUTO_TRADING_ENABLED if not set."""
    val = os.environ.get('SCALP_AUTO_TRADING_ENABLED', '').strip().lower()
    if val in ('true', '1', 'yes'):
        return True
    if val in ('false', '0', 'no'):
        return False
    return auto_trading_enabled()  # fallback to shared toggle


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

# Track SL exits by underlying+direction to block same-direction re-entry.
# Key: "UNDERLYING:CE" or "UNDERLYING:PE", Value: count of SL exits
_sl_direction_ledger: dict[str, int] = {}
_MAX_SL_PER_DIRECTION = 1  # After 1 SL hit on same underlying+direction, block re-entry

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

def _load_trading_state() -> None:
    """Load persisted runtime state (bumped limits) from SQLite into os.environ on server start.
    Only applies the DB value for same-day restarts; new-day resets happen in _restore_daily_pnl."""
    try:
        from .pnl_store import get_trading_state, last_trade_date
        today_str = bu.now_ist().strftime('%Y-%m-%d')
        last_date = last_trade_date()
        if last_date and last_date < today_str:
            # New day — ignore stale DB value, will be reset in _restore_daily_pnl
            logger.info(f"[executor] New day (last trade {last_date}) — ignoring stale DB limit")
            return
        val = get_trading_state('DAILY_LOSS_LIMIT_INR')
        if val:
            os.environ['DAILY_LOSS_LIMIT_INR'] = val
            logger.info(f"[executor] Loaded trading_state: DAILY_LOSS_LIMIT_INR={val}")
    except Exception as e:
        logger.warning(f"[executor] Could not load trading state from DB: {e}")


_load_trading_state()


def _persist_state(key: str, value: str) -> None:
    """Persist runtime state to SQLite trading_state table (replaces .env writes)."""
    try:
        from .pnl_store import set_trading_state
        set_trading_state(key, value)
    except Exception as e:
        logger.warning(f"[executor] Could not persist {key} to DB: {e}")


# Base loss limit as set in .env (never bumped) — restored every new day
_DAILY_LOSS_LIMIT_BASE: float = _cfg_float('DAILY_LOSS_LIMIT_BASE', 1000.0)

# Override: user-blocked symbols that should NOT auto-enter this session.
_blocked_symbols: set[str] = set()

# Override: user-forced exit IDs (frontend can push a force-exit).
_force_exit_ids: set[str] = set()

# In-flight BUY symbols: prevents TOCTOU duplicate orders when two scanner
# cycles evaluate the same symbol concurrently and both pass the re-entry check.
_inflight: set[str] = set()

# Track which IST date the current in-memory P&L belongs to.
# If the date rolls over (server stays up past midnight without _force_exit_loop
# firing, or midnight reset thread not started yet), we detect it here.
_pnl_date: str = bu.now_ist().strftime('%Y-%m-%d')


def _check_day_rollover() -> None:
    """If IST date changed since last trade, auto-reset daily state + limits."""
    global _pnl_date
    today = bu.now_ist().strftime('%Y-%m-%d')
    if today != _pnl_date:
        logger.info(f"[executor] Day rollover detected ({_pnl_date} → {today}) — resetting")
        reset_daily()
        _pnl_date = today


def _restore_daily_pnl() -> None:
    """On server start: detect new day from last trade date, restore limit if needed,
    then reload today's net swing P&L from SQLite."""
    global _daily_realized_pnl, _daily_kill_switch
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    today_str = _dt.now(_tz(_td(hours=5, minutes=30))).strftime('%Y-%m-%d')

    try:
        from .pnl_store import today_net_by_mode, last_trade_date
        last_date = last_trade_date()
        if last_date and last_date < today_str:
            # Trades exist but last one was before today → new day, restore limit to base
            base = _DAILY_LOSS_LIMIT_BASE
            os.environ['DAILY_LOSS_LIMIT_INR'] = str(int(base))
            _persist_state('DAILY_LOSS_LIMIT_INR', str(int(base)))
            logger.info(f"[executor] New day detected (last trade {last_date}) — swing limit restored to ₹{base:.0f}")
            return  # P&L is 0, kill switch off
        # Same day or no trades ever → restore today's net P&L
        restored = today_net_by_mode().get('swing', 0.0)
        if restored != 0.0:
            _daily_realized_pnl = restored
            if _daily_realized_pnl <= -daily_loss_limit():
                _daily_kill_switch = True
                logger.warning(f"[executor] Kill switch re-armed on boot: ₹{_daily_realized_pnl:.2f}")
            logger.info(f"[executor] Restored daily swing net P&L: ₹{_daily_realized_pnl:+.2f}")
    except Exception as _e:
        logger.warning(f"[executor] Could not restore daily P&L: {_e}")

_restore_daily_pnl()


def reset_daily():
    """Called at midnight IST to reset daily state. Restores loss limit to startup base."""
    global _daily_realized_pnl, _daily_kill_switch
    with _lock:
        _daily_ledger.clear()
        _sl_direction_ledger.clear()
        _exit_fired.clear()
        _partial_exited.clear()
        _force_exit_ids.clear()
        _inflight.clear()
        _daily_realized_pnl = 0.0
        _daily_kill_switch = False
    # Restore limit to base
    base = _DAILY_LOSS_LIMIT_BASE
    os.environ['DAILY_LOSS_LIMIT_INR'] = str(int(base))
    _persist_state('DAILY_LOSS_LIMIT_INR', str(int(base)))
    logger.info(f"[executor] Daily state reset — swing limit restored to ₹{base:.0f}")


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


def record_exit_pnl(entry_premium: float, exit_premium: float, qty: int,
                    net_pnl: Optional[float] = None):
    """Record realized P&L from an exit. Pass net_pnl (after brokerage) when available.
    Triggers kill-switch if limit breached."""
    global _daily_realized_pnl, _daily_kill_switch
    _check_day_rollover()
    pnl = net_pnl if net_pnl is not None else (exit_premium - entry_premium) * qty
    with _lock:
        _daily_realized_pnl += pnl
        logger.info(f"[executor] Exit P&L (net): ₹{pnl:+.2f} | "
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
                    'timestamp': bu.now_ist().isoformat(),
                })
            except Exception:
                pass


def is_kill_switch_active() -> bool:
    return _daily_kill_switch


def reset_kill_switch() -> float:
    """Re-enable swing auto-trading after kill-switch fires, without zeroing P&L.
    Also bumps the daily loss limit by ₹500 and persists it to SQLite. Returns the new limit."""
    global _daily_kill_switch
    with _lock:
        _daily_kill_switch = False
    current = daily_loss_limit()
    new_limit = current + 500
    os.environ['DAILY_LOSS_LIMIT_INR'] = str(new_limit)
    _persist_state('DAILY_LOSS_LIMIT_INR', str(int(new_limit)))
    logger.info(f"[executor] Swing kill switch reset — limit bumped ₹{current:.0f} → ₹{new_limit:.0f}")
    return new_limit


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
    # ── Day rollover: reset limits if we crossed midnight ──
    _check_day_rollover()

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
            'timestamp': bu.now_ist().isoformat(),
        })
        return None

    # ── Direction-based re-entry block: if we got stopped out on
    #    the same underlying+direction (e.g. NIFTY CE), block any
    #    new CE entry for NIFTY regardless of strike. Prevents
    #    re-entering a losing direction with a different strike.
    _underlying = (ticket or {}).get('underlying', '')
    _opt_type = (ticket or {}).get('option_type', '').upper()
    if _underlying and _opt_type in ('CE', 'PE'):
        _dir_key = f"{_underlying}:{_opt_type}"
        with _lock:
            _dir_sl_count = _sl_direction_ledger.get(_dir_key, 0)
        if _dir_sl_count >= _MAX_SL_PER_DIRECTION:
            logger.warning(f"[executor] BLOCKED {sym} — {_dir_key} already hit SL "
                           f"{_dir_sl_count}x today. No more {_opt_type} entries for {_underlying}.")
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

    # ── Daily budget guard: reject if worst-case SL loss exceeds remaining budget ──
    _sl_pts = sl_max_points(ticket.get('underlying', ''))
    _worst_case_loss = (_sl_pts * qty) + 70   # +70 for brokerage
    headroom = daily_loss_limit() + _daily_realized_pnl
    if _worst_case_loss > headroom:
        logger.warning(f"[executor] BUDGET BLOCK {sym} — worst-case loss ₹{_worst_case_loss:.0f} "
                       f"> headroom ₹{headroom:.0f} (P&L ₹{_daily_realized_pnl:+.0f})")
        return None

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
                'timestamp': bu.now_ist().isoformat(),
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

    # Guard against TOCTOU: two concurrent scanner cycles passing all checks
    # and both placing a BUY before either records the entry in the ledger.
    with _lock:
        if sym in _inflight:
            logger.info(f"[executor] Skip {sym} — BUY already in-flight")
            return None
        _inflight.add(sym)

    logger.info(f"[executor] AUTO-ENTRY: BUY {trading_symbol} qty={qty} "
                f"({num_lots} lot(s) × {lot_size}) "
                f"conf={conf}% (threshold={threshold}%)")

    from ..api.indmoney import _order_broadcast
    try:
        order_result = _place_fo_buy(trading_symbol, qty)
    finally:
        with _lock:
            _inflight.discard(sym)

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
            'timestamp': bu.now_ist().isoformat(),
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
            'timestamp': bu.now_ist().isoformat(),
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
                # Also track by underlying+direction to block same-direction re-entry
                _und = ticket.get('underlying', '')
                _otype = ticket.get('option_type', '').upper()
                if _und and _otype in ('CE', 'PE'):
                    _dk = f"{_und}:{_otype}"
                    _sl_direction_ledger[_dk] = _sl_direction_ledger.get(_dk, 0) + 1
                    logger.warning(f"[executor] SL direction ledger: {_dk} = "
                                   f"{_sl_direction_ledger[_dk]} (max {_MAX_SL_PER_DIRECTION})")

        # ── Record realized P&L and check daily loss limit ───────────
        entry_prem = float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
        is_scalp = bool(ticket.get('trade_mode') == 'scalp' or ticket.get('scalp_meta'))
        if entry_prem > 0:
            gross_pnl = (premium - entry_prem) * exit_qty
            # Compute net P&L (brokerage deducted) — in-memory trackers and kill switch use net
            try:
                from .pnl_store import _calc_brokerage
                brokerage = _calc_brokerage(entry_prem, premium, exit_qty)
            except Exception:
                brokerage = 0.0
            net_pnl_amt = round(gross_pnl - brokerage, 2)
            if is_scalp:
                # Route to scalp-specific P&L tracker
                try:
                    from .scalp_scanner import record_scalp_pnl
                    hold_sec = 0
                    try:
                        entered = ticket.get('scalp_meta', {}).get('entered_at', '')
                        if entered:
                            hold_sec = (bu.now_ist() - datetime.fromisoformat(entered)).total_seconds()
                    except Exception:
                        pass
                    record_scalp_pnl(net_pnl_amt, hold_sec, exit_reason=status,
                                     underlying=ticket.get('underlying', ''))
                    logger.info(f"[executor] Scalp P&L: gross ₹{gross_pnl:+.2f} "
                                f"brokerage ₹{brokerage:.2f} net ₹{net_pnl_amt:+.2f} "
                                f"(hold {hold_sec:.0f}s)")
                except Exception as _e:
                    logger.warning(f"[executor] record_scalp_pnl failed: {_e}")
                try:
                    from .pnl_store import record_trade as _rec_pnl
                    _rec_pnl('scalp', sym, ticket.get('underlying', ''),
                             entry_prem, premium, exit_qty,
                             int(ticket.get('lot_size', 1) or 1), exit_type)
                except Exception as _pe:
                    logger.debug(f"[executor] pnl_store scalp record failed: {_pe}")
            else:
                # Swing P&L — pass net_pnl so kill switch threshold uses net
                record_exit_pnl(entry_prem, premium, exit_qty, net_pnl_amt)
                try:
                    from .pnl_store import record_trade as _rec_pnl
                    _rec_pnl('swing', sym, ticket.get('underlying', ''),
                             entry_prem, premium, exit_qty,
                             int(ticket.get('lot_size', 1) or 1), exit_type)
                except Exception as _pe:
                    logger.debug(f"[executor] pnl_store swing record failed: {_pe}")

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
            'timestamp': bu.now_ist().isoformat(),
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
            'timestamp': bu.now_ist().isoformat(),
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


# ── Scalp entry ──────────────────────────────────────────────────────────────

def try_scalp_entry(signal: dict) -> Optional[dict]:
    """
    Called by scalp_scanner when a momentum signal fires.
    Uses scalp-specific gates: separate kill-switch, higher re-entry limits.
    Returns the tracked record if entry was placed, else None.
    """
    if not scalp_auto_trading_enabled():
        logger.info(f"[executor] SCALP MONITOR ONLY — skipping scalp entry for {signal.get('ticker')}")
        return None

    # Scalp kill-switch — only honour the flag, not the raw P&L.
    # record_scalp_pnl() already sets the flag when limit is hit (inside _scalp_lock).
    # Re-checking _pnl here would defeat a manual reset_kill_switch() call because
    # the P&L is still negative after reset — that's intentional (user acknowledged loss).
    from . import scalp_scanner as _scalp_mod
    with _scalp_mod._scalp_lock:
        _ks = _scalp_mod._scalp_kill_switch
        _pnl = _scalp_mod._scalp_daily_pnl
    if _ks:
        logger.warning(f"[executor] SCALP BLOCKED {signal.get('ticker')} — "
                       f"kill switch active (daily P&L ₹{_pnl:+.0f})")
        return None

    # Scalp headroom guard — block entry if remaining budget too thin
    _scalp_limit = _scalp_mod.SCALP_DAILY_LOSS()
    _scalp_headroom = _scalp_limit + _pnl
    if _scalp_headroom < 50:
        logger.warning(f"[executor] SCALP BUDGET BLOCK — only ₹{_scalp_headroom:.0f} headroom "
                       f"(P&L ₹{_pnl:+.0f}, limit ₹{_scalp_limit:.0f})")
        return None

    ticket = signal.get('ticket')
    if not ticket:
        logger.info(f"[executor] Scalp skip {signal.get('ticker')} — no ticket")
        return None

    sym = (ticket.get('trading_symbol') or '').strip().upper()
    if not sym:
        logger.info(f"[executor] Scalp skip {signal.get('ticker')} — empty trading_symbol")
        return None

    conf = int(signal.get('confidence', 0))

    # Check not already tracking this symbol
    from . import tracked_positions as tp
    existing = tp.list_tracked()
    for rec in existing:
        t = (rec.get('ticket') or {}).get('trading_symbol', '').strip().upper()
        if t == sym:
            logger.info(f"[executor] Skip scalp {sym} — already tracked")
            return None

    lot_size = int(ticket.get('lot_size', 1) or 1)
    trading_symbol = ticket.get('trading_symbol', '')

    _scalp_sl_pts = float(ticket['exit'].get('stop_loss_points') or 8)
    num_lots = _scalp_mod.SCALP_LOTS_PER_TRADE()
    qty = lot_size * num_lots

    # ── Scalp per-trade budget guard: worst-case SL + open exposure must fit ──
    _scalp_worst = (_scalp_sl_pts * qty) + 70
    _existing_exposure = 0
    try:
        for _rec in existing:
            _t = _rec.get('ticket') or {}
            if _t.get('trade_mode') != 'scalp': continue
            _esl = float((_t.get('exit') or {}).get('stop_loss_points', 8) or 8)
            _eq = int(_rec.get('qty', 1) or 1)
            _existing_exposure += (_esl * _eq) + 70
    except Exception: pass
    _total_worst = _scalp_worst + _existing_exposure
    if _total_worst > _scalp_headroom:
        logger.warning(f"[executor] SCALP BUDGET BLOCK {sym} — worst ₹{_total_worst:.0f} "
                       f"(new ₹{_scalp_worst:.0f} + open ₹{_existing_exposure:.0f}) "
                       f"> headroom ₹{_scalp_headroom:.0f}")
        return None

    logger.info(f"[executor] SCALP-ENTRY: BUY {trading_symbol} qty={qty} "
                f"conf={conf}% SL_pts={ticket['exit'].get('stop_loss_points')}")

    from ..api.indmoney import _order_broadcast
    order_result = _place_fo_buy(trading_symbol, qty)

    if order_result and order_result.get('success'):
        record = tp.add_tracked(ticket, qty=qty,
                                notes=f"Scalp entry conf={conf}% | {signal.get('ticker')}")

        evt = {
            'type': 'order_update', 'severity': 'success',
            'title': f"⏱ Scalp BUY: {ticket.get('display_symbol', trading_symbol)}",
            'status': 'SCALP_ENTRY', 'symbol': trading_symbol,
            'txn_type': 'BUY', 'qty': qty,
            'message': f"Conf {conf}% — SL ₹{ticket['exit']['stop_loss_inr']:.2f} | "
                       f"T1 ₹{ticket['exit']['target_1_inr']:.2f} | "
                       f"Max hold {ticket.get('scalp_meta', {}).get('max_hold_min', '?')}m",
            'timestamp': bu.now_ist().isoformat(),
        }
        _order_broadcast(evt)
        _scalp_mod._broadcast(evt)
        logger.info(f"[executor] Scalp entry success: {trading_symbol} → {record.get('id')}")
        return record
    else:
        err = (order_result or {}).get('error', 'Unknown error')
        evt = {
            'type': 'order_update', 'severity': 'error',
            'title': f"Scalp BUY FAILED: {trading_symbol}",
            'status': 'SCALP_ENTRY_FAILED', 'symbol': trading_symbol,
            'txn_type': 'BUY', 'qty': qty,
            'message': str(err),
            'timestamp': bu.now_ist().isoformat(),
        }
        _order_broadcast(evt)
        _scalp_mod._broadcast(evt)
        logger.error(f"[executor] Scalp entry failed: {trading_symbol} — {err}")
        return None


def check_scalp_hold_timeout(track_id: str, rec: dict, premium: float) -> bool:
    """
    Check if a scalp position has exceeded its max hold time.
    Called by tracked_monitor on every poll cycle for scalp positions.
    Returns True if timeout exit was triggered.
    """
    ticket = rec.get('ticket') or {}
    scalp_meta = ticket.get('scalp_meta')
    if not scalp_meta:
        return False  # not a scalp position

    entered_at_str = scalp_meta.get('entered_at', '')
    max_hold = int(scalp_meta.get('max_hold_min', 10))

    try:
        entered_at = datetime.fromisoformat(entered_at_str)
    except (ValueError, TypeError):
        return False

    elapsed = bu.now_ist() - entered_at
    elapsed_min = elapsed.total_seconds() / 60.0

    if elapsed_min >= max_hold:
        logger.info(f"[executor] SCALP TIMEOUT: {ticket.get('trading_symbol')} "
                    f"held {elapsed_min:.1f}m ≥ {max_hold}m — forcing exit")
        return try_auto_exit(track_id, 'time_exit', rec, premium)

    return False


# ── Internal order helpers ────────────────────────────────────────────────────

def _place_fo_buy(trading_symbol: str, qty: int) -> dict:
    """Place a BUY MARKET order for an F&O instrument."""
    try:
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
                'timestamp': bu.now_ist().isoformat(),
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
                'timestamp': bu.now_ist().isoformat(),
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
