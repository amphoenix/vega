"""
Trade API — FastAPI router.

Covers: tracked positions, executor status, P&L, F&O scanner,
scalp scanner, option chain, levels, indicators, backtest.
"""

from __future__ import annotations

import asyncio
import json
import re
from threading import Lock, Thread

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..config import settings
from ..dependencies import get_broker
from ..infrastructure.db import state_store
from ..shared.logger import get_logger
from ..shared.time import clock, datetime, now_ist, timedelta, today_ist

logger = get_logger('api.trade')
router = APIRouter(prefix='/api/trade', tags=['trade'])


# ── In-memory cache (same pattern as old backend) ────────────────────────────

_cache: dict = {}
_cache_lock = Lock()

def _cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and clock() < entry['expires']:
            return entry['data']
        return None

def _cache_set(key: str, data, ttl: int = 300):
    with _cache_lock:
        _cache[key] = {'data': data, 'expires': clock() + ttl}

def _cache_key(*parts) -> str:
    return ':'.join(str(p) for p in parts)


# ── OHLCV fetcher (broker first, yfinance fallback — same as old backend) ────

_FNO_PAT = re.compile(r'^[A-Z]+-[A-Z]{3}\d{4}-(\d+-(CE|PE)|FUT)$')

def _fetch_ohlcv(ticker: str, days: int = 365, interval: str = '1d'):
    """Return a pandas DataFrame of OHLCV data (broker → yfinance fallback)."""
    import pandas as pd

    broker = get_broker()

    # F&O contracts — broker candles only
    if _FNO_PAT.match(ticker.upper()):
        candles = broker.get_candles(ticker, interval=interval, days=days)
        if not candles:
            return None
        rows = [{'date': c.date, 'open': c.open, 'high': c.high,
                 'low': c.low, 'close': c.close, 'volume': c.volume}
                for c in candles]
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        return df[['open', 'high', 'low', 'close', 'volume']]

    # Equity — try broker first
    candles = broker.get_candles(ticker, interval=interval, days=days)
    if candles and len(candles) >= 5:
        rows = [{'date': c.date, 'open': c.open, 'high': c.high,
                 'low': c.low, 'close': c.close, 'volume': c.volume}
                for c in candles]
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        return df[['open', 'high', 'low', 'close', 'volume']]

    # yfinance fallback
    import yfinance as yf
    end   = now_ist()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start.strftime('%Y-%m-%d'),
                     end=end.strftime('%Y-%m-%d'),
                     interval=interval, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.index = pd.to_datetime(df.index)
    df = df.rename(columns=str.lower)
    return df


# ── Pivot / CPR / Levels (ported from old backend — identical logic) ─────────

def _pivot_levels(h: float, l: float, c: float):
    """Standard Floor Pivot Points — Zerodha Kite / Groww formula."""
    pp = (h + l + c) / 3
    r1 = 2 * pp - l
    s1 = 2 * pp - h
    r2 = pp + (h - l)
    s2 = pp - (h - l)
    r3 = pp + 2 * (h - l)
    s3 = pp - 2 * (h - l)
    return {"pp": pp, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}


def _cpr(h: float, l: float, c: float):
    """Central Pivot Range — PP/BC/TC + narrow/wide classification."""
    pp = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * pp - bc
    width_pct = abs(tc - bc) / pp * 100
    return {
        "pp": pp, "bc": bc, "tc": tc,
        "width_pct": round(width_pct, 3),
        "type": "narrow" if width_pct < 0.5 else "wide",
    }


