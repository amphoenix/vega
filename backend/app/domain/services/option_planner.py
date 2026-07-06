"""
Option Trade Planner — ported from old backend.

Given an underlying + directional bias (BULL → CE, BEAR → PE), produces a
precise, executable trade ticket: strike, expiry, premium, SL/T1/T2 in ₹,
time-exit, live Greeks.

Public API:
    plan_option_trade(underlying, bias, ...) -> dict | None
"""
from __future__ import annotations

import math

from ...shared.logger import get_logger
from ...shared.time import FORCE_EXIT, SAFE_OPEN, date, datetime, dt_time, now_ist, today_ist
from . import greeks as gk

logger = get_logger('option_planner')

# Strike steps per underlying
_STRIKE_STEP = {
    'NIFTY':       50,
    'BANKNIFTY':   100,
    'FINNIFTY':    50,
    'MIDCPNIFTY':  25,
    'SENSEX':      100,
}


def _strike_step(underlying_base: str) -> int:
    return _STRIKE_STEP.get(underlying_base.upper(), 0) or 0


def _nse_base(ticker: str) -> str:
    t = (ticker or '').upper().replace('.NS', '').replace('.BO', '').lstrip('^')
    return {
        'NSEI':   'NIFTY',
        'NSEBANK':'BANKNIFTY',
        'CNXFIN': 'FINNIFTY',
        'BSESN':  'SENSEX',
    }.get(t, t)


def _stock_strike_step(spot: float) -> int:
    if spot <= 50:    return 1
    if spot <= 100:   return 2
    if spot <= 250:   return 5
    if spot <= 500:   return 10
    if spot <= 1000:  return 20
    if spot <= 2500:  return 50
    return 100


def _field(inst: dict, key: str, default=''):
    """Case-insensitive getter for instrument master rows."""
    _ALIASES = {
        'trading_symbol': ('TRADING_SYMBOL', 'tradingsymbol', 'trading_symbol'),
        'option_type':    ('OPTION_TYPE', 'option_type', 'INSTRUMENT_TYPE', 'instrument_type'),
        'expiry':         ('EXPIRY_DATE', 'expiry_date', 'expiry', 'EXPIRY'),
        'strike':         ('STRIKE_PRICE', 'strike_price', 'strike', 'STRIKE'),
        'security_id':    ('SECURITY_ID', 'security_id'),
        'exchange':       ('EXCH', 'exchange', 'SEGMENT', 'segment'),
        'lot_size':       ('LOT_UNITS', 'LOT_SIZE', 'lot_size', 'lot_units'),
        'display_symbol': ('DISPLAY_SYMBOL', 'display_symbol', 'CUSTOM_SYMBOL'),
    }
    for alias in _ALIASES.get(key, (key,)):
        v = inst.get(alias)
        if v not in (None, ''):
            return v
    return default


def _parse_expiry(s) -> date | None:
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


def _get_broker():
    from ...dependencies import get_broker
    return get_broker()


def _load_fno_instruments():
    return _get_broker().load_instruments('fno')


def _nearest_expiry(base: str, opt_type: str, max_dte: int, min_dte: int) -> dict | None:
    today = today_ist()
    best  = None
    for inst in _load_fno_instruments():
        sym = str(_field(inst, 'trading_symbol', '')).strip().upper()
        if not sym.startswith(base):
            continue
        opt = str(_field(inst, 'option_type', '')).strip().upper()
        if opt != opt_type.upper():
            continue
        exp = _parse_expiry(_field(inst, 'expiry', ''))
        if not exp:
            continue
        dte = (exp - today).days
        if dte < min_dte or dte > max_dte:
            continue
        if best is None or exp < best['expiry']:
            best = {'expiry': exp, 'sample': sym}
    return best


def _live_spot(ticker: str) -> float | None:
    try:
        broker = _get_broker()
        p = broker.get_ltp(ticker, exchange='NSE')
        if p:
            return float(p)
    except Exception:
        pass
    # Broker-agnostic: no yfinance fallback — spot comes only from the active broker.
    return None


