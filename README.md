# Vega

**Multi-Market Trading Terminal**

A professional-grade trading terminal combining real-time broker WebSocket data, full-stack technical analysis with pure-Python indicators, a pure-technical (LLM-free) F&O signal scanner, a momentum scalp scanner with let-winners-run trailing exits, browser-side Black-Scholes option pricing (live chain ladder + per-ticket repricing), and a full auto-entry/auto-exit execution engine — all in a single browser-based interface. Broker-agnostic: swap Dhan ⇄ IndMoney ⇄ Groww (or add another) via `config/brokers.yaml`, zero code changes.

**Broker-agnostic** — switch between INDmoney, Dhan, Groww, or any future broker by changing one line in `config/brokers.yaml`. Zero code changes.

**Two operating modes:**
- **Auto mode** — scanner signals trigger paper or live orders automatically via `order_executor`, with strict SL enforcement, trailing stops, and daily loss kill-switch
- **Monitor mode** — scanner runs and displays signals, but no orders are placed; you decide and execute manually at your broker

---

## What it does

Vega gives you a complete research-to-decision workflow for F&O intraday trading:

1. **Watch** live candlestick charts — broker WebSocket candles across 6 intervals (5M/30M/1H/1D/1W/1Y), default 5M; Y-axis on left, price-level labels on right; mouse-wheel zoom + reset controls
2. **Scan** the index F&O universe (NIFTY 50, SENSEX) every cycle — two-stage pipeline (pure-Python technical prefilter) emits BUY/SELL CE/PE signals when confidence ≥ threshold; in Auto mode, qualifying signals trigger paper/live orders via `order_executor`
3. **Scalp** with a separate momentum scanner — 1-min candles, Donchian breakout + RSI + volume spike, auto-entry/exit, tick-driven SL via WS, daily loss kill-switch
4. **Reprice live in the browser** — option chain ladder (ATM ± 5 strikes) and per-position ticket cards re-priced on every spot tick via JS Black-Scholes (delta, theta, vega, premium) — zero server round-trip per tick
5. **Decide which strike** with built-in buy-quality tags — CONSERVATIVE / BALANCED / AGGRESSIVE / LOTTERY / EXPENSIVE — derived from |Δ| so you instantly see whether a strike fits your risk profile
6. **Signal** intraday with pure-Python indicators: Supertrend, ADX, Bollinger Bands, RSI, EMA stack, MACD, VWAP, Donchian — plus candlestick pattern detection (Engulfing, Morning Star, Doji, Hammer, Three Soldiers, etc.); S/R levels derived from Standard Floor Pivot Points (Zerodha/Groww formula)
7. **Track** the trades you take manually — pin entered tickets via "I entered" so the server-side watcher fires SL / T1 / T2 / theta-zone / 15:00 exit alerts via SSE even if your tab is hidden
8. **Auto-execute** — `order_executor` places BUY orders when scanner confidence meets threshold, auto-exits on SL hit / T1 / T2 / time exit, records realized P&L, enforces daily loss limit kill-switch
9. **Trail stops** — tracked_monitor dynamically trails SL upward after 20%+ premium gain, caps unrealistic targets by DTE
10. **Backtest** with vectorbt multi-strategy (RSI, EMA cross, Bollinger, MACD)
11. **Stream** live prices via broker WebSocket — tick cache → REST quote → yfinance fallback chain
12. **Other segments** — BTST (overnight index options, scanned at 15:20), plus Crypto, Polymarket and Forex tabs (`backend/app/engines/`, `backend/app/api/`)

---

## Feature Reference

### Live Market Data

| Feature | Detail |
|---|---|
| **OHLCV Charts** | Candlestick + volume bars; intervals: 5M (default), 30M, 1H, 1D, 1W, 1Y; Y-axis left, price-line label boxes right |
| **Real-time Price** | Broker WebSocket tick cache → broker REST quote → yfinance fallback |
| **Intraday Candles** | 5-minute broker historical candles (last 7 days = 75+ bars per analysis) |
| **Daily Candles** | 200-day broker historical candles for trend/EMA200 |
| **World Indices** | Real-time index grid — NIFTY, SENSEX, S&P 500, Nasdaq, DAX, Nikkei, etc. |
| **Market Status** | Live NSE open/closed badge — 09:15–15:30 IST, holiday-aware |

### Technical Indicators (Pure Python, Zero Dependencies)

All indicators computed server-side — no talib, no pandas-ta, no external deps.

| Indicator | Detail |
|---|---|
| **Supertrend** | ATR × 3 band-following trend filter — primary trend signal for F&O; direction (1=bullish, -1=bearish) + line level |
| **ADX** | Average Directional Index — ADX > 25 = tradeable trend, < 20 = ranging; +DI / -DI for direction |
| **RSI (14)** | Wilder smoothing — divergence vs price level is key signal |
| **EMA 9/20/50/200** | Stack analysis — price vs EMA20/50/200 for trend bias |
| **MACD** | 12/26/9 — bullish/bearish crossover + histogram for momentum |
| **Bollinger Bands** | 20-period, 2σ — BB rating system: STRONG_BREAKOUT / BUY / NEUTRAL / SELL / STRONG_BREAKDOWN |
| **ATR (14)** | True Range — used for SL sizing and position risk |
| **VWAP** | Session VWAP (last 78 × 5-min bars) — above = institutional buy bias |
| **Donchian Channel** | 20-period high/low channel — breakout detection |
| **Volume Ratio** | Last bar volume / 20-bar average — expanding vs contracting |
| **52W High/Low** | Range proximity — within 3% = high reaction probability level |

### Candlestick Pattern Detection

Pre-computed by `ta_utils.detect_patterns()` on last 5 bars.

| Category | Patterns |
|---|---|
| **Single-bar** | Doji, Gravestone Doji, Dragonfly Doji, Bullish/Bearish Marubozu, Hammer, Hanging Man, Shooting Star, Inverted Hammer |
| **Two-bar** | Bullish/Bearish Engulfing (⭐ high-confidence reversal), Bullish/Bearish Harami, Tweezer Top, Tweezer Bottom |
| **Three-bar** | Morning Star (⭐ strong bull reversal — buy CE), Evening Star (⭐ strong bear reversal — buy PE), Three White Soldiers, Three Black Crows, Inside Bar Compression |

Each pattern is labeled with F&O action hint (e.g. `Morning Star (STRONG BULLISH REVERSAL — buy CE/long FUT)`).

### F&O Signal Scanner

