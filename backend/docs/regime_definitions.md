# Vega — Regime Definitions

> The regime tells you *what kind of market you're in*.
> It doesn't tell you *what to trade* — that's the strategy's job.

## Why Regimes Matter

A strategy that works in a trending market will bleed in a ranging market.
A strategy tuned for low volatility will get destroyed in a VIX spike.

The Regime Engine classifies the current market state so that:

1. **Strategies** can adapt their logic per regime
2. **Risk Engine** can adjust position sizing
3. **Trade Supervisor** can block trades in hostile regimes
4. **Analytics** can show "best regime" / "worst regime" for post-trade review

## Regime Classifications

### TRENDING_BULL

**Condition**: ADX > 25 and price above 20 EMA and VIX < 20

**Character**:
- Clean uptrend with follow-through
- Breakouts tend to work
- Pullbacks are shallow

**Strategy bias**: Long-biased (CE). Entries on pullback to EMA or breakout of range.

**Position sizing**: 1.0× (full size)

### TRENDING_BEAR

**Condition**: ADX > 25 and price below 20 EMA and VIX < 20

**Character**:
- Clean downtrend with follow-through
- Rallies are sold
- Supports break

**Strategy bias**: Short-biased (PE). Entries on rally to EMA or breakdown.

**Position sizing**: 1.0× (full size)

### RANGING

**Condition**: ADX ≤ 25 and VIX < 20

**Character**:
- Price oscillates between support and resistance
- Breakouts fail and reverse
- Mean-reversion works

**Strategy bias**: Range-bound — buy support, sell resistance. Avoid breakout trades.

**Position sizing**: 0.8× (slightly reduced — false breakouts eat capital)

### HIGH_VOLATILITY

**Condition**: VIX ≥ 20

**Character**:
- Wide intraday ranges
- Options premiums inflated
- Stop-losses get triggered frequently
- Unpredictable — news-driven

**Strategy bias**: Caution. Reduce size. Widen SL or sit out.

**Position sizing**: 0.5× (half size)

### LOW_VOLATILITY

**Condition**: VIX < 13 and ADX ≤ 20

**Character**:
- Very tight ranges
- Low premium → cheap options
- Breakouts eventually happen after compression

**Strategy bias**: Accumulate positions cheaply. Prepare for breakout but don't force trades.

**Position sizing**: 1.0× (full size — options are cheap)

## Regime Detection Parameters

| Parameter | Source | Default |
|-----------|--------|---------|
| VIX | India VIX | Real-time from data provider |
| ADX | 14-period ADX on NIFTY spot | Calculated from OHLCV |
| EMA | 20-period EMA on NIFTY spot | Calculated from close prices |
| RSI | 14-period RSI on NIFTY spot | Calculated from close prices |

## Regime Transitions

```
HIGH_VOLATILITY ←→ (any regime when VIX crosses 20)
TRENDING_BULL ←→ RANGING (when ADX crosses 25, or price crosses EMA)
TRENDING_BEAR ←→ RANGING (when ADX crosses 25, or price crosses EMA)
LOW_VOLATILITY ←→ RANGING (when VIX crosses 13 or ADX crosses 20)
```

## Event: RegimeChanged

When the regime transitions, the system emits a `RegimeChanged` event containing:

- `old_regime` — previous classification
- `new_regime` — current classification
- `vix` — current India VIX
- `adx` — current 14-period ADX

Subscribers (strategies, risk engine) react accordingly.

## Multi-Market Regimes (Future)

| Market | Volatility Indicator | Trend Indicator |
|--------|---------------------|-----------------|
| Indian F&O | India VIX | NIFTY ADX/EMA |
| Crypto | BTC ATR / Realized Vol | BTC ADX/EMA |
| Polymarket | Spread + volume | Not applicable (event-driven) |

Each market will eventually have its own regime classification, but they all feed into the same `RegimeEngine` interface.

## Rules

1. **Regime is read-only** — it describes the market, it doesn't change it
2. **One regime per market** — no mixing NIFTY regime with BTC regime
3. **Regime persists** — don't flip on every tick. Use smoothing / confirmation
4. **Log every transition** — for post-session review
5. **Regime affects sizing, not direction** — the strategy decides direction, the regime scales size
