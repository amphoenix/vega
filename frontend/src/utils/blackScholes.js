/**
 * Black-Scholes option pricing in pure JS — runs on every tick to keep
 * the UI ticket card live as the underlying price moves. Mirrors the
 * Python implementation in backend/app/services/greeks.py so values
 * match the server's ticket exactly when spot == ticket.spot.
 */

const RISK_FREE_RATE = 0.07   // RBI repo-ish; same default as backend

// Abramowitz & Stegun approximation of the standard normal CDF.
function normCdf(x) {
  const a1 =  0.254829592
  const a2 = -0.284496736
  const a3 =  1.421413741
  const a4 = -1.453152027
  const a5 =  1.061405429
  const p  =  0.3275911
  const sign = x < 0 ? -1 : 1
  const ax = Math.abs(x) / Math.SQRT2
  const t = 1.0 / (1.0 + p * ax)
  const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax)
  return 0.5 * (1.0 + sign * y)
}

function normPdf(x) {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI)
}

/** BS theoretical price (S=spot, K=strike, dteDays, sigma=IV decimal, type='CE'|'PE'). */
export function bsPrice(S, K, dteDays, sigma, type = 'CE', r = RISK_FREE_RATE) {
  if (!S || !K || sigma <= 0) return 0
  const T = Math.max(1e-6, dteDays / 365)
  if (dteDays <= 0) {
    return Math.max(0, type === 'CE' ? S - K : K - S)
  }
  const sqrtT = Math.sqrt(T)
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
  const d2 = d1 - sigma * sqrtT
  if (type === 'CE') {
    return S * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2)
  }
  return K * Math.exp(-r * T) * normCdf(-d2) - S * normCdf(-d1)
}

/** All Greeks + theoretical price at (S, K, dte, sigma). */
export function bsGreeks(S, K, dteDays, sigma, type = 'CE', r = RISK_FREE_RATE) {
  if (!S || !K || sigma <= 0) {
    return { price: 0, delta: 0, gamma: 0, theta_per_day: 0, vega_per_volpt: 0, iv_used: sigma }
  }
  const T = Math.max(1e-6, dteDays / 365)
  const sqrtT = Math.sqrt(T)
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
  const d2 = d1 - sigma * sqrtT
  const phi = normPdf(d1)
  const Nd1 = normCdf(d1)
  const Nd2 = normCdf(d2)
  let delta, thetaAnnual
  if (type === 'CE') {
    delta = Nd1
    thetaAnnual = -S * phi * sigma / (2 * sqrtT) - r * K * Math.exp(-r * T) * Nd2
  } else {
    delta = Nd1 - 1
    thetaAnnual = -S * phi * sigma / (2 * sqrtT) + r * K * Math.exp(-r * T) * normCdf(-d2)
  }
  return {
    price:          bsPrice(S, K, dteDays, sigma, type, r),
    delta:          delta,
    gamma:          phi / (S * sigma * sqrtT),
    theta_per_day:  thetaAnnual / 365,
    vega_per_volpt: S * phi * sqrtT / 100,
    iv_used:        sigma,
  }
}

/**
 * Re-price a static trade ticket against a live spot price.
 * Returns an object you can spread over the original ticket to refresh
 * the dynamic fields (premium estimate, all Greeks, breakeven distance,
 * SL/T1/T2 still valid, P&L vs entry).
 *
 *   const live = repriceTicket(latestTicket, currentSpot)
 *   live.premium_now      // BS theoretical at current spot, today
 *   live.delta, theta...  // refreshed Greeks
 *   live.pct_from_entry   // (premium_now - entry) / entry * 100
 *   live.pct_to_t1        // (t1 - premium_now) / premium_now * 100
 *   live.pct_to_sl        // (sl - premium_now) / premium_now * 100
 */