Background service that cycles through the F&O universe and emits trade signals to the UI. In **Auto mode**, qualifying signals are forwarded to `order_executor` for paper/live order placement. In **Monitor mode**, signals display in the UI but no orders fire.

**Universe (configurable via `FO_UNIVERSE` env):**
`^NSEI` (NIFTY 50), `^BSESN` (SENSEX)

**Per-ticker pipeline:**
1. Fetch 5-min broker candles (last 7 days) + compute all indicators
2. **Pure-Python technical filter** (instant, no LLM)
   - Tiered confidence based on Supertrend × ADX × RSI × EMA stack:
     - `STRONG BUY/SELL` (conf 82) — ADX > 25 + RSI > 55/<45 + price vs EMA20 confirms
     - `BUY/SELL` (conf 72) — ADX > 20 + RSI > 50/<50
     - soft `BUY/SELL` (conf 62) — ADX 15-20 with directional Supertrend (low-vol regimes)
     - SKIP — ADX < 15 or no Supertrend direction
3. Option ticket built via `option_planner.plan_option_trade()` — delta-targeted strike, BS-derived premium SL/T1/T2, full Greeks
4. Signal broadcast to the UI via SSE — `scan_signal` carries the full ticket (verdict, confidence, strike, expiry, SL, T1, T2, Greeks)

**Strike selection — `option_planner.plan_option_trade()`:**
- Scanner default `target_delta = 0.35` (`FO_TARGET_DELTA`); the planner's own fallback is 0.50 (ATM, best gamma:theta tradeoff)
- DTE bumped automatically when `dte < 4` to avoid OTM theta-cliff
- Premium SL/T1/T2 are **back-derived from spot targets via Black-Scholes** at the planner
- Full Greeks attached to every ticket: Δ, Γ, θ/day, vega, IV

**SSE events streamed to frontend:**
`scan_start` → `scan_progress` → `scan_signal` → `scan_complete`

### Scalp Scanner (`scalp_scanner.py`)

Fast momentum-based scalp trading on 1-minute candles — **no LLM, pure technical**. Runs as a separate background thread alongside the F&O scanner. Designed for quick in-and-out trades (≤10 min hold) on NIFTY/SENSEX ATM options.

**Detection pipeline (per scan cycle):**
1. Fetch last 30 × 1-min candles via broker historical API
2. Compute Donchian channel (N-bar high/low), RSI(14), volume spike ratio
3. Signal when: close breaks above/below channel + RSI confirms + volume spike (indices get volume-free compensation since they report volume=0)
4. Confidence scoring: base 50–55 + volume spike (+15) + RSI confirm (+10) + breakout strength (+10/+5) + momentum bars (+10), capped at 95
5. If confidence ≥ `SCALP_MIN_CONFIDENCE` → build ATM option ticket with fixed-point SL/T1
6. Auto-entry via `order_executor.try_scalp_entry()` (paper or live depending on `LIVE_TRADING_ENABLED`)

**Tick-driven SL/T1 (no REST latency):**
- At entry: broker WS subscribes to the option's scrip code (`opt_code`) for live ticks
- `_on_option_tick` fires on each tick — instant SL/T1 check, no polling
- Cache keyed by **scrip code** (not trading symbol) — different expiry contracts on same strike never share a cache slot
- On exit: WS subscription unregistered so dead contract ticks stop immediately

**Let winners run (`SCALP_LET_WINNERS_RUN`, default on):**
- At T1, the position does **not** hard-exit. Instead the SL is floored to breakeven+brokerage and the graduated trailing stop rides the position toward T2 and beyond.
- Exit becomes the **trailing SL** or **T2** — so a strong move is captured in full instead of being capped at the first target. Downside is locked at T1-level.
- Set `SCALP_LET_WINNERS_RUN=false` to restore the classic hard T1 exit.

**Risk controls:**
| Control | Detail |
|---|---|
| **Daily loss kill-switch** | Blocks all scalp entries when daily P&L ≤ `-SCALP_DAILY_LOSS_LIMIT` (default ₹1500) |
| **Max re-entries** | `SCALP_MAX_REENTRIES` per symbol per day (default 10) |
| **Max hold timer** | Auto-exit after `SCALP_MAX_HOLD_MIN` minutes (default 10) |
| **ATR-based SL/T1** | `SCALP_USE_ATR_SL` (default on): SL = ATR × 1.5, T1 = ATR × 2.0; SL clamped to 3–20 pts (NIFTY) / 3–30 pts (SENSEX). With it off, fixed points are used: `SCALP_SL_PTS` / `SCALP_T1_PTS` (8 / 15; SENSEX 15 / 25) |
| **Budget fit** | Each lot's risk (SL pts × lot size + ₹70 costs) must fit in the remaining daily-loss headroom, otherwise the entry is skipped and logged as `SCALP BUDGET BLOCK` |
| **30s grace period** | Adverse-move and thesis-flip exits skip first 30s — prevents stale-price instant exits on new entries |

**UI (Scalp Mode tab):**
- 3-pane chart grid: NIFTY 50, SENSEX, and the active scalp ticker (5-min candles via `HomeChart`)
- All live via SSE — no polling. Live prices from `liveSpots` store, scanner state every 10s
- Momentum signal cards (click to focus 3rd chart pane)
- Active position cards with hold-time progress bar
- Live trade feed showing entries, exits, paper fills from order event SSE

**SSE events:** `scalp_state` (initial + every 10s) → `scalp_scan_start` → `scalp_signal` → `scalp_scan_complete`

### Position Tracker & Auto-Execution

Positions are tracked server-side at `/api/trade/tracked` — created either manually ("I entered" button) or automatically by `order_executor`. The frontend reprices on every spot tick (Black-Scholes), and a server-side watcher (`tracked_monitor`) fires SSE alerts + desktop notifications even when your browser tab is hidden:

| Trigger | Condition |
|---|---|
| **SL hit** | option premium ≤ SL level (strict points-based: 15pts NIFTY, 50pts SENSEX) |
| **NEAR SL** | premium within 10% of SL (3% if SL trailed above entry — avoids profit-position spam) |
| **T1 hit** | premium reaches target 1 (DTE-scaled, capped realistically) |
| **T2 hit** | premium reaches target 2 |
| **Theta zone** | CE/PE after 13:00 IST on expiry day |
| **Time exit** | 15:00 IST (warn before broker auto-square-off at 15:20) |

**Trailing SL**: After premium rises 20%+ above entry, SL ratchets upward to `high_water - max_sl_points` (only goes up, never down).

