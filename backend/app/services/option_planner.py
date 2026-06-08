"""
Option Trade Planner.

Given an underlying + directional bias (BULL → CE, BEAR → PE), produces a
*precise, executable* trade ticket: which strike, which expiry, when to enter,
what premium to pay, where the stop-loss / targets sit in ₹, when to exit on
time, and the live Greeks.

Output dict (one ticket):
  {
    'underlying':        '^NSEI',
    'spot':              18432.5,
    'bias':              'BULL'|'BEAR',
    'option_type':       'CE'|'PE',
    'trading_symbol':    'NIFTY26APR18450CE',
    'security_id':       '...',
    'exchange':          'NFO',
    'expiry':            '2026-04-30',
    'strike':            18450,
    'lot_size':           75,
    'days_to_expiry':     5,
    'entry':  {
        'window_ist':    '09:20–09:45 IST',
        'order_type':    'MARKET',
        'expected_premium_inr': 110.5,
        'mid_bid_ask':   (108.3, 112.8),
    },
    'exit':  {
        'stop_loss_inr':  55.25,    # 50% premium SL (fixed risk)
        'target_1_inr':   221.0,    # 2× → exit half
        'target_2_inr':   331.5,    # 3× → exit full
        'time_exit_ist':  '15:10 IST today (or 13:00 on expiry day)',
        'trail_after_t1': '20% trailing on premium',
    },
    'greeks':  {
        'delta':         0.34,
        'gamma':         0.00094,
        'theta_per_day': -3.21,     # ₹ premium lost per day if nothing moves
        'vega_per_volpt':10.32,
        'iv':            0.142,
    },
    'risk':  {
        'max_loss_inr':       cost_per_lot * 0.5 + fees,
        'breakeven_spot':     strike + entry_premium  (CE)  or  strike − entry_premium  (PE),
        'theta_burn_today':   -theta_per_day,
    },
    'rationale':         'STRONG BUY conf=82% | Supertrend BULL ADX=27 ...',
  }

Public API:
    plan_option_trade(underlying, bias, spot=None, target_delta=0.35,
                      max_dte=14, min_dte=2) -> dict | None
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time
from typing import Optional

from . import broker_utils as bu
from . import greeks as gk
from ..utils.logger import get_logger

logger = get_logger('vega.option_planner')

# Strike steps per underlying (₹). Sourced from NSE; update if NSE re-fixes.
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
    """For F&O stocks, NSE uses dynamic strike intervals based on price band."""
    if spot <= 50:    return 1
    if spot <= 100:   return 2
    if spot <= 250:   return 5
    if spot <= 500:   return 10
    if spot <= 1000:  return 20
    if spot <= 2500:  return 50
    return 100


def _nearest_expiry(base: str, opt_type: str, max_dte: int, min_dte: int) -> Optional[dict]:
    """
    Scan the IndStocks F&O master for the nearest expiry of `base` with
    min_dte ≤ DTE ≤ max_dte. Returns one representative contract row's expiry
    info (the actual strike will be re-picked).
    """
    try:
        from ..api.indmoney import _load_instruments
    except Exception:
        return None

    today = bu.today_ist()
    best  = None
    seen_expiries: set = set()
    for inst in _load_instruments('fno'):
        sym = str(bu._field(inst, 'trading_symbol', '')).strip().upper()
        if not sym.startswith(base):
            continue
        opt = str(bu._field(inst, 'option_type', '')).strip().upper()
        if opt != opt_type.upper():
            continue
        exp = bu._parse_expiry(bu._field(inst, 'expiry', ''))
        if not exp:
            continue
        dte = (exp - today).days
        seen_expiries.add(dte)
        if dte < min_dte or dte > max_dte:
            continue
        if best is None or exp < best['expiry']:
            best = {'expiry': exp, 'sample': sym}
    if not best and seen_expiries:
        logger.warning(f"_nearest_expiry: {base} {opt_type} — no expiry in "
                       f"[{min_dte},{max_dte}]d window. Available DTEs: "
                       f"{sorted(d for d in seen_expiries if d >= 0)[:10]}")
    return best


def _live_spot(ticker: str) -> Optional[float]:
    try:
        from ..api.indmoney import _ind_ltp
        p = _ind_ltp(ticker)
        if p:
            return float(p)
    except Exception:
        pass
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        p = getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None)
        if p:
            return float(p)
    except Exception:
        pass
    return None


def _atm_iv_estimate(base: str, spot: float, expiry_d: date,
                     opt_type: str = 'CE') -> float:
    """
    Try to infer ATM IV from a real broker quote: fetch the ATM strike's
    market premium, back-solve via implied_vol(). Falls back to the
    underlying's regime default.
    """
    step = _strike_step(base) or _stock_strike_step(spot)
    atm  = int(round(spot / step) * step)
    dte  = max(1, (expiry_d - bu.today_ist()).days)
    try:
        from ..api.indmoney import _ind_option_ltp
        # Build expected ATM symbol
        for ot in (opt_type, 'CE', 'PE'):
            sym = _build_atm_symbol(base, atm, expiry_d, ot)
            ltp = _ind_option_ltp(sym) if sym else None
            if ltp and ltp > 0:
                iv = gk.implied_vol(spot, atm, dte, float(ltp), ot)
                if iv and 0.05 <= iv <= 2.0:
                    return iv
    except Exception:
        pass
    return gk.default_iv(base)


def _build_atm_symbol(base: str, strike: int, expiry: date, opt_type: str) -> str:
    """
    Best-effort symbol builder. Many brokers differ slightly; the resolver below
    will fall back to scanning the F&O master if this string isn't found.
    """
    expiry_token = expiry.strftime('%y%b').upper()    # e.g. '26APR'
    return f"{base}{expiry_token}{int(strike)}{opt_type.upper()}"


def _resolve_strike(base: str, target_strike: int, expiry_d: date,
                    opt_type: str) -> Optional[dict]:
    """
    Find the F&O master row matching (base, opt_type, expiry, strike).
    Returns its `bu.fno_meta`-style dict.
    """
    try:
        from ..api.indmoney import _load_instruments
    except Exception:
        return None
    for inst in _load_instruments('fno'):
        sym = str(bu._field(inst, 'trading_symbol', '')).strip().upper()
        if not sym.startswith(base):
            continue
        opt = str(bu._field(inst, 'option_type', '')).strip().upper()
        if opt != opt_type.upper():
            continue
        exp = bu._parse_expiry(bu._field(inst, 'expiry', ''))
        if exp != expiry_d:
            continue
        try:    k = float(bu._field(inst, 'strike', 0) or 0)
        except: continue
        if int(k) != int(target_strike):
            continue
        try:    lot = int(float(bu._field(inst, 'lot_size', 0) or 0))
        except: lot = 0
        exch = str(bu._field(inst, 'exchange', 'NFO')).strip().upper()
        if 'NFO' in exch or 'NSE' in exch:   exch = 'NFO'
        elif 'BFO' in exch or 'BSE' in exch: exch = 'BFO'
        return {
            'security_id':    str(bu._field(inst, 'security_id', '')).strip(),
            'exchange':       exch,
            'lot_size':       lot,
            'expiry':         exp,
            'strike':         float(k),
            'option_type':    opt,
            'trading_symbol': sym,
            'display_symbol': str(bu._field(inst, 'display_symbol', '')).strip() or sym,
        }
    return None


def plan_option_trade(
    underlying: str,
    bias: str,                       # 'BULL' | 'BEAR'
    spot: Optional[float] = None,
    target_delta: float = 0.50,
    min_dte: int = 3,
    max_dte: int = 21,
    rationale: str = '',
    spot_target_1: Optional[float]    = None,   # CIO's spot target 1
    spot_target_2: Optional[float]    = None,   # CIO's spot target 2
    spot_stop_loss: Optional[float]   = None,   # CIO's spot stop loss
    atr: Optional[float]              = None,   # ATR(14) — used to clamp CIO's
                                                # spot targets to intraday-realistic
                                                # bands (CIO often returns multi-day
                                                # levels that wreck R:R when applied
                                                # to a same-day F&O trade).
    expected_days_to_t1: Optional[int] = None,  # default = dte/2
    expected_days_to_t2: Optional[int] = None,  # default = dte×0.8
) -> Optional[dict]:
    """
    Produce a concrete option trade ticket for `underlying` given a directional
    bias. Always returns a dict the caller can show / execute, or None if
    insufficient market data is available.

    Rules:
      * BULL → buy CE; BEAR → buy PE.
      * Pick *nearest weekly/monthly expiry* with DTE in [min_dte, max_dte]
        (avoids same-day theta cliff and far-OTM theta drag).
      * Pick strike whose live BS-delta is closest to ±target_delta.
      * Stop-loss = 50% of entry premium; T1 = 2×, T2 = 3×.
      * Time exit = 15:10 IST today, or 13:00 IST on expiry day (theta cliff).
    """
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
        logger.warning(f"plan_option_trade: no {opt_type} expiry in "
                       f"[{min_dte},{max_dte}]d for {base}")
        return None
    dte_d = (exp['expiry'] - bu.today_ist()).days
    iv    = _atm_iv_estimate(base, spot, exp['expiry'], opt_type)

    # 2. Pick strike by target-delta — but never OTM on short DTE.
    # Theta dominates < 4 DTE: an OTM strike with Δ < 0.5 has almost no
    # intrinsic value; the entire premium is gamble money that decays to zero
    # overnight. Force the strike at-or-slightly-ITM (Δ ≥ 0.55) when expiry is
    # near, regardless of caller's target_delta.
    effective_target = target_delta
    if dte_d <= 1:
        effective_target = max(target_delta, 0.65)   # deep ITM, real intrinsic
    elif dte_d <= 3:
        effective_target = max(target_delta, 0.55)   # mildly ITM
    if effective_target != target_delta:
        logger.info(f"plan_option_trade: short DTE={dte_d} → bumping target_delta "
                    f"{target_delta:.2f} → {effective_target:.2f} (avoid OTM theta cliff)")
    step = _strike_step(base) or _stock_strike_step(spot)
    target_strike = gk.pick_strike_by_delta(
        spot=spot, target_delta=effective_target, dte_days=dte_d,
        iv=iv, opt_type=opt_type, step=step,
    )

    # 3. Resolve to broker contract
    meta = _resolve_strike(base, target_strike, exp['expiry'], opt_type)
    if not meta:
        # Fall back to nearest available strike in master
        from ..api.indmoney import _load_instruments
        candidates = []
        for inst in _load_instruments('fno'):
            sym = str(bu._field(inst, 'trading_symbol', '')).strip().upper()
            if not sym.startswith(base):                                       continue
            if str(bu._field(inst, 'option_type', '')).strip().upper() != opt_type: continue
            if bu._parse_expiry(bu._field(inst, 'expiry', '')) != exp['expiry']:    continue
            try:    k = float(bu._field(inst, 'strike', 0) or 0)
            except: continue
            candidates.append((abs(k - target_strike), inst, k))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        inst = candidates[0][1]
        exch = str(bu._field(inst, 'exchange', 'NFO')).strip().upper()
        if 'NFO' in exch or 'NSE' in exch:   exch = 'NFO'
        elif 'BFO' in exch or 'BSE' in exch: exch = 'BFO'
        ts = str(bu._field(inst, 'trading_symbol', '')).strip().upper()
        meta = {
            'security_id':    str(bu._field(inst, 'security_id', '')).strip(),
            'exchange':       exch,
            'lot_size':       int(float(bu._field(inst, 'lot_size', 0) or 0)),
            'expiry':         exp['expiry'],
            'strike':         float(bu._field(inst, 'strike', 0) or 0),
            'option_type':    opt_type,
            'trading_symbol': ts,
            'display_symbol': str(bu._field(inst, 'display_symbol', '')).strip() or ts,
        }

    if not meta.get('lot_size'):
        return None

    # 4. Get live premium / depth
    try:
        from ..api.indmoney import _ind_option_quote, _ind_option_ltp
        q = _ind_option_quote(meta['trading_symbol']) or {}
    except Exception:
        q = {}
    bid = float(q.get('bid') or 0)
    ask = float(q.get('ask') or 0)
    ltp = float(q.get('ltp') or 0)
    if not ltp:
        try:
            ltp = float(_ind_option_ltp(meta['trading_symbol']) or 0)
        except Exception:
            ltp = 0.0
    mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (ltp or 0.0)
    if mid <= 0:
        # Use BS theoretical as last resort
        mid = gk.bs_price(spot, meta['strike'], dte_d / 365.0,
                          gk.RISK_FREE_RATE, iv, opt_type)

    # 5. Re-solve IV from actual mid (more accurate than ATM estimate)
    iv_market = gk.implied_vol(spot, meta['strike'], dte_d, mid, opt_type) or iv

    # 6. Compute Greeks at the actual contract
    g = gk.greeks(spot, meta['strike'], dte_d, iv_market,
                  opt_type=opt_type)

    # 7. Build risk numbers — PREMIUM targets derived from CIO's SPOT targets
    # via Black-Scholes pricing at the future spot + reduced DTE (theta-aware).
    # This is the only honest way to set targets: blindly multiplying entry
    # premium by 2x/3x ignores theta decay and the actual delta exposure.
    entry_px = round(mid, 2)
    lot      = meta['lot_size']
    strike   = meta['strike']
    K        = float(strike)
    rate     = gk.RISK_FREE_RATE

    def _bs_at(future_spot: float, days_remaining: float) -> float:
        """BS premium at a future underlying spot with `days_remaining` to expiry."""
        days_remaining = max(0.05, float(days_remaining))   # never negative/zero
        T = days_remaining / 365.0
        return max(0.05, gk.bs_price(future_spot, K, T, rate, iv_market, opt_type))

    days_to_t1 = expected_days_to_t1 if expected_days_to_t1 is not None else max(1, int(dte_d * 0.5))
    days_to_t2 = expected_days_to_t2 if expected_days_to_t2 is not None else max(2, int(dte_d * 0.8))
    dte_remaining_t1 = max(0.5, dte_d - days_to_t1)
    dte_remaining_t2 = max(0.25, dte_d - days_to_t2)

    # ─── ATR ceiling on CIO spot targets ────────────────────────────────────
    # The CIO often returns spot SL/T1/T2 calibrated for a multi-day swing
    # trade (e.g. NIFTY -3.6% SL, +3.9% T2). Applied to a single-session F&O
    # play, that produces R:R skew (T2 = +260% premium) and unhittable targets.
    #
    # Clamp each level so it's never WIDER than ATR bands. CIO can still be
    # TIGHTER if it has high-conviction technical reasons (consolidation,
    # tight breakout, etc.) — we just refuse to widen.
    #
    #   BULL (CE): SL is below spot, T1/T2 above
    #   BEAR (PE): SL is above spot, T1/T2 below
    #
    # Multipliers tuned for intraday/short-swing F&O:
    #   1.5×ATR  ≈ ~0.6-0.8% NIFTY move (typical noise stop)
    #   2.5×ATR  ≈ ~1.0-1.4% (achievable T1 in 1-3h, +30-50% premium)
    #   5.0×ATR  ≈ ~2.0-2.7% (stretch T2 in a session, +80-130% premium)
    # The asymmetric R:R (1.5 vs 5) comes from the convex premium gain on
    # the up-side — directional intraday calls reward holding past T1 if
    # momentum confirms.
    if atr and float(atr) > 0:
        a = float(atr)
        S = float(spot)
        # Scale ATR multipliers by DTE — short-dated options can't achieve
        # multi-day spot moves. sqrt(DTE) scaling models diffusion.
        # DTE=1 → 0.45×, DTE=4 → 0.9×, DTE=7 → 1.0× (full mult at 7+ DTE)
        import math
        _dte_scale = min(1.0, math.sqrt(dte_d / 7.0))
        SL_MULT  = 1.5 * max(0.5, _dte_scale)   # floor 0.75 ATR for SL
        T1_MULT  = 2.5 * _dte_scale              # 1DTE: ~1.1 ATR
        T2_MULT  = 5.0 * _dte_scale              # 1DTE: ~1.9 ATR

        # ── Direction validation — Cerebrum sometimes returns BUY-oriented
        #    spot levels even on a SELL/BEAR signal. If the supplied levels
        #    point the wrong way for our bias, OVERRIDE with ATR-derived bands
        #    rather than trust them. This is what produced wildly off SL/T1/T2
        #    on real PE positions earlier (T1 above spot for a put trade).
        def _wrong_dir(level, side):
            if level is None or level <= 0: return False
            if bias == 'BULL':
                return (side == 'sl' and level >= S) or (side in ('t1','t2') and level <= S)
            else:  # BEAR
                return (side == 'sl' and level <= S) or (side in ('t1','t2') and level >= S)

        if _wrong_dir(spot_stop_loss, 'sl') or \
           _wrong_dir(spot_target_1,  't1') or \
           _wrong_dir(spot_target_2,  't2'):
            logger.warning(
                f"plan_option_trade: {underlying} {bias} levels point wrong way "
                f"(spot={S}, sl={spot_stop_loss}, t1={spot_target_1}, t2={spot_target_2}) "
                f"— overriding with ATR-derived bands"
            )
            if bias == 'BULL':
                spot_stop_loss = S - SL_MULT * a
                spot_target_1  = S + T1_MULT * a
                spot_target_2  = S + T2_MULT * a
            else:
                spot_stop_loss = S + SL_MULT * a
                spot_target_1  = S - T1_MULT * a
                spot_target_2  = S - T2_MULT * a

        # Otherwise just clamp the supplied levels into reasonable ATR bands so
        # an over-zealous CIO target doesn't produce stretched SL/T1/T2.
        elif bias == 'BULL':
            sl_band  = S - SL_MULT * a
            t1_band  = S + T1_MULT * a
            t2_band  = S + T2_MULT * a
            if spot_stop_loss is not None: spot_stop_loss = max(float(spot_stop_loss), sl_band)
            if spot_target_1  is not None: spot_target_1  = min(float(spot_target_1),  t1_band)
            if spot_target_2  is not None: spot_target_2  = min(float(spot_target_2),  t2_band)
        else:  # BEAR
            sl_band  = S + SL_MULT * a
            t1_band  = S - T1_MULT * a
            t2_band  = S - T2_MULT * a
            if spot_stop_loss is not None: spot_stop_loss = min(float(spot_stop_loss), sl_band)
            if spot_target_1  is not None: spot_target_1  = max(float(spot_target_1),  t1_band)
            if spot_target_2  is not None: spot_target_2  = max(float(spot_target_2),  t2_band)

    # ─── Real-money safety floors ───────────────────────────────────────────
    # Long-option premium loss is hard-capped: industry standard is 40-50%.
    # If BS spits out an SL that implies > 50% premium loss (because the CIO's
    # spot SL was very close to current spot, collapsing the option to ~zero),
    # we override it with a 50% premium floor — you walk away with half your
    # premium, not zero.
    SL_MAX_LOSS_PCT = 0.50    # SL premium ≥ 50% of entry  → max loss 50%
    SL_MIN_LOSS_PCT = 0.15    # SL premium ≤ 85% of entry  → at least 15% noise buffer
    T1_MIN_GAIN_PCT = 0.30    # T1 ≥ 1.30× entry           → at least +30% target
    T2_MIN_OVER_T1  = 1.40    # T2 ≥ 1.40× T1              → meaningful 2-leg

    # SL: hit early (1 day from now), so theta has barely moved.
    if spot_stop_loss is not None and spot_stop_loss > 0:
        sl_bs = round(_bs_at(float(spot_stop_loss), max(0.5, dte_d - 1)), 2)
    else:
        sl_bs = round(entry_px * SL_MAX_LOSS_PCT, 2)
    # Clamp: floor at 50% of entry (max loss), cap at 85% of entry (min loss).
    sl_floor = round(entry_px * SL_MAX_LOSS_PCT, 2)
    sl_cap   = round(entry_px * (1 - SL_MIN_LOSS_PCT), 2)
    sl_px    = max(sl_floor, min(sl_bs, sl_cap))
    sl_clamped = (sl_px != sl_bs)

    # T1: midway-through-DTE, spot at CIO's target_1
    if spot_target_1 is not None and spot_target_1 > 0:
        t1_bs = round(_bs_at(float(spot_target_1), dte_remaining_t1), 2)
    else:
        t1_bs = round(entry_px * 2.00, 2)
    # T1 must be at least entry × 1.30 — guarantees a positive R:R trade.
    t1_floor = round(entry_px * (1 + T1_MIN_GAIN_PCT), 2)
    t1_px    = max(t1_floor, t1_bs)
    t1_clamped = (t1_px != t1_bs)

    # T2: closer to expiry, spot at CIO's target_2
    if spot_target_2 is not None and spot_target_2 > 0:
        t2_bs = round(_bs_at(float(spot_target_2), dte_remaining_t2), 2)
    else:
        t2_bs = round(entry_px * 3.00, 2)
    t2_floor = round(t1_px * T2_MIN_OVER_T1, 2)
    t2_px    = max(t2_floor, t2_bs)

    # Theta warning preserved — emit if BS originally said the trade was
    # theta-dominated (T1 ≤ entry before clamp). The clamp ensures the
    # ticket has a usable T1, but the user should still know the move
    # required to actually hit T1 is bigger than CIO predicted.
    theta_warning = None
    if t1_bs <= entry_px:
        theta_warning = (
            f"BS-derived T1 (₹{t1_bs}) ≤ entry (₹{entry_px}): expected spot move to "
            f"₹{spot_target_1} won't overcome theta decay over {days_to_t1}d. "
            f"T1 was floored to ₹{t1_px} (+{int(T1_MIN_GAIN_PCT*100)}% min). "
            f"Consider a closer expiry or more ITM strike."
        )
    elif sl_clamped and sl_bs < sl_floor:
        theta_warning = (
            f"BS-derived SL (₹{sl_bs}) implied >{int((1-sl_bs/entry_px)*100)}% premium loss. "
            f"Clamped to ₹{sl_px} (max 50% loss / industry standard for long options)."
        )

    fees_buy  = bu.compute_fees('BUY',  opt_type, entry_px, lot)['total']
    fees_sell = bu.compute_fees('SELL', opt_type, sl_px,    lot)['total']
    max_loss  = round((entry_px - sl_px) * lot + fees_buy + fees_sell, 2)
    breakeven = round(K + entry_px, 2) if opt_type == 'CE' else round(K - entry_px, 2)

    # Expected P&L if T1 hits (per lot, after fees)
    expected_pnl_t1 = round((t1_px - entry_px) * lot - fees_buy - fees_sell, 2)
    expected_pnl_t2 = round((t2_px - entry_px) * lot - fees_buy - fees_sell, 2)
    rr_ratio        = round(expected_pnl_t1 / max_loss, 2) if max_loss > 0 else 0

    # 8. Time-of-day window
    now_t  = bu.now_ist().time()
    if now_t < bu.SAFE_OPEN:
        entry_window = f"{bu.SAFE_OPEN.strftime('%H:%M')}–09:45 IST"
    elif now_t <= dt_time(13, 0):
        entry_window = f"NOW – within 30 min (deadline {dt_time(14,30).strftime('%H:%M')} IST)"
    else:
        entry_window = "AVOID — late session, theta + low momentum"
    on_expiry = meta['expiry'] == bu.today_ist()
    time_exit = ("13:00 IST (expiry-day theta cliff)" if on_expiry
                 else f"{bu.FORCE_EXIT.strftime('%H:%M')} IST today")

    return {
        'underlying':     underlying,
        'spot':           round(spot, 2),
        'bias':           bias,
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
            'order_type':           'MARKET' if (bid > 0 and ask > 0
                                                 and (ask - bid) / mid < 0.02)
                                    else 'LIMIT',
            'expected_premium_inr': entry_px,
            'mid_bid_ask':          (round(bid, 2), round(ask, 2)),
        },
        'exit': {
            'stop_loss_inr':   sl_px,
            'target_1_inr':    t1_px,    # exit 50% here
            'target_2_inr':    t2_px,    # exit remainder
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
            'theta_warning':      theta_warning,
            'spot_targets_used':  {
                'sl': spot_stop_loss,
                't1': spot_target_1,
                't2': spot_target_2,
            },
        },
        'rationale':      rationale or f"{bias} {opt_type} on {underlying}",
        'generated_at':   bu.now_ist().isoformat(),
    }
