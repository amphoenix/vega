"""
Position Monitor — background thread watching open paper/live F&O + equity positions.

Auto-exits when stop-loss or target is hit.
Understands F&O lots, premiums, expiry, and Indian-market fees.

Position dict schema (stored in paper_wallet.json → positions):
  {
    "qty":            int,        # number of LOTS for F&O, shares for EQ
    "lot_size":       int,        # 50 for NIFTY, 1 for EQ
    "qty_units":      int,        # qty × lot_size (cached) — what broker sees
    "avg_entry":      float,      # premium per unit (CE/PE) or price (FUT/EQ)
    "underlying":     str,
    "instrument_type": "FUT"|"CE"|"PE"|"EQ",
    "strike_price":   float,      # 0 for FUT/EQ
    "expiry":         str,        # ISO yyyy-mm-dd  (or '' for EQ)
    "security_id":    str,        # broker security_id (cached from master)
    "exchange":       str,        # NFO / BFO / NSE / BSE
    "stop_loss":      float,
    "target_1":       float,
    "target_2":       float,
    "entry_date":     str,        # ISO timestamp
    "entry_order_id": str,        # broker order id of entry leg (live only)
    "sl_order_id":    str,        # broker order id of resting SL-M (live only)
    "reason":         str,
    "t1_hit":         bool,
    "trailing_stop":  bool,
    "exit_pending":   bool,       # True between exit-submit and confirmed fill
    "mode":           "paper"|"live",
  }
"""
from __future__ import annotations

import json
import threading
import time
import os
from datetime import datetime
from typing import Optional

from ..utils.logger import get_logger
from . import broker_utils as bu

logger = get_logger('phoenixtrade.position_monitor')

POLL_INTERVAL = int(os.environ.get('POSITION_POLL_INTERVAL', '1'))   # seconds (safety-net; tick path is primary)
PAPER_WALLET  = os.path.join(os.path.dirname(__file__), '../../uploads/paper_wallet.json')

# Daily kill-switch — stop new entries if cumulative realised loss exceeds this %
DAILY_MAX_LOSS_PCT = float(os.environ.get('DAILY_MAX_LOSS_PCT', '5'))   # 5% of starting cash

_MONITOR_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()

# Per-underlying lock — prevents parallel scanner + auto_trade racing on same name
_underlying_locks: dict[str, threading.Lock] = {}
_underlying_lock_factory = threading.Lock()

# SSE broadcast queues
_sse_subscribers: list = []
_sse_lock = threading.Lock()


# ── Time helpers (delegated to broker_utils for IST + holiday correctness) ───
def is_market_hours() -> bool:
    return bu.is_market_hours()


def is_safe_hours() -> bool:
    return bu.is_safe_hours()


def _is_expiry_theta_zone(pos: dict) -> bool:
    """True if past 13:00 IST on the position's expiry day."""
    expiry = pos.get('expiry') or ''
    if not expiry:
        return False
    exp_d = bu._parse_expiry(expiry)
    if not exp_d:
        return False
    n = bu.now_ist()
    return exp_d == n.date() and n.time() >= bu.THETA_EXIT


def _underlying_lock(underlying: str) -> threading.Lock:
    with _underlying_lock_factory:
        lk = _underlying_locks.get(underlying)
        if not lk:
            lk = threading.Lock()
            _underlying_locks[underlying] = lk
        return lk


# ── SSE ──────────────────────────────────────────────────────────────────────
def subscribe_sse():
    import queue
    q = queue.Queue(maxsize=50)
    with _sse_lock:
        _sse_subscribers.append(q)
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


# ── Wallet helpers ────────────────────────────────────────────────────────────
def _load_wallet() -> dict:
    try:
        if os.path.exists(PAPER_WALLET):
            with open(PAPER_WALLET) as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "cash":          500000.0,
        "starting_cash": 500000.0,
        "positions":     {},
        "trades":        [],
        "total_trades":  0,
        "won":           0,
        "lost":          0,
        "kill_switch":   False,
        "kill_reason":   "",
        "daily_realised_pnl": {},   # {yyyy-mm-dd: float}
        "created_at":    datetime.now().isoformat(),
    }


def _save_wallet(wallet: dict):
    os.makedirs(os.path.dirname(PAPER_WALLET), exist_ok=True)
    with open(PAPER_WALLET, 'w') as f:
        json.dump(wallet, f, indent=2)


def _today_key() -> str:
    return bu.today_ist().isoformat()


