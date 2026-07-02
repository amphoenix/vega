from __future__ import annotations

import json
import logging
import os
import sqlite3

from app.shared.logger import get_logger
from app.shared.time import now_ist as _now_ist, IST, is_trading_day, clock
import pathlib
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any


# ── Config Schema ─────────────────────────────────────────────────────────────

BTST_CONFIG_SCHEMA: dict[str, dict] = {
    'BTST_MAX_POSITIONS':      {'default': 3,       'type': int,   'label': 'Max Positions',           'group': 'Limits'},
    'BTST_MIN_SCORE':          {'default': 50,      'type': int,   'label': 'Min Score',               'group': 'Filters'},
    'BTST_NOTIONAL_PER_TRADE': {'default': 50000,   'type': float, 'label': 'Notional per Trade (₹)',  'group': 'Trade'},
    'BTST_VOLUME_RATIO_MIN':   {'default': 1.5,     'type': float, 'label': 'Volume Ratio Min',        'group': 'Filters'},
    'BTST_ADX_MIN':            {'default': 20,      'type': float, 'label': 'ADX Min',                 'group': 'Filters'},
    'BTST_SCAN_START':         {'default': '15:20', 'type': str,   'label': 'Scan Start (IST)',        'group': 'Schedule'},
    'BTST_SCAN_END':           {'default': '15:28', 'type': str,   'label': 'Scan End (IST)',          'group': 'Schedule'},
    'BTST_MORNING_EXIT_TIME':  {'default': '09:20', 'type': str,   'label': 'Morning Exit Time (IST)', 'group': 'Schedule'},
    'BTST_HARD_EXIT_BY':       {'default': '10:00', 'type': str,   'label': 'Hard Exit By (IST)',      'group': 'Schedule'},
    'BTST_PAPER_MODE':         {'default': True,    'type': bool,  'label': 'Paper Mode',              'group': 'Trade'},
    'BTST_MACRO_BLOCK_DATES':  {'default': '',      'type': str,   'label': 'Extra Macro Block Dates (YYYY-MM-DD, comma-sep)', 'group': 'Filters'},
    'BTST_MAX_DAILY_LOSS':     {'default': 0,       'type': float, 'label': 'Max Daily Loss (₹, 0=disabled)',                'group': 'Limits'},
    'BTST_MAX_VIX':            {'default': 20,      'type': float, 'label': 'Max VIX for entry',                            'group': 'Filters'},
    'BTST_MAX_DAILY_NOTIONAL': {'default': 0,       'type': float, 'label': 'Max Daily Notional (₹, 0=disabled)',           'group': 'Limits'},
}

# ── Module State ──────────────────────────────────────────────────────────────

_config: dict[str, Any] = {k: v['default'] for k, v in BTST_CONFIG_SCHEMA.items()}
_config_lock = threading.Lock()

_state: dict[str, Any] = {
    'status': 'IDLE',
    'signals': [],
    'positions': [],
    'last_scan_ts': None,
    'candidates_found': 0,
    'stats': {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl_abs': 0.0,
        'total_pnl_pct': 0.0,
    },
}
_state_lock = threading.Lock()

_running = False
_running_lock = threading.Lock()

_btst_killed: bool = False        # kill switch — set when daily loss limit hit
_btst_daily_loss: float = 0.0     # running daily realized loss (negative = loss)

# ── SSE ───────────────────────────────────────────────────────────────────────

_sse_subscribers: list[queue.Queue] = []
_sse_lock = threading.Lock()

_logger = get_logger('btst_scanner')


def subscribe_sse() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_subscribers.append(q)
        with _state_lock:
            for sig in _state['signals']:
                try:
                    q.put_nowait(json.dumps({'type': 'signal', '_replay': True, **sig}, default=str))
                except queue.Full:
                    break
            for pos in _state['positions']:
                try:
                    q.put_nowait(json.dumps({'type': 'position_update', '_replay': True, **pos}, default=str))
                except queue.Full:
                    break
    return q


def unsubscribe_sse(q: queue.Queue) -> None:
    with _sse_lock:
        try:
            _sse_subscribers.remove(q)
        except ValueError:
            pass


def _broadcast(event: dict) -> None:
    msg = json.dumps(event, default=str)
    with _sse_lock:
        dead: list[queue.Queue] = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _sse_subscribers.remove(q)
            except ValueError:
                pass


def _log(message: str, level: str = 'info') -> None:
    getattr(_logger, level, _logger.info)(f'[btst] {message}')
    _broadcast({'type': 'log', 'message': message, 'level': level, 'ts': _now_ist().isoformat()})


# ── Public API ────────────────────────────────────────────────────────────────

def get_state() -> dict:
    with _state_lock:
        raw_positions = list(_state['positions'])
        raw_signals = list(_state['signals'])
        result = {
            'status': _state['status'],
            'positions_count': len(raw_positions),
            'signals_count': len(raw_signals),
            'last_scan_ts': _state['last_scan_ts'],
            'candidates_found': _state['candidates_found'],
        }
    # Enrich OUTSIDE the lock — broker LTP calls are slow/networked
    result['positions'] = _enrich_positions_with_ltp(raw_positions)
    result['signals'] = raw_signals
    return result


def _enrich_positions_with_ltp(positions: list) -> list:
    """Compute live P&L for HOLDING positions using current LTP."""
    enriched = []
    for pos in positions:
        p = dict(pos)
        if p.get('status') == 'HOLDING':
            try:
                option_sym = p.get('option_symbol', '')
                entry_prem = float(p.get('entry_premium') or 0)
                qty = int(p.get('qty', 1))
                lot_size = int(p.get('lot_size', 1))
                if option_sym and entry_prem > 0:
                    # Use option LTP for P&L — not the underlying index price
                    option_ltp = _get_current_price(option_sym)
                    if option_ltp > 0:
                        pnl_pct = round((option_ltp - entry_prem) / entry_prem * 100, 2)
                        pnl_abs = round((option_ltp - entry_prem) * qty * lot_size, 2)
                        p['pnl_pct'] = pnl_pct
                        p['pnl_abs'] = pnl_abs
                        p['current_price'] = option_ltp
                else:
                    entry = float(p.get('entry_price') or 0)
                    ltp = _get_current_price(p['symbol'])
                    if entry > 0 and ltp > 0:
                        pnl_pct = round((ltp - entry) / entry * 100, 2) if p['direction'] == 'bull' else round((entry - ltp) / entry * 100, 2)
                        p['pnl_pct'] = pnl_pct
                        p['pnl_abs'] = round((ltp - entry) * qty, 2) if p['direction'] == 'bull' else round((entry - ltp) * qty, 2)
                        p['current_price'] = ltp
            except Exception:
                pass
        enriched.append(p)
    return enriched


def get_stats() -> dict:
    with _state_lock:
        stats = dict(_state['stats'])
    total = stats['wins'] + stats['losses']
    stats['win_rate'] = round(stats['wins'] / total * 100, 1) if total else 0.0
    stats['total_pnl_abs'] = round(stats['total_pnl_abs'], 2)
    stats['total_pnl_pct'] = round(stats['total_pnl_pct'], 2)
    # Include today's DB-backed P&L
    stats['today'] = get_today_pnl()
    return stats


def get_all_config() -> dict:
    with _config_lock:
        return dict(_config)


def update_config(updates: dict) -> dict:
    valid = {k: v for k, v in updates.items() if k in BTST_CONFIG_SCHEMA}
    with _config_lock:
        for k, v in valid.items():
            _config[k] = BTST_CONFIG_SCHEMA[k]['type'](v)
    return get_all_config()


def start() -> None:
    global _running
    with _running_lock:
        _running = True
    _log('BTST scanner started')


def stop() -> None:
    global _running
    with _running_lock:
        _running = False
    _log('BTST scanner stopped')


def is_running() -> bool:
    with _running_lock:
        return _running


# ── Universe ──────────────────────────────────────────────────────────────────

# Trade universe: only index options (Dhan-native symbols)
BTST_UNIVERSE: list[str] = ['NIFTY', 'SENSEX']

# Symbol mapping for yfinance fallback
_SYM_TO_YF: dict[str, str] = {'NIFTY': '^NSEI', 'SENSEX': '^BSESN'}


# ── Stage 1: OHLCV Fetch + Technical Pre-filter ───────────────────────────────

