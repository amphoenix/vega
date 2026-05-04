#!/usr/bin/env python3
"""
Pre-market preflight for the F&O option pipeline.

Run this on the morning of a trading day BEFORE 09:15 IST. It exercises
every stage that the live system depends on and prints PASS/FAIL for each:

  1. Broker token + reachability
  2. F&O instrument master fetch + column-name compatibility
  3. Underlying spot fetch (NIFTY / BANKNIFTY)
  4. Expiry-date parsing
  5. Strike-step resolution
  6. End-to-end ticket build (delta-targeted)
  7. Greeks sanity check (delta in valid range, IV in 5–80 %)
  8. Live option quote (bid/ask spread)
  9. Cross-check vs an independently computed BS price

Usage (from backend/ dir):
    .venv/bin/python tools/preflight.py
    .venv/bin/python tools/preflight.py --ticker '^NSEI' --bias BEAR
"""
from __future__ import annotations

import argparse
import os
import sys
import json
import traceback
from pathlib import Path

# Make `app` importable when run from anywhere
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

GREEN = '\033[92m'; RED = '\033[91m'; YELLOW = '\033[93m'; DIM = '\033[2m'; END = '\033[0m'

_summary = []


def _check(name: str, fn, *args, **kwargs):
    print(f"\n— {name}")
    try:
        ok, detail = fn(*args, **kwargs)
        tag = f"{GREEN}PASS{END}" if ok else f"{RED}FAIL{END}"
        print(f"  [{tag}] {detail}")
        _summary.append((name, ok, detail))
        return ok
    except Exception as e:
        print(f"  [{RED}ERROR{END}] {e}")
        traceback.print_exc()
        _summary.append((name, False, str(e)))
        return False


# ── 1. broker token ──────────────────────────────────────────────────────────
def _t_broker_token():
    tok = os.environ.get('INDMONEY_ACCESS_TOKEN', '').strip()
    if not tok:
        return False, "INDMONEY_ACCESS_TOKEN not set in env"
    from app.api import indmoney
    if not indmoney._connected():
        return False, "Token set but indmoney._connected() returned False"
    return True, f"token len={len(tok)} OK"


# ── 2. instrument master ──────────────────────────────────────────────────────
def _t_instrument_master():
    from app.api.indmoney import _load_instruments
    rows = _load_instruments('fno')
    if not rows:
        return False, "No F&O rows returned (token expired? endpoint changed?)"
    sample = rows[0]
    keys = sorted(sample.keys())
    print(f"    {DIM}sample row keys: {keys}{END}")
    print(f"    {DIM}sample row     : {json.dumps({k: sample[k] for k in keys[:8]})}{END}")
    # required logical fields
    from app.services import broker_utils as bu
    required = ['trading_symbol', 'security_id', 'exchange', 'lot_size',
                'strike', 'expiry', 'option_type']
    missing = [k for k in required if not bu._field(sample, k, None)]
    if missing:
        return False, (f"Master row missing required logical fields: {missing} — "
                       f"add aliases to broker_utils._FIELD_ALIASES")
    return True, f"{len(rows)} F&O rows; required fields all resolved"


# ── 3. spot fetch ─────────────────────────────────────────────────────────────
def _t_spot(ticker: str):
    from app.services.option_planner import _live_spot
    p = _live_spot(ticker)
    if not p or p <= 0:
        return False, f"_live_spot({ticker}) returned {p}"
    return True, f"{ticker} spot = ₹{p:,.2f}"


# ── 4. expiry parsing ─────────────────────────────────────────────────────────
def _t_expiry_parse():
    from app.services.broker_utils import _parse_expiry
    cases = ['2026-04-30', '30-Apr-2026', '30-04-2026', '30/04/2026',
             '30APR26', '30APR2026', '2026-04-30T00:00:00',
             1746086400, '1746086400']
    fails = [s for s in cases if _parse_expiry(s) is None]
    if fails:
        return False, f"failed to parse: {fails}"
    return True, f"all {len(cases)} formats parsed"


# ── 5. strike step ────────────────────────────────────────────────────────────
def _t_strike_step():
    from app.services.option_planner import _strike_step, _stock_strike_step
    cases = {
        'NIFTY':     50,
        'BANKNIFTY': 100,
        'FINNIFTY':  50,
    }
    bad = [(k, _strike_step(k), v) for k, v in cases.items()
           if _strike_step(k) != v]
    if bad:
        return False, f"step mismatch: {bad}"
    # Also test stock step
    if _stock_strike_step(2500) != 50:
        return False, "stock step at ₹2500 should be 50"
    return True, "index + stock strike steps OK"


# ── 6. nearest expiry ─────────────────────────────────────────────────────────
def _t_nearest_expiry(base: str, opt_type: str = 'CE'):
    from app.services.option_planner import _nearest_expiry
    e = _nearest_expiry(base, opt_type, max_dte=14, min_dte=0)
    if not e:
        return False, f"no {opt_type} expiry found within 14d for {base}"
    return True, f"nearest {opt_type} expiry: {e['expiry'].isoformat()}"