def _compute_levels(df, exec_price: float = None) -> dict:
    """Compute entry, stop-loss, and targets from OHLCV DataFrame."""
    import numpy as np
    c_ser = df['close']; h_ser = df['high']; l_ser = df['low']
    price = float(c_ser.iloc[-1])

    tr  = np.maximum(h_ser - l_ser, np.maximum(abs(h_ser - c_ser.shift()), abs(l_ser - c_ser.shift())))
    atr = float(tr.rolling(14).mean().iloc[-1])

    delta = c_ser.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = float((100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1])

    ema20 = float(c_ser.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(c_ser.ewm(span=50, adjust=False).mean().iloc[-1])

    # Daily pivots + CPR from yesterday's candle
    prev_idx = -2 if len(df) >= 2 else -1
    prev_h = float(h_ser.iloc[prev_idx])
    prev_l = float(l_ser.iloc[prev_idx])
    prev_c = float(c_ser.iloc[prev_idx])
    d_piv = _pivot_levels(prev_h, prev_l, prev_c)
    cpr   = _cpr(prev_h, prev_l, prev_c)

    # Weekly pivots from previous complete week
    weekly = df.resample('W').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    if len(weekly) >= 2:
        w_piv = _pivot_levels(
            float(weekly['high'].iloc[-2]),
            float(weekly['low'].iloc[-2]),
            float(weekly['close'].iloc[-2]),
        )
    else:
        w_piv = d_piv  # fallback

    # Combine daily + weekly S/R; deduplicate values within 0.1% of each other
    raw_supports    = sorted([d_piv['s1'], d_piv['s2'], d_piv['s3'],
                               w_piv['s1'], w_piv['s2']], reverse=True)
    raw_resistances = sorted([d_piv['r1'], d_piv['r2'], d_piv['r3'],
                               w_piv['r1'], w_piv['r2']])

    def _dedup(vals, tol=0.001):
        out = []
        for v in vals:
            if not out or abs(v - out[-1]) / max(abs(out[-1]), 1) > tol:
                out.append(v)
        return out

    supports    = _dedup([v for v in raw_supports    if v < price])[:3]
    resistances = _dedup([v for v in raw_resistances if v > price])[:3]

    # Entry / SL / Targets
    if rsi < 40:
        entry = supports[0] if supports and (price - supports[0]) / price < 0.03 else price
    elif price > ema20 > ema50:
        entry = ema20
    elif supports:
        entry = supports[0]
    else:
        entry = price

    anchor    = exec_price if exec_price else entry
    sl_atr    = anchor - atr * 1.5
    sl_sup    = (supports[0] - atr * 0.3) if supports and supports[0] < anchor else sl_atr
    stop_loss = max(sl_atr, sl_sup)
    risk      = max(anchor - stop_loss, atr * 0.5)

    t1 = resistances[0] if resistances and (resistances[0] - anchor) >= risk * 1.5 else anchor + risk * 1.5
    t2 = resistances[1] if len(resistances) > 1 and (resistances[1] - anchor) >= risk * 2.5 else anchor + risk * 2.5
    t3 = anchor + risk * 4.0
    t1, t2, t3 = sorted([t1, t2, t3])

    return {
        "entry":      round(entry, 4),
        "stop_loss":  round(stop_loss, 4),
        "target_1":   round(t1, 4),
        "target_2":   round(t2, 4),
        "target_3":   round(t3, 4),
        "risk":       round(risk, 4),
        "atr":        round(atr, 4),
        "rsi":        round(rsi, 1),
        "pivot":      round(d_piv['pp'], 4),
        "supports":   [round(v, 4) for v in supports],
        "resistances":[round(v, 4) for v in resistances],
        "cpr_pp":     round(cpr['pp'], 4),
        "cpr_bc":     round(cpr['bc'], 4),
        "cpr_tc":     round(cpr['tc'], 4),
        "cpr_width_pct": cpr['width_pct'],
        "cpr_type":   cpr['type'],
    }


# ── Tracked positions (persistent JSON store) ────────────────────────────────

from ..engines import order_executor as _oe
from ..engines.monitor import tracked_monitor as tm
from ..infrastructure.db import tracked_positions as tp


@router.get('/tracked')
def get_tracked():
    return {'success': True, 'data': tp.list_tracked()}


@router.post('/tracked')
async def add_tracked(request: Request):
    body = await request.json()
    ticket = body.get('ticket', body)
    qty = int(body.get('qty', ticket.get('qty', 1)))
    notes = body.get('notes', '')
    try:
        record = tp.add_tracked(ticket, qty=qty, notes=notes)
        return {'success': True, 'data': record}
    except ValueError as e:
        return JSONResponse({'success': False, 'error': str(e)}, 400)


@router.delete('/tracked/{track_id}')
def remove_tracked(track_id: str, exit_premium: str = '', exit_reason: str = 'manual'):
    ep = float(exit_premium) if exit_premium else None
    # Fetch ticket BEFORE removing so we can record P&L
    rec = next((r for r in tp.list_tracked() if r.get('id') == track_id), None)
    removed = tp.remove_tracked(track_id, exit_premium=ep, exit_reason=exit_reason)
    if removed:
        # Record P&L for manual exits (same as auto-exit path)
        if rec and ep is not None:
            try:
                ticket = rec.get('ticket') or {}
                entry_prem = float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
                qty = int(rec.get('qty', 1) or 1)
                trade_mode = ticket.get('trade_mode', 'swing')
                is_scalp = trade_mode == 'scalp' or bool(ticket.get('scalp_meta'))
                is_forex = trade_mode == 'forex'
                sym = ticket.get('trading_symbol', '')
                if entry_prem > 0:
                    is_short = ticket.get('direction') == 'SHORT'
                    if is_short:
                        gross_pnl = (entry_prem - ep) * qty
                    else:
                        gross_pnl = (ep - entry_prem) * qty
                    try:
                        from ..domain.services.brokerage_calc import segment_brokerage
                        _seg = 'forex' if is_forex else ('scalp' if is_scalp else trade_mode)
                        brokerage = segment_brokerage(_seg, entry_prem, ep, qty, gross_pnl=gross_pnl)
                    except Exception:
                        brokerage = 0.0
                    net_pnl = round(gross_pnl - brokerage, 2)
                    if is_scalp:
                        from ..engines.scalp_scanner import record_scalp_pnl
                        record_scalp_pnl(net_pnl, 0, exit_reason=exit_reason,
                                         underlying=ticket.get('underlying', ''),
                                         opt_type=ticket.get('instrument_type', '') or ticket.get('option_type', ''))
                    elif is_forex:
                        pass  # forex P&L logged below via pnl_store
                    else:
                        from ..engines.order_executor import record_exit_pnl
                        record_exit_pnl(entry_prem, ep, qty, net_pnl)
                    from ..infrastructure.db.pnl_store import record_trade as _rec_pnl
                    _mode = 'scalp' if is_scalp else ('forex' if is_forex else 'swing')
                    _entry_oid = ticket.get('entry_order_id', '') or ''
                    _rec_pnl(_mode, sym, ticket.get('underlying', ''),
                             entry_prem, ep, qty,
                             int(ticket.get('lot_size', 1) or 1), exit_reason,
                             order_id=_entry_oid)
                    import logging
                    logging.getLogger('vega').info(
                        f"[trade] Manual exit P&L: {sym} net ₹{net_pnl:+.2f} "
                        f"(entry ₹{entry_prem:.2f} exit ₹{ep:.2f} qty={qty})")
            except Exception as _e:
                import logging
                logging.getLogger('vega').warning(f"[trade] Manual exit P&L recording failed: {_e}")
        return {'success': True, 'data': removed}
    return JSONResponse({'success': False, 'error': 'Not found'}, 404)


# ── Executor status ──────────────────────────────────────────────────────────

@router.get('/executor/status')
def executor_status():
    """Return current executor state (matches old backend response shape)."""
    # All P&L from DB — single source of truth
    by_mode = state_store.today_net_by_mode()
    swing_pnl = round(by_mode.get('swing', 0.0), 2)
    scalp_pnl = round(by_mode.get('scalp', 0.0), 2)
    forex_pnl = round(by_mode.get('forex', 0.0), 2)
    return {
        'success': True,
        'data': {
            'auto_trading_enabled': _oe.auto_trading_enabled(),
            'scalp_auto_trading_enabled': settings.scalp_auto_trade,
            'live_trading_enabled': settings.live_trading_enabled,
            'kill_switch_active': _oe.is_kill_switch_active(),
            'daily_loss_limit_inr': _oe.daily_loss_limit(),
            'daily_realized_pnl': round(swing_pnl + scalp_pnl + forex_pnl, 2),
            'swing_realized_pnl': swing_pnl,
            'scalp_realized_pnl': scalp_pnl,
            'forex_realized_pnl': forex_pnl,
            'blocked_symbols': list(_oe._blocked_symbols),
            'tracked_count': len(tp.list_tracked()),
        },
    }


@router.post('/executor/auto-trading')
async def toggle_auto_trading(request: Request):
    body = await request.json()
    enabled = body.get('enabled', False)
    # settings is frozen (Pydantic); store in state_store for runtime toggle
    state_store.set_state('auto_trading_enabled', str(enabled).lower())
    return {'success': True, 'data': {'auto_trading_enabled': enabled}}


@router.post('/executor/scalp-auto-trading')
async def toggle_scalp_auto(request: Request):
    body = await request.json()
    enabled = body.get('enabled', False)
    settings.scalp_auto_trade = enabled
    return {'success': True, 'data': {'scalp_auto_trading_enabled': enabled}}


@router.post('/executor/force-exit/{track_id}')
def force_exit(track_id: str):
    rec = next((r for r in tp.list_tracked() if r.get('id') == track_id), None)
    if not rec:
        return JSONResponse({'success': False, 'message': f'Position {track_id} not found'}, 404)

    _oe.force_exit(track_id)  # add to _force_exit_ids so try_auto_exit sees is_forced=True

    def _do_exit():
        ticket = rec.get('ticket') or {}
        sym = ticket.get('trading_symbol', '')
        entry_fallback = float((ticket.get('entry') or {}).get('expected_premium_inr', 0) or 0)
        try:
            broker = get_broker()
            prem = broker.get_ltp(sym,
                                  exchange=ticket.get('exchange', 'NFO'),
                                  security_id=ticket.get('security_id', ''))
            # Paper/stub mode: broker.get_ltp returns None — try WS cache & monitor
            if not prem:
                try:
                    from ..engines.scalp_scanner import _option_ltp_cache
                    prem = _option_ltp_cache.get(sym.strip().upper())
                except Exception:
                    pass
            if not prem:
                try:
                    last = tm.get_status().get(track_id, {})
                    prem = last.get('premium') or None
                except Exception:
                    pass
            prem = prem or entry_fallback
            _oe.try_auto_exit(track_id, 'force_exit', rec, prem)
        except Exception as _e:
            logger.error(f'[trade] force-exit thread failed {track_id}: {_e}')

    Thread(target=_do_exit, daemon=True).start()
    return {'success': True, 'message': f'Force-exit executing for {track_id}'}


@router.post('/executor/reset-killswitch')
def reset_kill_switch():
    new_limit = _oe.reset_kill_switch()
    by_mode = state_store.today_net_by_mode()
    return {
        'success': True,
        'message': f'Swing kill switch reset — limit now ₹{new_limit:.0f}',
        'new_limit': new_limit,
        'daily_pnl': by_mode.get('swing', 0.0),
        'limit': new_limit,
    }


@router.post('/executor/reset-daily')
def executor_reset_daily():
    """Reset kill-switch and executor state. P&L stays in DB (source of truth)."""
    _oe.reset_daily()
    return {'success': True, 'message': 'Daily state reset — kill-switch off'}


@router.post('/executor/block-entry')
async def executor_block_entry(request: Request):
    """Block auto-entry for a specific symbol this session."""
    body = await request.json()
    symbol = (body.get('symbol') or '').strip()
    if not symbol:
        return JSONResponse({'success': False, 'error': 'symbol required'}, 400)
    _oe.block_symbol(symbol)
    return {'success': True, 'message': f'Auto-entry blocked for {symbol}'}


@router.post('/executor/unblock-entry')
async def executor_unblock_entry(request: Request):
    """Re-allow auto-entry for a symbol."""
    body = await request.json()
    symbol = (body.get('symbol') or '').strip()
    if not symbol:
        return JSONResponse({'success': False, 'error': 'symbol required'}, 400)
    _oe.unblock_symbol(symbol)
    return {'success': True, 'message': f'Auto-entry unblocked for {symbol}'}


# ── P&L ──────────────────────────────────────────────────────────────────────

@router.get('/pnl/summary')
def pnl_summary(date: str = '', market_type: str = ''):
    state_store.init_db()
    data = state_store.daily_summary(date or None, market_type or None)
    segments = state_store.daily_summary_by_segment(date or None)

    # Inject per-mode capital allocation so frontend can show ROI%
    capital = {
        'swing':  settings.swing_capital_inr,
        'scalp':  settings.scalp_capital_inr,
        'forex':  settings.forex_capital_inr,
        'crypto': settings.crypto_capital_usd,
    }
    for mode, cap in capital.items():
        if mode in segments:
            segments[mode]['capital'] = cap
            net = segments[mode].get('net', 0.0)
            segments[mode]['roi_pct'] = round(net / cap * 100, 2) if cap else 0.0

    return {'success': True, 'data': data, 'segments': segments, 'capital': capital}


@router.get('/pnl/trades')
def pnl_trades(
    limit: int = Query(50, ge=1, le=500),
    mode: str = Query('', description='swing | scalp | (empty for all)'),
    date: str = Query('', description='YYYY-MM-DD filter'),
    market_type: str = Query('', description='fo | crypto | poly | (empty for all)'),
):
    state_store.init_db()
    trades = state_store.recent_trades(limit, mode or None, market_type or None)
    if date:
        trades = [t for t in trades if t.get('date') == date]
    return {'success': True, 'trades': trades}


# ── F&O Scanner ──────────────────────────────────────────────────────────────

from ..engines import fo_scanner as _fo_svc


@router.get('/fo-scanner/status')
def fo_scanner_status():
    return {'success': True, 'data': _fo_svc.get_state()}


@router.post('/fo-scanner/start')
def fo_scanner_start():
    _fo_svc.start()
    return {'success': True, 'message': 'F&O scanner started'}


@router.post('/fo-scanner/stop')
def fo_scanner_stop():
    _fo_svc.stop()
    return {'success': True, 'message': 'F&O scanner stopped'}


@router.post('/fo-scanner/trigger')
def fo_scanner_trigger():
    _fo_svc.trigger_now()
    return {'success': True, 'message': 'Scan cycle triggered'}


@router.get('/fo-scanner/stream')
async def fo_scanner_stream(request: Request):
    import queue as _queue
    loop = asyncio.get_running_loop()
    q = await loop.run_in_executor(None, _fo_svc.subscribe_sse)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                # Drain all pending messages without blocking a thread
                sent = False
                while True:
                    try:
                        msg = q.get_nowait()
                        yield {'event': 'message', 'data': msg}
                        sent = True
                    except _queue.Empty:
                        break
                if not sent:
                    yield {'event': 'heartbeat', 'data': json.dumps({'ts': clock()})}
                await asyncio.sleep(1)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            loop.run_in_executor(None, _fo_svc.unsubscribe_sse, q)

    return EventSourceResponse(
        event_generator(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Scalp Scanner ────────────────────────────────────────────────────────────

from ..engines import btst_scanner as _btst_svc
from ..engines import scalp_scanner as _scalp_svc


@router.get('/scalp-scanner/status')
def scalp_scanner_status():
    return {'success': True, 'data': _scalp_svc.get_state()}


@router.get('/scalp-scanner/stats')
def scalp_scanner_stats():
    return {'success': True, 'data': _scalp_svc.get_scalp_stats()}




@router.post('/scalp-scanner/start')
def scalp_scanner_start():
    _scalp_svc.start()
    return {'success': True, 'message': 'Scalp scanner started'}


@router.post('/scalp-scanner/stop')
def scalp_scanner_stop():
    _scalp_svc.stop()
    return {'success': True, 'message': 'Scalp scanner stopped'}


@router.post('/scalp-scanner/trigger')
def scalp_scanner_trigger():
    _scalp_svc.trigger_now()
    return {'success': True, 'message': 'Scalp scan triggered'}


@router.post('/scalp-scanner/reset-daily')
def scalp_reset_daily():
    try:
        _scalp_svc.reset_scalp_daily()
    except AttributeError:
        pass
    return {'success': True, 'message': 'Daily scalp stats reset'}


@router.post('/scalp-scanner/reset-killswitch')
def scalp_reset_killswitch():
    """Re-enable scalp trading after kill-switch fires."""
    new_limit = _scalp_svc.reset_kill_switch()
    return {
        'success': True,
        'message': f'Kill switch reset — limit now ₹{new_limit:.0f}',
        'new_limit': new_limit,
        'stats': _scalp_svc.get_scalp_stats(),
    }


@router.get('/scalp-scanner/stream')
async def scalp_scanner_stream(request: Request):
    import queue as _queue
    loop = asyncio.get_running_loop()
    q = await loop.run_in_executor(None, _scalp_svc.subscribe_sse)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                sent = False
                while True:
                    try:
                        msg = q.get_nowait()
                        yield {'event': 'message', 'data': msg}
                        sent = True
                    except _queue.Empty:
                        break
                if not sent:
                    yield {'event': 'heartbeat', 'data': json.dumps({'ts': clock()})}
                await asyncio.sleep(1)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            loop.run_in_executor(None, _scalp_svc.unsubscribe_sse, q)

    return EventSourceResponse(
        event_generator(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Option chain ─────────────────────────────────────────────────────────────

def _parse_expiry_date(s) -> date | None:
    """Parse many expiry formats → datetime.date (ported from old broker_utils)."""
    if s is None or s == '':
        return None
    if isinstance(s, (int, float)):
        try:
            ts = float(s)
            if ts > 1e12: ts /= 1000.0
            return datetime.utcfromtimestamp(ts).date()
        except Exception:
            return None
    s = str(s).strip()
    if s.isdigit() and len(s) >= 10:
        try:
            ts = float(s)
            if ts > 1e12: ts /= 1000.0
            return datetime.utcfromtimestamp(ts).date()
        except Exception:
            pass
    if 'T' in s:
        s = s.split('T', 1)[0]
    if ' ' in s and ':' in s:
        s = s.split(' ', 1)[0]
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d %b %Y', '%d %B %Y',
                '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y',
                '%d%b%y', '%d%b%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s.upper(), fmt).date()
        except Exception:
            continue
    return None


def _inst_field(inst: dict, key: str, default=''):
    """Case-insensitive getter for instrument master rows.

    Supports column names from both INDmoney and Dhan CSV formats.
    """
    _ALIASES = {
        'trading_symbol': ('TRADING_SYMBOL', 'tradingsymbol', 'trading_symbol',
                           'SEM_TRADING_SYMBOL'),
        'option_type':    ('OPTION_TYPE', 'option_type', 'INSTRUMENT_TYPE', 'instrument_type',
                           'SEM_OPTION_TYPE', 'SEM_EXCH_INSTRUMENT_TYPE'),
        'expiry':         ('EXPIRY_DATE', 'expiry_date', 'expiry', 'EXPIRY',
                           'SEM_EXPIRY_DATE'),
        'strike':         ('STRIKE_PRICE', 'strike_price', 'strike', 'STRIKE',
                           'SEM_STRIKE_PRICE'),
        'security_id':    ('SECURITY_ID', 'security_id',
                           'SEM_SMST_SECURITY_ID'),
        'exchange':       ('EXCH', 'exchange', 'SEGMENT', 'segment',
                           'SEM_SEGMENT', 'SEM_EXM_EXCH_ID'),
        'lot_size':       ('LOT_UNITS', 'LOT_SIZE', 'lot_size', 'lot_units',
                           'SEM_LOT_UNITS'),
        'symbol_name':    ('SYMBOL_NAME', 'symbol_name',
                           'SM_SYMBOL_NAME', 'SEM_CUSTOM_SYMBOL'),
    }
    for alias in _ALIASES.get(key, (key,)):
        v = inst.get(alias)
        if v not in (None, ''):
            return v
    return default


@router.get('/option-chain')
def option_chain(
    underlying: str = Query('NIFTY'),
    strikes: int = Query(11, ge=3, le=50),
    min_dte: int = Query(2, ge=0),
    max_dte: int = Query(35, ge=1),
):
    """ATM-centred option chain with OI/volume — matches old backend contract."""
    from ..domain.value_objects.underlying import Underlying

    broker = get_broker()

    # ── Resolve underlying ────────────────────────────────────────────
    ticker = underlying.strip()
    try:
        und = Underlying.from_ticker(ticker)
        base = und.value
        step = und.strike_step
        exch = und.exchange          # NFO or BFO
    except ValueError:
        base = ticker.upper().replace('.NS', '').replace('.BO', '').lstrip('^')
        step = 0
        exch = 'NFO'

    # ── Get spot price ────────────────────────────────────────────────
    yahoo = {
        'NIFTY': '^NSEI', 'BANKNIFTY': '^NSEBANK', 'FINNIFTY': '^CNXFIN',
        'SENSEX': '^BSESN', 'MIDCPNIFTY': '^NSEMDCP50',
    }
    spot_ticker = yahoo.get(base, f'{base}.NS')
    spot = broker.get_ltp(spot_ticker, exchange='NSE')
    if not spot:
        try:
            import yfinance as yf
            info = yf.Ticker(spot_ticker).fast_info
            spot = getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None)
        except Exception:
            pass
    if not spot:
        return JSONResponse({'success': False, 'error': f'No spot for {ticker}'}, 404)
    spot = float(spot)

    # ── Dynamic strike step for stocks ────────────────────────────────
    if not step:
        if spot <= 50:     step = 1
        elif spot <= 100:  step = 2
        elif spot <= 250:  step = 5
        elif spot <= 500:  step = 10
        elif spot <= 1000: step = 20
        elif spot <= 2500: step = 50
        else:              step = 100

    # ── Scan F&O master for nearest expiry ────────────────────────────
    today = today_ist()
    fno_rows = broker.load_instruments('fno')
    best_expiry = None
    for inst in fno_rows:
        sym = str(_inst_field(inst, 'trading_symbol', '')).strip().upper()
        if not sym.startswith(base):
            continue
        opt = str(_inst_field(inst, 'option_type', '')).strip().upper()
        if opt not in ('CE', 'PE'):
            continue
        exp = _parse_expiry_date(_inst_field(inst, 'expiry', ''))
        if not exp:
            continue
        dte = (exp - today).days
        if dte < min_dte or dte > max_dte:
            continue
        if best_expiry is None or exp < best_expiry:
            best_expiry = exp

    if not best_expiry:
        return JSONResponse({'success': False,
                             'error': f'No expiry in [{min_dte},{max_dte}]d for {base}'}, 404)

    dte_days = (best_expiry - today).days

    # ── Build strike ladder around ATM ────────────────────────────────
    atm_strike = int(round(spot / step) * step)
    half = strikes // 2
    target_strikes = [atm_strike + (i - half) * step for i in range(strikes)]

    # ── Index F&O master by (option_type, strike) for this expiry ─────
    rows_ce: dict[int, dict] = {}
    rows_pe: dict[int, dict] = {}
    for inst in fno_rows:
        sym = str(_inst_field(inst, 'trading_symbol', '')).strip().upper()
        if not sym.startswith(base):
            continue
        exp = _parse_expiry_date(_inst_field(inst, 'expiry', ''))
        if exp != best_expiry:
            continue
        opt = str(_inst_field(inst, 'option_type', '')).strip().upper()
        try:
            k = int(float(_inst_field(inst, 'strike', 0) or 0))
        except Exception:
            continue
        seg = str(_inst_field(inst, 'exchange', exch)).strip().upper()
        if 'NFO' in seg or 'NSE' in seg:   seg = 'NFO'
        elif 'BFO' in seg or 'BSE' in seg: seg = 'BFO'
        try:
            lot = int(float(_inst_field(inst, 'lot_size', 0) or 0))
        except Exception:
            lot = 0
        meta = {
            'security_id':    str(_inst_field(inst, 'security_id', '')).strip(),
            'exchange':       seg,
            'lot_size':       lot,
            'strike':         k,
            'trading_symbol': sym,
        }
        if opt == 'CE':
            rows_ce[k] = meta
        elif opt == 'PE':
            rows_pe[k] = meta

    chain = []
    for k in target_strikes:
        ce = rows_ce.get(k)
        pe = rows_pe.get(k)
        if not ce and not pe:
            continue
        chain.append({'strike': k, 'ce': ce, 'pe': pe})

    if not chain:
        return JSONResponse({'success': False,
                             'error': f'No strikes resolved around ATM for {base}'}, 404)

    # ── Batch quote enrichment (OI / volume / bid-ask) ────────────────
    codes: list[str] = []
    for row in chain:
        if row['ce'] and row['ce'].get('security_id'):
            codes.append(f"{row['ce']['exchange']}_{row['ce']['security_id']}")
        if row['pe'] and row['pe'].get('security_id'):
            codes.append(f"{row['pe']['exchange']}_{row['pe']['security_id']}")

    # Batch fetch quotes via broker abstraction (works for any broker)
    quotes: dict[str, dict] = broker.get_quotes_batch(codes) if codes else {}

    def _enrich(meta):
        if not meta:
            return None
        c = f"{meta['exchange']}_{meta['security_id']}"
        q = quotes.get(c) or {}
        bid  = float(q.get('bid', 0) or 0)
        ask  = float(q.get('ask', 0) or 0)
        ltp  = float(q.get('ltp', 0) or 0)
        oi   = int(q.get('oi', 0) or 0)
        vol  = int(q.get('volume', 0) or 0)
        spread_pct = (
            round((ask - bid) / ((bid + ask) / 2) * 100, 2)
            if bid > 0 and ask > 0 else None
        )
        return {**meta, 'bid': bid, 'ask': ask, 'ltp': ltp,
                'oi': oi, 'volume': vol, 'spread_pct': spread_pct}

    enriched = []
    for row in chain:
        enriched.append({
            'strike': row['strike'],
            'ce': _enrich(row['ce']),
            'pe': _enrich(row['pe']),
        })

    # ── Aggregate metrics ─────────────────────────────────────────────
    total_ce_oi  = sum(r['ce']['oi']     for r in enriched if r['ce'])
    total_pe_oi  = sum(r['pe']['oi']     for r in enriched if r['pe'])
    total_ce_vol = sum(r['ce']['volume'] for r in enriched if r['ce'])
    total_pe_vol = sum(r['pe']['volume'] for r in enriched if r['pe'])
    pcr_oi  = round(total_pe_oi  / total_ce_oi,  2) if total_ce_oi  else None
    pcr_vol = round(total_pe_vol / total_ce_vol, 2) if total_ce_vol else None

    # Max pain
    max_pain = None
    if total_ce_oi + total_pe_oi > 0:
        candidates = [r['strike'] for r in enriched]
        scores = []
        for K in candidates:
            pain = 0.0
            for r in enriched:
                s = r['strike']
                ce_oi = r['ce']['oi'] if r['ce'] else 0
                pe_oi = r['pe']['oi'] if r['pe'] else 0
                if s < K:
                    pain += (K - s) * ce_oi
                elif s > K:
                    pain += (s - K) * pe_oi
            scores.append((pain, K))
        max_pain = min(scores)[1]

    lot_size = 0
    if enriched:
        lot_size = (enriched[0]['ce']['lot_size'] if enriched[0].get('ce')
                    else enriched[0]['pe']['lot_size'] if enriched[0].get('pe') else 0)

    return {
        'success': True,
        'data': {
            'underlying':     ticker,
            'base':           base,
            'spot':           round(spot, 2),
            'atm_strike':     atm_strike,
            'step':           step,
            'expiry':         best_expiry.isoformat(),
            'days_to_expiry': dte_days,
            'lot_size':       lot_size,
            'strikes':        enriched,
            'totals': {
                'ce_oi':      total_ce_oi,
                'pe_oi':      total_pe_oi,
                'ce_volume':  total_ce_vol,
                'pe_volume':  total_pe_vol,
                'pcr_oi':     pcr_oi,
                'pcr_volume': pcr_vol,
                'max_pain':   max_pain,
            },
        },
    }


# ── Option Plan ──────────────────────────────────────────────────────────────

@router.api_route('/option-plan', methods=['GET', 'POST'])
async def option_plan(request: Request):
    """Produce a precise, executable option trade ticket."""
    from ..domain.services.option_planner import plan_option_trade

    if request.method == 'POST':
        body = await request.json()
    else:
        body = dict(request.query_params)

    ticker = (body.get('ticker') or '').strip()
    bias   = (body.get('bias')   or '').upper().strip()
    if not ticker or bias not in ('BULL', 'BEAR'):
        return JSONResponse({'success': False, 'error': 'ticker and bias=BULL|BEAR required'}, 400)

    try:
        _f = lambda k: float(body[k]) if body.get(k) not in (None, '', 0) else None
        ticket = plan_option_trade(
            underlying=ticker,
            bias=bias,
            spot=_f('spot'),
            target_delta=float(body.get('target_delta') or 0.50),
            min_dte=int(body.get('min_dte') or 3),
            max_dte=int(body.get('max_dte') or 21),
            rationale=body.get('rationale') or 'manual /option-plan',
            spot_target_1  = _f('spot_target_1'),
            spot_target_2  = _f('spot_target_2'),
            spot_stop_loss = _f('spot_stop_loss'),
            atr            = _f('atr'),
        )
        if not ticket:
            return JSONResponse({'success': False,
                                 'error': 'Could not build ticket — check ticker, '
                                          'broker token, or expiry availability'}, 404)
        return {'success': True, 'data': ticket}
    except Exception as e:
        logger.error(f"option-plan failed: {e}", exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, 500)


# ── Levels ───────────────────────────────────────────────────────────────────

@router.get('/levels/{ticker:path}')
def trade_levels(ticker: str, days: int = Query(180)):
    """Compute entry/SL/targets using Floor Pivot Points + CPR (same as old backend)."""
    from .market import _normalize_ticker
    ticker = _normalize_ticker(ticker)

    levels_key = _cache_key('levels', ticker, days)
    cached = _cache_get(levels_key)
    if cached:
        return {'success': True, 'data': cached, '_cached': True}

    try:

        df = _fetch_ohlcv(ticker, days=days)
        if df is None or len(df) < 5:
            return JSONResponse({'success': False, 'error': f'Not enough data for {ticker}'}, 404)

        lv     = _compute_levels(df)
        price  = float(df['close'].iloc[-1])
        c      = df['close']
        ema20  = float(c.ewm(span=20,  adjust=False).mean().iloc[-1])
        ema50  = float(c.ewm(span=50,  adjust=False).mean().iloc[-1])
        ema200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])
        rsi    = lv['rsi']
        entry  = lv['entry']
        risk   = lv['risk']

        if rsi < 40:
            reason = f"RSI oversold ({rsi:.1f}) — pivot S1/S2 reversal zone"
        elif price > ema20 > ema50:
            reason = "Uptrend intact (EMA20 > EMA50) — buy pullback to EMA20"
        elif lv['supports']:
            reason = f"Near pivot support S1 ({lv['supports'][0]:,.2f})"
        else:
            reason = "Entry at current market price"

        if rsi > 60 and price < ema20:
            bias = "BEARISH"
        elif rsi < 40 or (price > ema20 > ema50):
            bias = "BULLISH"
        else:
            bias = "NEUTRAL"

        levels_data = {
            "ticker":           ticker,
            "current_price":    round(price, 4),
            "bias":             bias,
            "entry":            lv['entry'],
            "stop_loss":        lv['stop_loss'],
            "target_1":         lv['target_1'],
            "target_2":         lv['target_2'],
            "target_3":         lv['target_3'],
            "risk_per_share":   lv['risk'],
            "reward_t1":        round(lv['target_1'] - entry, 4),
            "rr_t1":            round((lv['target_1'] - entry) / risk, 2) if risk > 0 else None,
            "rr_t2":            round((lv['target_2'] - entry) / risk, 2) if risk > 0 else None,
            "atr":              lv['atr'],
            "rsi":              round(rsi, 1),
            "pivot":            lv.get('pivot'),
            "ema20":            round(ema20, 4),
            "ema50":            round(ema50, 4),
            "ema200":           round(ema200, 4),
            "entry_reason":     reason,
            "supports":         lv['supports'],
            "resistances":      lv['resistances'],
            "cpr_pp":           lv.get('cpr_pp'),
            "cpr_bc":           lv.get('cpr_bc'),
            "cpr_tc":           lv.get('cpr_tc'),
            "cpr_width_pct":    lv.get('cpr_width_pct'),
            "cpr_type":         lv.get('cpr_type'),
        }
        _cache_set(levels_key, levels_data, ttl=300)
        return {'success': True, 'data': levels_data}

    except Exception as e:
        logger.error(f"Levels failed for {ticker}: {e}", exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, 500)


# ── Indicators ───────────────────────────────────────────────────────────────

@router.get('/indicators/{ticker:path}')
def indicators(ticker: str, interval: str = '1d', days: int = 365):
    """Return RSI, MACD, Bollinger Bands, EMA-20/50, ATR for chart overlays."""
    ticker   = ticker.upper().strip()
    # yfinance caps intraday history at ~60 days; enforce it
    if interval in ('1h', '30m', '15m', '5m', '1m'):
        days = min(days, 59)

    try:
        import math

        import numpy as np

        df = _fetch_ohlcv(ticker, days=days, interval=interval)
        if df is None:
            return JSONResponse({'success': False, 'error': f'No data for {ticker}'}, 404)

        c = df['close']
        h = df['high']
        l = df['low']

        # RSI-14
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))

        # MACD (12,26,9)
        ema12  = c.ewm(span=12, adjust=False).mean()
        ema26  = c.ewm(span=26, adjust=False).mean()
        macd_l = ema12 - ema26
        macd_s = macd_l.ewm(span=9, adjust=False).mean()
        macd_h = macd_l - macd_s

        # Bollinger Bands (20, 2σ)
        sma20    = c.rolling(20).mean()
        std20    = c.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20

        # EMA 20 / 50
        ema20 = c.ewm(span=20, adjust=False).mean()
        ema50 = c.ewm(span=50, adjust=False).mean()

        # ATR-14
        tr = np.maximum(h - l, np.maximum(abs(h - c.shift()), abs(l - c.shift())))
        atr = tr.rolling(14).mean()

        def safe(v):
            if v is None: return None
            try:
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else round(f, 4)
            except Exception:
                return None

        result = []
        for i, (ts, row) in enumerate(df.iterrows()):
            result.append({
                "date":       ts.strftime('%Y-%m-%d') if interval == '1d' else ts.isoformat(),
                "rsi":        safe(rsi.iloc[i]),
                "macd":       safe(macd_l.iloc[i]),
                "macd_signal":safe(macd_s.iloc[i]),
                "macd_hist":  safe(macd_h.iloc[i]),
                "bb_upper":   safe(bb_upper.iloc[i]),
                "bb_mid":     safe(sma20.iloc[i]),
                "bb_lower":   safe(bb_lower.iloc[i]),
                "ema20":      safe(ema20.iloc[i]),
                "ema50":      safe(ema50.iloc[i]),
                "atr":        safe(atr.iloc[i]),
                "close":      safe(row.get('close')),
            })

        # Current signal summary (last bar)
        last = result[-1] if result else {}
        signal = "NEUTRAL"
        if last.get('rsi') and last.get('ema20') and last.get('ema50'):
            _rsi  = last['rsi']
            _price = safe(df['close'].iloc[-1])
            _ema20 = last['ema20']
            _ema50 = last['ema50']
            if _rsi < 35 and _price and _price > _ema20:
                signal = "BUY"
            elif _rsi > 65 and _price and _price < _ema20:
                signal = "SELL"
            elif _ema20 and _ema50 and _ema20 > _ema50:
                signal = "BULLISH"
            elif _ema20 and _ema50 and _ema20 < _ema50:
                signal = "BEARISH"

        return {
            'success': True,
            'data': {
                'ticker':  ticker,
                'signal':  signal,
                'indicators': result,
                'latest': last,
            },
        }

    except Exception as e:
        logger.error(f"Indicators failed for {ticker}: {e}", exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, 500)