**DTE-scaled target caps**: Targets are capped based on days-to-expiry to prevent unrealistic T1/T2 values on short-DTE options (e.g., 1DTE SENSEX: T1 ≤ 1.57× entry, T2 ≤ 2.44×).

### Order Executor (`order_executor.py`)

All risk controls live here, **identical for paper and live** (only the capital source and the final order send differ): daily-loss kill switch, max open positions, margin and daily-loss budget per lot, stale-ticket check, re-entry cooldown, per-direction stop-loss limit, and a shared claim so Swing and Scalp never buy the same contract.

Full auto-entry and auto-exit engine, gated by the **Auto/Monitor toggle** in the UI:

| Feature | Detail |
|---|---|
| **Auto-entry** | Places BUY when scanner confidence ≥ `AUTO_ENTRY_MIN_CONFIDENCE` (default 70%) |
| **Auto-exit** | Fires SELL on SL hit, T1, T2, time exit, or user force-exit |
| **Paper mode** | `LIVE_TRADING_ENABLED=false` — no real money, but orders behave like real market orders: they only fill while the market is open, BUY fills at the live ask and SELL at the live bid, and a ticket more than `FO_MAX_TICKET_DRIFT_PCT` (10%) off the live premium is rejected as stale |
| **Re-entry cooldown** | After any swing exit, no new entry on the same index + direction for `FO_REENTRY_COOLDOWN_SEC` (default 900 s) — stops buying straight back in at a fresh high |
| **Live mode** | `LIVE_TRADING_ENABLED=true` — real orders via the active broker (`config/brokers.yaml`) |
| **Monitor mode** | `AUTO_TRADING_ENABLED=false` — scanner runs, signals display, zero orders |
| **Scalp daily loss limit** | Kill-switch at `-SCALP_DAILY_LOSS_LIMIT` — blocks all scalp entries when breached |
| **Re-entry control** | Configurable re-entry after SL exit (`ALLOW_REENTRY`, `MAX_REENTRIES`) |
| **Slippage guard** | Skips entry if price moved >5% from signal time |
| **Capital gate** | `FO_MAX_RISK_PCT` of capital (paper or live) — blocks if max_loss exceeds limit |
| **AVOID window** | Warns (logs) when planner flags late-session / high-theta entries |
| **Realized P&L** | Recorded per exit, broadcast via SSE, displayed in daily P&L strip |

---

## Architecture

```
frontend/                         Vue 3 + Vite (port 53847)
  src/
    views/Home.vue                main terminal shell
    api/                          axios client (index.js) + per-segment API modules in api/market/
                                  (broker, scanner, scalp, btst, crypto, forex, poly, pnl, options, ...)
    components/
      chart/                      HomeChart, ChartGrid (NIFTY / SENSEX / 3rd pane), Crypto/Forex/Poly charts
      panels/                     FoScannerPanel, ScalpPanel, BtstPanel, LiveTradingPanel, CryptoPanel,
                                  ForexPanel, PolyPanel, IntelFeedPanel, ...
      layout/AppLayout.vue
      ui/SnackBar.vue            toast notifications for order updates + alerts
    stores/                       Pinia: useMarketStore, useFoScannerStore, useLiveTradingStore,
                                  useCryptoStore, usePolyStore
    composables/                  useSSE, useTrackedAlerts
    utils/blackScholes.js         pure-JS Black-Scholes engine — premium + Greeks (Δ Γ Θ vega)
                                  + IV solver. Reprices option chain & ticket cards on every
                                  spot tick without round-tripping to backend.
    utils/                        snack.js (toast bus), notifSound.js (desktop alerts), formatters.js

backend/                          FastAPI on Hypercorn (HTTPS + HTTP/2 + HTTP/3, port 47291)
  app/
    api/                          broker, market, trade (F&O, scalp, BTST, tracked, executor), crypto, forex, poly
    engines/                      fo_scanner (swing), scalp_scanner, btst_scanner, order_executor,
                                  forex_scanner, crypto_fo_scanner, monitor/ (tracked_monitor, position_monitor)
    domain/
      services/                   option_planner, greeks, brokerage_calc, pnl_manager, ...
      strategies/, analytics/, regime/, audit/, ...
    application/                  event bus
    infrastructure/
      broker/                     base.py (BrokerAdapter ABC + BrokerFactory, YAML-driven, zero if/else),
                                  indmoney_broker, dhan_broker, groww_broker
      db/                         state_store (SQLite P&L ledger), pnl_store, tracked_positions
      exchange/, market_data/, notifications/
      llm/client.py               optional LLM client — used only by the Polymarket tab
    monitoring/                   health_monitor, latency_tracker
    shared/                       time.py (IST helpers, market hours, holidays), indicators, ta_utils, logger
    dependencies.py               FastAPI DI — get_broker(), get_event_bus(), get_scheduler()
    config.py                     Pydantic Settings (.env driven)
  config/                         brokers.yaml (active_broker, env_keys, defaults), strategies.yaml,
                                  exchanges.yaml
  tests/                          pytest suite (`cd backend && uv run pytest`)
.githooks/pre-push                runs backend tests + frontend lint before every push
```

### Key Patterns

| Pattern | Used for |
|---|---|
| **Broker-first price chain** | All price lookups: WS tick cache → REST quote → yfinance — zero 15-min delay |
| **5-min broker candles** | Scanner + analysis: 75 bars = full intraday day, not daily bars |
| **SSE streaming** | Scanner events, position alerts, daily P&L, order updates |
| **Broker-agnostic adapter** | `BrokerAdapter` ABC + `BrokerFactory` — switch brokers via YAML, zero code changes |
| **Scrip-code keyed WS cache** | `_option_ltp_cache` keyed by opt_code not trading_symbol — different expiry contracts never share a cache slot |
| **Tick-driven scalp SL** | `_on_option_tick` fires instantly on WS tick — no REST polling latency in SL path |
| **WS subscription lifecycle** | Subscribe on scalp entry (opt_code), unregister on exit — dead contract ticks stop immediately |
| **Pure-Python TA** | Supertrend + ADX + patterns — zero talib/pandas-ta deps |
| **Pre-computed patterns** | detect_patterns() runs on last 5 bars → labeled strings with an F&O action hint |
| **Index handling** | `^NSEI`/`^NSEBANK` skip all yfinance fundamentals — pure technical analysis |
| **Auto/Monitor toggle** | Runtime switch via API + UI toggle — no restart needed |
| **Trailing SL** | Ratchets upward after 20%+ premium gain, never ratchets down |
| **DTE-scaled caps** | Target prices capped by days-to-expiry — prevents absurd T1/T2 on 1DTE |
| **Kill-switch** | Scalp daily loss limit — blocks all scalp entries when breached |
| **Desktop alerts** | Browser Notification API + audio for SL/T1/T2 — works even when tab is hidden |