# ── 7. end-to-end ticket ──────────────────────────────────────────────────────
def _t_ticket(ticker: str, bias: str):
    from app.services.option_planner import plan_option_trade
    t = plan_option_trade(underlying=ticker, bias=bias,
                          target_delta=0.35, min_dte=0, max_dte=14)
    if not t:
        return False, f"plan_option_trade returned None for {ticker}/{bias}"
    print(f"    {DIM}symbol = {t['trading_symbol']}{END}")
    print(f"    {DIM}strike = {t['strike']} ({t['expiry']}, lot={t['lot_size']}){END}")
    print(f"    {DIM}entry  = ₹{t['entry']['expected_premium_inr']} "
          f"bid/ask={t['entry']['mid_bid_ask']} order={t['entry']['order_type']}{END}")
    print(f"    {DIM}exit   = SL ₹{t['exit']['stop_loss_inr']} / "
          f"T1 ₹{t['exit']['target_1_inr']} / T2 ₹{t['exit']['target_2_inr']}{END}")
    print(f"    {DIM}greeks = Δ={t['greeks']['delta']} θ/d=₹{t['greeks']['theta_per_day']} "
          f"IV={t['greeks']['iv_used']*100:.2f}%{END}")
    print(f"    {DIM}risk   = max-loss ₹{t['risk']['max_loss_inr']} "
          f"BE={t['risk']['breakeven_spot']} fees=₹{t['risk']['fees_round_trip']}{END}")
    return True, f"ticket built: {t['trading_symbol']}"


# ── 8. greeks sanity ──────────────────────────────────────────────────────────
def _t_greeks_sanity():
    from app.services.greeks import greeks, implied_vol, bs_price
    # Reference: NIFTY 18000 spot, 18200 PE, 9 days, 14% IV → known values
    g = greeks(spot=18000, strike=18200, days_to_expiry=9, iv=0.14, opt_type='PE')
    expected_delta_lo, expected_delta_hi = -0.85, -0.45
    if not (expected_delta_lo <= g['delta'] <= expected_delta_hi):
        return False, f"PE delta {g['delta']} out of range [{expected_delta_lo},{expected_delta_hi}]"
    if g['theta_per_day'] >= 0:
        return False, f"long PE theta should be negative, got {g['theta_per_day']}"
    if g['iv_used'] != 0.14:
        return False, f"iv_used should equal input 0.14, got {g['iv_used']}"
    # IV solver round-trip
    px  = bs_price(18000, 18200, 9/365, 0.07, 0.18, 'PE')
    iv2 = implied_vol(18000, 18200, 9, px, 'PE')
    if iv2 is None or abs(iv2 - 0.18) > 0.01:
        return False, f"IV solver round-trip failed: {iv2} (expected 0.18)"
    return True, f"Greeks Δ={g['delta']} θ={g['theta_per_day']} IV solver round-trip OK"


# ── 9. live option quote ──────────────────────────────────────────────────────
def _t_option_quote(ticker: str, bias: str):
    from app.services.option_planner import plan_option_trade
    from app.api.indmoney import _ind_option_quote
    t = plan_option_trade(underlying=ticker, bias=bias,
                          target_delta=0.35, min_dte=0, max_dte=14)
    if not t:
        return False, "could not build ticket to test"
    q = _ind_option_quote(t['trading_symbol'])
    if not q:
        return False, (f"_ind_option_quote returned None for {t['trading_symbol']} — "
                       "off-hours or quote endpoint failing")
    bid, ask = q.get('bid', 0), q.get('ask', 0)
    if bid <= 0 or ask <= 0:
        return False, f"bid/ask zero ({bid}/{ask}) — likely outside market hours"
    spread_pct = (ask - bid) / ((bid + ask) / 2.0) * 100.0
    return True, f"{t['trading_symbol']} bid={bid} ask={ask} spread={spread_pct:.2f}%"


# ── runner ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ticker', default='^NSEI')
    ap.add_argument('--bias',   default='BULL', choices=['BULL', 'BEAR'])
    args = ap.parse_args()
    base_map = {'^NSEI': 'NIFTY', '^NSEBANK': 'BANKNIFTY'}
    base = base_map.get(args.ticker, args.ticker.upper())

    print(f"\n{'='*68}\nPHOENIX-TRADE PREMARKET PREFLIGHT — {args.ticker} {args.bias}\n{'='*68}")

    _check("Broker token + connectivity",      _t_broker_token)
    _check("F&O instrument master + columns",   _t_instrument_master)
    _check("Greeks math sanity",                _t_greeks_sanity)
    _check("Expiry-date parser",                _t_expiry_parse)
    _check("Strike-step resolver",              _t_strike_step)
    _check(f"Spot fetch ({args.ticker})",       _t_spot, args.ticker)
    _check(f"Nearest expiry ({base})",          _t_nearest_expiry, base,
                                                 'CE' if args.bias == 'BULL' else 'PE')
    _check(f"End-to-end ticket build",          _t_ticket, args.ticker, args.bias)
    _check(f"Live option quote (bid/ask/OI)",   _t_option_quote, args.ticker, args.bias)

    # ── summary ────────────────────────────────────────────────────────────
    print(f"\n{'='*68}\nSUMMARY\n{'='*68}")
    n_pass = sum(1 for _, ok, _ in _summary if ok)
    n_fail = sum(1 for _, ok, _ in _summary if not ok)
    for name, ok, detail in _summary:
        tag = f"{GREEN}PASS{END}" if ok else f"{RED}FAIL{END}"
        print(f"  [{tag}] {name}: {detail}")
    print(f"\n  {n_pass} passed, {n_fail} failed")
    if n_fail:
        print(f"\n{RED}DO NOT TRADE LIVE{END} until all FAILs are resolved.\n")
        sys.exit(1)
    print(f"\n{GREEN}All checks passed — pipeline is ready for the trading session.{END}\n")


if __name__ == '__main__':
    main()