import pandas as pd  # noqa: E402


def _fetch_ohlcv(symbol: str) -> 'pd.DataFrame | None':
    try:
        from ..dependencies import get_broker
        broker = get_broker()
        candles = broker.get_candles(symbol, interval='1d', days=30) or []
        if len(candles) >= 21:
            df = pd.DataFrame([{
                'Open': float(c.open), 'High': float(c.high),
                'Low': float(c.low), 'Close': float(c.close),
                'Volume': int(c.volume or 0),
            } for c in candles],
            index=pd.to_datetime([str(getattr(c, 'date', i)) for i, c in enumerate(candles)])).sort_index()
            return df
    except Exception as e:
        _log(f'Broker OHLCV failed for {symbol}, trying yfinance: {e}', 'warn')

    try:
        import yfinance as yf
        yf_sym = _SYM_TO_YF.get(symbol, symbol)
        df = yf.Ticker(yf_sym).history(period='35d', interval='1d')
        if df is None or df.empty or len(df) < 21:
            return None
        return df
    except Exception as e:
        _log(f'OHLCV fetch failed for {symbol}: {e}', 'warn')
        return None


def _fetch_intraday_5m(symbol: str) -> 'pd.DataFrame | None':
    """Fetch today's 5m candles for accurate intraday state at scan time (15:20)."""
    try:
        from ..dependencies import get_broker
        broker = get_broker()
        candles = broker.get_candles(symbol, interval='5m', days=1) or []
        if len(candles) >= 6:
            df = pd.DataFrame([{
                'Open': float(c.open), 'High': float(c.high),
                'Low': float(c.low), 'Close': float(c.close),
                'Volume': int(c.volume or 0),
            } for c in candles],
            index=pd.to_datetime([str(getattr(c, 'time', getattr(c, 'date', i))) for i, c in enumerate(candles)])).sort_index()
            return df
    except Exception as e:
        _log(f'Broker 5m failed for {symbol}: {e}', 'warn')
    try:
        import yfinance as yf
        yf_sym = _SYM_TO_YF.get(symbol, symbol)
        df = yf.Ticker(yf_sym).history(period='1d', interval='5m')
        if df is None or df.empty or len(df) < 6:
            return None
        return df
    except Exception as e:
        _log(f'5m intraday fetch failed for {symbol}: {e}', 'warn')
        return None


def _stage1_filter(symbol: str, df: 'pd.DataFrame', direction: str) -> bool:
    if df is None or len(df) < 21:
        return False
    try:
        high, low, close = df['High'], df['Low'], df['Close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        length = 14
        atr = tr.ewm(alpha=1/length, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1/length, adjust=False).mean() / atr
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float('nan')))
        adx = float(dx.ewm(alpha=1/length, adjust=False).mean().iloc[-1])
        if pd.isna(adx) or adx < _config['BTST_ADX_MIN']:
            return False

        avg_vol = df['Volume'].iloc[-21:-1].mean()
        today_vol = float(df['Volume'].iloc[-1])
        if avg_vol <= 0 or (today_vol / avg_vol) < _config['BTST_VOLUME_RATIO_MIN']:
            return False

        day_high = float(df['High'].iloc[-1])
        day_low = float(df['Low'].iloc[-1])
        close = float(df['Close'].iloc[-1])
        day_range = day_high - day_low
        if day_range <= 0:
            return False
        range_position = (close - day_low) / day_range

        rolling_high = float(df['High'].iloc[-21:-1].max())
        rolling_low = float(df['Low'].iloc[-21:-1].min())

        if direction == 'bull':
            # Near day low → oversold → mean-reversion bounce candidate
            if range_position > 0.30:
                return False
        else:
            # Near day high → overbought → mean-reversion fade candidate
            if range_position < 0.70:
                return False

        return True
    except Exception as e:
        _log(f'Stage 1 filter error for {symbol}: {e}', 'warn')
        return False


_OPTION_META: dict[str, dict] = {
    'NIFTY':  {'base': 'NIFTY',  'step': 50},
    'SENSEX': {'base': 'SENSEX', 'step': 100},
}


def _fetch_option_quotes(symbol: str, spot: float, direction: str) -> 'list[dict]':
    """Return ATM + 1 OTM option quotes for CE (bull) or PE (bear), next weekly expiry."""
    try:
        from ..dependencies import get_broker
        import datetime as _dt
        broker = get_broker()
        meta = _OPTION_META.get(symbol, {})
        step = meta.get('step', 50)
        opt_type = 'CE' if direction == 'bull' else 'PE'
        atm = round(spot / step) * step
        strikes = [atm, atm + step] if direction == 'bull' else [atm, atm - step]

        results = []
        for strike in strikes:
            contract = broker.resolve_option_contract(symbol, opt_type, strike)
            if not contract:
                continue
            # Normalise keys (resolve_option_contract returns expiry_date/security_id/trading_symbol)
            if 'expiry' not in contract and 'expiry_date' in contract:
                contract['expiry'] = contract['expiry_date']
            if 'sec_id' not in contract and 'security_id' in contract:
                contract['sec_id'] = contract['security_id']
            if 'symbol' not in contract and 'trading_symbol' in contract:
                contract['symbol'] = contract['trading_symbol']
            # Parse expiry string to date if needed
            exp_val = contract.get('expiry', '')
            if isinstance(exp_val, str) and exp_val:
                from ..infrastructure.broker.dhan_broker import DhanBroker
                exp_val = DhanBroker._parse_expiry_date(exp_val) or exp_val
                contract['expiry'] = exp_val
            # Skip same-day expiry (expiry-day risk)
            today = _dt.date.today()
            if contract.get('expiry') == today:
                contract2 = broker.resolve_option_contract(symbol, opt_type, strike + step * 100)
                # Just re-resolve with a far strike to get next expiry — hack
                # Better: filter candidates by expiry > today
                # For now, try to find next expiry contract
                from ..infrastructure.broker.dhan_broker import DhanBroker
                if isinstance(broker, DhanBroker):
                    rows = broker.load_instruments('fno')
                    base = _OPTION_META.get(symbol, {}).get('base', 'NIFTY')
                    candidates = [
                        r for r in rows
                        if str(r.get('TRADING_SYMBOL', '')).upper().startswith(base)
                        and str(r.get('OPTION_TYPE', '')).upper() == opt_type
                        and broker._parse_expiry_date(str(r.get('EXPIRY_DATE', ''))) > today
                    ]
                    candidates.sort(key=lambda x: (
                        broker._parse_expiry_date(str(x.get('EXPIRY_DATE', ''))),
                        abs(float(x.get('STRIKE_PRICE', 0) or 0) - strike)
                    ))
                    if candidates:
                        best = candidates[0]
                        contract = {
                            'symbol': str(best.get('TRADING_SYMBOL', '')),
                            'sec_id': str(best.get('SECURITY_ID', '')),
                            'expiry': broker._parse_expiry_date(str(best.get('EXPIRY_DATE', ''))),
                            'expiry_s': str(best.get('EXPIRY_DATE', '')),
                            'strike': float(best.get('STRIKE_PRICE') or 0),
                            'lot_size': int(float(best.get('LOT_SIZE') or best.get('LOT_UNITS') or 0)),
                        }

            exch = 'BFO' if 'SENSEX' in str(contract.get('symbol', '')).upper() else 'NFO'
            code = f"{exch}_{contract['sec_id']}"
            quotes = broker.get_quotes_batch([code])
            q = quotes.get(code, {})
            ltp = float(q.get('ltp', 0) or 0)
            oi  = int(q.get('oi', 0) or 0)
            results.append({
                'strike':   int(contract['strike']),
                'expiry':   str(contract['expiry']),
                'lot_size': contract.get('lot_size', 0),
                'ltp':      ltp,
                'oi':       oi,
                'symbol':   contract.get('symbol', ''),
            })
        return results
    except Exception as e:
        _log(f'Option quote fetch failed for {symbol}/{direction}: {e}', 'warn')
        return []


def _run_stage1() -> tuple[list[str], list[str]]:
    # Index instruments don't have meaningful volume/ADX for stock-style filters.
    # Both directions go straight to technical scoring.
    bull = list(BTST_UNIVERSE)
    bear = list(BTST_UNIVERSE)
    with _state_lock:
        _state['candidates_found'] = len(BTST_UNIVERSE)
    _log(f'Stage 1: {len(bull)} bull, {len(bear)} bear (index instruments — technical scoring)')
    return bull, bear


