"""
Black-Scholes Greeks for Indian-market options.

Indian listed options are European style (no early exercise) on indices
and futures — plain Black-Scholes applies.

Pure domain service — no I/O, no framework imports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RISK_FREE_RATE = 0.07


# ── Internal math ────────────────────────────────────────────────────────────

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


# ── Public API ───────────────────────────────────────────────────────────────

def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             opt_type: str = 'CE') -> float:
    """Black-Scholes price. T in years, sigma annualised."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if opt_type.upper() == 'CE' else (K - S))
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if opt_type.upper() == 'CE':
        return S * _phi(d1) - K * math.exp(-r * T) * _phi(d2)
    return K * math.exp(-r * T) * _phi(-d2) - S * _phi(-d1)


@dataclass(frozen=True, slots=True)
class GreeksResult:
    premium_bs: float
    delta: float
    gamma: float
    theta_per_day: float
    vega_per_volpt: float
    iv_used: float
    dte_years: float


def greeks(spot: float, strike: float, days_to_expiry: float, iv: float,
           rate: float = RISK_FREE_RATE, opt_type: str = 'CE') -> GreeksResult:
    """Compute BS price + Greeks."""
    T = max(1e-6, float(days_to_expiry) / 365.0)
    sigma = max(1e-6, float(iv))
    K = float(strike)
    S = float(spot)
    r = float(rate)
    typ = opt_type.upper()

    d1, d2 = _d1_d2(S, K, T, r, sigma)
    pdf_d1 = _pdf(d1)

    if typ == 'CE':
        delta = _phi(d1)
        theta_yr = (-S * pdf_d1 * sigma / (2.0 * math.sqrt(T))
                    - r * K * math.exp(-r * T) * _phi(d2))
    else:
        delta = _phi(d1) - 1.0
        theta_yr = (-S * pdf_d1 * sigma / (2.0 * math.sqrt(T))
                    + r * K * math.exp(-r * T) * _phi(-d2))

    gamma = pdf_d1 / (S * sigma * math.sqrt(T))
    vega = S * pdf_d1 * math.sqrt(T)

    return GreeksResult(
        premium_bs=round(bs_price(S, K, T, r, sigma, typ), 2),
        delta=round(delta, 4),
        gamma=round(gamma, 6),
        theta_per_day=round(theta_yr / 365.0, 4),
        vega_per_volpt=round(vega / 100.0, 4),
        iv_used=round(sigma, 4),
        dte_years=round(T, 5),
    )


def implied_vol(spot: float, strike: float, dte_days: float,
                market_price: float, opt_type: str = 'CE',
                rate: float = RISK_FREE_RATE,
                lo: float = 0.01, hi: float = 5.0,
                tol: float = 1e-4, max_iter: int = 80) -> float | None:
    """Solve for sigma via bisection such that BS(sigma) ≈ market_price."""
    if market_price is None or market_price <= 0:
        return None
    T = max(1e-6, float(dte_days) / 365.0)
    intrinsic = max(0.0, (spot - strike) if opt_type.upper() == 'CE' else (strike - spot))
    if market_price < intrinsic - 0.01:
        return None

    f_lo = bs_price(spot, strike, T, rate, lo, opt_type) - market_price
    f_hi = bs_price(spot, strike, T, rate, hi, opt_type) - market_price
    if f_lo > 0 and f_hi > 0:
        return lo
    if f_lo < 0 and f_hi < 0:
        return hi

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_m = bs_price(spot, strike, T, rate, mid, opt_type) - market_price
        if abs(f_m) < tol:
            return round(mid, 6)
        if f_lo * f_m < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_m
    return round(0.5 * (lo + hi), 6)


def pick_strike_by_delta(spot: float, target_delta: float, dte_days: float,
                         iv: float, opt_type: str = 'CE', step: int = 50,
                         rate: float = RISK_FREE_RATE,
                         search_pct: float = 0.20) -> int:
    """Find strike (rounded to step) whose BS-delta is closest to target_delta.

    DTE is floored at 1 day for the delta calculation: on expiry day (DTE=0)
    the BS delta degenerates to a step function (every ITM strike → 1.0, every
    OTM → 0.0), which makes the "closest to target" search collapse onto the
    deepest-ITM strike in iteration order. Flooring keeps the delta curve smooth
    so an ATM-ish strike is chosen. This floor affects strike *selection* only —
    P&L/theta greeks elsewhere still use the true DTE.
    """
    target = abs(target_delta)
    typ = opt_type.upper()
    dte_eff = max(float(dte_days), 1.0)
    candidates: list[tuple[float, float, int]] = []
    span = int(spot * search_pct)
    base = int(round(spot / step) * step)
    for k in range(base - span, base + span + step, step):
        if k <= 0:
            continue
        g = greeks(spot, k, dte_eff, iv, rate, typ)
        d = abs(g.delta)
        # Tie-break on distance-to-ATM so near-equal deltas prefer the ATM strike.
        candidates.append((abs(d - target), abs(k - base), k))
    if not candidates:
        return base
    candidates.sort()
    return candidates[0][2]


_DEFAULT_INDEX_IV = 0.14
_DEFAULT_STOCK_IV = 0.30


def default_iv(underlying: str) -> float:
    base = (underlying or '').upper().lstrip('^').replace('.NS', '').replace('.BO', '')
    if base in ('NSEI', 'NIFTY', 'NSEBANK', 'BANKNIFTY', 'CNXFIN', 'FINNIFTY',
                'NSEMDCP50', 'BSESN', 'SENSEX'):
        return _DEFAULT_INDEX_IV
    return _DEFAULT_STOCK_IV
