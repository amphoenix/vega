"""
Black-Scholes Greeks for Indian-market options.

Indian listed options are *European* style (no early exercise) on indices
and futures, so plain Black-Scholes / Black-76 applies.

Public API:
  greeks(spot, strike, days_to_expiry, iv, rate=0.07, opt_type='CE')
      → dict with delta, gamma, theta_per_day, vega_per_volpt, premium_bs

  implied_vol(spot, strike, dte_days, market_price, opt_type, rate=0.07)
      → IV (annualised, decimal) such that BS price ≈ market_price
        Returns None if cannot solve (e.g. premium below intrinsic).

  pick_strike_by_delta(spot, target_delta, dte_days, iv, rate, opt_type, step)
      → strike rounded to `step` whose BS-delta is closest to target_delta
"""
from __future__ import annotations

import math
from typing import Optional

# Risk-free rate proxy for Indian market (10-yr G-Sec around 6.8–7.2%).
RISK_FREE_RATE = 0.07


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vt
    d2 = d1 - vt
    return d1, d2


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             opt_type: str = 'CE') -> float:
    """Black-Scholes price (T in *years*, sigma annualised)."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if opt_type.upper() == 'CE' else (K - S))
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if opt_type.upper() == 'CE':
        return S * _phi(d1) - K * math.exp(-r * T) * _phi(d2)
    return K * math.exp(-r * T) * _phi(-d2) - S * _phi(-d1)


def greeks(spot: float, strike: float, days_to_expiry: float, iv: float,
           rate: float = RISK_FREE_RATE, opt_type: str = 'CE') -> dict:
    """
    Returns dict with:
      premium_bs       : theoretical BS premium
      delta            : ∂price / ∂spot   (CE: 0..1, PE: -1..0)
      gamma            : ∂delta / ∂spot
      theta_per_day    : ∂price / ∂t  (negative for long; ₹ lost per calendar day)
      vega_per_volpt   : ∂price / ∂σ scaled to *1 volatility point* (1% IV)
      iv_used          : annualised σ used
      dte_years        : T

    All inputs in market units: spot/strike in ₹, IV as decimal (0.18 = 18%),
    days_to_expiry in calendar days.
    """
    T  = max(1e-6, float(days_to_expiry) / 365.0)
    σ  = max(1e-6, float(iv))
    K  = float(strike)
    S  = float(spot)
    r  = float(rate)
    typ = opt_type.upper()

    d1, d2 = _d1_d2(S, K, T, r, σ)
    pdf_d1 = _pdf(d1)

    if typ == 'CE':
        delta = _phi(d1)
        theta_yr = (-S * pdf_d1 * σ / (2.0 * math.sqrt(T))
                    - r * K * math.exp(-r * T) * _phi(d2))
    else:
        delta = _phi(d1) - 1.0
        theta_yr = (-S * pdf_d1 * σ / (2.0 * math.sqrt(T))
                    + r * K * math.exp(-r * T) * _phi(-d2))

    gamma = pdf_d1 / (S * σ * math.sqrt(T))
    vega  = S * pdf_d1 * math.sqrt(T)        # per 1.0 (i.e. 100 vol-points)
    return {
        'premium_bs':     round(bs_price(S, K, T, r, σ, typ), 2),
        'delta':          round(delta, 4),
        'gamma':          round(gamma, 6),
        'theta_per_day':  round(theta_yr / 365.0, 4),
        'vega_per_volpt': round(vega / 100.0, 4),
        'iv_used':        round(σ, 4),
        'dte_years':      round(T, 5),
    }


def implied_vol(spot: float, strike: float, dte_days: float,
                market_price: float, opt_type: str = 'CE',
                rate: float = RISK_FREE_RATE,
                lo: float = 0.01, hi: float = 5.0,
                tol: float = 1e-4, max_iter: int = 80) -> Optional[float]:
    """
    Solve for σ such that BS(σ) ≈ market_price using bisection.
    Returns None if market_price < intrinsic or solver fails.
    """
    if market_price is None or market_price <= 0:
        return None
    T = max(1e-6, float(dte_days) / 365.0)
    intrinsic = max(0.0, (spot - strike) if opt_type.upper() == 'CE'
                       else (strike - spot))
    if market_price < intrinsic - 0.01:
        return None

    f_lo = bs_price(spot, strike, T, rate, lo, opt_type) - market_price
    f_hi = bs_price(spot, strike, T, rate, hi, opt_type) - market_price
    if f_lo > 0 and f_hi > 0:
        return lo                       # very deep ITM, fitted to floor
    if f_lo < 0 and f_hi < 0:
        return hi                       # extreme IV
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_m = bs_price(spot, strike, T, rate, mid, opt_type) - market_price
        if abs(f_m) < tol:
            return round(mid, 6)
        if f_lo * f_m < 0:
            hi, f_hi = mid, f_m
        else:
            lo, f_lo = mid, f_m
    return round(0.5 * (lo + hi), 6)


def pick_strike_by_delta(spot: float, target_delta: float, dte_days: float,
                         iv: float, opt_type: str = 'CE', step: int = 50,
                         rate: float = RISK_FREE_RATE,
                         search_pct: float = 0.20) -> int:
    """
    Find strike (rounded to `step`) whose BS-delta is closest to `target_delta`.

    target_delta is in absolute terms (0.35 means 35-delta call OR 35-delta put).
    `step` is the strike interval (NIFTY 50, BANKNIFTY 100, FINNIFTY 50, stocks vary).
    `search_pct` limits the search window around spot (default ±20 %).
    """
    target = abs(target_delta)
    typ    = opt_type.upper()
    candidates = []
    span = int(spot * search_pct)
    base = int(round(spot / step) * step)
    for k in range(base - span, base + span + step, step):
        if k <= 0:
            continue
        g = greeks(spot, k, dte_days, iv, rate, typ)
        d = abs(g['delta'])
        candidates.append((abs(d - target), k, g))
    if not candidates:
        return base
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


# ── Indian-market IV regime helper ────────────────────────────────────────────
# Quick fallback when broker doesn't expose IV directly.
_DEFAULT_INDEX_IV = 0.14   # NIFTY ATM ~ 12-16 % in normal regime
_DEFAULT_STOCK_IV = 0.30   # mid-cap stock options ~ 25-40 %


def default_iv(underlying: str) -> float:
    base = (underlying or '').upper().lstrip('^').replace('.NS', '').replace('.BO', '')
    if base in ('NSEI', 'NIFTY', 'NSEBANK', 'BANKNIFTY', 'CNXFIN', 'FINNIFTY',
                'NSEMDCP50', 'BSESN', 'SENSEX'):
        return _DEFAULT_INDEX_IV
    return _DEFAULT_STOCK_IV