# ── Rank ─────────────────────────────────────────────────────────────────────

@router.post('/rank')
async def rank_tickers(request: Request):
    body = await request.json()
    tickers = [t.upper().strip() for t in body.get('tickers', [])][:20]
    if not tickers:
        return JSONResponse({'success': False, 'error': 'Provide tickers list'}, status_code=400)

    results = []
    for ticker in tickers:
        try:
            df = _fetch_ohlcv(ticker, days=60)
            if df is None or len(df) < 20:
                continue

            closes = df['close'].tolist()
            vols   = df['volume'].tolist()
            price  = closes[-1]

            c = df['close']
            delta = c.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, float('nan'))
            rsi_s = 100 - (100 / (1 + rs))
            rsi   = float(rsi_s.iloc[-1]) if not rsi_s.empty else 50.0

            ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

            chg5d     = ((closes[-1] - closes[-6]) / closes[-6] * 100
                         if len(closes) >= 6 and closes[-6] != 0 else 0)
            avg_vol   = sum(vols[:-1]) / max(len(vols) - 1, 1)
            vol_ratio = vols[-1] / avg_vol if avg_vol else 1

            rsi_score = max(0, min(100, (50 - rsi) * 2 + 50))
            ema_score = 100 if (price > ema20 > ema50) else (50 if price > ema20 else 0)
            vol_score = min(100, max(0, (vol_ratio - 1) * 40 + 50))
            mom_score = min(100, max(0, chg5d * 5 + 50))

            composite = round(rsi_score * 0.30 + ema_score * 0.25
                              + vol_score * 0.25 + mom_score * 0.20, 1)

            action = ('STRONG BUY' if composite >= 70 else
                      'BUY'        if composite >= 58 else
                      'HOLD'       if composite >= 45 else
                      'SELL'       if composite >= 30 else 'STRONG SELL')

            results.append({
                'ticker':    ticker,
                'price':     round(float(price), 2),
                'score':     composite,
                'action':    action,
                'rsi':       round(rsi, 1),
                'vol_ratio': round(vol_ratio, 2),
                'chg5d':     round(chg5d, 2),
                'ema_trend': ('BULL' if price > ema20 > ema50 else
                              'BEAR' if price < ema20 < ema50 else 'MIXED'),
            })
        except Exception as e:
            logger.warning('Rank skip %s: %s', ticker, e)

    results.sort(key=lambda x: x['score'], reverse=True)
    return {'success': True, 'data': results}