def _check_daily_kill(wallet: dict) -> tuple[bool, str]:
    """Returns (blocked, reason). Trips kill-switch if today's loss > threshold."""
    if wallet.get('kill_switch'):
        return True, wallet.get('kill_reason', 'kill_switch active')
    starting = float(wallet.get('starting_cash') or 1) or 1.0
    today_pnl = float((wallet.get('daily_realised_pnl') or {}).get(_today_key(), 0.0))
    loss_pct  = -today_pnl / starting * 100.0
    if loss_pct >= DAILY_MAX_LOSS_PCT:
        wallet['kill_switch'] = True
        wallet['kill_reason'] = (f"Daily loss {loss_pct:.2f}% ≥ "
                                 f"{DAILY_MAX_LOSS_PCT:.2f}% — new entries blocked")
        _save_wallet(wallet)
        return True, wallet['kill_reason']
    return False, ""


# ── Live price ───────────────────────────────────────────────────────────────
def _live_price(ticker: str) -> Optional[float]:
    try:
        from ..api.indmoney import _ind_ltp
        p = _ind_ltp(ticker)
        if p:
            return p
    except Exception as e:
        logger.debug(f"IndMoney price failed for {ticker}: {e}")
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        p = getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None)
        if p:
            return float(p)
    except Exception:
        pass
    return None


def _live_option_premium(pos_key: str) -> Optional[float]:
    try:
        from ..api.indmoney import _ind_option_ltp
        p = _ind_option_ltp(pos_key)
        if p:
            return p
    except Exception as e:
        logger.debug(f"Option premium fetch failed for {pos_key}: {e}")
    return None


# ── P&L (with Indian fee model) ──────────────────────────────────────────────
def _gross_pnl(pos: dict, current_price: float,
               option_premium: Optional[float] = None) -> float:
    qty       = pos.get('qty', 0)
    lot_size  = pos.get('lot_size', 1)
    avg_entry = pos.get('avg_entry', 0.0)
    itype     = pos.get('instrument_type', 'EQ')
    units     = qty * lot_size

    if itype == 'EQ':
        return (current_price - avg_entry) * qty
    if itype == 'FUT':
        return (current_price - avg_entry) * units
    if itype in ('CE', 'PE'):
        if option_premium is not None:
            return (option_premium - avg_entry) * units
        # Fallback intrinsic-value estimate (less accurate)
        strike = pos.get('strike_price', 0.0)
        intrinsic = max(0.0, current_price - strike) if itype == 'CE' \
                    else max(0.0, strike - current_price)
        return (intrinsic - avg_entry) * units
    return 0.0


def _round_trip_fees(pos: dict, exit_price: float) -> float:
    """Approximate STT+brokerage+exch+GST+stamp+SEBI for entry+exit legs."""
    itype = pos.get('instrument_type', 'EQ')
    qty   = pos.get('qty', 0)
    lot   = pos.get('lot_size', 1)
    units = qty if itype == 'EQ' else qty * lot
    if units <= 0:
        return 0.0
    buy  = bu.compute_fees('BUY',  itype, pos.get('avg_entry', 0.0), units)['total']
    sell = bu.compute_fees('SELL', itype, exit_price,                  units)['total']
    return buy + sell


def _net_pnl(pos: dict, current_price: float,
             option_premium: Optional[float] = None) -> tuple[float, float]:
    """Returns (gross, net_after_fees)."""
    gross = _gross_pnl(pos, current_price, option_premium)
    px    = option_premium if option_premium is not None else current_price
    fees  = _round_trip_fees(pos, px)
    return gross, gross - fees


def _position_cost(pos: dict) -> float:
    qty      = pos.get('qty', 0)
    lot_size = pos.get('lot_size', 1)
    premium  = pos.get('avg_entry', 0.0)
    itype    = pos.get('instrument_type', 'EQ')
    if itype == 'EQ':
        return premium * qty
    return premium * qty * lot_size