# ── Stage 2: Technical Scoring ────────────────────────────────────────────────

def _get_market_context() -> tuple[float, float]:
    # Use 5m intraday for accurate today's change — daily iloc[-1] incomplete at scan time
    try:
        nifty_chg, sensex_chg = 0.0, 0.0
        for sym, attr in [('NIFTY', 'nifty_chg'), ('SENSEX', 'sensex_chg')]:
            df_d = _fetch_ohlcv(sym)
            df_5m = _fetch_intraday_5m(sym)
            if df_d is not None and len(df_d) >= 2:
                prev_close = float(df_d['Close'].iloc[-2])
                current = float(df_5m['Close'].iloc[-1]) if df_5m is not None and len(df_5m) >= 1 else float(df_d['Close'].iloc[-1])
                chg = round((current - prev_close) / prev_close * 100, 2)
                if attr == 'nifty_chg':
                    nifty_chg = chg
                else:
                    sensex_chg = chg
        return nifty_chg, sensex_chg
    except Exception:
        return 0.0, 0.0


def _build_score_prompt(
    symbol: str,
    df_daily: 'pd.DataFrame',
    df_5m: 'pd.DataFrame | None',
    direction: str,
    nifty_chg: float,
    sensex_chg: float,
    option_quotes: 'list[dict] | None' = None,
    technicals: 'dict | None' = None,
) -> str:
    # Previous 5 completed daily sessions (exclude today's incomplete candle)
    prev_days = df_daily.iloc[:-1].tail(5)[['Open', 'High', 'Low', 'Close', 'Volume']].round(2)
    daily_table = prev_days.to_string()
    prev_close = float(df_daily['Close'].iloc[-2]) if len(df_daily) >= 2 else float(df_daily['Close'].iloc[-1])
    d20_high = float(df_daily['High'].iloc[-21:-1].max())
    d20_low = float(df_daily['Low'].iloc[-21:-1].min())

    if df_5m is not None and len(df_5m) >= 6:
        intraday_high = float(df_5m['High'].max())
        intraday_low = float(df_5m['Low'].min())
        current_price = float(df_5m['Close'].iloc[-1])
        intraday_open = float(df_5m['Open'].iloc[0])
        intraday_range = intraday_high - intraday_low
        range_pos = round((current_price - intraday_low) / intraday_range, 2) if intraday_range > 0 else 0.5
        today_change_pct = round((current_price - prev_close) / prev_close * 100, 2)
        last6 = df_5m['Close'].iloc[-6:]
        momentum_pct = round((float(last6.iloc[-1]) - float(last6.iloc[0])) / float(last6.iloc[0]) * 100, 2)
        avg_daily_vol = float(df_daily['Volume'].iloc[-21:-1].mean())
        bars_elapsed = len(df_5m)
        expected_vol = avg_daily_vol / 75 * bars_elapsed
        today_vol = float(df_5m['Volume'].sum())
        vol_ratio = round(today_vol / expected_vol, 2) if expected_vol > 0 else 0.0
        last12 = df_5m.tail(12)[['Open', 'High', 'Low', 'Close', 'Volume']].round(2)
        intraday_table = last12.to_string()
        intraday_section = (
            f'Intraday (5m candles, last 1 hour as of ~15:20 IST):\n{intraday_table}\n\n'
            f'Today: Open={intraday_open:.2f}  High={intraday_high:.2f}  Low={intraday_low:.2f}  Current={current_price:.2f}\n'
            f'Range position (0=day low, 1=day high): {range_pos:.2f}\n'
            f'Change vs prev close: {today_change_pct:+.2f}%\n'
            f'Last 30-min momentum: {momentum_pct:+.2f}%\n'
            f'Volume pace vs 20d avg: {vol_ratio:.1f}x\n'
        )
    else:
        current_price = float(df_daily['Close'].iloc[-1])
        today_change_pct = round((current_price - prev_close) / prev_close * 100, 2)
        intraday_section = f'Intraday data unavailable. Last daily close: {current_price:.2f} ({today_change_pct:+.2f}%)\n'

    # Technical indicators section (from _fetch_market_data)
    tech = technicals or {}
    tech_parts = []
    if tech.get('rsi'):
        tech_parts.append(f'RSI(14)={tech["rsi"]:.1f}')
    if tech.get('adx'):
        tech_parts.append(f'ADX={tech["adx"]:.1f}  +DI={tech.get("adx_plus_di", 0):.1f}  -DI={tech.get("adx_minus_di", 0):.1f}')
    if tech.get('macd_hist') is not None:
        cross = tech.get('macd_cross', '')
        tech_parts.append(f'MACD_hist={tech["macd_hist"]:.2f}({cross})')
    if tech.get('supertrend_dir'):
        tech_parts.append(f'Supertrend={tech["supertrend_dir"]}')
    for em in ['ema9', 'ema20', 'ema50', 'ema200']:
        if tech.get(em):
            tech_parts.append(f'{em.upper()}={tech[em]:.2f}')
    if tech.get('trend'):
        tech_parts.append(f'Trend={tech["trend"]}')
    tech_section = ('Technical indicators: ' + '  '.join(tech_parts) + '\n') if tech_parts else ''

    # Option chain section
    opt_type = 'CE' if direction == 'bull' else 'PE'
    if option_quotes:
        opt_lines = []
        for q in option_quotes:
            opt_lines.append(
                f'  Strike {q["strike"]} {opt_type} | Expiry {q["expiry"]} | '
                f'LTP ₹{q["ltp"]:.1f} | OI {q["oi"]:,} | Lot {q["lot_size"]}'
            )
        option_section = f'Available {opt_type} options:\n' + '\n'.join(opt_lines) + '\n'
    else:
        option_section = f'Option chain unavailable — estimate premiums based on spot and typical IV.\n'

    return (
        f'You are a BTST options trading analyst for Indian equity markets.\n\n'
        f'Symbol: {symbol}  |  Scan time: ~15:20 IST (10 min before market close)\n'
        f'Evaluate for BTST {"BUY" if direction == "bull" else "SHORT"} — buy {opt_type} today, sell next morning.\n\n'
        f'Previous 5 daily sessions (completed candles):\n{daily_table}\n\n'
        f'20-day range: Low={d20_low:.2f}  High={d20_high:.2f}\n'
        f'Prev close: {prev_close:.2f}\n\n'
        f'{intraday_section}\n'
        f'{tech_section}'
        f'Broader market today: Nifty {nifty_chg:+.2f}%  Sensex {sensex_chg:+.2f}%\n\n'
        f'{option_section}\n'
        f'Score 0–100 for overnight BTST {direction} potential. Score >=65 = viable.\n'
        f'Consider: intraday momentum into close, volume confirmation, support/resistance, gap-up/down risk.\n'
        f'Pick the best strike from the options listed. Set target and SL on the option premium.\n\n'
        f'Return only the JSON — no markdown, no extra text:\n'
        f'{{"score": <int 0-100>, "direction": "{direction}", '
        f'"rationale": "<one sentence>", "target_pct": <float>, "risk_note": "<one sentence>", '
        f'"strike": <int>, "expiry": "<YYYY-MM-DD>", "entry_premium": <float>, '
        f'"target_premium": <float>, "sl_premium": <float>}}'
    )