### F&O Signal & Execution Flow

```
FOScanner thread (every cycle)
  └─ _scan_one(ticker)
       └─ _fetch_market_data(ticker)
            ├─ Broker 5-min candles (last 7 days, 75+ bars)  ← PRIMARY
            ├─ Broker daily candles (200 days fallback)
            └─ yfinance fundamentals (stocks only, indices skipped)
       └─ compute indicators (shared/indicators.py, shared/ta_utils.py)
            └─ Supertrend + ADX + RSI + EMA + MACD + BB + patterns → technicals dict
       └─ Technical prefilter → confidence score + direction
       └─ option_planner.plan_option_trade()  ← BS-derived SL/T1/T2 + Greeks
       └─ broadcast scan_signal SSE event with full ticket
       └─ [Auto mode] order_executor.try_auto_entry(signal)
            ├─ check: AUTO_TRADING_ENABLED, kill_switch, confidence ≥ threshold
            ├─ check: not blocked, re-entry limit, slippage guard, capital gate
            ├─ place BUY (paper or live) → add to tracked_positions
            └─ broadcast order_update SSE + snackbar notification
  ⇒ [Monitor mode] user reads signal in UI, executes manually at broker

ScalpScanner thread (every 60s)
  └─ _scan_scalp(ticker)
       └─ fetch 1-min broker candles → Donchian + RSI + volume spike detection
       └─ confidence score → build ATM ticket → try_scalp_entry()
            ├─ register_tick_callback(opt_code, _opt_cb)  ← WS subscribe
            └─ _on_option_tick fires on each WS tick:
                 ├─ _option_ltp_cache[opt_code] = ltp
                 ├─ match position by opt_code (not trading_symbol)
                 └─ instant SL/T1 check → try_auto_exit if hit
  └─ on exit: unregister_tick_callback(opt_code)  ← WS unsubscribe

tracked_monitor (1s poll loop)
  └─ for each tracked position:
       ├─ reprice via spot tick or Black-Scholes fallback
       ├─ trail SL upward if premium +20% above entry
       ├─ cap T1/T2 by DTE sanity limits
       ├─ classify: safe / near_sl / sl_hit / near_t1 / past_t1 / past_t2
       └─ on status change → SSE alert + desktop notification + sound
  └─ [Auto mode] order_executor.try_auto_exit() on SL/T2/time triggers
  └─ broadcast daily_pnl every cycle

User clicks "I entered" on a LIVE ticket card
  └─ POST /api/trade/tracked  ← pin position
  └─ tracked_monitor watches it server-side (same flow as auto-entered)
```

### Request flow

```
                ┌──────────────────────────────────────────────┐
                │  Browser tab (Vue 3 SPA + EventSources)      │
                │  http://localhost:53847                      │
                └───────┬──────────────────────┬───────────────┘
                        │                      │
              static assets                  /api/* (SSE + XHR)
              (HMR, Vue, JS, CSS)            HTTPS · HTTP/2 (HTTP/3 capable)
                        │                      │
                        ▼                      ▼
       ┌────────────────────────┐  ┌───────────────────────────────────┐
       │  Vite dev server       │  │  Hypercorn                        │
       │  :53847 (HTTP/1.1)     │  │  :47291 (TLS, HTTP/2 + HTTP/3)    │
       │  ─ HMR / asset reload  │  │  ─ certs/cert.pem + key.pem       │
       │  ─ no proxy            │  │  ─ keep-alive 600s for SSE        │
       └────────────────────────┘  │  ─ serves the FastAPI app (ASGI)  │
                                   └───────────────┬───────────────────┘
                                                   │
                                                   ▼
                                 ┌─────────────────────────────────┐
                                 │  FastAPI app                    │
                                 │  ─ async routes + SSE streams   │
                                 │  ─ BrokerFactory → active broker│
                                 │  ─ domain-driven architecture   │
                                 └────────┬────────────────────────┘
                                          │
                                          ▼
                       ┌──────────────────────────────────┐
                       │  Background services             │
                       │  ─ Scanners (swing, scalp, BTST) │
                       │  ─ Scheduler (daily resets)      │
                       │  ─ EventBus (pub/sub)            │
                       └──────┬───────────────────────────┘
                              │
                              ▼
              ┌──────────────────────────────┐
              │  External services           │
              │  ─ Broker WS+REST (via       │
              │    BrokerAdapter)             │
              │  ─ yfinance (fallback)       │
              │  ─ News feeds (MC RSS, ET)   │
              └──────────────────────────────┘
```

### Why Hypercorn (HTTP/2) directly

The browser caps **6 simultaneous connections per origin** over HTTP/1.1. Vega keeps ~7 long-lived SSE streams open (scanner feed, position monitor, live-feed, alerts, plus per-underlying tick streams). Without HTTP/2 the 6-cap exhausts every available socket, leaving zero capacity for normal XHRs (`/api/market/ohlcv`, `/api/market/search`, `POST /api/trade/tracked`) — they queue indefinitely and the UI appears frozen.

Hypercorn terminates **TLS + HTTP/2 (and HTTP/3 over QUIC)** on `:47291` and multiplexes every stream over a single connection, removing the 6-cap entirely. There is no nginx or other proxy in front — the FastAPI app runs on Hypercorn itself (`backend/hypercorn.toml`).

### Process layout under `start.sh`

```
start.sh (parent shell, traps SIGINT; records its PID in .vega.pid)
├── caffeinate -dis                     ←  keeps the Mac awake while running
├── hypercorn app.main:app              ←  $BE_PID  (TLS + HTTP/2 + HTTP/3 on :47291)
│   ├── Scheduler (daily resets)
│   ├── EventBus (pub/sub)
│   └── BrokerAdapter (via BrokerFactory)
└── npm run dev (Vite)                  ←  $FE_PID
    └── esbuild + HMR worker
```

`Ctrl-C` → trap fires → all children + their descendants killed. Starting `start.sh` again first stops any previous run recorded in `.vega.pid`, then frees ports 47291 / 53847.

### Frontend SSE management