# ── Order execution ──────────────────────────────────────────────────────────
def _place_broker_order(trading_symbol: str, txn: str, qty_units: int,
                        itype: str, order_type: str = 'MARKET',
                        trigger_price: float | None = None,
                        limit_price: float | None = None) -> dict:
    """Thin wrapper around indmoney._ind_place_order."""
    try:
        from ..api.indmoney import _ind_place_order
        return _ind_place_order(
            trading_symbol, txn, int(qty_units), itype,
            product='MIS', order_type=order_type,
            trigger_price=trigger_price, limit_price=limit_price,
        )
    except Exception as e:
        logger.error(f"Broker order failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def _await_fill(order_id: str, timeout_s: float = 8.0) -> dict:
    try:
        from ..api.indmoney import _ind_wait_for_fill
        return _ind_wait_for_fill(order_id, timeout_s=timeout_s)
    except Exception as e:
        return {"status": "UNKNOWN", "filled_qty": 0,
                "traded_price": 0.0, "error": str(e)}


def _cancel_broker_order(order_id: str) -> dict:
    if not order_id:
        return {"status": "ok"}
    try:
        from ..api.indmoney import _ind_cancel_order
        return _ind_cancel_order(order_id)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _execute_entry(trading_symbol: str, qty_lots: int, lot_size: int,
                   itype: str, planned_price: float, reason: str,
                   live: bool = False) -> dict:
    """
    Place an entry BUY. Returns:
      {"status":"paper"|"live", "filled_price": float, "filled_qty_units": int,
       "order_id": str, "error": str?}
    """
    if not live:
        logger.info(f"[PAPER BUY] {trading_symbol} lots={qty_lots} "
                    f"@ {planned_price:.2f} | {reason}")
        return {
            "status": "paper",
            "filled_price":     planned_price,
            "filled_qty_units": qty_lots * lot_size if itype != 'EQ' else qty_lots,
            "order_id":         f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        }

    qty_units = qty_lots * lot_size if itype != 'EQ' else qty_lots
    placed    = _place_broker_order(trading_symbol, 'BUY', qty_units, itype)
    if placed.get('status') != 'live':
        return {"status": "error", "error": placed.get('error', 'unknown')}

    fill = _await_fill(placed['order_id'])
    fstatus = (fill.get('status') or '').upper()
    if fstatus in ('REJECTED', 'CANCELLED', 'CANCELED', 'NOT_FOUND'):
        return {"status": "error", "error": f"order {fstatus}",
                "order_id": placed.get('order_id')}
    filled_qty   = int(fill.get('filled_qty') or 0)
    traded_price = float(fill.get('traded_price') or 0)
    if filled_qty <= 0 or traded_price <= 0:
        # Order accepted but not yet filled — treat as failure for safety
        _cancel_broker_order(placed['order_id'])
        return {"status": "error",
                "error": f"entry not filled in time (status={fstatus})",
                "order_id": placed.get('order_id')}
    return {
        "status": "live",
        "filled_price":     traded_price,
        "filled_qty_units": filled_qty,
        "order_id":         placed['order_id'],
    }


def _execute_exit(trading_symbol: str, qty_lots: int, lot_size: int,
                  itype: str, planned_price: float, reason: str,
                  live: bool = False) -> dict:
    """Place exit SELL. Same return shape as `_execute_entry`."""
    if not live:
        logger.info(f"[PAPER EXIT] {trading_symbol} lots={qty_lots} "
                    f"@ {planned_price:.2f} | {reason}")
        return {
            "status": "paper",
            "filled_price":     planned_price,
            "filled_qty_units": qty_lots * lot_size if itype != 'EQ' else qty_lots,
            "order_id":         f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        }

    qty_units = qty_lots * lot_size if itype != 'EQ' else qty_lots
    placed    = _place_broker_order(trading_symbol, 'SELL', qty_units, itype)
    if placed.get('status') != 'live':
        return {"status": "error", "error": placed.get('error', 'unknown')}

    fill = _await_fill(placed['order_id'])
    fstatus = (fill.get('status') or '').upper()
    if fstatus in ('REJECTED', 'CANCELLED', 'CANCELED', 'NOT_FOUND'):
        return {"status": "error", "error": f"exit order {fstatus}",
                "order_id": placed.get('order_id')}
    return {
        "status": "live",
        "filled_price":     float(fill.get('traded_price') or planned_price),
        "filled_qty_units": int(fill.get('filled_qty') or qty_units),
        "order_id":         placed['order_id'],
    }


def _place_resting_sl(trading_symbol: str, qty_lots: int, lot_size: int,
                      itype: str, sl_price: float) -> str:
    """Place a SL-M order at the broker for an open F&O position.
    Works for FUT (trigger on futures price) and CE/PE (trigger on option
    premium ₹). Returns order_id, or empty on failure.

    For options the SL trigger is the option premium itself — IndStocks
    accepts trigger_price directly on the contract. Slippage on SL-M can be
    wide during fast moves, but it's still strictly better than no broker
    protection at all (e.g. if the laptop sleeps or backend crashes).
    """
    if itype == 'EQ' or sl_price <= 0:
        return ''
    qty_units = qty_lots * lot_size
    # SL-M: triggers a market sell when price <= trigger.
    # For CE/PE long positions, sl_price is the option-premium ₹ floor.
    res = _place_broker_order(
        trading_symbol, 'SELL', qty_units, itype,
        order_type='SL-M', trigger_price=sl_price,
    )
    if res.get('status') == 'live':
        logger.info(f"Broker SL-M placed: {trading_symbol} qty={qty_units} "
                    f"trigger=₹{sl_price:.2f} order_id={res.get('order_id')}")
        return res.get('order_id', '')
    logger.warning(f"Resting SL placement failed for {trading_symbol}: "
                   f"{res.get('error')}")
    return ''


# ── Reconciliation on startup ────────────────────────────────────────────────
def _reconcile_with_broker():
    """
    On startup, fetch live broker positions. Drop any wallet position whose
    matching broker leg has been closed (qty=0). Log discrepancies.
    """
    if not bu.is_live_mode():
        return
    try:
        from ..api.indmoney import _ind_positions
        broker_pos = _ind_positions()
    except Exception as e:
        logger.warning(f"Reconcile: broker positions fetch failed: {e}")
        return

    if not broker_pos:
        return

    held = {}
    for p in broker_pos:
        sym = (p.get('trading_symbol') or p.get('tradingsymbol') or '').upper()
        net = int(float(p.get('net_qty') or p.get('netQty') or 0))
        if sym:
            held[sym] = net

    wallet = _load_wallet()
    changed = False
    for k, pos in list(wallet.get('positions', {}).items()):
        if pos.get('mode') != 'live':
            continue
        if held.get(k.upper(), 0) == 0:
            logger.warning(f"Reconcile: dropping wallet position {k} "
                           f"(broker reports flat)")
            del wallet['positions'][k]
            changed = True
    if changed:
        _save_wallet(wallet)


# ── Core monitor loop ────────────────────────────────────────────────────────
def _monitor_loop():
    logger.info("Position monitor started")
    _reconcile_with_broker()
    while not _STOP_EVENT.is_set():
        try:
            if is_market_hours():
                _check_positions()
        except Exception as e:
            logger.error(f"Monitor error: {e}", exc_info=True)
        _STOP_EVENT.wait(POLL_INTERVAL)
    logger.info("Position monitor stopped")


def _check_positions():
    wallet    = _load_wallet()
    positions = wallet.get('positions', {})
    if not positions:
        return

    live_mode = bu.is_live_mode()
    changed   = False
    n_ist     = bu.now_ist()
    force_exit_now = bu.is_force_exit_time()

    for pos_key, pos in list(positions.items()):
        if pos.get('exit_pending'):
            continue   # idempotency: skip while a previous exit is in flight

        qty       = pos.get('qty', 0)
        lot_size  = pos.get('lot_size', 1)
        avg_entry = pos.get('avg_entry', 0.0)
        stop_loss = pos.get('stop_loss')
        target_1  = pos.get('target_1')
        target_2  = pos.get('target_2')
        itype     = pos.get('instrument_type', 'EQ')
        underlying = pos.get('underlying', pos_key)

        if not qty or not avg_entry:
            continue

        # ── Prices
        option_premium: Optional[float] = None
        if itype in ('CE', 'PE'):
            option_premium = _live_option_premium(pos_key)
        watch_ticker = underlying if itype in ('FUT', 'CE', 'PE') else pos_key
        price = _live_price(watch_ticker)
        if price is None and option_premium is None:
            logger.debug(f"No price for {pos_key}, skipping")
            continue
        if price is None:
            price = 0.0

        gross_pnl, net_pnl = _net_pnl(pos, price, option_premium)
        cost      = _position_cost(pos) or 1
        pnl_pct   = round(net_pnl / cost * 100, 2)

        # ── CE/PE: premium-level SL/T1/T2 ────────────────────────────────────
        # Use B-S repriced levels from option_planner when available (stored at
        # open time as premium_sl/t1/t2). Fall back to entry-multiple heuristics
        # for positions opened without option_planner (e.g. manual entries).
        if itype in ('CE', 'PE') and option_premium is not None:
            opt_price = option_premium
            opt_sl    = round(float(pos.get('premium_sl') or avg_entry * 0.50), 2)
            opt_t1    = round(float(pos.get('premium_t1') or avg_entry * 2.00), 2)
            opt_t2    = round(float(pos.get('premium_t2') or avg_entry * 3.00), 2)

            exit_reason = None
            if force_exit_now:
                exit_reason = (f"FORCE_EXIT: {n_ist.strftime('%H:%M IST')} "
                               f"≥ {bu.FORCE_EXIT.strftime('%H:%M')}")
            elif _is_expiry_theta_zone(pos):
                exit_reason = f"THETA_EXIT: expiry-day after 13:00 IST (premium=₹{opt_price:.2f})"
            elif opt_price <= opt_sl:
                exit_reason = f"PREMIUM_SL: ₹{opt_price:.2f} ≤ 50% of entry ₹{avg_entry:.2f}"
            elif opt_price >= opt_t2:
                exit_reason = f"TARGET_2: premium ₹{opt_price:.2f} = {opt_price/avg_entry:.1f}× entry"
            else:
                # Theta-burn guard: if daily theta loss > 10% of remaining premium
                # AND we're already in the red, cut losses before next day's decay.
                try:
                    from . import greeks as _gk
                    underlying_p = price or _live_price(pos.get('underlying') or '')
                    exp_d = bu._parse_expiry(pos.get('expiry') or '')
                    if underlying_p and exp_d and pos.get('strike_price'):
                        dte = max(1, (exp_d - bu.today_ist()).days)
                        iv  = _gk.implied_vol(
                            spot=underlying_p, strike=float(pos['strike_price']),
                            dte_days=dte, market_price=opt_price,
                            opt_type=itype) or _gk.default_iv(pos.get('underlying',''))
                        g = _gk.greeks(underlying_p, float(pos['strike_price']),
                                       dte, iv, opt_type=itype)
                        theta_today = abs(g['theta_per_day'])
                        if (opt_price < avg_entry and dte <= 3
                                and theta_today > 0.10 * opt_price):
                            exit_reason = (f"THETA_BURN: θ/day=₹{theta_today:.2f} "
                                           f"> 10% of premium ₹{opt_price:.2f}, "
                                           f"DTE={dte}d, in-the-red")
                except Exception:
                    pass

            t1_triggered = (not pos.get('t1_hit') and opt_price >= opt_t1
                            and exit_reason is None)
            if t1_triggered:
                changed |= _do_partial_exit_ce_pe(wallet, pos_key, pos, opt_price,
                                                  price, lot_size, qty, live_mode)
                continue

            if exit_reason:
                changed |= _do_full_exit(wallet, pos_key, pos, opt_price, price,
                                         exit_reason, live_mode,
                                         option_premium=opt_price)
            continue

        # ── FUT / EQ: underlying-price levels ────────────────────────────────
        if stop_loss is None:
            stop_loss = round(avg_entry * 0.95, 2) if itype == 'EQ' else round(price * 0.97, 2)
        if target_1 is None:
            target_1 = round(avg_entry * 1.08, 2) if itype == 'EQ' else round(price * 1.04, 2)
        if target_2 is None:
            target_2 = round(avg_entry * 1.15, 2) if itype == 'EQ' else round(price * 1.08, 2)

        exit_reason = None
        if force_exit_now and itype != 'EQ':
            exit_reason = (f"FORCE_EXIT: {n_ist.strftime('%H:%M IST')} "
                           f"≥ {bu.FORCE_EXIT.strftime('%H:%M')}")
        elif price <= stop_loss:
            exit_reason = f"STOP_LOSS: {price:.2f} ≤ {stop_loss:.2f}"
        elif price >= target_2:
            exit_reason = f"TARGET_2: {price:.2f} ≥ {target_2:.2f}"

        if (price >= target_1) and (not pos.get('t1_hit')) and exit_reason is None:
            changed |= _do_partial_exit_fut_eq(wallet, pos_key, pos, price,
                                               lot_size, qty, avg_entry, live_mode)
            continue

        if pos.get('trailing_stop') and exit_reason is None and price > avg_entry:
            new_trail = round(price * 0.98, 2)
            if new_trail > pos.get('stop_loss', 0):
                pos['stop_loss'] = new_trail
                changed = True

        if exit_reason:
            changed |= _do_full_exit(wallet, pos_key, pos, price, price,
                                     exit_reason, live_mode)

    if changed:
        _save_wallet(wallet)


# ── Exit helpers (split out for clarity) ─────────────────────────────────────
def _do_partial_exit_ce_pe(wallet, pos_key, pos, opt_price, underlying_price,
                            lot_size, qty, live_mode) -> bool:
    half = max(1, qty // 2)
    pos['exit_pending'] = True
    result = _execute_exit(pos_key, half, lot_size, pos['instrument_type'],
                           opt_price, "TARGET_1: premium 2×", live=live_mode)
    pos['exit_pending'] = False
    if result.get('status') not in ('paper', 'live'):
        logger.warning(f"T1 partial exit FAILED for {pos_key}: {result.get('error')}")
        return False

    fill_px = result.get('filled_price', opt_price)
    pos_slice = {**pos, 'qty': half}
    gross, net = _net_pnl(pos_slice, underlying_price, fill_px)
    cost_back  = _position_cost(pos_slice)

    if pos.get('mode') == 'paper':
        wallet['cash'] = round(wallet.get('cash', 0) + cost_back + net, 2)
    _record_realised_pnl(wallet, net)

    pos['qty'] = qty - half
    pos['t1_hit'] = True
    pos['trailing_stop'] = True
    wallet.setdefault('trades', []).append({
        'pos_key': pos_key, 'underlying': pos.get('underlying'),
        'instrument_type': pos['instrument_type'], 'action': 'SELL_PARTIAL',
        'qty': half, 'lot_size': lot_size,
        'option_premium': fill_px, 'avg_entry': pos['avg_entry'],
        'gross_pnl': round(gross, 2), 'pnl': round(net, 2),
        'reason': 'TARGET_1: premium 2×',
        'order_id': result.get('order_id'),
        'mode': pos.get('mode'),
        'timestamp': datetime.now().isoformat(),
    })
    wallet['total_trades'] = wallet.get('total_trades', 0) + 1
    wallet['won']          = wallet.get('won', 0) + (1 if net > 0 else 0)
    _broadcast({'type': 'position_exit', 'pos_key': pos_key,
                'action': 'SELL_PARTIAL', 'option_premium': fill_px,
                'qty': half, 'pnl': round(net, 2),
                'reason': 'TARGET_1: premium 2×',
                'remaining_qty': pos['qty']})
    logger.info(f"T1 partial: {pos_key} sold {half} lots @ ₹{fill_px:.2f} net=₹{net:.0f}")
    return True


def _do_partial_exit_fut_eq(wallet, pos_key, pos, price, lot_size, qty,
                             avg_entry, live_mode) -> bool:
    half = max(1, qty // 2)
    pos['exit_pending'] = True
    result = _execute_exit(pos_key, half, lot_size, pos['instrument_type'],
                           price, "TARGET_1 hit", live=live_mode)
    pos['exit_pending'] = False
    if result.get('status') not in ('paper', 'live'):
        return False

    fill_px = result.get('filled_price', price)
    pos_slice = {**pos, 'qty': half}
    gross, net = _net_pnl(pos_slice, fill_px)
    cost_back  = _position_cost(pos_slice)

    if pos.get('mode') == 'paper':
        wallet['cash'] = round(wallet.get('cash', 0) + cost_back + net, 2)
    _record_realised_pnl(wallet, net)

    pos['qty'] = qty - half
    pos['t1_hit'] = True
    pos['stop_loss'] = avg_entry        # raise SL to breakeven
    pos['trailing_stop'] = True
    wallet.setdefault('trades', []).append({
        'pos_key': pos_key, 'underlying': pos.get('underlying'),
        'instrument_type': pos['instrument_type'], 'action': 'SELL_PARTIAL',
        'qty': half, 'lot_size': lot_size,
        'underlying_price': fill_px, 'avg_entry': avg_entry,
        'gross_pnl': round(gross, 2), 'pnl': round(net, 2),
        'reason': 'TARGET_1 hit',
        'order_id': result.get('order_id'),
        'mode': pos.get('mode'),
        'timestamp': datetime.now().isoformat(),
    })
    wallet['total_trades'] = wallet.get('total_trades', 0) + 1
    wallet['won']          = wallet.get('won', 0) + (1 if net > 0 else 0)
    _broadcast({'type': 'position_exit', 'pos_key': pos_key,
                'action': 'SELL_PARTIAL', 'underlying_price': fill_px,
                'qty': half, 'pnl': round(net, 2),
                'reason': 'TARGET_1 hit', 'remaining_qty': pos['qty'],
                'new_sl': pos['stop_loss']})
    logger.info(f"T1 partial: {pos_key} sold {half} lots @ {fill_px:.2f} net=₹{net:.0f}")
    return True


def _do_full_exit(wallet, pos_key, pos, exit_px, underlying_price, reason,
                   live_mode, option_premium: Optional[float] = None) -> bool:
    qty      = pos.get('qty', 0)
    lot_size = pos.get('lot_size', 1)
    pos['exit_pending'] = True
    result = _execute_exit(pos_key, qty, lot_size, pos['instrument_type'],
                           exit_px, reason, live=live_mode)
    if result.get('status') not in ('paper', 'live'):
        pos['exit_pending'] = False
        logger.warning(f"Exit FAILED for {pos_key}: {result.get('error')} — will retry")
        return False

    fill_px    = result.get('filled_price', exit_px)
    if pos['instrument_type'] in ('CE', 'PE'):
        gross, net = _net_pnl(pos, underlying_price, fill_px)
    else:
        gross, net = _net_pnl(pos, fill_px)
    cost_back = _position_cost(pos)

    # Cancel any resting SL leg at the broker
    sl_oid = pos.get('sl_order_id')
    if sl_oid and pos.get('mode') == 'live':
        _cancel_broker_order(sl_oid)

    if pos.get('mode') == 'paper':
        wallet['cash'] = round(wallet.get('cash', 0) + cost_back + net, 2)
    _record_realised_pnl(wallet, net)

    is_win = net > 0
    wallet.setdefault('trades', []).append({
        'pos_key': pos_key, 'underlying': pos.get('underlying'),
        'instrument_type': pos['instrument_type'], 'action': 'SELL',
        'qty': qty, 'lot_size': lot_size,
        'exit_price': fill_px, 'avg_entry': pos.get('avg_entry'),
        'gross_pnl': round(gross, 2), 'pnl': round(net, 2),
        'reason': reason, 'order_id': result.get('order_id'),
        'mode': pos.get('mode'),
        'timestamp': datetime.now().isoformat(),
    })
    wallet['total_trades'] = wallet.get('total_trades', 0) + 1
    if is_win: wallet['won']  = wallet.get('won', 0) + 1
    else:      wallet['lost'] = wallet.get('lost', 0) + 1

    del wallet['positions'][pos_key]
    _broadcast({'type': 'position_exit', 'pos_key': pos_key,
                'action': 'SELL', 'exit_price': fill_px,
                'qty': qty, 'pnl': round(net, 2),
                'reason': reason, 'is_win': is_win})
    logger.info(f"Exit {pos_key} ({pos['instrument_type']}) "
                f"@ {fill_px:.2f} net=₹{net:.0f} | {reason}")
    return True


def _record_realised_pnl(wallet: dict, pnl: float):
    daily = wallet.setdefault('daily_realised_pnl', {})
    k = _today_key()
    daily[k] = round(float(daily.get(k, 0.0)) + float(pnl), 2)


# ── Public API ───────────────────────────────────────────────────────────────
def open_position(
    pos_key: str,
    underlying: str,
    instrument_type: str,
    qty: int,                       # number of LOTS (F&O) or shares (EQ)
    lot_size: int,
    avg_entry: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    premium_sl: float = 0.0,        # option premium SL (₹/unit) from option_planner
    premium_t1: float = 0.0,        # option premium T1 (₹/unit)
    premium_t2: float = 0.0,        # option premium T2 (₹/unit)
    strike_price: float = 0.0,
    expiry: str = '',
    security_id: str = '',
    exchange: str = '',
    reason: str = '',
    live: bool = False,
) -> dict:
    """
    Open a new F&O / equity position. Atomic per-underlying via lock,
    polls broker for fill in live mode, and uses the actual filled price
    (not the planned one) for wallet accounting and SL placement.
    """
    lock = _underlying_lock(underlying or pos_key)
    with lock:
        wallet = _load_wallet()

        # Kill switch
        blocked, reason_blk = _check_daily_kill(wallet)
        if blocked:
            logger.warning(f"Entry blocked by kill-switch: {reason_blk}")
            return {"status": "blocked", "error": reason_blk}

        if pos_key in wallet.get('positions', {}):
            return {"status": "exists", "error": f"already holding {pos_key}"}

        # Place entry
        result = _execute_entry(pos_key, qty, lot_size, instrument_type,
                                avg_entry, reason, live=live)
        if result['status'] not in ('paper', 'live'):
            logger.error(f"Entry failed for {pos_key}: {result.get('error')}")
            return result

        filled_price = float(result['filled_price'])
        units_filled = int(result['filled_qty_units'])
        # Convert filled units back to lots for storage
        filled_lots  = units_filled if instrument_type == 'EQ' \
                       else max(1, units_filled // max(1, lot_size))

        # Place broker-side resting SL-M for ALL F&O live trades (FUT + CE/PE).
        # For options, the trigger is on the option premium itself. This is
        # critical real-money protection: if our backend crashes or laptop
        # sleeps, the broker SL still fires — no naked exposure to gap moves.
        sl_oid = ''
        if live and instrument_type in ('FUT', 'CE', 'PE') and stop_loss and stop_loss > 0:
            sl_oid = _place_resting_sl(pos_key, filled_lots, lot_size,
                                       instrument_type, stop_loss)

        cost = filled_price * filled_lots * lot_size if instrument_type != 'EQ' \
               else filled_price * filled_lots

        mode = 'live' if live else 'paper'
        if mode == 'paper':
            wallet['cash'] = round(wallet.get('cash', 0) - cost, 2)

        wallet.setdefault('positions', {})[pos_key] = {
            'qty':             filled_lots,
            'lot_size':        lot_size,
            'qty_units':       units_filled,
            'avg_entry':       filled_price,
            'underlying':      underlying,
            'instrument_type': instrument_type,
            'strike_price':    strike_price,
            'expiry':          expiry,
            'security_id':     security_id,
            'exchange':        exchange,
            'stop_loss':       stop_loss,
            'target_1':        target_1,
            'target_2':        target_2,
            'premium_sl':      premium_sl,
            'premium_t1':      premium_t1,
            'premium_t2':      premium_t2,
            'entry_date':      datetime.now().isoformat(),
            'entry_order_id':  result.get('order_id', ''),
            'sl_order_id':     sl_oid,
            'reason':          reason,
            't1_hit':          False,
            'trailing_stop':   False,
            'exit_pending':    False,
            'mode':            mode,
        }
        wallet.setdefault('trades', []).append({
            'pos_key': pos_key, 'underlying': underlying,
            'instrument_type': instrument_type, 'action': 'BUY',
            'qty': filled_lots, 'lot_size': lot_size,
            'avg_entry': filled_price, 'planned_entry': avg_entry,
            'stop_loss': stop_loss, 'target_1': target_1, 'target_2': target_2,
            'cost': round(cost, 2), 'reason': reason,
            'order_id': result.get('order_id', ''),
            'mode': mode,
            'timestamp': datetime.now().isoformat(),
        })
        wallet['total_trades'] = wallet.get('total_trades', 0) + 1
        _save_wallet(wallet)
        _sync_live_monitor()    # subscribe live_monitor to this new position's ticks

        _broadcast({'type': 'position_open', 'pos_key': pos_key,
                    'underlying': underlying, 'instrument_type': instrument_type,
                    'qty': filled_lots, 'lot_size': lot_size,
                    'avg_entry': filled_price, 'stop_loss': stop_loss,
                    'target_1': target_1, 'target_2': target_2,
                    'cost': round(cost, 2), 'mode': mode, 'reason': reason})
        logger.info(f"Position opened: {pos_key} ({instrument_type}) "
                    f"lots={filled_lots} entry=₹{filled_price:.2f} cost=₹{cost:.0f} "
                    f"SL={stop_loss} mode={mode}")
        return result


def reset_kill_switch():
    """Manual override — clear the daily kill-switch."""
    wallet = _load_wallet()
    wallet['kill_switch'] = False
    wallet['kill_reason'] = ''
    _save_wallet(wallet)


def _sync_live_monitor():
    """Best-effort hook so the tick-driven monitor refreshes its subs."""
    try:
        from . import live_monitor
        live_monitor.sync()
    except Exception:
        pass


def start():
    global _MONITOR_THREAD
    if _MONITOR_THREAD and _MONITOR_THREAD.is_alive():
        return
    # Boot tick-driven monitor + commentary loop alongside
    try:
        from . import live_monitor
        live_monitor.start()
    except Exception as e:
        logger.warning(f"live_monitor start failed: {e}")
    try:
        from . import commentary
        commentary.start()
    except Exception as e:
        logger.warning(f"commentary start failed: {e}")
    _STOP_EVENT.clear()
    _MONITOR_THREAD = threading.Thread(
        target=_monitor_loop, name='PositionMonitor', daemon=True)
    _MONITOR_THREAD.start()
    logger.info("Position monitor thread started")


def stop():
    _STOP_EVENT.set()
    if _MONITOR_THREAD:
        _MONITOR_THREAD.join(timeout=10)


# Backwards-compat shim — old call sites still invoke `_fo_pnl(...)` directly.
def _fo_pnl(pos: dict, current_price: float,
            option_premium: Optional[float] = None) -> float:
    return _gross_pnl(pos, current_price, option_premium)


# Constants kept for backward compatibility with imports elsewhere.
LOT_SIZES: dict = {}    # deprecated — use broker_utils.underlying_lot_size()
