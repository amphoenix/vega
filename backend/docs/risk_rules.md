# Vega — Risk Rules

> Every rule exists because of a loss. Don't weaken them.

## Kill Switch

**What**: Halts all trading for the rest of the day when cumulative daily P&L exceeds the loss limit.

| Parameter | Swing | Scalp |
|-----------|-------|-------|
| Daily loss limit | ₹2,500 | ₹500 |
| Scope | All underlyings | All underlyings |
| Reset | Midnight IST | Midnight IST |
| Manual override | `force_reset()` | `force_reset()` |

**Why it exists**: Prevents revenge trading after bad days. Once you're down ₹2.5k, the day is lost — stop trading and come back tomorrow.

## Loss Streak Guard

**What**: After 3 consecutive losses, blocks the next trade entry.

| Parameter | Value |
|-----------|-------|
| Max consecutive losses | 3 |
| Reset trigger | 1 winning trade |
| Scope | Per supervisor instance |

**Why it exists**: Consecutive losses often signal wrong regime read. Pause, reassess, then continue.

## Re-Entry Guard

**What**: Limits how many times you can re-enter the same underlying+direction after a stop-loss.

| Parameter | Value |
|-----------|-------|
| Max re-entries after SL | 2 per underlying+direction |
| Cooldown | Per trading session |

**Why it exists**: Prevents hammering the same losing trade. If you got stopped out twice on NIFTY CE, stop trying.

## Exposure Guard

**What**: Caps net directional exposure across all open positions.

| Parameter | Value |
|-----------|-------|
| Max lots per direction | 5 (CE) + 5 (PE) |
| Calculation | Open lots in direction |

**Why it exists**: Prevents over-concentration in one direction. If market reverses, you don't blow up.

## Budget Guard

**What**: Ensures sufficient capital before opening a new position.

| Parameter | Value |
|-----------|-------|
| Risk per trade | 20% of available capital |
| Max lot cap | 5 lots |

**Why it exists**: Position sizing relative to capital. Never bet the house on a single trade.

## Regime Guard

**What**: Adjusts position sizing based on current market regime.

| Regime | Size Multiplier |
|--------|----------------|
| TRENDING_BULL | 1.0× |
| TRENDING_BEAR | 1.0× |
| RANGING | 0.8× |
| HIGH_VOLATILITY | 0.5× |
| LOW_VOLATILITY | 1.0× |

**Why it exists**: In HIGH_VOL (VIX > 20), options premiums are inflated and moves are unpredictable. Cut size in half.

## Correlation Guard

**What**: Limits concurrent positions on the same underlying.

| Parameter | Value |
|-----------|-------|
| Max positions per underlying | 2 |

**Why it exists**: Two NIFTY positions should be enough. More than that and you're doubling down, not diversifying.

## Trade Supervisor Gate Order

Gates are checked in this order (first failure blocks):

```
1. Kill switch active?          → BLOCKED
2. Consecutive loss streak?     → BLOCKED
3. Daily trade count exceeded?  → BLOCKED
4. Cooldown not elapsed?        → BLOCKED
5. Correlation limit hit?       → BLOCKED
6. All clear                    → ALLOWED
```

## Multi-Market Risk (Future)

When crypto and Polymarket go live, risk rules extend:

| Asset Class | Kill Switch | Max Exposure | Position Sizing |
|-------------|------------|--------------|-----------------|
| INDIAN_OPTION | ₹2,500/day | 5 lots/dir | ATR-clamped |
| CRYPTO_PERP | USDT-based limit | leverage-aware | volatility-scaled |
| PREDICTION_MARKET | USDC-based limit | per-market cap | fixed stake |

Cross-market correlation risk will be handled by a future **Portfolio Risk** module inside the Portfolio Engine.

## Rules for Rules

1. **Never delete a risk rule** without a documented reason
2. **Never weaken a limit** in production — only tighten
3. **Test risk gates** — every gate has unit tests, don't skip them
4. **Log every block** — every time a gate blocks a trade, log it with the reason
5. **Review monthly** — look at blocked trades and ask "was this correct?"