| Concern | How it's handled |
|---|---|
| **Duplicate streams across HMR reloads** | `EventSource` registries pinned to `window.__vega*`; on every `<script setup>` re-eval the previous instance's streams are explicitly closed before a fresh registry replaces them |
| **Per-underlying tick streams** | Opened only for tracked WATCHING positions and the currently charted ticker — non-watched underlyings rely on `/api/trade/live-feed` for ticks (no duplicate work) |
| **DOM reuse bugs in scanner feed** | `:key="ticker+type+timestamp"` on `v-for` so foFeed mutations don't reuse stale bindings on the Watch button |
| **Self-signed cert acceptance** | One-time `Advanced → Proceed` per browser at `https://localhost:47291`, persisted indefinitely |

---

## Math Reference

All formulas used by Vega for option pricing, Greeks, P&L, position sizing, and signal scoring. Mirrors `backend/app/domain/services/greeks.py` (Python) and `frontend/src/utils/blackScholes.js` (JS) — both implementations are unit-verified to match SciPy ground truth within rounding tolerance (< ₹0.01 on premium, < 1e-5 on Greeks).

### Black-Scholes option pricing

For a European option on a non-dividend-paying underlying:

```
                  ln(S/K) + (r + σ²/2)·T
       d₁  =  ─────────────────────────────
                       σ·√T

       d₂  =  d₁ − σ·√T

   CE price =  S·N(d₁)  −  K·e^(−rT)·N(d₂)
   PE price =  K·e^(−rT)·N(−d₂)  −  S·N(−d₁)
```

| Symbol | Meaning | Source / convention |
|---|---|---|
| `S` | Spot price of underlying | Live broker LTP via SSE |
| `K` | Strike price | Contract spec |
| `T` | Time to expiry, **in years** | `days_to_expiry / 365` (calendar days, not trading) |
| `σ` | Implied volatility, decimal | Solved via bisection on market price; default 14% (indices) / 30% (stocks) if no market data |
| `r` | Risk-free rate, decimal | `0.07` (RBI repo proxy) — overridable via `RISK_FREE_RATE` env |
| `N(x)` | Standard normal CDF | Abramowitz & Stegun 26.2.17 approximation, max error 7.5e-8 |

### Greeks (partial derivatives of price)

```
   Δ_CE  =  N(d₁)                                       (range 0..1)
   Δ_PE  =  N(d₁) − 1                                   (range −1..0)

   Γ     =  φ(d₁) / (S · σ · √T)                        (same for CE & PE)

   ν     =  S · φ(d₁) · √T / 100                        (per +1 vol-point, i.e. +1% IV)

   θ_CE  =  [ −S·φ(d₁)·σ / (2·√T)  −  r·K·e^(−rT)·N(d₂) ] / 365
   θ_PE  =  [ −S·φ(d₁)·σ / (2·√T)  +  r·K·e^(−rT)·N(−d₂) ] / 365
```

`φ(x) = e^(−x²/2) / √(2π)` is the standard normal PDF.

Theta is **divided by 365** so it's reported as ₹/day (calendar-day decay, what a long position loses if everything else holds still).

### Implied volatility (back-solving σ from market price)

We don't directly observe IV — we observe the option's market premium, then solve for the σ that makes Black-Scholes return that price. Done with bisection on `[0.01, 5.0]` (i.e., 1% – 500% annualised):

```
  σ*  =  argmin |bs_price(S, K, T, r, σ, type) − market_price|
```

Convergence to ±1e-4 in ≤ 80 iterations, < 1ms per call. Returns `None` if `market_price < intrinsic_value` (arbitrage opportunity, indicates stale data).

### Strike selection by target delta (F&O scanner)

The scanner picks a strike with delta close to the configured target (`FO_TARGET_DELTA`, default 0.35; 0.50 is ATM, the "gamma sweet spot"):

```
  best_strike = argmin |Δ(S, K_i, T, σ, type)  −  target_delta|
                  K_i in available strikes (₹50 spaced for NIFTY, ₹100 for BANKNIFTY)
```

Why 0.50: best gamma:theta ratio. Δ=0.50 means the option moves ~₹0.50 per ₹1 spot move while paying minimum theta drag. Higher Δ (deeper ITM) costs more capital; lower Δ (further OTM) suffers theta cliff and rarely catches up.

### Premium SL/T1/T2 back-derivation

The scanner supplies spot-level targets (e.g., `target_1 = 24,200`). The planner converts those to **premium-level** targets so the position monitor can fire on actual P&L, not on underlying levels:

```
  premium_at_spot(S_target)  =  bs_price(S_target, K, T_remaining, r, σ, type)

  target_1_premium  =  premium_at_spot(spot_target_1)
  target_2_premium  =  premium_at_spot(spot_target_2)
  stop_loss_premium =  premium_at_spot(spot_stop_loss)
```

`T_remaining` accounts for theta decay during the expected hold period (rough estimate: 1-3 days). This is why a 2x spot move doesn't always = 2x premium.

### Premium stop-loss bounds

The planner clamps the premium SL so it is never set absurdly deep or absurdly tight:

```
  sl_premium = max( entry × floor ,  min( sl_from_spot_stop ,  entry × (1 − min_loss) ) )

     DTE > 2 :  floor = 0.50 (SL never below 50% of entry),  min_loss = 15%
     DTE ≤ 2 :  floor = 0.30,                                min_loss = 10%
```

On top of that, `order_executor.enforce_strict_sl()` clamps the SL to at most `SL_MAX_POINTS_NIFTY` (15) / `SL_MAX_POINTS_SENSEX` (50) premium points below entry. Together they cap max loss per lot regardless of underlying behaviour.

### P&L formulas

For a buy-side option position (long CE or long PE):

```
  unrealised_pnl  =  (premium_now − entry_premium) × qty × lot_size
  realised_pnl    =  (exit_premium  − entry_premium) × qty × lot_size  −  fees

  fees per round trip ≈ exchange 0.03553% + SEBI + stamp 0.003% (buy) + GST 18%, plus
                        STT 0.15% of premium on the sell side (from 1 Apr 2026) + broker flat fee
  (see domain/services/brokerage_calc.py)
```

At expiry (intrinsic value only):

```
  CE payoff  =  max(0, S_expiry − K) × qty × lot_size  −  entry_premium × qty × lot_size
  PE payoff  =  max(0, K − S_expiry) × qty × lot_size  −  entry_premium × qty × lot_size
```

### Breakeven spot

Spot level at which P&L = 0 at expiry, ignoring time decay before then:

```
  CE:  S_breakeven  =  K + entry_premium
  PE:  S_breakeven  =  K − entry_premium
```

### Position sizing (`order_executor.try_auto_entry`)