export function repriceTicket(ticket, currentSpot) {
  if (!ticket || !currentSpot) return null
  const K        = Number(ticket.strike)
  const sigma    = Number(ticket.greeks?.iv_used) || 0.18
  const dte      = Number(ticket.days_to_expiry) || 1
  const type     = ticket.option_type === 'PE' ? 'PE' : 'CE'
  const entry    = Number(ticket.entry?.expected_premium_inr) || 0
  const lot      = Number(ticket.lot_size) || 1
  const t1       = Number(ticket.exit?.target_1_inr) || 0
  const t2       = Number(ticket.exit?.target_2_inr) || 0
  const sl       = Number(ticket.exit?.stop_loss_inr) || 0

  const g       = bsGreeks(currentSpot, K, dte, sigma, type)
  const premium = Math.max(0.05, g.price)

  const pctFromEntry = entry > 0 ? ((premium - entry) / entry) * 100 : 0
  const pnlPerLot    = (premium - entry) * lot
  const moneyness    = type === 'CE'
    ? (currentSpot >= K ? 'ITM' : 'OTM')
    : (currentSpot <= K ? 'ITM' : 'OTM')
  const distToStrike = type === 'CE' ? K - currentSpot : currentSpot - K   // pts to ATM

  return {
    spot_now:       currentSpot,
    premium_now:    Math.round(premium * 100) / 100,
    pnl_per_lot:    Math.round(pnlPerLot),
    pct_from_entry: Math.round(pctFromEntry * 100) / 100,
    moneyness,
    dist_to_strike: Math.round(distToStrike * 100) / 100,
    pct_to_t1:      premium > 0 ? Math.round(((t1 - premium) / premium) * 100 * 100) / 100 : 0,
    pct_to_t2:      premium > 0 ? Math.round(((t2 - premium) / premium) * 100 * 100) / 100 : 0,
    pct_to_sl:      premium > 0 ? Math.round(((sl - premium) / premium) * 100 * 100) / 100 : 0,
    delta:          Math.round(g.delta * 1000) / 1000,
    gamma:          g.gamma,
    theta_per_day:  Math.round(g.theta_per_day * 100) / 100,
    vega_per_volpt: Math.round(g.vega_per_volpt * 100) / 100,
    iv_used:        sigma,
  }
}

/**
 * Plain-English glossary used by the ticket card tooltips.
 * Keep terse — these go into HTML title="" attributes.
 */
export const GLOSSARY = {
  spot:    'SPOT — current market price of the underlying (e.g. NIFTY index).',
  strike:  'STRIKE — the price at which the option becomes “in the money”. CE profits when spot > strike at expiry; PE when spot < strike.',
  entry:   'ENTRY — what 1 unit of this option costs to buy right now. Multiply by LOT to get total cost per lot.',
  premium_now: 'LIVE PREMIUM — what the option is theoretically worth right now, recomputed from current spot via Black-Scholes. Updates with every tick.',
  sl:      'STOP LOSS — exit immediately if premium falls to this level. Caps your loss.',
  t1:      'TARGET 1 — premium level for partial exit (sell half). Computed from your spot target_1, theta-aware.',
  t2:      'TARGET 2 — premium level for final exit. Computed from your spot target_2.',
  delta:   'DELTA (Δ) — how much the premium moves per ₹1 spot move. Δ=0.5 means ATM; closer to 1 means more like the underlying.',
  theta:   'THETA (θ) — daily premium decay from time passing. Negative = you lose this much per day even if spot stays still.',
  iv:      'IMPLIED VOLATILITY (IV) — market’s expectation of annual underlying volatility. Higher IV = more expensive premium.',
  gamma:   'GAMMA (Γ) — how fast Δ itself changes. High gamma near expiry = explosive P&L swings on small spot moves.',
  vega:    'VEGA (ν) — how much premium changes per +1% IV move. Volatile news days move IV more than spot.',
  be:      'BREAKEVEN — spot price at which P&L = 0 at expiry, ignoring time decay before then.',
  dte:     'DAYS TO EXPIRY (DTE) — days until contract expires. Theta accelerates dramatically in the last 3 days.',
  rr:      'RISK : REWARD — if SL hits you lose ₹X; if T1 hits you make ₹Y. R:R = Y/X. Above 1:1 means rewards outweigh risk.',
  itm:     'ITM (In The Money) — strike already crossed. Option has intrinsic value, less theta-vulnerable.',
  otm:     'OTM (Out of The Money) — strike not yet reached. Premium is purely time value, decays fast.',
  lot:     'LOT SIZE — minimum tradable units per contract (NIFTY=65, BANKNIFTY=30, stocks vary).',
  bias:    'BIAS — BULL means we expect spot to rise (buy CE); BEAR means we expect it to fall (buy PE).',
  max_loss:'MAX LOSS — the most you can lose on this trade if SL hits, including round-trip fees.',
  pnl_t1:  'EXPECTED P&L AT T1 — net profit per lot if Target 1 hits, after both buy + sell fees.',
  warning: 'THETA WARNING — even if T1 spot hits, theta decay during the holding period eats more premium than you gain. Skip or pick a closer expiry.',
}