# ── Intraday signal ──────────────────────────────────────────────────────────

@router.get('/intraday-signal/{ticker:path}')
def intraday_signal(ticker: str):
    """Fast intraday signal — pure technical math on 1h bars (same as old backend)."""
    ticker = ticker.upper().strip()
    try:
        import numpy as np

        # 1h OHLCV — broker first, yfinance fallback
        df = _fetch_ohlcv(ticker, days=30, interval='1h')
        if df is None or len(df) < 20:
            return JSONResponse({'success': False, 'error': f'Not enough intraday data for {ticker}'}, 404)

        price = float(df['close'].iloc[-1])
        c = df['close']
        h = df['high']
        l = df['low']
        v = df['volume']

        # RSI-14
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        _rsi_raw = (100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1]
        rsi = float(_rsi_raw) if not np.isnan(_rsi_raw) else 50.0

        # EMA 9 / 21
        ema9  = float(c.ewm(span=9,  adjust=False).mean().iloc[-1])
        ema21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
        ema9_prev  = float(c.ewm(span=9,  adjust=False).mean().iloc[-2])
        ema21_prev = float(c.ewm(span=21, adjust=False).mean().iloc[-2])
        ema_cross  = (ema9 > ema21)
        fresh_cross = (ema9 > ema21) != (ema9_prev > ema21_prev)

        # VWAP (rolling today approximation: last 6 1h bars)
        bars = min(len(df), 6)
        typical = (h.iloc[-bars:] + l.iloc[-bars:] + c.iloc[-bars:]) / 3
        vwap = float((typical * v.iloc[-bars:]).sum() / v.iloc[-bars:].sum()) if v.iloc[-bars:].sum() > 0 else price
        above_vwap = price > vwap

        # Volume spike
        avg_vol   = float(v.iloc[-21:-1].mean()) if len(v) > 21 else float(v.mean())
        vol_ratio = float(v.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

        # ATR-14
        tr  = np.maximum(h - l, np.maximum(abs(h - c.shift()), abs(l - c.shift())))
        _atr_raw = tr.rolling(14).mean().iloc[-1]
        atr = float(_atr_raw) if not np.isnan(_atr_raw) else float(tr.mean())

        # CPR from previous trading day
        _daily = df.resample('D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
        cpr_data = None
        if len(_daily) >= 2:
            cpr_data = _cpr(
                float(_daily['high'].iloc[-2]),
                float(_daily['low'].iloc[-2]),
                float(_daily['close'].iloc[-2]),
            )

        # Score (0–100)
        rsi_score  = max(0, min(100, (50 - rsi) * 2 + 50))
        ema_score  = 80 if ema_cross else 20
        vwap_score = 70 if above_vwap else 30
        vol_score  = min(100, max(0, (vol_ratio - 1) * 40 + 50))
        chg3h      = float((c.iloc[-1] - c.iloc[-4]) / c.iloc[-4] * 100) if len(c) >= 4 else 0
        mom_score  = min(100, max(0, chg3h * 10 + 50))

        score = round(
            rsi_score  * 0.25 +
            ema_score  * 0.25 +
            vwap_score * 0.20 +
            vol_score  * 0.15 +
            mom_score  * 0.15, 1)

        if cpr_data:
            if cpr_data['type'] == 'narrow':
                score = round(min(100.0, score + 3), 1)
            else:
                score = round(max(0.0, score - 3), 1)

        # Action
        if score >= 63:
            action = "BUY"
        elif score <= 37:
            action = "SELL"
        else:
            action = "HOLD"

        # Reasons
        reasons = []
        if rsi < 35:   reasons.append(f"RSI {rsi:.0f} — oversold")
        elif rsi > 65: reasons.append(f"RSI {rsi:.0f} — overbought")
        else:          reasons.append(f"RSI {rsi:.0f} — neutral")

        if fresh_cross:
            reasons.append(f"EMA9/21 {'bullish' if ema_cross else 'bearish'} crossover just triggered")
        elif ema_cross:
            reasons.append("EMA9 above EMA21 — uptrend intact")
        else:
            reasons.append("EMA9 below EMA21 — downtrend")

        reasons.append(f"Price {'above' if above_vwap else 'below'} VWAP ({vwap:.2f})")

        if vol_ratio >= 1.5:
            reasons.append(f"Volume spike {vol_ratio:.1f}x avg — strong conviction")
        elif vol_ratio <= 0.6:
            reasons.append(f"Low volume {vol_ratio:.1f}x avg — weak move")

        if cpr_data:
            if cpr_data['type'] == 'narrow':
                reasons.append(f"Narrow CPR ({cpr_data['width_pct']}%) — trending day, momentum trade favoured")
            else:
                reasons.append(f"Wide CPR ({cpr_data['width_pct']}%) — choppy day expected, tighter targets")

        # Levels
        entry = round(price, 2)
        sl    = round(price - atr * 1.2, 2)
        t1    = round(price + atr * 1.5, 2)
        t2    = round(price + atr * 2.5, 2)

        return {
            'success': True,
            'data': {
                'ticker':      ticker,
                'action':      action,
                'score':       score,
                'price':       round(price, 2),
                'rsi':         round(rsi, 1),
                'ema_bull':    ema_cross,
                'fresh_cross': fresh_cross,
                'above_vwap':  above_vwap,
                'vwap':        round(vwap, 2),
                'vol_ratio':   round(vol_ratio, 2),
                'atr':         round(atr, 2),
                'entry':       entry,
                'sl':          sl,
                't1':          t1,
                't2':          t2,
                'cpr_type':    cpr_data['type']              if cpr_data else None,
                'cpr_pp':      round(cpr_data['pp'], 2)      if cpr_data else None,
                'cpr_bc':      round(cpr_data['bc'], 2)      if cpr_data else None,
                'cpr_tc':      round(cpr_data['tc'], 2)      if cpr_data else None,
                'reasons':     reasons,
                'interval':    '1h',
            },
        }

    except Exception as e:
        logger.error(f"Intraday signal failed for {ticker}: {e}", exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, 500)


# ── Backtest ─────────────────────────────────────────────────────────────────

@router.get('/backtest/{ticker:path}')
def backtest(ticker: str, days: int = 365, cash: float = 10000):
    import numpy as np
    import pandas as pd
    try:
        import backtrader as bt
    except ImportError:
        return JSONResponse({'success': False, 'error': 'backtrader not installed'}, status_code=501)

    try:
        df = _fetch_ohlcv(ticker, days=days)
        if df is None or len(df) < 60:
            return JSONResponse({'success': False, 'error': f'Not enough data for {ticker}'}, status_code=404)

        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = 'datetime'

        class FOMOStrategy(bt.Strategy):
            params = dict(rsi_period=14, ema_period=20, rsi_buy=40, rsi_sell=65)

            def __init__(self):
                self.rsi      = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
                self.ema      = bt.indicators.EMA(self.data.close, period=self.p.ema_period)
                self.vol_sma  = bt.indicators.SMA(self.data.volume, period=10)
                self.order    = None
                self.trades_log = []

            def notify_order(self, order):
                if order.status in [order.Completed]:
                    side = 'BUY' if order.isbuy() else 'SELL'
                    self.trades_log.append({
                        'date':  self.data.datetime.date(0).isoformat(),
                        'side':  side,
                        'price': round(order.executed.price, 2),
                        'size':  order.executed.size,
                    })
                self.order = None

            def next(self):
                if self.order:
                    return
                vol_ratio = (self.data.volume[0] / self.vol_sma[0]) if self.vol_sma[0] else 1
                if not self.position:
                    if (self.rsi[0] < self.p.rsi_buy
                            and self.data.close[0] > self.ema[0]
                            and vol_ratio > 1.2):
                        size = int(self.broker.getcash() * 0.95 / self.data.close[0])
                        if size > 0:
                            self.order = self.buy(size=size)
                else:
                    if self.rsi[0] > self.p.rsi_sell or self.data.close[0] < self.ema[0]:
                        self.order = self.close()

        class PandasOHLCV(bt.feeds.PandasData):
            params = (('datetime', None), ('open', 'open'), ('high', 'high'),
                      ('low', 'low'), ('close', 'close'), ('volume', 'volume'),
                      ('openinterest', None))

        cerebro = bt.Cerebro()
        cerebro.addstrategy(FOMOStrategy)
        cerebro.adddata(PandasOHLCV(dataname=df))
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        cerebro.addanalyzer(bt.analyzers.SharpeRatio,  _name='sharpe', riskfreerate=0.05)
        cerebro.addanalyzer(bt.analyzers.DrawDown,      _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')

        bt_results = cerebro.run()
        strat      = bt_results[0]
        final_val  = cerebro.broker.getvalue()

        sharpe_a  = strat.analyzers.sharpe.get_analysis()
        dd_a      = strat.analyzers.drawdown.get_analysis()
        trades_a  = strat.analyzers.trades.get_analysis()

        total_t   = trades_a.get('total', {}).get('total', 0)
        won_t     = trades_a.get('won',   {}).get('total', 0)
        win_rate  = round(won_t / total_t * 100, 1) if total_t else 0
        _sr       = sharpe_a.get('sharperatio')
        sharpe    = 0 if (_sr is None or (_sr != _sr)) else _sr
        max_dd    = dd_a.get('max', {}).get('drawdown', 0)
        total_ret = round((final_val - cash) / cash * 100, 2)

        last_close = float(df['close'].iloc[-1])
        _h = df['high']; _l = df['low']; _c = df['close']
        _tr = np.maximum(_h - _l, np.maximum(abs(_h - _c.shift()), abs(_l - _c.shift())))
        atr_val   = float(_tr.rolling(14).mean().iloc[-1]) if len(_tr) > 14 else last_close * 0.02
        stop_dist = atr_val * 1.5
        recommended_size = int(cash * 0.01 / stop_dist) if stop_dist else 1

        return {
            'success': True,
            'data': {
                'ticker':           ticker,
                'days':             days,
                'start_cash':       cash,
                'final_value':      round(final_val, 2),
                'total_return_pct': total_ret,
                'sharpe_ratio':     round(float(sharpe), 3) if sharpe else None,
                'max_drawdown_pct': round(float(max_dd), 2),
                'total_trades':     total_t,
                'win_rate_pct':     win_rate,
                'won_trades':       won_t,
                'lost_trades':      total_t - won_t,
                'risk_mgmt': {
                    'recommended_size':   recommended_size,
                    'stop_loss_distance': round(stop_dist, 2),
                    'entry_price':        round(last_close, 2),
                    'stop_price':         round(last_close - stop_dist, 2),
                    'take_profit_price':  round(last_close + stop_dist * 2, 2),
                    'risk_per_trade':     round(cash * 0.01, 2),
                },
                'recent_trades': strat.trades_log[-10:],
            },
        }

    except Exception as e:
        logger.error('Backtest failed for %s: %s', ticker, e, exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


@router.get('/vbt-backtest/{ticker:path}')
def vbt_backtest(ticker: str, days: int = 365, cash: float = 100_000):
    try:
        import vectorbt as vbt
    except ImportError:
        return JSONResponse({'success': False, 'error': 'vectorbt not installed'}, status_code=501)

    try:
        df = _fetch_ohlcv(ticker, days=days)
        if df is None or len(df) < 60:
            return JSONResponse({'success': False, 'error': f'Not enough data for {ticker}'}, status_code=404)

        price  = df['close'].astype(float)
        bh_ret = round((float(price.iloc[-1]) - float(price.iloc[0])) / float(price.iloc[0]) * 100, 2)

        def _safe(val, default=0, decimals=2):
            try:
                v = float(val)
                if v != v or v in (float('inf'), float('-inf')):
                    return default
                return round(v, decimals)
            except Exception:
                return default

        def _stats(pf):
            s = pf.stats()
            return {
                'total_return_pct': _safe(s.get('Total Return [%]'),   0, 2),
                'sharpe':           _safe(s.get('Sharpe Ratio'),        0, 3),
                'max_drawdown_pct': _safe(s.get('Max Drawdown [%]'),    0, 2),
                'win_rate_pct':     _safe(s.get('Win Rate [%]'),        0, 1),
                'total_trades':     int(_safe(s.get('Total Trades'),    0, 0)),
                'final_value':      _safe(pf.final_value(),             cash, 2),
            }

        strategies = {}

        try:
            rsi = vbt.RSI.run(price, window=14).rsi
            pf  = vbt.Portfolio.from_signals(price,
                      rsi.vbt.crossed_below(35), rsi.vbt.crossed_above(65),
                      init_cash=cash, fees=0.001, freq='1D')
            strategies['RSI Bounce (14, 35/65)'] = _stats(pf)
        except Exception as e:
            strategies['RSI Bounce (14, 35/65)'] = {'error': str(e)}

        try:
            e9  = vbt.MA.run(price, window=9,  ewm=True).ma
            e21 = vbt.MA.run(price, window=21, ewm=True).ma
            pf  = vbt.Portfolio.from_signals(price,
                      e9.vbt.crossed_above(e21), e9.vbt.crossed_below(e21),
                      init_cash=cash, fees=0.001, freq='1D')
            strategies['EMA Crossover (9/21)'] = _stats(pf)
        except Exception as e:
            strategies['EMA Crossover (9/21)'] = {'error': str(e)}

        try:
            e21b = vbt.MA.run(price, window=21, ewm=True).ma
            e50  = vbt.MA.run(price, window=50, ewm=True).ma
            pf   = vbt.Portfolio.from_signals(price,
                       e21b.vbt.crossed_above(e50), e21b.vbt.crossed_below(e50),
                       init_cash=cash, fees=0.001, freq='1D')
            strategies['EMA Crossover (21/50)'] = _stats(pf)
        except Exception as e:
            strategies['EMA Crossover (21/50)'] = {'error': str(e)}

        try:
            bb  = vbt.BBANDS.run(price, window=20, alpha=2)
            pf  = vbt.Portfolio.from_signals(price,
                      price.vbt.crossed_above(bb.lower), price.vbt.crossed_above(bb.upper),
                      init_cash=cash, fees=0.001, freq='1D')
            strategies['Bollinger Bands (20, 2σ)'] = _stats(pf)
        except Exception as e:
            strategies['Bollinger Bands (20, 2σ)'] = {'error': str(e)}

        valid = {k: v for k, v in strategies.items() if 'error' not in v}
        best  = max(valid, key=lambda k: valid[k]['sharpe']) if valid else None

        return {
            'success': True,
            'data': {
                'ticker':           ticker,
                'days':             days,
                'start_cash':       cash,
                'buy_and_hold_pct': bh_ret,
                'strategies':       strategies,
                'best_strategy':    best,
                'note': 'Sharpe > 1.0 = good, > 2.0 = excellent.',
            },
        }

    except Exception as e:
        logger.error('VBT backtest failed for %s: %s', ticker, e, exc_info=True)
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


# ── Tracked alerts ──────────────────────────────────────────────────────────

@router.get('/tracked/alerts/state')
def tracked_alerts_state():
    """Diagnostic — current per-position last-known status & alert payload."""
    return {'success': True, 'data': tm.get_status()}


@router.get('/tracked/alerts/stream')
async def tracked_alerts_stream(request: Request):
    import queue as _queue
    loop = asyncio.get_running_loop()
    q = await loop.run_in_executor(None, tm.subscribe_sse)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                sent = False
                while True:
                    try:
                        payload = q.get_nowait()
                        yield {'event': 'message', 'data': json.dumps(payload, default=str)}
                        sent = True
                    except _queue.Empty:
                        break
                if not sent:
                    yield {'event': 'heartbeat', 'data': json.dumps({'ts': clock()})}
                await asyncio.sleep(1)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            loop.run_in_executor(None, tm.unsubscribe_sse, q)

    return EventSourceResponse(
        event_generator(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── BTST Scanner ──────────────────────────────────────────────────────────────

@router.get('/btst-scanner/status')
def btst_scanner_status():
    return {'success': True, 'data': _btst_svc.get_state()}


@router.get('/btst-scanner/stats')
def btst_scanner_stats():
    return {'success': True, 'data': _btst_svc.get_stats()}


@router.get('/btst-scanner/config')
def btst_scanner_config():
    return {'success': True, 'data': {'params': _btst_svc.get_all_config()}}


@router.put('/btst-scanner/config')
async def update_btst_config(request: Request):
    body = await request.json()
    if not body:
        return JSONResponse({'success': False, 'message': 'No params provided'}, status_code=400)
    valid = {k: v for k, v in body.items() if k in _btst_svc.BTST_CONFIG_SCHEMA}
    if not valid:
        return JSONResponse({
            'success': False,
            'message': f'No valid keys. Valid: {list(_btst_svc.BTST_CONFIG_SCHEMA.keys())}',
        }, status_code=400)
    updated = _btst_svc.update_config(valid)
    return {'success': True, 'data': {'params': updated}}


@router.post('/btst-scanner/start')
def btst_scanner_start():
    _btst_svc.start()
    return {'success': True, 'message': 'BTST scanner started'}


@router.post('/btst-scanner/stop')
def btst_scanner_stop():
    _btst_svc.stop()
    return {'success': True, 'message': 'BTST scanner stopped'}


@router.post('/btst-scanner/trigger')
def btst_scanner_trigger():
    _btst_svc.trigger_now()
    return {'success': True, 'message': 'BTST scan triggered manually'}


@router.get('/btst-scanner/stream')
async def btst_scanner_stream(request: Request):
    import queue as _queue
    loop = asyncio.get_running_loop()
    q = await loop.run_in_executor(None, _btst_svc.subscribe_sse)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await loop.run_in_executor(None, q.get, True, 1.0)
                    yield {'data': msg}
                except _queue.Empty:
                    yield {'data': json.dumps({'type': 'heartbeat'})}
        finally:
            await loop.run_in_executor(None, _btst_svc.unsubscribe_sse, q)

    return EventSourceResponse(
        event_generator(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