Lots are the **minimum of three limits** — and the entry is skipped if even one lot does not fit:

```
  lots_by_margin  =  floor(available_cash / (premium × lot_size))
  lots_by_budget  =  floor(headroom / (SL_pts × lot_size + 70))     headroom = daily_loss_limit + today's P&L
  num_lots        =  min(FO_MAX_LOTS_PER_TRADE (default 1), lots_by_margin, lots_by_budget)
```

`SL_pts` is `SL_MAX_POINTS_NIFTY` / `SL_MAX_POINTS_SENSEX`; the `+70` is a flat cost estimate. Scalp uses the same idea with `SCALP_DAILY_LOSS_LIMIT` as the budget.

### IST trading-hours math (used for scan-loop gating)

```
  IST_minutes(now)  =  hour × 60 + minute  (in Asia/Kolkata)
  market_open      =  IST_minutes ≥ 555  AND  IST_minutes ≤ 930
                       (i.e. 09:15 ≤ now ≤ 15:30)
                     AND  weekday ∈ Mon..Fri
                     AND  date ∉ NSE_HOLIDAYS
```

Pre-warm runs at `IST_minutes == 550` (09:10) — caches F&O master, primes spot LTP — so the first real scan at 09:15:00 fires sub-second.

### Verification

Quick sanity check against SciPy (which both `greeks.py` and `blackScholes.js` track within rounding tolerance):

```bash
cd backend
.venv/bin/python -c "
from app.domain.services.greeks import greeks
from scipy.stats import norm; import math

S, K, dte, iv = 24093, 24100, 6, 0.16
g = greeks(spot=S, strike=K, days_to_expiry=dte, iv=iv, opt_type='CE')
print('Our impl :', g)

r, T = 0.07, dte/365
d1 = (math.log(S/K) + (r + 0.5*iv*iv)*T) / (iv*math.sqrt(T))
d2 = d1 - iv*math.sqrt(T)
truth = S*norm.cdf(d1) - K*math.exp(-r*T)*norm.cdf(d2)
print('SciPy ref:  premium=%.4f  delta=%.4f' % (truth, norm.cdf(d1)))
# Expected match within ₹0.01 on premium, 1e-5 on delta.
"
```

Expected output:

```
Our impl : GreeksResult(premium_bs=207.62, delta=0.5208, gamma=0.000806,
            theta_per_day=-18.7755, vega_per_volpt=12.3066, iv_used=0.16, ...)
SciPy ref:  premium=207.6170  delta=0.5208
```

If those numbers drift by more than ₹0.05 on premium or 0.001 on delta, the math has a bug.

---

## Quick Start

### Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Node.js | 18+ | |
| Python | 3.12+ | |
| uv | latest | Python package manager |
| openssl | any | For self-signed cert (already on macOS/Linux) |

### Setup & Start

**Step 1 — Configure API keys**

```bash
cp .env.example .env
# Edit .env — set your broker token (e.g. INDMONEY_ACCESS_TOKEN) for live prices.
# LLM keys are optional (only the Polymarket tab uses an LLM).
```

**Step 2 — Start**

```bash
./start.sh
```

`start.sh` auto-installs Python+JS deps on first run, generates the self-signed cert if missing, and brings up two servers:
- **Frontend (Vite)** → http://localhost:53847
- **Backend (FastAPI on Hypercorn, HTTPS + HTTP/2 + HTTP/3)** → https://localhost:47291  *(API endpoint)*

**Step 3 — Trust the self-signed cert (one-time)**

Open `https://localhost:47291/api/market/world-indices` in your browser → click **Advanced → Proceed to localhost (unsafe)**. Browser remembers the exception indefinitely. The cert lives at `certs/cert.pem`, signed for `localhost` and `127.0.0.1` only.

```bash
./start.sh             # both (default)
./start.sh --backend   # Hypercorn + FastAPI only (no Vite)
./start.sh --frontend  # Vite only
```

### Environment Variables

**LLM (optional).** The Swing, Scalp and BTST scanners are pure technical and never call an LLM; only the Polymarket tab does. Defaults live in `backend/app/config.py` (`LLM_PROVIDER` defaults to `gemini`). Two modes — pick one:

**Option A — OpenAI-compatible API** (`LLM_PROVIDER=openai` or `gemini`)

```env
LLM_PROVIDER=openai          # or gemini (default), ollama
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.anthropic.com/v1
LLM_MODEL_NAME=claude-sonnet-4-6
```

| Provider | `LLM_BASE_URL` | Model example |
|----------|----------------|---------------|
| Anthropic Claude | `https://api.anthropic.com/v1` | `claude-sonnet-4-6` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| Groq (free) | `https://api.groq.com/openai/v1` | `llama3-70b-8192` |
| Alibaba Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Ollama (local) | `http://localhost:11434/v1` | `llama3.1` |

**Option B — AWS Bedrock** (`LLM_PROVIDER=bedrock`, no proxy needed)

```env
LLM_PROVIDER=bedrock
AWS_ACCESS_KEY_ID=your_key_id
AWS_SECRET_ACCESS_KEY=your_secret
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6-20251001-v1:0
```

litellm calls Bedrock directly — no separate proxy process required. Enable the model in **AWS Console → Bedrock → Model access** first.

**Broker (live prices + orders):**

Set the active broker in `backend/config/brokers.yaml` and provide its env vars:

```env
# INDmoney
INDMONEY_ACCESS_TOKEN=your_token   # INDstocks.com → API section

# Dhan (alternative)
DHAN_CLIENT_ID=your_client_id
DHAN_ACCESS_TOKEN=your_token

# Groww (alternative) — dashboard → account settings → Trading APIs → Generate API keys
GROWW_API_KEY=your_api_key         # + secret: auto-refreshes token, no daily manual step
GROWW_API_SECRET=your_api_secret
GROWW_ACCESS_TOKEN=your_token      # OR a static daily token instead of key+secret
```