def _technical_fallback_score(
    symbol: str,
    df_daily: 'pd.DataFrame',
    df_5m: 'pd.DataFrame | None',
    direction: str,
    technicals: 'dict | None',
    option_quotes: 'list[dict]',
    nifty_chg: float = 0.0,
) -> 'dict | None':
    """Pure technical BTST scorer.

    BTST logic: buy oversold for overnight gap-up recovery (bull CE),
    or buy overbought for gap-down (bear PE). Contrarian, not trend-following.

    Signals (max ~95):
      Base:          35
      ADX trend:     0–12
      RSI:          -10–15
      Nifty move:    0–12
      Momentum:      0–8
      Supertrend:    0–5
      MACD:          0–8
      EMA alignment: 0–5
      Range position: 0–5
      Volume pace:   0–5
    """
    tech = technicals or {}
    rsi = tech.get('rsi', 50)
    adx = tech.get('adx', 0)
    macd_hist = tech.get('macd_hist')
    macd_cross = str(tech.get('macd_cross', '')).lower()
    supertrend_dir = str(tech.get('supertrend_dir', '')).lower()
    ema9 = tech.get('ema9', 0)
    ema20 = tech.get('ema20', 0)
    parts = []

    score = 35

    # ADX — strong trend makes reversal more tradeable (+0 to +12)
    score += min(12, max(0, int((adx - 15) * 0.5)))
    if adx >= 25:
        parts.append(f'ADX={adx:.0f}')

    # ── BULL (CE) logic: mean-reversion after sell-off ──────────────
    if direction == 'bull':
        # Oversold RSI is GOOD for bull BTST
        if rsi <= 35:
            score += 15
            parts.append(f'RSI oversold {rsi:.0f}')
        elif rsi < 45:
            score += 8
            parts.append(f'RSI low {rsi:.0f}')
        elif rsi > 70:
            score -= 10
            parts.append(f'RSI overbought {rsi:.0f}')

        # Index dropped today → gap-up bounce likely
        if nifty_chg < -0.8:
            score += 12
            parts.append(f'Nifty down {nifty_chg:+.1f}%')
        elif nifty_chg < -0.3:
            score += 6
            parts.append(f'Nifty dip {nifty_chg:+.1f}%')

        # Intraday recovery into close — shows demand
        if df_5m is not None and len(df_5m) >= 12:
            last12 = df_5m['Close'].iloc[-12:]
            mom = (float(last12.iloc[-1]) - float(last12.iloc[0])) / float(last12.iloc[0]) * 100
            if mom > 0.15:
                score += 8
                parts.append(f'close recovery +{mom:.1f}%')

        # Supertrend bullish = near support
        if supertrend_dir == 'bullish':
            score += 5
            parts.append('ST bull')

        # MACD bullish cross or histogram turning positive
        if macd_hist is not None and macd_hist > 0:
            score += 5
            parts.append(f'MACD+')
        if macd_cross == 'bullish':
            score += 3
            parts.append('MACD cross↑')

        # EMA9 > EMA20 = short-term bullish alignment
        if ema9 and ema20 and ema9 > ema20:
            score += 5
            parts.append('EMA9>20')

        # Closing near day's low → oversold intraday (good for bounce)
        if df_5m is not None and len(df_5m) >= 6:
            day_high = float(df_5m['High'].max())
            day_low = float(df_5m['Low'].min())
            close = float(df_5m['Close'].iloc[-1])
            rng = day_high - day_low
            if rng > 0:
                rp = (close - day_low) / rng
                if rp < 0.25:  # near day low → oversold
                    score += 5
                    parts.append(f'near day low rp={rp:.2f}')

    # ── BEAR (PE) logic: mean-reversion after rally ────────────────
    else:
        # Overbought RSI is GOOD for bear BTST
        if rsi >= 70:
            score += 15
            parts.append(f'RSI overbought {rsi:.0f}')
        elif rsi > 60:
            score += 8
            parts.append(f'RSI high {rsi:.0f}')
        elif rsi <= 35:
            score -= 10
            parts.append(f'RSI oversold {rsi:.0f}')

        # Index rallied today → gap-down correction likely
        if nifty_chg > 0.8:
            score += 12
            parts.append(f'Nifty up {nifty_chg:+.1f}%')
        elif nifty_chg > 0.3:
            score += 6
            parts.append(f'Nifty rally {nifty_chg:+.1f}%')

        # Intraday weakness into close
        if df_5m is not None and len(df_5m) >= 12:
            last12 = df_5m['Close'].iloc[-12:]
            mom = (float(last12.iloc[-1]) - float(last12.iloc[0])) / float(last12.iloc[0]) * 100
            if mom < -0.15:
                score += 8
                parts.append(f'close weakness {mom:.1f}%')

        # Supertrend bearish = near resistance
        if supertrend_dir == 'bearish':
            score += 5
            parts.append('ST bear')

        # MACD bearish cross or histogram negative
        if macd_hist is not None and macd_hist < 0:
            score += 5
            parts.append('MACD-')
        if macd_cross == 'bearish':
            score += 3
            parts.append('MACD cross↓')

        # EMA9 < EMA20 = short-term bearish
        if ema9 and ema20 and ema9 < ema20:
            score += 5
            parts.append('EMA9<20')

        # Closing near day's high → overbought intraday (good for drop)
        if df_5m is not None and len(df_5m) >= 6:
            day_high = float(df_5m['High'].max())
            day_low = float(df_5m['Low'].min())
            close = float(df_5m['Close'].iloc[-1])
            rng = day_high - day_low
            if rng > 0:
                rp = (close - day_low) / rng
                if rp > 0.75:  # near day high → overbought
                    score += 5
                    parts.append(f'near day high rp={rp:.2f}')

    # Volume pace — above-average volume adds conviction
    if df_5m is not None and len(df_5m) >= 6 and df_daily is not None and len(df_daily) >= 21:
        avg_vol = float(df_daily['Volume'].iloc[-21:-1].mean())
        bars = len(df_5m)
        expected = avg_vol / 75 * bars
        today_vol = float(df_5m['Volume'].sum())
        if expected > 0:
            vr = today_vol / expected
            if vr >= 1.5:
                score += 5
                parts.append(f'vol {vr:.1f}x')

    score = max(0, min(95, score))
    rejected = score < int(_config['BTST_MIN_SCORE'])

    best_quote = option_quotes[0] if option_quotes else {}
    rationale = ', '.join(parts) if parts else f'ADX={adx:.0f} RSI={rsi:.0f}'

    entry_prem = float(best_quote.get('ltp', 0) or 0)
    # Target: +44% on premium (+40% return + ~4% overnight theta drag to maintain 1:1 R:R net)
    target_prem = round(entry_prem * 1.44, 1) if entry_prem > 0 else None
    sl_prem = round(entry_prem * 0.60, 1) if entry_prem > 0 else None
    # Store option trading symbol for accurate LTP lookups at exit
    option_sym = best_quote.get('symbol') or best_quote.get('trading_symbol', '')

    return {
        'symbol':         symbol,
        'direction':      direction,
        'score':          score,
        'rejected':       rejected,
        'rationale':      rationale,
        'target_pct':     2.0,
        'risk_note':      'Technical-only score — no LLM confirmation',
        'strike':         best_quote.get('strike'),
        'expiry':         str(best_quote.get('expiry', '')),
        'entry_premium':  entry_prem or None,
        'target_premium': target_prem,
        'sl_premium':     sl_prem,
        'opt_type':       'CE' if direction == 'bull' else 'PE',
        'option_symbol':  option_sym,
        'lot_size':       best_quote.get('lot_size', 0),
        'ts':             _now_ist().isoformat(),
        '_source':        'technical',
    }


def _score_candidate(
    symbol: str,
    df_daily: 'pd.DataFrame',
    df_5m: 'pd.DataFrame | None',
    direction: str,
    technicals: 'dict | None' = None,
    nifty_chg: float = 0.0,
    sensex_chg: float = 0.0,
) -> 'dict | None':
    # Spot from 5m close or daily
    spot = float(df_5m['Close'].iloc[-1]) if df_5m is not None and len(df_5m) >= 1 else float(df_daily['Close'].iloc[-1])
    option_quotes = _fetch_option_quotes(symbol, spot, direction)

    # Pure technical score — no LLM
    result = _technical_fallback_score(symbol, df_daily, df_5m, direction, technicals, option_quotes, nifty_chg)
    _log(f'{symbol}/{direction}: score={result["score"]} ({result.get("rationale", "")})')
    return result


