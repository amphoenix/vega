# Vega — Trading Playbook

> What we trade, how we trade, and why.

## Markets

| Market | Assets | Exchange | Mode |
|--------|--------|----------|------|
| **Indian F&O** | NIFTY, BANKNIFTY, FINNIFTY, SENSEX CE/PE | INDMoney | Scalp + Swing |
| **Crypto** | BTC, ETH, SOL perps | Binance, Bybit, OKX (via CCXT) | Swing |
| **Polymarket** | Event markets, macro, crypto prediction | Polymarket (py-clob-client) | Research + Swing |

## Trading Modes

### Scalp (No AI)

- **Hold time**: 2–10 minutes
- **SL**: 5–10 points (tight)
- **Target**: 15–25 points
- **Entry**: Pure momentum — Donchian breakout + volume spike + RSI gate
- **Regime filter**: paused during HIGH_VOLATILITY
- **Max re-entries**: 10/day
- **Daily loss limit**: ₹500 (separate from swing)

### Swing (AI-Assisted)

- **Hold time**: minutes to hours (intraday) or multi-day
- **SL**: ATR-clamped, typically 15–40 points
- **Target**: T1 (50% exit), T2 (remaining)
- **Entry**: Technical pre-filter → LLM confirmation → Risk gates → Auto-entry
- **Regime filter**: adapted sizing per regime
- **Daily loss limit**: ₹2500

## Entry Flow

```
1. Scanner detects signal (momentum / LLM-confirmed)
2. Risk Engine checks all 6 gates:
   - Budget guard (daily P&L limit)
   - SL enforcer (stop-loss validates)
   - Re-entry guard (max re-entries per underlying after SL)
   - Exposure guard (net directional limit)
   - Regime guard (multiplier 0.5x in HIGH_VOL)
   - Position sizer (capital-aware lot calculation)
3. Trade Supervisor checks:
   - Kill switch not active
   - Consecutive losses < 3
   - Trade count < daily max
   - Cooldown elapsed
   - Correlation limit per underlying
4. Order placed → fill confirmed
5. Position tracked with SL/T1/T2 levels
```

## Exit Flow

```
Position monitor (1s poll) evaluates:
  - SL hit → full exit, record loss
  - T1 hit → 50% exit, trail SL to breakeven
  - T2 hit → full exit, record profit
  - Time exit → force close at 15:00 IST (F&O)
  - Manual exit → user override
```

## Risk Rules Summary

See `risk_rules.md` for full details.

| Rule | Value |
|------|-------|
| Daily loss limit (swing) | ₹2,500 |
| Daily loss limit (scalp) | ₹500 |
| Max consecutive losses | 3 (blocks next trade) |
| Max trades/day | configurable |
| Re-entry after SL | max 2 per underlying+direction |
| Exposure limit | max 5 lots net per direction |
| Regime HIGH_VOL | position size × 0.5 |
| Max lot cap | 5 lots |
| Risk per trade | 20% of available capital |

## Brokerage Model (Indian F&O)

- Flat brokerage: ₹40 per round trip
- Exchange txn: 0.035% of turnover
- STT: 0.1% of sell value
- SEBI: 0.0001% of turnover
- Stamp: 0.003% of buy value
- GST: 18% of (brokerage + exchange txn + SEBI)

## NSE Market Hours

- Pre-open: 09:00–09:15 IST
- Trading: 09:15–15:30 IST
- No trading on NSE holidays (see `shared/time.py` for calendar)

## Crypto Hours

- 24/7
- No holiday calendar
- Funding rate awareness needed for perps (future)

## Polymarket Hours

- 24/7
- Settlement is on-chain (Polygon)
- USDC-denominated