**Access control and role** — see [Security & operations](#security--operations) below.

```env
VEGA_API_KEY=<name>:<secret>,<name>:<secret>   # one entry per person; secrets are 32+ characters
VEGA_ROLE=server                                # server = runs scanners/executor/API; consumer = UI only, never trades
VEGA_SERVER_URL=                                # consumer only: https://your-server:53847
```

**Trading execution** (defaults are in `backend/app/config.py`; override via `.env`):

```env
AUTO_TRADING_ENABLED=true          # true = auto-entry on signals; false = monitor-only
LIVE_TRADING_ENABLED=false         # false = paper trading; true = real orders
SWING_CAPITAL_INR=6500             # Swing capital (SCALP_CAPITAL_INR for scalp)
DAILY_LOSS_LIMIT_INR=1300          # Swing kill-switch: block entries after ₹X daily loss
AUTO_ENTRY_MIN_CONFIDENCE=70       # Minimum scanner confidence to trigger auto-entry
FO_MAX_RISK_PCT=20                 # Max % of capital risked per trade
```

### Docker

```bash
cp .env.example .env
docker compose up -d
```

Exposes port `47291` (Hypercorn, HTTPS + HTTP/2).

---

## API Reference

### Market (`/api/market`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ohlcv` | Standalone OHLCV — active broker's candles first, yfinance fallback |
| GET | `/world-indices` | All world index prices (parallel fetch, TTL 10s) |
| GET | `/universe` | Asset universe metadata |
| GET | `/scan` | Smart scanner — ranked BUY/SELL signals |
| GET | `/signals/<ticker>` | Multi-source news + Reddit + FOMO |
| GET | `/search` | Ticker symbol/name search |
| GET | `/ai-predict/<ticker>` | Placeholder — returns `neutral` with 0 confidence |
| GET | `/fundamentals/<ticker>` | yfinance fundamentals (stocks only) |
| GET | `/mc-news` | Moneycontrol RSS news feed |

### Trade (`/api/trade`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/intraday-signal/<ticker>` | Fast technical signal — all ta_utils indicators, no LLM |
| GET | `/indicators/<ticker>` | RSI, MACD, Bollinger, EMA |
| GET | `/levels/<ticker>` | Entry, stop loss, T1/T2/T3; S/R via Standard Floor Pivot Points (PP, R1–R3, S1–S3) from previous session H/L/C |
| GET | `/vbt-backtest/<ticker>` | vectorbt multi-strategy backtest |
| POST | `/fo-scanner/start` | Start F&O signal scanner background thread |
| POST | `/fo-scanner/stop` | Stop F&O signal scanner |
| POST | `/fo-scanner/trigger` | Force immediate scan cycle |
| GET | `/fo-scanner/status` | Scanner state — running, last scan, signals |
| GET | `/fo-scanner/stream` | SSE — live scan events (scan_signal, scan_complete) |
| POST | `/scalp-scanner/start` | Start scalp scanner background thread |
| POST | `/scalp-scanner/stop` | Stop scalp scanner |
| POST | `/scalp-scanner/trigger` | Force immediate scalp scan cycle |
| GET | `/scalp-scanner/status` | Scalp scanner state |
| GET | `/scalp-scanner/stats` | Daily P&L, kill-switch state, win rate |
| GET | `/scalp-scanner/stream` | SSE — live scalp events |
| GET | `/scalp-scanner/config` | Get scalp config (SL pts, T1 pts, daily limit, etc.) |
| PUT | `/scalp-scanner/config` | Update scalp config at runtime |
| POST | `/scalp-scanner/reset-daily` | Reset daily P&L counter |
| POST | `/scalp-scanner/reset-killswitch` | Re-arm kill-switch after daily limit breach |
| GET | `/tracked` | List tracked positions |
| POST | `/tracked` | Pin a position (when user clicks "I entered" on a ticket) |
| DELETE | `/tracked/<id>` | Unpin a position |
| GET | `/tracked/alerts/stream` | SSE — server-side SL/T1/T2/theta/time-exit alerts |
| GET | `/tracked/alerts/state` | Latest alert state per pinned position |
| GET | `/executor/status` | Executor state — auto_trading_enabled, kill_switch, daily P&L, ledger |
| POST | `/executor/auto-trading` | Toggle auto-trading on/off at runtime `{"enabled": bool}` |
| POST | `/executor/scalp-auto-trading` | Toggle scalp auto-trading on/off `{"enabled": bool}` |
| POST | `/executor/force-exit/<id>` | Force exit a specific tracked position |
| POST | `/executor/block-entry` | Manually block all new entries |
| POST | `/executor/unblock-entry` | Unblock entries |
| POST | `/executor/reset-killswitch` | Re-arm swing kill-switch |
| POST | `/executor/reset-daily` | Reset swing daily P&L counter |
| GET | `/pnl/summary` | Daily P&L summary (swing + scalp) |
| GET | `/pnl/trades` | Individual trade P&L records |
| GET/POST | `/option-plan` | Build a single executable option ticket — delta-targeted strike, Greeks, BS premium SL/T1/T2 |
| GET | `/option-chain` | ATM ± N strikes with broker contract metadata for browser BS-repricing |
| GET | `/btst-scanner/status`, `/btst-scanner/stats` | BTST scanner state and stats (also `/config`, `/start`, … — see `backend/app/api/trade.py`) |

Other routers: `/api/broker` (status, quote, positions, orders), `/api/crypto`, `/api/forex`, `/api/poly` — see `backend/app/api/*.py`.

---

## Market Hours & F&O Trading Windows

| Window | Time (IST) | Purpose |
|---|---|---|
| Market open | 09:15 | NSE market open |
| Safe trading open | 09:15 | Scanner emits signals from market open |
| Theta danger zone | 13:00 (expiry day) | tracked_monitor fires `theta_zone` alert on pinned CE/PE positions on their expiry date |
| Time-exit alert | 15:00 | tracked_monitor fires `time_exit` alert — 20 min before broker auto-square-off |
| Market close | 15:30 | NSE close |
| Broker auto-square-off | ~15:20 | MIS auto-square-off (time varies by broker) — exit by 15:00 to avoid this |

**Weekly expiries** come from the broker's instrument master (currently NIFTY on Tuesday, SENSEX on Thursday). Pinned positions get a theta-zone alert on their expiry day at 13:00; there is no automatic block on buying near expiry.

---

## Buy-Quality Tags (Option Chain UI)

Every row in the LIVE option chain is auto-tagged based on the option's |Δ| so you can pick a strike that matches your risk profile at a glance:

| Tag | \|Δ\| range | Profile | When to pick |
|---|---|---|---|
| **CONSERVATIVE** 🔵 | 0.65 – 0.85 | Deep ITM — premium ≈ intrinsic, low theta, expensive | High conviction, want low leverage |
| **BALANCED** 🟢 | 0.40 – 0.65 | ATM — best gamma:theta tradeoff | Default sweet spot — AI usually picks here |
| **AGGRESSIVE** 🟡 | 0.25 – 0.40 | Slightly OTM — cheap, high leverage | Tight time horizon + high conviction |
| **LOTTERY** 🔴 | 0.10 – 0.25 | Deep OTM — theta destroys it fast | Event-day plays only (budget, results) |
| **EXPENSIVE** 🟣 | > 0.85 | Very deep ITM, behaves like the underlying | Avoid — buy futures instead, better margin |

Each row also displays **cost per lot** (`premium × lot_size`) and **breakeven spot** so capital and risk are visible without math.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ERR_CERT_AUTHORITY_INVALID` on `/api/*` requests | Self-signed cert not yet trusted by this browser | Visit `https://localhost:47291/api/market/world-indices` once → Advanced → Proceed |
| All `/api/*` requests stuck `(pending)` or spinners never finish | Backend not running, or the self-signed cert isn't trusted in this browser | `lsof -nP -iTCP:47291` should show Python LISTEN; if not, restart `./start.sh`. Then open `https://localhost:47291/health` once and accept the cert |
| `Address already in use` on 47291 or 53847 | A previous run is still alive | Re-run `./start.sh` — it stops the previous run (via `.vega.pid`) and frees both ports |
| Multiple SSE rows pinging the same URL in DevTools | HMR orphaned old streams | Hard-refresh (`Cmd+Shift+R`); window-pinned cleanup will close stale ones automatically next save |
| Watch button click does nothing | Vite saved a file mid-click and HMR aborted the in-flight POST | Don't edit code while clicking; the click handler shows an alert if the symbol is already tracked / in-flight |
| Backend reloads constantly on file save | A `--reload` flag was passed to Hypercorn | Don't use `--reload` with SSE — the streams are killed on every reload |
| Scalp SL doesn't fire | WS option tick subscription failed at entry | Check logs for `Option WS subscribed:` — if missing, `opt_code` resolution failed; REST fallback is off by design |
| `ModuleNotFoundError: No module named 'app'` | Running python directly instead of via uv | Run from `backend/` with `uv run hypercorn app.main:app` or activate the venv first |
| `429 too_many_attempts` | 5 wrong keys from this address — locked out for 15 min | Wait, then use the correct key (nothing from a locked address is evaluated) |
| `503 server_has_no_api_key_configured` | The server has no strong key (32+ chars) | Run `python3 backend/app/auth/keygen.py ensure` and restart |
| Everything returns `401 unauthorized` / UI shows no data | The server has `VEGA_API_KEY` set and this client isn't sending it | Put the same key in this machine's `.env` and restart `start.sh`, or run `localStorage.setItem('vega_api_key','…')` in the browser console |
| `Instrument master fno: HTTP 403` / WS handshake 513 | Broker token missing or expired (IndMoney tokens expire daily) | Refresh the token in `.env` and restart |

---

## Development

```bash
cd backend && uv run pytest            # backend tests
cd frontend && npm run lint            # frontend lint
git config core.hooksPath .githooks    # enable the pre-push gate (tests + lint) in this clone
```

The pre-push hook (`.githooks/pre-push`) runs both checks and blocks the push if either fails. Bypass once with `git push --no-verify`.

---

## Security & operations

### How access works
- **Every route except `/health` needs a key** — header `X-Vega-Key`, or `?key=` on live streams (browsers can't set headers there). `/health` answers a bare `{"status":"ok"}` and reveals nothing else.
- **One key per person**, written as `name:secret` in `VEGA_API_KEY`. The name appears in the audit trail and in server log lines (`rid=… by=alice`); clients send only the secret. Revoke someone by deleting their entry — it takes effect **without a restart**.
- **Weak keys are refused.** A secret must be 32+ characters with real variety. If no strong key exists the server answers `503` to everything (fail closed). `start.sh` creates a strong key named `owner` automatically; add people with `python3 backend/app/auth/keygen.py add <name>` (it prints the secret to give them — once).
- **Brute force is blocked.** 5 wrong keys from one address lock that address out for 15 minutes (`429`), and during the lockout nothing from it is evaluated, so the key cannot be probed. Each person is also limited to 1200 requests/minute. Behind a local tunnel the real client address is read from `CF-Connecting-IP` / `X-Forwarded-For`, but only when the connection itself comes from this machine, so it can't be spoofed from outside.
- **Hardening headers** on every response (`nosniff`, `no-store`, `no-referrer`, HSTS over HTTPS); CORS has **no wildcard** (`VEGA_CORS_EXTRA` adds origins); credentials are never cookies.

### Tracing
- Every request gets an id, returned as `X-Request-ID` (send your own for end-to-end tracing). Server log lines written while serving a request end with `rid=<id> by=<person>`.
- `logs/audit-YYYY-MM-DD.log` is a separate JSON-lines audit trail: logins that failed, lockouts, rate limits, every state-changing call, every log read, and the first call of each person per hour. It never contains a key, a query string or a header. Files older than 90 days are deleted at startup.

### Reading the server's logs from another machine
```bash
python3 scripts/vega_logs.py --url https://your-server:53847 --key <your secret> --lines 100
python3 scripts/vega_logs.py --follow --level WARNING        # live tail
python3 scripts/vega_logs.py --file audit --q auth_failed    # who tried to get in
```
Needs only Python 3 and a checkout of this repo (no packages). Add `--insecure` only while the certificate is self-signed. Credentials, tokens and cookies are blanked in every line before it leaves the server, and every read is recorded in the audit trail. The same data is available at `GET /api/logs` (`file`, `date`, `lines`, `level`, `q`, `offset`) and `GET /api/logs/files`.

### What running this over the internet still needs
Code can't supply these — they're setup you must do:
1. **A reachable address.** A home laptop usually isn't reachable from outside. Use a tunnel (Tailscale / Cloudflare Tunnel) or host the server on a small VPS.
2. **A real TLS certificate** for a domain you control (the generated one is self-signed: every device shows a warning).
3. **A static IP registered with your broker** before live orders (SEBI rule since April 2026).
4. **Keep it running:** run `./start.sh` under a supervisor that restarts it (launchd / systemd / Docker `restart: unless-stopped`) and stop the machine sleeping.
5. **Back up `backend/data/`** (positions, P&L ledger, watchlist, signal history).
6. **Rotate a key** by adding a new entry, moving the person over, then deleting the old entry.

---

## Acknowledgments

- Market data: [yfinance](https://github.com/ranaroussi/yfinance) (fundamentals fallback)
- TA indicators inspired by: [tradingview-mcp](https://github.com/atilaahmettaner/tradingview-mcp)
- Indian fundamentals: [Screener.in](https://www.screener.in)