def _run_stage2(
    bull_candidates: list[str],
    bear_instruments: list[str],
) -> list[dict]:
    from time import sleep as _sleep

    # Pre-fetch: broker OHLCV (fast, Dhan-native) + market data for technicals
    prefetched: dict[str, dict] = {}
    nifty_chg, sensex_chg = 0.0, 0.0
    from ..dependencies import get_broker as _get_broker
    _broker = _get_broker()
    for sym in BTST_UNIVERSE:
        try:
            # Daily OHLCV via broker (Dhan symbol)
            df_d = _fetch_ohlcv(sym)
            # 5m intraday via broker
            df_5m = _fetch_intraday_5m(sym)
            # LTP via broker
            ltp = _broker.get_ltp(sym) or 0

            # Compute change from daily candles + live LTP
            if df_d is not None and len(df_d) >= 2 and ltp > 0:
                prev_close = float(df_d['Close'].iloc[-2])
                chg = round((ltp - prev_close) / prev_close * 100, 2)
                if sym == 'NIFTY':
                    nifty_chg = chg
                elif sym == 'SENSEX':
                    sensex_chg = chg

            # Compute technicals inline from daily data (no yfinance needed)
            technicals = {}
            if df_d is not None and len(df_d) >= 21:
                from ..shared.indicators import CandleData, ema, rsi as _rsi_fn, adx_full, supertrend, macd as _macd_fn
                closes = [float(x) for x in df_d['Close']]
                candles = [CandleData(open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v))
                           for o, h, l, c, v in zip(df_d['Open'], df_d['High'], df_d['Low'], df_d['Close'], df_d['Volume'])]
                technicals['rsi'] = _rsi_fn(closes, 14)
                adx_result = adx_full(candles, 14)
                technicals['adx'] = adx_result.get('adx', 0) if adx_result else 0
                technicals['ema9'] = ema(closes, 9) or 0
                technicals['ema20'] = ema(closes, 20) or 0
                try:
                    macd_result = _macd_fn(closes)
                    if macd_result:
                        technicals['macd_hist'] = macd_result.get('histogram', 0)
                        cross = str(macd_result.get('cross', '')).lower()
                        if cross != 'none':
                            technicals['macd_cross'] = cross
                except Exception:
                    pass
                try:
                    st_result = supertrend(candles, 10, 3.0)
                    if st_result:
                        d = st_result.get('direction', 0)
                        technicals['supertrend_dir'] = 'bullish' if d == 1 else 'bearish'
                except Exception:
                    pass

            prefetched[sym] = {'df_daily': df_d, 'df_5m': df_5m, 'technicals': technicals}
        except Exception as e:
            _log(f'Pre-fetch failed for {sym}: {e}', 'warn')
            prefetched[sym] = {'df_daily': None, 'df_5m': None, 'technicals': {}}
    _log(f'Market context: Nifty {nifty_chg:+.2f}%  Sensex {sensex_chg:+.2f}%')

    tasks: list[tuple[str, str]] = (
        [(sym, 'bull') for sym in bull_candidates]
        + [(sym, 'bear') for sym in bear_instruments]
    )
    results: list[dict] = []

    def _score_task(symbol: str, direction: str) -> 'dict | None':
        entry = prefetched.get(symbol, {})
        df_daily = entry.get('df_daily')
        df_5m = entry.get('df_5m')
        technicals = entry.get('technicals', {})

        if df_daily is None:
            _log(f'No daily OHLCV for {symbol}, skipping', 'warn')
            return None

        return _score_candidate(symbol, df_daily, df_5m, direction, technicals, nifty_chg, sensex_chg)

    # Score both directions per symbol, then keep only the stronger one
    _all_scored: dict[str, list[dict]] = {}  # symbol -> [results]
    for sym, direction in tasks:
        try:
            result = _score_task(sym, direction)
            if result:
                _all_scored.setdefault(sym, []).append(result)
        except Exception as e:
            _log(f'Scoring task failed for {sym}/{direction}: {e}', 'warn')
        _sleep(1)

    # Per symbol, pick the direction with the highest score
    for sym, scored_list in _all_scored.items():
        best = max(scored_list, key=lambda x: x['score'])
        results.append(best)
        _broadcast({'type': 'signal', **best})
        if len(scored_list) > 1:
            other = [s for s in scored_list if s is not best][0]
            _log(f'{sym}: picked {best["direction"]} (score={best["score"]}) over {other["direction"]} (score={other["score"]})')

    with _state_lock:
        _state['signals'] = results

    return sorted(results, key=lambda x: x['score'], reverse=True)


# ── Persistence (SQLite) ──────────────────────────────────────────────────────

_DB_PATH = str(pathlib.Path(__file__).parent.parent.parent / 'data' / 'pnl.db')

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS btst_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT,
        entry_price REAL,
        qty INTEGER,
        notional REAL,
        rationale TEXT,
        target_pct REAL,
        risk_note TEXT,
        ai_score INTEGER,
        strike INTEGER,
        expiry TEXT,
        opt_type TEXT,
        lot_size INTEGER,
        entry_premium REAL,
        target_premium REAL,
        sl_premium REAL,
        status TEXT DEFAULT 'HOLDING',
        ts TEXT,
        exit_price REAL,
        exit_ts TEXT,
        pnl_pct REAL,
        pnl_abs REAL,
        exit_reason TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS btst_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT,
        score INTEGER,
        rejected INTEGER,
        rationale TEXT,
        target_pct REAL,
        risk_note TEXT,
        strike INTEGER,
        expiry TEXT,
        entry_premium REAL,
        target_premium REAL,
        sl_premium REAL,
        opt_type TEXT,
        lot_size INTEGER,
        ts TEXT,
        source TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS btst_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    # Migrations for schema additions
    for _sql in (
        'ALTER TABLE btst_positions ADD COLUMN option_symbol TEXT',
        'ALTER TABLE btst_signals ADD COLUMN option_symbol TEXT',
    ):
        try:
            conn.execute(_sql)
            conn.commit()
        except Exception:
            pass  # column already exists
    return conn


def _get_current_price(symbol: str) -> float:
    try:
        from ..dependencies import get_broker
        ltp = get_broker().get_ltp(symbol)
        if ltp and ltp > 0:
            return float(ltp)
    except Exception:
        pass
    try:
        df = _fetch_ohlcv(symbol)
        if df is not None and not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception:
        pass
    return 0.0