def _atm_iv_estimate(base: str, spot: float, expiry_d: date, opt_type: str = 'CE') -> float:
    step = _strike_step(base) or _stock_strike_step(spot)
    atm  = int(round(spot / step) * step)
    dte  = max(1, (expiry_d - today_ist()).days)
    try:
        broker = _get_broker()
        expiry_token = expiry_d.strftime('%y%b').upper()
        sym = f"{base}{expiry_token}{atm}{opt_type.upper()}"
        ltp = broker.get_ltp(sym, exchange='NFO')
        if ltp and ltp > 0:
            iv = gk.implied_vol(spot, atm, dte, float(ltp), opt_type)
            if iv and 0.05 <= iv <= 2.0:
                return iv
    except Exception:
        pass
    return gk.default_iv(base)


def _resolve_strike(base: str, target_strike: int, expiry_d: date, opt_type: str) -> dict | None:
    for inst in _load_fno_instruments():
        sym = str(_field(inst, 'trading_symbol', '')).strip().upper()
        if not sym.startswith(base):
            continue
        opt = str(_field(inst, 'option_type', '')).strip().upper()
        if opt != opt_type.upper():
            continue
        exp = _parse_expiry(_field(inst, 'expiry', ''))
        if exp != expiry_d:
            continue
        try:    k = float(_field(inst, 'strike', 0) or 0)
        except: continue
        if int(k) != int(target_strike):
            continue
        try:    lot = int(float(_field(inst, 'lot_size', 0) or 0))
        except: lot = 0
        exch = str(_field(inst, 'exchange', 'NFO')).strip().upper()
        if 'NFO' in exch or 'NSE' in exch:   exch = 'NFO'
        elif 'BFO' in exch or 'BSE' in exch: exch = 'BFO'
        return {
            'security_id':    str(_field(inst, 'security_id', '')).strip(),
            'exchange':       exch,
            'lot_size':       lot,
            'expiry':         exp,
            'strike':         float(k),
            'option_type':    opt,
            'trading_symbol': sym,
            'display_symbol': str(_field(inst, 'display_symbol', '')).strip() or sym,
        }
    return None


def _compute_fees(side: str, opt_type: str, premium: float, lot: int) -> dict:
    """Simplified fee model for F&O options."""
    turnover = premium * lot
    brokerage = 20.0
    stt = turnover * 0.000625 if side == 'SELL' else 0.0
    txn_charge = turnover * 0.00053
    gst = (brokerage + txn_charge) * 0.18
    stamp = turnover * 0.00003 if side == 'BUY' else 0.0
    sebi = turnover * 0.000001
    total = brokerage + stt + txn_charge + gst + stamp + sebi
    return {'total': round(total, 2)}


def plan_option_trade(
    underlying: str,
    bias: str,
    spot: float | None = None,
    target_delta: float = 0.50,
    min_dte: int = 3,
    max_dte: int = 21,
    rationale: str = '',
    spot_target_1: float | None = None,
    spot_target_2: float | None = None,
    spot_stop_loss: float | None = None,
    atr: float | None = None,
    expected_days_to_t1: int | None = None,
    expected_days_to_t2: int | None = None,
) -> dict | None:
    """Produce a concrete option trade ticket. Returns dict or None."""
    bias = bias.upper()
    opt_type = 'CE' if bias == 'BULL' else 'PE'
    base = _nse_base(underlying)

    spot = spot if spot is not None else _live_spot(underlying)
    if not spot or spot <= 0:
        logger.warning(f"plan_option_trade: no spot for {underlying}")
        return None

    # 1. Pick expiry
    exp = _nearest_expiry(base, opt_type, max_dte=max_dte, min_dte=min_dte)
    if not exp:
        logger.warning(f"plan_option_trade: no expiry found for {base} {opt_type} dte={min_dte}-{max_dte}")
        return None
    dte_d = (exp['expiry'] - today_ist()).days
    iv    = _atm_iv_estimate(base, spot, exp['expiry'], opt_type)

    # 2. Pick strike by target-delta
    effective_target = target_delta
    if dte_d <= 1:
        effective_target = max(target_delta, 0.65)
    elif dte_d <= 3:
        effective_target = max(target_delta, 0.55)
    step = _strike_step(base) or _stock_strike_step(spot)
    target_strike = gk.pick_strike_by_delta(
        spot=spot, target_delta=effective_target, dte_days=dte_d,
        iv=iv, opt_type=opt_type, step=step,
    )

    # 3. Resolve to broker contract
    meta = _resolve_strike(base, target_strike, exp['expiry'], opt_type)
    if not meta:
        # Fall back to nearest available strike
        candidates = []
        for inst in _load_fno_instruments():
            sym = str(_field(inst, 'trading_symbol', '')).strip().upper()
            if not sym.startswith(base): continue
            if str(_field(inst, 'option_type', '')).strip().upper() != opt_type: continue
            if _parse_expiry(_field(inst, 'expiry', '')) != exp['expiry']: continue
            try:    k = float(_field(inst, 'strike', 0) or 0)
            except: continue
            candidates.append((abs(k - target_strike), inst, k))
        if not candidates:
            logger.warning(f"plan_option_trade: no strike candidates for {base} {opt_type} exp={exp['expiry']} target_strike={target_strike}")
            return None
        candidates.sort(key=lambda x: x[0])
        inst = candidates[0][1]
        exch = str(_field(inst, 'exchange', 'NFO')).strip().upper()
        if 'NFO' in exch or 'NSE' in exch:   exch = 'NFO'
        elif 'BFO' in exch or 'BSE' in exch: exch = 'BFO'
        ts = str(_field(inst, 'trading_symbol', '')).strip().upper()
        meta = {
            'security_id':    str(_field(inst, 'security_id', '')).strip(),
            'exchange':       exch,
            'lot_size':       int(float(_field(inst, 'lot_size', 0) or 0)),
            'expiry':         exp['expiry'],
            'strike':         float(_field(inst, 'strike', 0) or 0),
            'option_type':    opt_type,
            'trading_symbol': ts,
            'display_symbol': str(_field(inst, 'display_symbol', '')).strip() or ts,
        }

    if not meta.get('lot_size'):
        logger.warning(f"plan_option_trade: lot_size=0 for {meta.get('trading_symbol')}")
        return None

    # 4. Get live premium
    broker = _get_broker()
    ltp = 0.0
    try:
        ltp = float(broker.get_ltp(meta['trading_symbol'], exchange='NFO',
                                    security_id=meta.get('security_id', '')) or 0)
    except Exception:
        pass
    mid = ltp if ltp > 0 else gk.bs_price(spot, meta['strike'], dte_d / 365.0,
                                            gk.RISK_FREE_RATE, iv, opt_type)

    # 5. Re-solve IV
    iv_market = gk.implied_vol(spot, meta['strike'], dte_d, mid, opt_type) or iv

    # 6. Greeks (convert dataclass to dict for JSON serialization)
    _gr = gk.greeks(spot, meta['strike'], dte_d, iv_market, opt_type=opt_type)
    g = {
        'delta': _gr.delta,
        'gamma': _gr.gamma,
        'theta_per_day': _gr.theta_per_day,
        'vega_per_volpt': _gr.vega_per_volpt,
        'iv_used': _gr.iv_used,
        'premium_bs': _gr.premium_bs,
    }

    # 7. Risk numbers
    entry_px = round(mid, 2)
    lot      = meta['lot_size']
    K        = float(meta['strike'])
    rate     = gk.RISK_FREE_RATE

    def _bs_at(future_spot: float, days_remaining: float) -> float:
        days_remaining = max(0.05, float(days_remaining))
        T = days_remaining / 365.0
        return max(0.05, gk.bs_price(future_spot, K, T, rate, iv_market, opt_type))

    days_to_t1 = expected_days_to_t1 if expected_days_to_t1 is not None else max(1, int(dte_d * 0.5))
    days_to_t2 = expected_days_to_t2 if expected_days_to_t2 is not None else max(2, int(dte_d * 0.8))
    dte_remaining_t1 = max(0.5, dte_d - days_to_t1)
    dte_remaining_t2 = max(0.25, dte_d - days_to_t2)

    # ATR clamping
    if atr and float(atr) > 0:
        a = float(atr)
        S = float(spot)
        _dte_scale = min(1.0, math.sqrt(dte_d / 7.0))
        SL_MULT  = 1.5 * max(0.5, _dte_scale)
        T1_MULT  = 2.5 * _dte_scale
        T2_MULT  = 5.0 * _dte_scale

        def _wrong_dir(level, side):
            if level is None or level <= 0: return False
            if bias == 'BULL':
                return (side == 'sl' and level >= S) or (side in ('t1','t2') and level <= S)
            else:
                return (side == 'sl' and level <= S) or (side in ('t1','t2') and level >= S)

        if _wrong_dir(spot_stop_loss, 'sl') or _wrong_dir(spot_target_1, 't1') or _wrong_dir(spot_target_2, 't2'):
            if bias == 'BULL':
                spot_stop_loss = S - SL_MULT * a
                spot_target_1  = S + T1_MULT * a
                spot_target_2  = S + T2_MULT * a
            else:
                spot_stop_loss = S + SL_MULT * a
                spot_target_1  = S - T1_MULT * a
                spot_target_2  = S - T2_MULT * a
        elif bias == 'BULL':
            sl_band  = S - SL_MULT * a
            t1_band  = S + T1_MULT * a
            t2_band  = S + T2_MULT * a
            if spot_stop_loss is not None: spot_stop_loss = max(float(spot_stop_loss), sl_band)
            if spot_target_1  is not None: spot_target_1  = min(float(spot_target_1),  t1_band)
            if spot_target_2  is not None: spot_target_2  = min(float(spot_target_2),  t2_band)
        else:
            sl_band  = S + SL_MULT * a
            t1_band  = S - T1_MULT * a
            t2_band  = S - T2_MULT * a
            if spot_stop_loss is not None: spot_stop_loss = min(float(spot_stop_loss), sl_band)
            if spot_target_1  is not None: spot_target_1  = max(float(spot_target_1),  t1_band)
            if spot_target_2  is not None: spot_target_2  = max(float(spot_target_2),  t2_band)

    # DTE-aware loss/gain limits
    if dte_d <= 2:
        SL_MAX_LOSS_PCT = 0.30
        SL_MIN_LOSS_PCT = 0.10
        T1_MIN_GAIN_PCT = 0.20
        T2_MIN_OVER_T1  = 1.30
    else:
        SL_MAX_LOSS_PCT = 0.50
        SL_MIN_LOSS_PCT = 0.15
        T1_MIN_GAIN_PCT = 0.30
        T2_MIN_OVER_T1  = 1.40

    if spot_stop_loss is not None and spot_stop_loss > 0:
        sl_bs = round(_bs_at(float(spot_stop_loss), max(0.5, dte_d - 1)), 2)
    else:
        sl_bs = round(entry_px * SL_MAX_LOSS_PCT, 2)
    sl_floor = round(entry_px * SL_MAX_LOSS_PCT, 2)
    sl_cap   = round(entry_px * (1 - SL_MIN_LOSS_PCT), 2)
    sl_px    = max(sl_floor, min(sl_bs, sl_cap))

    if spot_target_1 is not None and spot_target_1 > 0:
        t1_bs = round(_bs_at(float(spot_target_1), dte_remaining_t1), 2)
    else:
        t1_bs = round(entry_px * 2.00, 2)
    t1_floor = round(entry_px * (1 + T1_MIN_GAIN_PCT), 2)
    t1_px    = max(t1_floor, t1_bs)

    if spot_target_2 is not None and spot_target_2 > 0:
        t2_bs = round(_bs_at(float(spot_target_2), dte_remaining_t2), 2)
    else:
        t2_bs = round(entry_px * 3.00, 2)
    t2_floor = round(t1_px * T2_MIN_OVER_T1, 2)
    t2_px    = max(t2_floor, t2_bs)

    fees_buy  = _compute_fees('BUY',  opt_type, entry_px, lot)['total']
    fees_sell = _compute_fees('SELL', opt_type, sl_px,    lot)['total']
    max_loss  = round((entry_px - sl_px) * lot + fees_buy + fees_sell, 2)
    breakeven = round(K + entry_px, 2) if opt_type == 'CE' else round(K - entry_px, 2)

    expected_pnl_t1 = round((t1_px - entry_px) * lot - fees_buy - fees_sell, 2)
    expected_pnl_t2 = round((t2_px - entry_px) * lot - fees_buy - fees_sell, 2)
    rr_ratio        = round(expected_pnl_t1 / max_loss, 2) if max_loss > 0 else 0

    # 8. Time window
    #    Allow intraday entries until 14:45; only the final ~45 min is AVOID
    #    (theta cliff). Expiry-day contracts are cut earlier via `on_expiry`.
    now_t = now_ist().time()
    on_expiry = meta['expiry'] == today_ist()
    if now_t < SAFE_OPEN:
        entry_window = f"{SAFE_OPEN.strftime('%H:%M')}–09:45 IST"
    elif on_expiry and now_t > dt_time(13, 0):
        entry_window = "AVOID — expiry-day theta cliff (after 13:00)"
    elif now_t <= dt_time(14, 45):
        entry_window = "NOW – intraday"
    else:
        entry_window = "AVOID — last 45 min, theta cliff"
    time_exit = ("13:00 IST (expiry-day theta cliff)" if on_expiry
                 else f"{FORCE_EXIT.strftime('%H:%M')} IST today")

    return {
        'underlying':     underlying,
        'spot':           round(spot, 2),
        'bias':           bias,
        'trade_mode':     'swing',          # so P&L/exit/guards attribute correctly
        'option_type':    opt_type,
        'trading_symbol': meta['trading_symbol'],
        'display_symbol': meta.get('display_symbol') or meta['trading_symbol'],
        'security_id':    meta['security_id'],
        'exchange':       meta['exchange'],
        'expiry':         meta['expiry'].isoformat(),
        'strike':         int(meta['strike']),
        'lot_size':       lot,
        'days_to_expiry': dte_d,
        'entry': {
            'window_ist':           entry_window,
            'order_type':           'MARKET',
            'expected_premium_inr': entry_px,
        },
        'exit': {
            'stop_loss_inr':   sl_px,
            'target_1_inr':    t1_px,
            'target_2_inr':    t2_px,
            'time_exit_ist':   time_exit,
            'trail_after_t1':  '20% premium trail',
        },
        'greeks':         g,
        'risk': {
            'cost_per_lot_inr':   round(entry_px * lot, 2),
            'max_loss_inr':       max_loss,
            'breakeven_spot':     breakeven,
            'theta_burn_today':   round(g['theta_per_day'] * lot, 2),
            'fees_round_trip':    round(fees_buy + fees_sell, 2),
            'expected_pnl_t1':    expected_pnl_t1,
            'expected_pnl_t2':    expected_pnl_t2,
            'risk_reward_ratio':  rr_ratio,
            'spot_targets_used':  {
                'sl': spot_stop_loss,
                't1': spot_target_1,
                't2': spot_target_2,
            },
        },
        'rationale':      rationale or f"{bias} {opt_type} on {underlying}",
        'generated_at':   now_ist().isoformat(),
    }