def _enter_paper_position(signal: dict) -> dict | None:
    # Daily notional cap check
    max_daily_notional = float(_config.get('BTST_MAX_DAILY_NOTIONAL', 0))
    if max_daily_notional > 0:
        with _state_lock:
            held_notional = sum(
                float(p.get('notional', 0))
                for p in _state['positions']
                if p.get('status') == 'HOLDING'
            )
        if held_notional >= max_daily_notional:
            _log(f'Skipping {signal["symbol"]}: daily notional cap ₹{max_daily_notional:,.0f} reached (held=₹{held_notional:,.0f})', 'warn')
            return None

    entry_price = _get_current_price(signal['symbol'])
    entry_prem = float(signal.get('entry_premium') or 0)
    if entry_price <= 0 and entry_prem <= 0:
        _log(f'Skipping {signal["symbol"]}: could not fetch price', 'warning')
        return None
    lot_size = int(signal.get('lot_size') or 1)
    notional = float(_config['BTST_NOTIONAL_PER_TRADE'])
    # Qty in lots based on premium * lot_size, fallback to index price
    if entry_prem > 0 and lot_size > 0:
        qty = max(1, int(notional // (entry_prem * lot_size)))
    else:
        qty = max(1, int(notional // entry_price)) if entry_price > 0 else 1
    position = {
        'symbol': signal['symbol'],
        'direction': signal['direction'],
        'entry_price': entry_price,
        'qty': qty,
        'notional': round((entry_prem * lot_size * qty) if entry_prem > 0 else (entry_price * qty), 2),
        'rationale': signal.get('rationale', ''),
        'target_pct': signal.get('target_pct', 0.0),
        'risk_note': signal.get('risk_note', ''),
        'ai_score': signal.get('score', 0),
        'strike': signal.get('strike'),
        'expiry': signal.get('expiry'),
        'opt_type': signal.get('opt_type', 'CE'),
        'option_symbol': signal.get('option_symbol', ''),
        'lot_size': lot_size,
        'entry_premium': entry_prem or None,
        'target_premium': signal.get('target_premium'),
        'sl_premium': signal.get('sl_premium'),
        'status': 'HOLDING',
        'ts': _now_ist().isoformat(),
        'exit_price': None,
        'exit_ts': None,
        'pnl_pct': None,
        'pnl_abs': None,
        'exit_reason': None,
    }
    with _state_lock:
        _state['positions'].append(position)
    _broadcast({'type': 'position_update', **position})
    _log(f'Paper entry: {position["direction"].upper()} {position["symbol"]} @ ₹{entry_price:.2f} qty={qty}')
    return position


def _save_positions() -> None:
    try:
        today = _now_ist().strftime('%Y-%m-%d')
        with _state_lock:
            positions = list(_state['positions'])
            signals = list(_state['signals'])
            status = _state['status']
        conn = _get_db()
        try:
            # Clear today's rows and re-insert (simple upsert)
            conn.execute('DELETE FROM btst_positions WHERE date = ?', (today,))
            for p in positions:
                conn.execute(
                    '''INSERT INTO btst_positions
                       (date,symbol,direction,entry_price,qty,notional,rationale,target_pct,
                        risk_note,ai_score,strike,expiry,opt_type,option_symbol,lot_size,entry_premium,
                        target_premium,sl_premium,status,ts,exit_price,exit_ts,pnl_pct,pnl_abs,exit_reason)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (today, p.get('symbol'), p.get('direction'), p.get('entry_price'),
                     p.get('qty'), p.get('notional'), p.get('rationale'), p.get('target_pct'),
                     p.get('risk_note'), p.get('ai_score'), p.get('strike'), str(p.get('expiry', '')),
                     p.get('opt_type'), p.get('option_symbol', ''), p.get('lot_size'), p.get('entry_premium'),
                     p.get('target_premium'), p.get('sl_premium'), p.get('status'),
                     p.get('ts'), p.get('exit_price'), p.get('exit_ts'),
                     p.get('pnl_pct'), p.get('pnl_abs'), p.get('exit_reason')),
                )
            conn.execute('DELETE FROM btst_signals WHERE date = ?', (today,))
            for s in signals:
                conn.execute(
                    '''INSERT INTO btst_signals
                       (date,symbol,direction,score,rejected,rationale,target_pct,risk_note,
                        strike,expiry,entry_premium,target_premium,sl_premium,opt_type,lot_size,ts,source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (today, s.get('symbol'), s.get('direction'), s.get('score'),
                     1 if s.get('rejected') else 0, s.get('rationale'), s.get('target_pct'),
                     s.get('risk_note'), s.get('strike'), str(s.get('expiry', '')),
                     s.get('entry_premium'), s.get('target_premium'), s.get('sl_premium'),
                     s.get('opt_type'), s.get('lot_size'), s.get('ts'), s.get('_source', '')),
                )
            conn.execute('INSERT OR REPLACE INTO btst_state (key, value) VALUES (?, ?)',
                         ('status', status))
            conn.execute('INSERT OR REPLACE INTO btst_state (key, value) VALUES (?, ?)',
                         ('date', today))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        _log(f'Failed to save positions: {e}', 'error')


def _load_positions() -> None:
    if not os.path.exists(_DB_PATH):
        return
    try:
        today = _now_ist().strftime('%Y-%m-%d')
        conn = _get_db()
        try:
            row = conn.execute('SELECT value FROM btst_state WHERE key = ?', ('date',)).fetchone()
            saved_date = row['value'] if row else None
            status_row = conn.execute('SELECT value FROM btst_state WHERE key = ?', ('status',)).fetchone()
            saved_status = status_row['value'] if status_row else 'IDLE'

            if not saved_date:
                return

            if saved_date == today:
                # Same-day restore
                positions = [dict(r) for r in conn.execute(
                    'SELECT * FROM btst_positions WHERE date = ?', (today,)).fetchall()]
                signals = [dict(r) for r in conn.execute(
                    'SELECT * FROM btst_signals WHERE date = ?', (today,)).fetchall()]
                for s in signals:
                    s['rejected'] = bool(s.get('rejected'))
                    s['_source'] = s.pop('source', 'technical_fallback')
                with _state_lock:
                    _state['positions'] = positions
                    _state['signals'] = signals
                    _state['candidates_found'] = len(signals) or len(positions)
                    if saved_status in ('HOLDING', 'MORNING_EXIT'):
                        _state['status'] = saved_status
                _log(f'Restored {len(positions)} positions, {len(signals)} signals (state={saved_status})')
            elif saved_status == 'HOLDING':
                # Previous-day positions still HOLDING — restore so morning exit can close them
                positions = [dict(r) for r in conn.execute(
                    'SELECT * FROM btst_positions WHERE date = ? AND status = ?',
                    (saved_date, 'HOLDING')).fetchall()]
                if positions:
                    with _state_lock:
                        _state['positions'] = positions
                        _state['status'] = 'HOLDING'
                    _log(f'Restored {len(positions)} previous-day ({saved_date}) HOLDING '
                         f'positions for morning exit')
                    # Schedule immediate morning exit
                    threading.Timer(15.0, _morning_exit_check).start()
                else:
                    _log(f'Previous day ({saved_date}) had HOLDING status but no HOLDING positions')
            else:
                _log(f'DB data from {saved_date} (status={saved_status}) — no positions to restore')
        finally:
            conn.close()
    except Exception as e:
        _log(f'Failed to load positions: {e}', 'error')

    # Restore stats from btst_positions table so P&L survives restarts
    try:
        conn = _get_db()
        try:
            rows = conn.execute(
                '''SELECT status, pnl_pct, pnl_abs FROM btst_positions
                   WHERE status = 'EXITED' AND pnl_abs IS NOT NULL''',
            ).fetchall()
            if rows:
                wins = sum(1 for r in rows if (r['pnl_pct'] or 0) >= 0)
                losses = sum(1 for r in rows if (r['pnl_pct'] or 0) < 0)
                total_pnl_abs = sum(r['pnl_abs'] or 0 for r in rows)
                total_pnl_pct = sum(r['pnl_pct'] or 0 for r in rows)
                with _state_lock:
                    _state['stats'] = {
                        'total_trades': len(rows),
                        'wins': wins,
                        'losses': losses,
                        'total_pnl_abs': round(total_pnl_abs, 2),
                        'total_pnl_pct': round(total_pnl_pct, 2),
                    }
                _log(f'Restored stats: {len(rows)} trades, P&L ₹{total_pnl_abs:.2f}')
        finally:
            conn.close()
    except Exception as e:
        _log(f'Failed to restore stats: {e}', 'error')


def get_today_pnl() -> dict:
    """Get today's BTST P&L from the btst_positions table."""
    today = _now_ist().strftime('%Y-%m-%d')
    try:
        conn = _get_db()
        try:
            rows = conn.execute(
                '''SELECT pnl_pct, pnl_abs FROM btst_positions
                   WHERE date = ? AND status = 'EXITED' AND pnl_abs IS NOT NULL''',
                (today,),
            ).fetchall()
            total_abs = sum(r['pnl_abs'] or 0 for r in rows)
            total_pct = sum(r['pnl_pct'] or 0 for r in rows)
            return {
                'trades': len(rows),
                'pnl_abs': round(total_abs, 2),
                'pnl_pct': round(total_pct, 2),
            }
        finally:
            conn.close()
    except Exception:
        return {'trades': 0, 'pnl_abs': 0.0, 'pnl_pct': 0.0}


def trigger_now() -> None:
    _log('Manual trigger — starting scan cycle')
    t = threading.Thread(target=_scan_cycle, args=(True,), daemon=True)
    t.start()


# ── Macro Calendar Block ──────────────────────────────────────────────────────

# Dates when overnight gap risk is too high for BTST entries.
# Covers: RBI MPC decision days, Fed FOMC days, India Union Budget.
# US CPI/NFP omitted — those are morning India time and gap has already occurred.
# UPDATE this set at start of each calendar year.
_MACRO_BLOCK_DATES: frozenset[str] = frozenset({
    # RBI MPC 2025 (remaining)
    '2025-10-09', '2025-12-06',
    # RBI MPC 2026 (6 meetings — last Fri of each bi-monthly cycle)
    '2026-02-07', '2026-04-09', '2026-06-06', '2026-08-08', '2026-10-10', '2026-12-05',
    # Fed FOMC 2025 (remaining)
    '2025-09-17', '2025-11-07', '2025-12-17',
    # Fed FOMC 2026
    '2026-01-28', '2026-03-18', '2026-05-06', '2026-06-17',
    '2026-07-29', '2026-09-16', '2026-10-28', '2026-12-16',
    # India Union Budget
    '2026-02-01',
    # India Interim Budget / Economic Survey (if applicable)
    '2027-02-01',
})


def _is_macro_risk_day() -> bool:
    """Block BTST entries when overnight macro event risk is elevated.

    Checks hardcoded calendar + user-configured override dates.
    Returns True = block, False = safe to scan.
    """
    today_str = _now_ist().date().isoformat()
    if today_str in _MACRO_BLOCK_DATES:
        _log(f'Macro block: {today_str} is a known high-risk event day — no BTST entries')
        return True
    override_str = str(_config.get('BTST_MACRO_BLOCK_DATES', '') or '')
    for d in override_str.split(','):
        if d.strip() == today_str:
            _log(f'Macro block: {today_str} in user override list — no BTST entries')
            return True
    return False


# ── Scan Cycle ────────────────────────────────────────────────────────────────

def _scan_cycle(force: bool = False) -> None:
    if not force and not is_running():
        return

    with _state_lock:
        current_status = _state['status']
        if not force and current_status not in ('IDLE', 'SCANNING'):
            _log(f'Scan skipped — state is {current_status}')
            return
        if current_status == 'SCANNING' and not force:
            _log('Scan already in progress — skipping')
            return
        _state['status'] = 'SCANNING'

    _log('Starting afternoon scan cycle')

    if not force and _btst_killed:
        _log('Scan blocked — daily loss kill switch active', 'warn')
        with _state_lock:
            _state['status'] = 'IDLE'
        return

    if not force and _is_macro_risk_day():
        with _state_lock:
            _state['status'] = 'IDLE'
        return

    # VIX gate: elevated overnight gap risk above threshold
    max_vix = float(_config.get('BTST_MAX_VIX', 20))
    if max_vix > 0 and not force:
        try:
            vix = _get_current_price('^INDIAVIX')
            if vix and vix > max_vix:
                _log(f'Scan blocked — VIX {vix:.1f} > {max_vix} (elevated overnight gap risk)', 'warn')
                with _state_lock:
                    _state['status'] = 'IDLE'
                return
        except Exception:
            pass

    try:
        bull_candidates, bear_instruments = _run_stage1()
        all_scored = _run_stage2(bull_candidates, bear_instruments)

        accepted = [s for s in all_scored if not s['rejected']]
        max_pos = int(_config['BTST_MAX_POSITIONS'])

        with _state_lock:
            existing_count = len(_state['positions'])

        for signal in accepted[:max(0, max_pos - existing_count)]:
            _enter_paper_position(signal)

        with _state_lock:
            _state['last_scan_ts'] = _now_ist().isoformat()
            _state['status'] = 'HOLDING' if _state['positions'] else 'IDLE'

        _save_positions()
        _log(f'Scan complete: {len(accepted)} accepted, {len(_state["positions"])} positions taken')
        _broadcast({'type': 'stats', **get_stats(), 'ts': _now_ist().isoformat()})

    except Exception as e:
        _log(f'Scan cycle error: {e}', 'error')
        import traceback; _log(traceback.format_exc(), 'error')
        with _state_lock:
            _state['status'] = 'IDLE'


def _close_entry_window() -> None:
    with _state_lock:
        if _state['status'] == 'SCANNING':
            _state['status'] = 'HOLDING'
    _save_positions()
    _log(f'Entry window closed. {len(_state["positions"])} positions held overnight.')
    _broadcast({'type': 'stats', **get_stats(), 'ts': _now_ist().isoformat()})


# ── Morning Exit ──────────────────────────────────────────────────────────────

def _fetch_opening_candle(symbol: str) -> 'dict | None':
    try:
        from ..dependencies import get_broker
        candles = get_broker().get_candles(symbol, interval='5m', days=1) or []
        if candles:
            c = candles[0]
            return {
                'open': float(c.open), 'high': float(c.high),
                'low': float(c.low), 'close': float(c.close),
            }
    except Exception as e:
        _log(f'Broker opening candle failed for {symbol}: {e}', 'warn')
    try:
        import yfinance as yf
        yf_sym = _SYM_TO_YF.get(symbol, symbol)
        hist = yf.Ticker(yf_sym).history(period='1d', interval='5m')
        if hist.empty:
            return None
        first = hist.iloc[0]
        return {
            'open': float(first['Open']),
            'high': float(first['High']),
            'low': float(first['Low']),
            'close': float(first['Close']),
        }
    except Exception as e:
        _log(f'Opening candle fetch failed for {symbol}: {e}', 'warn')
        return None


def _build_exit_prompt(pos: dict, gap_pct: float, candle: 'dict | None') -> str:
    candle_str = (
        f'O={candle["open"]:.2f} H={candle["high"]:.2f} L={candle["low"]:.2f} C={candle["close"]:.2f}'
        if candle else 'unavailable'
    )
    entry = float(pos['entry_price'])
    current = _get_current_price(pos['symbol'])
    if pos['direction'] == 'bull':
        pnl_pct = (current - entry) / entry * 100
    else:
        pnl_pct = (entry - current) / entry * 100
    return (
        f'BTST exit decision for {pos["symbol"]} ({pos["direction"].upper()})\n'
        f'Entry: ₹{entry:.2f}  Current: ₹{current:.2f}  P&L: {pnl_pct:+.2f}%\n'
        f'Gap at open: {gap_pct:+.2f}%\n'
        f'First 5-min candle: {candle_str}\n'
        f'Original rationale: {pos.get("rationale", "N/A")}\n\n'
        f'Decide exit action. Respond ONLY with JSON:\n'
        f'{{"action": "<exit_now|wait|exit_by_1000>", "reason": "<one sentence>"}}'
    )


def _record_exit(pos: dict, reason: str) -> None:
    # Use option LTP for P&L; fall back to index LTP only if option_symbol not stored
    option_sym = pos.get('option_symbol', '')
    exit_price = _get_current_price(option_sym) if option_sym else 0.0
    if exit_price <= 0:
        exit_price = _get_current_price(pos['symbol'])  # fallback — will be index LTP
    entry = float(pos['entry_price']) or 0.0
    entry_prem = float(pos.get('entry_premium') or 0)
    qty = int(pos.get('qty', 1))
    lot_size = int(pos.get('lot_size', 1))
    # P&L: BTST always buys the option (CE or PE); profit = exit_premium - entry_premium
    if entry_prem > 0 and option_sym:
        pnl_pct = (exit_price - entry_prem) / entry_prem * 100
    elif entry > 0:
        pnl_pct = (exit_price - entry) / entry * 100 if pos['direction'] == 'bull' else (entry - exit_price) / entry * 100
    else:
        pnl_pct = 0.0
    # Compute gross P&L using option premium × qty × lot_size
    if entry_prem > 0:
        gross_pnl = (exit_price - entry_prem) * qty * lot_size
    else:
        gross_pnl = (exit_price - entry) * qty if pos['direction'] == 'bull' else (entry - exit_price) * qty
    gross_pnl = round(gross_pnl, 2)
    # Deduct brokerage (same F&O profile as swing)
    try:
        from ..domain.services.brokerage_calc import segment_brokerage
        _ep = entry_prem if entry_prem > 0 else entry
        _xp = exit_price
        _q = qty * lot_size if entry_prem > 0 else qty
        brokerage = segment_brokerage('swing', _ep, _xp, _q, gross_pnl=gross_pnl)
    except Exception:
        brokerage = 0.0
    pnl_abs = round(gross_pnl - brokerage, 2)
    pnl_pct = round(pnl_pct, 2)

    global _btst_killed, _btst_daily_loss
    with _state_lock:
        for p in _state['positions']:
            if p['symbol'] == pos['symbol'] and p['status'] == 'HOLDING':
                p['exit_price'] = exit_price
                p['exit_ts'] = _now_ist().isoformat()
                p['pnl_pct'] = pnl_pct
                p['pnl_abs'] = pnl_abs
                p['exit_reason'] = reason
                p['status'] = 'EXITED'
                break
        s = _state['stats']
        s['total_trades'] += 1
        s['total_pnl_abs'] += pnl_abs
        s['total_pnl_pct'] += pnl_pct
        if pnl_pct >= 0:
            s['wins'] += 1
        else:
            s['losses'] += 1

    # Kill switch: trip if cumulative daily loss exceeds limit
    max_loss = float(_config.get('BTST_MAX_DAILY_LOSS', 0))
    if max_loss > 0 and pnl_abs < 0:
        _btst_daily_loss += pnl_abs  # pnl_abs is negative on loss
        if abs(_btst_daily_loss) >= max_loss:
            _btst_killed = True
            _log(f'Kill switch tripped — daily loss ₹{abs(_btst_daily_loss):,.0f} >= limit ₹{max_loss:,.0f}', 'error')

    _broadcast({
        'type': 'exit', 'symbol': pos['symbol'],
        'exit_price': exit_price, 'pnl_pct': pnl_pct, 'pnl_abs': pnl_abs,
        'reason': reason, 'ts': _now_ist().isoformat(),
    })
    _log(f'Exit: {pos["symbol"]} @ ₹{exit_price:.2f} P&L={pnl_pct:+.2f}% ({reason})')
    _save_positions()
    _broadcast({'type': 'stats', **get_stats(), 'ts': _now_ist().isoformat()})

    # Record in the central pnl_trades ledger so BTST P&L shows in TopBar summary
    try:
        from ..infrastructure.db.pnl_store import record_trade as _pnl_record
        _pnl_record(
            mode='swing', symbol=pos['symbol'], underlying=pos['symbol'],
            entry_prem=_ep, exit_prem=_xp, qty=_q,
            lot_size=1,
            brokerage_or_exit_reason=brokerage,
            exit_reason=f'BTST: {reason}',
            market_type='fo',
            direction=pos.get('direction', ''),
        )
    except Exception:
        pass

    # Also broadcast to main order events bus so it's visible on the main panel
    try:
        from ..application.event_bus import event_bus
        from ..domain.events.events import DomainEvent
        pnl_emoji = '📈' if pnl_pct >= 0 else '📉'
        event_bus.publish(DomainEvent(name='order_update', data={
            'type': 'order_update',
            'severity': 'success' if pnl_pct >= 0 else 'warning',
            'title': f'{pnl_emoji} BTST Exit: {pos["symbol"]}',
            'status': 'BTST_EXIT',
            'symbol': pos['symbol'],
            'message': (f'{pos["direction"].upper()} {pos["symbol"]} — '
                        f'entry ₹{entry:.2f} → exit ₹{exit_price:.2f} — '
                        f'P&L {pnl_pct:+.2f}% (₹{pnl_abs:+.2f}) — {reason}'),
            'timestamp': _now_ist().isoformat(),
        }))
    except Exception:
        pass


def _schedule_recheck(pos: dict, retry_count: int) -> None:
    now = _now_ist()
    hard_exit_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= hard_exit_time or retry_count > 8:
        _record_exit(pos, 'hard exit — max retries or 10:00 AM reached')
        return
    _log(f'{pos["symbol"]}: re-evaluating exit in 5 min (retry {retry_count + 1})')

    def recheck() -> None:
        candle = _fetch_opening_candle(pos['symbol'])
        current = _get_current_price(pos['symbol'])
        entry = float(pos['entry_price'])
        if pos['direction'] == 'bull':
            gap_pct = (current - entry) / entry * 100
        else:
            gap_pct = (entry - current) / entry * 100
        _exit_position(pos, gap_pct=gap_pct, candle=candle)

    t = threading.Timer(300, recheck)
    t.daemon = True
    t.start()


def _exit_position(pos: dict, gap_pct: float = 0.0, candle: 'dict | None' = None) -> None:
    """Rule-based BTST exit — no LLM.
    
    Rules:
    - Target premium hit → exit_now (take profit)
    - SL premium hit → exit_now (cut loss)
    - Gap >= +1% in our direction → exit_now (book overnight gap profit)
    - Gap <= -1% against us → exit_now (cut gap-down loss)
    - Otherwise → wait, recheck every 5 min until hard exit at 10:00
    """
    entry_prem = float(pos.get('entry_premium') or pos.get('entry_price') or 0)
    target_prem = float(pos.get('target_premium') or 0)
    sl_prem = float(pos.get('sl_premium') or 0)
    # current = index LTP (for gap % checks only)
    current = _get_current_price(pos['symbol'])
    entry_price = float(pos.get('entry_price') or 0)
    is_bull = pos['direction'] == 'bull'
    # option_ltp = actual option premium (for target/SL checks and P&L)
    option_sym = pos.get('option_symbol', '')
    option_ltp = _get_current_price(option_sym) if option_sym else 0.0

    # P&L from option premium; both CE long and PE long profit when premium rises
    if entry_prem > 0 and option_ltp > 0:
        pnl_pct = (option_ltp - entry_prem) / entry_prem * 100
    elif entry_price > 0:
        pnl_pct = (current - entry_price) / entry_price * 100 if is_bull else (entry_price - current) / entry_price * 100
    else:
        pnl_pct = 0.0

    # Target hit — compare option LTP against stored target premium (same for CE and PE long)
    if target_prem > 0 and entry_prem > 0 and option_ltp > 0:
        if option_ltp >= target_prem:
            _record_exit(pos, f'target hit — P&L {pnl_pct:+.1f}%')
            return

    # SL hit — option premium dropped below SL level
    if sl_prem > 0 and entry_prem > 0 and option_ltp > 0:
        if option_ltp <= sl_prem:
            _record_exit(pos, f'SL hit — P&L {pnl_pct:+.1f}%')
            return

    # Favourable gap >= 1% in our direction → book profit (index-based check)
    if gap_pct >= 1.0:
        _record_exit(pos, f'gap-up profit +{gap_pct:.1f}%')
        return

    # Adverse gap >= 1% against us → cut loss (index-based check)
    if gap_pct <= -1.0:
        _record_exit(pos, f'gap-down loss {gap_pct:.1f}%')
        return

    # P&L > 2% at any recheck → exit
    if pnl_pct >= 2.0:
        _record_exit(pos, f'profit target +{pnl_pct:.1f}%')
        return

    # P&L < -2% at any recheck → cut
    if pnl_pct <= -2.0:
        _record_exit(pos, f'loss cut {pnl_pct:.1f}%')
        return

    # Otherwise wait — schedule recheck
    _schedule_recheck(pos, retry_count=0)


def _morning_exit_check() -> None:
    global _btst_killed, _btst_daily_loss
    # Reset daily kill switch — new trading day
    _btst_killed = False
    _btst_daily_loss = 0.0

    with _state_lock:
        if _state['status'] != 'HOLDING':
            return
        _state['status'] = 'MORNING_EXIT'
        positions_to_exit = [p for p in _state['positions'] if p.get('status') == 'HOLDING']

    if not positions_to_exit:
        with _state_lock:
            _state['status'] = 'IDLE'
        return

    _log(f'Morning exit: evaluating {len(positions_to_exit)} positions')

    for pos in positions_to_exit:
        try:
            candle = _fetch_opening_candle(pos['symbol'])
            entry = float(pos['entry_price']) or 0.0
            current = _get_current_price(pos['symbol'])
            if not entry:
                _log(f'Force-exiting {pos["symbol"]}: entry_price is 0', 'warning')
                _record_exit(pos, 'entry_price was 0 — cannot compute P&L')
                continue
            if pos['direction'] == 'bull':
                gap_pct = (current - entry) / entry * 100
            else:
                gap_pct = (entry - current) / entry * 100
            _exit_position(pos, gap_pct=gap_pct, candle=candle)
        except Exception as e:
            _log(f'Exit failed for {pos["symbol"]}: {e} — force-exiting', 'error')
            _record_exit(pos, f'error during exit: {e}')

    with _state_lock:
        all_exited = all(p.get('status') == 'EXITED' for p in _state['positions'])
        if all_exited:
            _state['status'] = 'IDLE'


# ── APScheduler ───────────────────────────────────────────────────────────────

def _init_scheduler() -> None:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BackgroundScheduler(timezone='Asia/Kolkata')
        scheduler.add_job(
            _scan_cycle,
            CronTrigger(hour=15, minute=20, timezone='Asia/Kolkata'),
            id='btst_scan_cycle',
            replace_existing=True,
        )
        scheduler.add_job(
            _close_entry_window,
            CronTrigger(hour=15, minute=28, timezone='Asia/Kolkata'),
            id='btst_close_entry',
            replace_existing=True,
        )
        scheduler.add_job(
            _morning_exit_check,
            CronTrigger(hour=9, minute=20, timezone='Asia/Kolkata'),
            id='btst_morning_exit',
            replace_existing=True,
        )
        scheduler.start()
        _log('APScheduler started: scan@15:20, close@15:28, exit@09:20')
    except Exception as e:
        _log(f'APScheduler init failed: {e}', 'error')


# ── Module Init ───────────────────────────────────────────────────────────────

_load_positions()
_init_scheduler()
