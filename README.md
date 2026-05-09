# PhoenixTrade

**AI-Powered F&O Intraday Monitoring Terminal**

A professional-grade monitoring terminal combining real-time IndStocks broker WebSocket data, full-stack technical analysis with pure-Python indicators, multi-agent Cerebrum investment research, a two-stage F&O signal scanner, browser-side Black-Scholes option pricing (live chain ladder + per-ticket repricing) — all in a single browser-based interface. **The system places no orders. All trades are entered manually by the user at their broker.**

---

## What it does

PhoenixTrade gives you a complete research-to-decision workflow for F&O intraday trading:

1. **Watch** live candlestick charts — broker WebSocket candles across 6 intervals (5M/30M/1H/1D/1W/1Y), default 5M; Y-axis on left, price-level labels on right; mouse-wheel zoom + reset controls
2. **Scan** the index F&O universe (NIFTY 50, SENSEX, BANKNIFTY) every cycle — two-stage pipeline (pure-Python prefilter + Cerebrum LLM) emits BUY/SELL CE/PE signals when confidence ≥ 70 to the UI; you decide which to act on
3. **Analyse** any ticker with multi-agent Cerebrum pipeline — 5 domain experts (Technical, Fundamental, Macro, Sentiment, Risk) + Bull/Bear debate + CIO final verdict with full F&O instrument selection
4. **Reprice live in the browser** — option chain ladder (ATM ± 5 strikes) and per-position ticket cards re-priced on every spot tick via JS Black-Scholes (delta, theta, vega, premium) — zero server round-trip per tick
5. **Decide which strike** with built-in buy-quality tags — CONSERVATIVE / BALANCED / AGGRESSIVE / LOTTERY / EXPENSIVE — derived from |Δ| so you instantly see whether a strike fits your risk profile
6. **Signal** intraday with pure-Python indicators: Supertrend, ADX, Bollinger Bands, RSI, EMA stack, MACD, VWAP, Donchian — plus candlestick pattern detection (Engulfing, Morning Star, Doji, Hammer, Three Soldiers, etc.); S/R levels derived from Standard Floor Pivot Points (Zerodha/Groww formula)
7. **Track** the trades you take manually — pin entered tickets via "I entered" so the server-side watcher fires SL / T1 / T2 / theta-zone / 15:00 exit alerts via SSE even if your tab is hidden
8. **Backtest** with vectorbt multi-strategy (RSI, EMA cross, Bollinger, MACD)
9. **Stream** live prices via the IndStocks broker WebSocket — tick cache → REST quote → yfinance fallback chain

---

## Feature Reference

### Live Market Data

| Feature | Detail |
|---|---|
| **OHLCV Charts** | Candlestick + volume bars; intervals: 5M (default), 30M, 1H, 1D, 1W, 1Y; Y-axis left, price-line label boxes right |
| **Real-time Price** | Kite WebSocket tick cache → Kite REST quote → yfinance fallback |
| **Intraday Candles** | 5-minute Kite historical candles (last 7 days = 75+ bars per analysis) |
| **Daily Candles** | 200-day Kite historical candles for trend/EMA200 (fallback if 5-min unavailable) |
| **World Indices** | Real-time index grid — NIFTY, SENSEX, S&P 500, Nasdaq, DAX, Nikkei, etc. |
| **Market Status** | Live NSE open/closed badge — 09:15–15:30 IST, holiday-aware |

### Technical Indicators (Pure Python, Zero Dependencies)

All indicators computed by `ta_utils.py` — no talib, no pandas-ta, no external deps. Ported from tradingview-mcp logic and extended.

| Indicator | Detail |
|---|---|
| **Supertrend** | ATR × 3 band-following trend filter — primary trend signal for F&O; direction (1=bullish, -1=bearish) + line level |
| **ADX** | Average Directional Index — ADX > 25 = tradeable trend, < 20 = ranging; +DI / -DI for direction |
| **RSI (14)** | Wilder smoothing — divergence vs price level is key signal |
| **EMA 9/20/50/200** | Stack analysis — price vs EMA20/50/200 for trend bias |
| **MACD** | 12/26/9 — bullish/bearish crossover + histogram for momentum |
| **Bollinger Bands** | 20-period, 2σ — BB rating system: STRONG_BREAKOUT / BUY / NEUTRAL / SELL / STRONG_BREAKDOWN |
| **ATR (14)** | True Range — used for SL sizing (FUT: 2.5×ATR) and position risk |
| **VWAP** | Session VWAP (last 78 × 5-min bars) — above = institutional buy bias |
| **Donchian Channel** | 20-period high/low channel — breakout detection |
| **Volume Ratio** | Last bar volume / 20-bar average — expanding vs contracting |
| **52W High/Low** | Range proximity — within 3% = high reaction probability level |

### Candlestick Pattern Detection

Pre-computed by `ta_utils.detect_patterns()` on last 5 bars — passed to TechnicalAgent as named patterns, not raw numbers.

| Category | Patterns |
|---|---|
| **Single-bar** | Doji, Gravestone Doji, Dragonfly Doji, Bullish/Bearish Marubozu, Hammer, Hanging Man, Shooting Star, Inverted Hammer |
| **Two-bar** | Bullish/Bearish Engulfing (⭐ high-confidence reversal), Bullish/Bearish Harami, Tweezer Top, Tweezer Bottom |
| **Three-bar** | Morning Star (⭐ strong bull reversal — buy CE), Evening Star (⭐ strong bear reversal — buy PE), Three White Soldiers, Three Black Crows, Inside Bar Compression |

Each pattern is labeled with F&O action hint (e.g. `Morning Star (STRONG BULLISH REVERSAL — buy CE/long FUT)`).

### F&O Signal Scanner

Background service that cycles through the F&O universe and emits trade signals to the UI. **Signals only — no orders are placed.** You watch the scanner feed and execute manually at your broker. Two-stage pipeline avoids LLM cost on ranging markets and keeps the LLM as a confirmation layer only.

**Universe (3 indices, F&O-active):**
`^NSEI` (NIFTY 50), `^BSESN` (SENSEX), `^NSEBANK` (BANKNIFTY)

**Per-ticker pipeline:**
1. Fetch 5-min broker candles (last 7 days) + compute all ta_utils indicators
2. **Stage 1 — pure-Python technical prefilter** (instant, no LLM)
   - Tiered confidence based on Supertrend × ADX × RSI × EMA stack:
     - `STRONG BUY/SELL` (conf 82) — ADX > 25 + RSI > 55/<45 + price vs EMA20 confirms
     - `BUY/SELL` (conf 72) — ADX > 20 + RSI > 50/<50
     - soft `BUY/SELL` (conf 62) — ADX 15-20 with directional Supertrend (low-vol regimes)
     - SKIP — ADX < 15 or no Supertrend direction
3. **Stage 2 — Cerebrum LLM confirmation** (only if Stage 1 emitted a signal)
   - 5 agents + Bull/Bear debate + CIO verdict
   - Stage-1 trust override: if Cerebrum waters a strong technical signal down to HOLD, the deterministic Stage-1 verdict wins (LLM is systematically over-cautious)
4. CIO decides: `instrument_type` (CE/PE), `expiry`, `strike_price`, `lot_size`, `estimated_premium`, `confidence_to_trade`
5. Option ticket built via `option_planner.plan_option_trade()` — delta-targeted strike, BS-derived premium SL/T1/T2, full Greeks
6. Signal broadcast to the UI via SSE — `scan_signal` carries the full ticket (verdict, confidence, strike, expiry, SL, T1, T2, Greeks)

**Strike selection — `option_planner.plan_option_trade()`:**
- Default `target_delta = 0.50` (ATM) — best gamma:theta tradeoff (overridable via `FO_TARGET_DELTA`)
- DTE bumped automatically when `dte < 4` to avoid OTM theta-cliff
- Premium SL/T1/T2 are **back-derived from CIO spot targets via Black-Scholes** at the planner — not the old (broken) 2x/3x entry-premium heuristic
- Full Greeks attached to every ticket: Δ, Γ, θ/day, vega, IV

**SSE events streamed to frontend:**
`scan_start` → `scan_progress` → `scan_signal` → `scan_complete`

### Manual Position Tracker

For trades you take manually at your broker, click "I entered" on a LIVE ticket card. The position is pinned server-side at `/api/trade/tracked` and survives browser refreshes. The frontend reprices on every spot tick (Black-Scholes), and a server-side watcher (`tracked_monitor`) fires SSE alerts even when your browser tab is hidden:

| Trigger | Condition |
|---|---|
| **SL hit** | underlying breaches SL (CE: ≤; PE: ≥) or option premium ≤ 50% of entry |
| **T1 hit** | underlying reaches T1 (suggest taking partial, raise SL to breakeven) |
| **T2 hit** | underlying reaches T2 (suggest full exit) |
| **Theta zone** | CE/PE after 13:00 IST on expiry day |
| **Time exit** | 15:00 IST (warn before broker auto-square-off at 15:20) |

The watcher only emits alerts. You exit at your broker. No orders are placed by PhoenixTrade.

### Cerebrum — Multi-Agent Analysis Pipeline

Full investment research pipeline triggered per ticker by the scanner or manually. Every agent result streams to the browser the instant it completes.

#### Agents

| Agent | Domain | Data used |
|---|---|---|
| **TechnicalAnalyst** | `technical` (HIGH priority) | Supertrend, ADX, RSI, EMA stack, MACD, BB rating, VWAP, vol_ratio, ATR, 52W range, pre-computed candlestick patterns, last 75 OHLCV bars |
| **FundamentalAnalyst** | `fundamental` | PE, PB, ROE, D/E, margins, revenue growth, market cap, analyst target |
| **MacroEconomist** | `macro` | India VIX, Nifty/BankNifty direction, sector, beta, global macro |
| **SentimentAnalyst** | `sentiment` | News (MC RSS + ET RSS + yfinance), Reddit posts (upvote-weighted), FOMO score |
| **RiskManager** | `risk` (HIGH priority) | ATR, 52W range, beta, drawdown, circuit limits |
| **EntityAgents** | any domain | Real named executives/holders/analysts extracted from news |

High-priority agents (Risk + Technical) are dispatched first and run at `HIGH=0` queue priority.

#### Pipeline phases

```
phase(start)
→ phase(data_fetch)          ← Kite 5-min candles + daily candles + yfinance fundamentals
→ phase(entity_extraction)   ← LLM extracts real named people from news/holders/analysts
→ phase(dispatch, N agents)  ← all agents in parallel via ThreadPoolExecutor(8)
→ agent × N                  ← each result emitted the moment it completes
→ debate                     ← Bull Advocate + Bear Advocate run in parallel
→ phase(cio_complete)        ← CIO reads all agents + debate → final verdict
→ done
```

#### CIO output (F&O fields)

```
final_verdict        · consensus_score     · bull_count / bear_count
entry_price          · stop_loss           · target_1 / target_2 / target_3
instrument_type      · expiry (DDMMMYY)    · strike_price
lot_size             · estimated_premium   · confidence_to_trade
short_term_action    · short_term_reason   · investment_thesis
position_size_pct    · time_horizon        · key_risks[]
```

### Right Sidebar Layout

Responsive width — `clamp(340px, 24vw, 460px)` — adapts to monitor size. Panels:

1. **SIGNAL & LEVELS** — intraday technical signal + entry/SL/T1/T2 levels, AI Predict button
2. **F&O SCANNER** — pulse dot (running/stopped), ▶ Start / ■ Stop / ⚡ Trigger buttons, live SSE feed with SIGNAL badges per index
3. **⚡ LIVE** — tick-driven, real-time:
   - **Trade ticket cards** — one card per index that produced a tradeable signal, each card re-priced independently on its own broker SSE stream (premium, %change, ladder of SL→now→T1→T2, full Greeks, ITM/OTM status, theta-burn warning). Click "I entered" to pin the position into the manual tracker for ongoing alerts.
   - **📊 OPTION CHAIN** — ladder of ATM ± 5 strikes for the selected underlying (NIFTY 50 / BANKNIFTY / SENSEX). Every CE and PE premium re-priced via JS Black-Scholes on every spot tick. Cells flash green/red on each up/down tick. Each row tagged `CONSERVATIVE / BALANCED / AGGRESSIVE / LOTTERY / EXPENSIVE` based on |Δ| with cost/lot and breakeven shown. AI-picked strike highlighted with ★ and gold ring. Optional `BUY-only` filter hides deep-OTM lottery strikes. Live pulse dot + tick-age counter prove stream health.
   - **Per-position cards** — per-pinned-symbol P&L, premium ladder, decision flash on alert triggers
4. **WATCHING** — pinned manual positions with SL/T1/T2 levels and live alert status
5. **BACKTEST** — collapsed by default, vectorbt multi-strategy results

### Data Sources — Priority Chain

| Data type | Priority 1 | Priority 2 | Priority 3 |
|---|---|---|---|
| **Live price** | IndStocks WebSocket tick cache | IndStocks REST quote | yfinance fast_info |
| **Intraday OHLCV** | IndStocks 5-min historical (last 7 days) | — | yfinance 1-day interval |
| **Daily OHLCV** | IndStocks daily historical (200 days) | — | yfinance 200d |
| **Option premium / Greeks** | **JS Black-Scholes (browser)** — re-prices on every tick from live spot + IV estimate | broker quote (one-shot at chain load to back-solve IV) | — |
| **Fundamentals** | — | — | yfinance info (stocks only — indices skip this) |
| **News** | Moneycontrol RSS + ET RSS | yfinance headlines | — |
| **India VIX** | IndStocks REST quote | — | yfinance ^INDIAVIX |

The **JS Black-Scholes engine** (`frontend/src/utils/blackScholes.js`) computes premium + Greeks client-side from spot ticks, IV estimate (back-solved at chain load), risk-free rate (~6.5%), and DTE. Avoids round-tripping every tick through Flask — repricing 11+ strikes at 1Hz costs ~1 ms total CPU on the user's machine.

**Indices (`^NSEI`, `^NSEBANK`, etc.) skip all yfinance fundamentals/financials/analysts calls** — they have no `quoteSummary` data. Analysis is purely technical.

---

## Architecture

```
frontend/                         Vue 3 + Vite (port 3000)
  src/
    views/Home.vue                main terminal — right sidebar, F&O signal scanner, LIVE panel
                                  with ticket cards + option chain ladder, chart zoom
    api/market.js                 all API calls + SSE stream factories
    utils/blackScholes.js         pure-JS Black-Scholes engine — premium + Greeks (Δ Γ Θ vega)
                                  + IV solver. Used to reprice option chain & ticket cards on
                                  every spot tick without round-tripping through Flask.

backend/                          Flask (port 5001)
  app/
    api/
      market.py                   OHLCV, signals, AI predict, invest-analysis SSE, _fetch_market_data()
      trade.py                    indicators, levels, backtest, manual position tracker
                                  F&O scanner endpoints: /fo-scanner/start|stop|trigger|status|stream
      kite.py                     Kite Connect — KiteTicker WS + REST fallback + _tick_cache
      graph.py                    D3 entity graph
      simulation.py               OASIS simulation runner
      report.py                   simulation report
    services/
      ta_utils.py                 Pure-Python indicators — Supertrend, ADX, ATR, RSI, EMA, MACD,
                                  Bollinger, Donchian, VWAP, BB rating, candlestick pattern detection
      fo_scanner.py               F&O signal scanner — 3 indices, two-stage pipeline
                                  (technical prefilter + Cerebrum confirmation), Stage-1 trust
                                  override, ticket build via option_planner, broadcasts
                                  scan_signal SSE events (no order placement)
      option_planner.py           plan_option_trade() — delta-targeted strike resolution against
                                  broker F&O master, BS-derived premium SL/T1/T2 from spot
                                  targets, ATM IV back-solve, full Greeks on every ticket
      tracked_monitor.py          Manual-position alert watcher — server-side reprice on
                                  spot ticks for pinned positions; SSE alerts for SL / T1 /
                                  T2 / theta zone / 15:00 time exit (alerts only, no orders)
      cerebrum/
        agent.py                  AgentInput / AgentOutput dataclasses, Agent ABC, AgentRegistry
        runner.py                 AnalysisRunner — all phases + SSE emit
        agents/
          technical.py            Supertrend, ADX, RSI, EMA stack, MACD, BB rating, VWAP,
                                  pre-computed patterns, 75-bar OHLCV, SMC/ICT framework prompt
          fundamental.py          PE, PB, ROE, D/E, revenue growth, balance sheet
          macro.py                India VIX, FII flows, interest rates, global macro
          sentiment.py            news tone, Reddit sentiment, FOMO score
          risk.py                 52W range, ATR volatility, circuit limits, drawdown
          entity_agent.py         real named people from news/holders/analysts
          debate.py               Bull vs Bear adversarial debate (parallel)
          cio.py                  CIO — F&O instrument selection, delta-based strike, expiry rules,
                                  safety rules, position sizing
      kernel/
        blackboard.py             shared key-value store — data written once, all agents read snapshot
        budget.py                 token cost ledger per agent/model
        event_bus.py              non-blocking pub/sub
        process_store.py          agent lifecycle (submitted → completed/failed) — disk-persisted
        queue.py                  priority queue (HIGH=0 risk/technical, NORMAL=1 others)
        memory.py                 optional semantic memory via Qdrant (graceful fallback)
    knowledge/
      trading_decision_rules.py   SMC/ICT/Wyckoff system prompt + F&O rules
      trading_knowledge_base.md   reference knowledge base
    utils/
      logger.py                   structured logger
      llm_client.py               LLM abstraction — OpenAI-compatible or AWS Bedrock (litellm, no proxy)
```

### Key Patterns

| Pattern | Used for |
|---|---|
| **Kite-first price chain** | All price lookups: WS tick cache → REST quote → yfinance — zero 15-min delay |
| **5-min Kite candles** | Scanner + analysis: 75 bars = full intraday day, not daily bars |
| **SSE streaming** | Scanner events, position monitor events, invest-analysis, portfolio sim |
| **ThreadPoolExecutor** | All 5+ Cerebrum agents run simultaneously — `as_completed` streams results |
| **Wave dispatch** | Wave 1 = agents parallel, Wave 2 = Bull+Bear parallel, Wave 3 = CIO waits for both |
| **Blackboard pattern** | Market data fetched once → shared snapshot → all agents read, no redundant calls |
| **Priority dispatch** | Risk + Technical at HIGH priority — dispatched first so CIO has signal context early |
| **Pure-Python TA** | ta_utils.py — Supertrend + ADX + patterns — zero talib/pandas-ta deps |
| **Pre-computed patterns** | detect_patterns() runs on last 5 bars → labeled strings in TechnicalAgent prompt |
| **Index handling** | `^NSEI`/`^NSEBANK` skip all yfinance fundamentals — pure technical analysis |
| **Panic recovery** | Every agent wrapped in try/except → fallback AgentOutput instead of pipeline crash |
| **JSON mode** | All LLM calls return structured JSON — no markdown parsing |
| **Budget tracking** | Every llm_client.complete() records prompt + completion tokens per agent |

### F&O Signal Flow (no order placement)

```
FOScanner thread (every cycle)
  └─ _scan_one(ticker)
       └─ _fetch_market_data(ticker)
            ├─ IndStocks 5-min candles (last 7 days, 75+ bars)  ← PRIMARY
            ├─ IndStocks daily candles (200 days fallback)
            └─ yfinance fundamentals (stocks only, indices skipped)
       └─ ta_utils.compute_all(candles)
            └─ Supertrend + ADX + RSI + EMA + MACD + BB + patterns → technicals dict
       └─ AnalysisRunner.run(ticker)
            ├─ 5 agents parallel (Technical gets patterns + 75 OHLCV bars)
            ├─ Bull vs Bear debate
            └─ CIO → instrument_type, strike, expiry, premium, confidence_to_trade
       └─ option_planner.plan_option_trade()  ← BS-derived SL/T1/T2 + Greeks
       └─ broadcast scan_signal SSE event with full ticket
  ⇒ user reads the signal in the UI and places the trade manually at their broker

User clicks "I entered" on a LIVE ticket card
  └─ POST /api/trade/tracked  ← pin position
  └─ tracked_monitor watches it server-side; emits SL/T1/T2/theta/time-exit SSE alerts
       (alerts only — user exits manually at broker)
```

### Investment Analysis Pipeline

```
Browser clicks "Run Analysis" (or scanner triggers internally)
  └─ AnalysisRunner.run(ticker, emit_fn, llm_client, market_data_fn)
       └─ _fetch_market_data(ticker)
            ├─ Kite 5-min candles → ta_utils.compute_all() → technicals + patterns
            └─ yfinance → fundamentals, news, analysts, holders, financials (stocks only)
       └─ Blackboard.write(technicals, candlesticks, fundamentals, macro, news, reddit, ...)
       └─ extract_entities() ← real people from news + 5 domain defaults
       └─ ThreadPoolExecutor(8) — dispatch all agents simultaneously
            ├─ HIGH priority: RiskManager, TechnicalAnalyst
            └─ NORMAL: FundamentalAnalyst, MacroEconomist, SentimentAnalyst + entity agents
       └─ run_debate(bull, bear in parallel)
       └─ run_cio(all agents + debate) → F&O verdict with instrument selection
```

---

## Architecture

### Request flow

```
                ┌──────────────────────────────────────────────┐
                │  Browser tab (Vue 3 SPA + EventSources)      │
                │  http://localhost:53847                      │
                └───────┬──────────────────────┬───────────────┘
                        │                      │
              static assets                  /api/* (SSE + XHR)
              (HMR, Vue, JS, CSS)            HTTPS · HTTP/2 · TLS 1.3
                        │                      │
                        ▼                      ▼
       ┌────────────────────────┐  ┌───────────────────────────────┐
       │  Vite dev server       │  │  nginx                         │
       │  :53847 (HTTP/1.1)     │  │  :47291 (TLS, HTTP/2, h2 ALPN)│
       │  ─ HMR / asset reload  │  │  ─ ssl_certificate cert.pem    │
       │  ─ no proxy            │  │  ─ proxy_buffering off (SSE)   │
       └────────────────────────┘  │  ─ proxy_read_timeout 24h      │
                                   └───────────┬───────────────────┘
                                               │
                                          HTTP/1.1 keep-alive
                                          (loopback, sub-µs)
                                               │
                                               ▼
                                 ┌─────────────────────────────────┐
                                 │  Flask threaded dev server      │
                                 │  127.0.0.1:47292 (internal)     │
                                 │  ─ Werkzeug WSGI                │
                                 │  ─ blueprints under /api/*      │
                                 │  ─ SSE generators yield events  │
                                 └────────┬────────────────────────┘
                                          │
                                          ▼
                       ┌──────────────────────────────────┐
                       │  Background services (threads)   │
                       │  ─ FOScanner (3-min cycles)      │
                       │  ─ PositionMonitor (5s polls)    │
                       │  ─ TrackedMonitor (3s polls)     │
                       │  ─ IndMoney WS subscriber        │
                       │  ─ Commentary loop (30s)         │
                       └──────┬───────────────────────────┘
                              │
                              ▼
              ┌──────────────────────────────┐
              │  External services           │
              │  ─ IndStocks broker WS+REST  │
              │  ─ AWS Bedrock (Claude)      │
              │  ─ Reddit/news APIs          │
              └──────────────────────────────┘
```

### Why nginx in front

The browser caps **6 simultaneous connections per origin** over HTTP/1.1. PhoenixTrade keeps ~7 long-lived SSE streams open (scanner feed, position monitor, live-feed, alerts, plus per-underlying tick streams). Without HTTP/2 the 6-cap exhausts every available socket, leaving zero capacity for normal XHRs (`/api/market/ohlcv`, `/api/market/search`, `POST /api/trade/tracked`) — they queue indefinitely and the UI appears frozen.

nginx terminates **TLS + HTTP/2** on `:47291` and multiplexes every stream over a single TCP connection, removing the 6-cap entirely. Backend stays simple Flask (no async refactor, no ASGI bridge).

### Process layout under `start.sh`

```
start.sh (parent shell, traps SIGINT)
├── nginx                               ←  $NGINX_PID
│   └── -p $ROOT_DIR -c nginx.conf
├── python run.py  (Flask threaded)     ←  $BE_PID
│   ├── FOScanner thread
│   ├── PositionMonitor thread
│   ├── TrackedMonitor poller
│   ├── IndMoney WS thread
│   └── Werkzeug request worker pool
└── npm run dev (Vite)                  ←  $FE_PID
    └── esbuild + HMR worker
```

`Ctrl-C` → trap fires → all three children + their descendants killed.

### Frontend SSE management

| Concern | How it's handled |
|---|---|
| **Duplicate streams across HMR reloads** | `EventSource` registries pinned to `window.__phoenix*`; on every `<script setup>` re-eval the previous instance's streams are explicitly closed before a fresh registry replaces them |
| **Per-underlying tick streams** | Opened only for tracked WATCHING positions and the currently charted ticker — non-watched underlyings rely on `/api/trade/live-feed` for ticks (no duplicate work) |
| **DOM reuse bugs in scanner feed** | `:key="ticker+type+timestamp"` on `v-for` so foFeed mutations don't reuse stale bindings on the Watch button |
| **Self-signed cert acceptance** | One-time `Advanced → Proceed` per browser at `https://localhost:47291`, persisted indefinitely |

---

## Math Reference

All formulas used by PhoenixTrade for option pricing, Greeks, P&L, position sizing, and signal scoring. Mirrors `backend/app/services/greeks.py` (Python) and `frontend/src/utils/blackScholes.js` (JS) — both implementations are unit-verified to match SciPy ground truth within rounding tolerance (< ₹0.01 on premium, < 1e-5 on Greeks).

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
| `σ` | Implied volatility, decimal | Solved via bisection on market price; default 18% if no market data |
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

The scanner picks an ATM-ish strike with delta close to the configured target (default 0.50, the "ATM gamma sweet spot"):

```
  best_strike = argmin |Δ(S, K_i, T, σ, type)  −  target_delta|
                  K_i in available strikes (₹50 spaced for NIFTY, ₹100 for BANKNIFTY)
```

Why 0.50: best gamma:theta ratio. Δ=0.50 means the option moves ~₹0.50 per ₹1 spot move while paying minimum theta drag. Higher Δ (deeper ITM) costs more capital; lower Δ (further OTM) suffers theta cliff and rarely catches up.

### Premium SL/T1/T2 back-derivation

CIO outputs spot-level targets (e.g., `target_1 = 24,200`). The planner converts those to **premium-level** targets so the position monitor can fire on actual P&L, not on underlying levels:

```
  premium_at_spot(S_target)  =  bs_price(S_target, K, T_remaining, r, σ, type)

  target_1_premium  =  premium_at_spot(spot_target_1)
  target_2_premium  =  premium_at_spot(spot_target_2)
  stop_loss_premium =  premium_at_spot(spot_stop_loss)
```

`T_remaining` accounts for theta decay during the expected hold period (rough estimate: 1-3 days). This is why a 2x spot move doesn't always = 2x premium.

### Hard premium-stop-loss override

Even if spot doesn't hit the SL level, exit if premium itself drops 50%:

```
  if (premium_now / entry_premium) ≤ 0.50  →  EXIT
```

Caps max-loss-per-lot at half the entered premium regardless of underlying behaviour. Catches IV-crush scenarios (e.g., post-event volatility collapse) that the spot-based SL would miss.

### P&L formulas

For a buy-side option position (long CE or long PE):

```
  unrealised_pnl  =  (premium_now − entry_premium) × qty × lot_size
  realised_pnl    =  (exit_premium  − entry_premium) × qty × lot_size  −  fees

  fees per round trip ≈ 0.05% of (premium × qty × lot_size) × 2  +  STT + brokerage flat
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

For a quick "is this trade alive?" check during the hold.

### Position sizing (`POSITION_SIZE_PCT`)

```
  capital_at_risk     =  cash × position_size_pct           (default 8%)
  cost_per_lot        =  premium × lot_size
  qty_lots            =  floor(capital_at_risk / cost_per_lot)
```

Falls back to `qty=1` if even one lot exceeds the capital-at-risk cap (provided cash ≥ cost_per_lot).

### Cerebrum confidence aggregation

After 5 parallel agents return verdicts (BUY/SELL/HOLD + 0-100 confidence), the bull/bear debate, then the CIO synthesises:

```
  bull_score  =  Σ confidence_i  for agents with verdict ∈ {STRONG_BUY, BUY}
  bear_score  =  Σ confidence_i  for agents with verdict ∈ {STRONG_SELL, SELL}

  net_score   =  bull_score − bear_score                  (range: −500 .. +500)
  confidence  =  |net_score| / 5                           (normalised 0..100)
```

Cerebrum overrides this if Stage-1 unanimous verdict has < 0.55 average confidence ("trust override") — see `cerebrum/runner.py:117-156`.

### IST trading-hours math (used for scan-loop gating)

```
  IST_minutes(now)  =  hour × 60 + minute  (in Asia/Kolkata)
  market_open      =  IST_minutes ≥ 555  AND  IST_minutes ≤ 930
                       (i.e. 09:15 ≤ now ≤ 15:30)
                     AND  weekday ∈ Mon..Fri
                     AND  date ∉ NSE_HOLIDAYS
```

Pre-warm runs at `IST_minutes == 550` (09:10) — caches F&O master, primes spot LTP, warms the LLM TLS handshake — so the first real scan at 09:15:00 fires sub-second.

### LLM cost calculation (Budget tab)

```
  cost_usd  =  (prompt_tokens × input_rate  +  completion_tokens × output_rate) / 1,000,000
  cost_inr  =  cost_usd × 83                                       (USD→INR proxy)
```

Rates from `backend/app/services/kernel/budget.py:_PRICING` — match AWS Bedrock / Anthropic / OpenAI list prices. Token counts come from the LLM API's `usage` object on every response (real, not estimated). Persisted to `backend/data/budget.db` (SQLite).

### Verification

Quick sanity check against SciPy (which both `greeks.py` and `blackScholes.js` track within rounding tolerance):

```bash
cd backend
.venv/bin/python -c "
from app.services.greeks import greeks
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
Our impl : {'premium_bs': 207.62, 'delta': 0.5208, 'gamma': 0.000806,
            'theta_per_day': -18.78, 'vega_per_volpt': 12.31, 'iv_used': 0.16}
SciPy ref:  premium=207.6170  delta=0.5208
```

If those numbers drift by more than ₹0.05 on premium or 0.001 on delta, the math has a bug.

---

## Quick Start

### Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Node.js | 18+ | |
| Python | 3.11 – 3.12 | |
| uv | latest | Python package manager |
| nginx | 1.25+ | `brew install nginx` (HTTP/2 reverse proxy) |
| openssl | any | For self-signed cert (already on macOS/Linux) |

### Setup & Start

**Step 1 — Configure API keys**

```bash
cp .env.example .env
# Edit .env — choose LLM_PROVIDER and fill in credentials (see below)
# For live prices: set INDMONEY_ACCESS_TOKEN
```

**Step 2 — Start**

```bash
./start.sh
```

`start.sh` auto-installs Python+JS deps on first run and brings up three processes:
- **Frontend (Vite)** → http://localhost:53847
- **nginx (HTTPS+HTTP/2)** → https://localhost:47291  *(public API endpoint)*
- **Backend (Flask)** → http://127.0.0.1:47292  *(internal, only nginx talks to it)*

**Step 3 — Trust the self-signed cert (one-time)**

Open `https://localhost:47291/api/market/world-indices` in your browser → click **Advanced → Proceed to localhost (unsafe)**. Browser remembers the exception indefinitely. The cert lives at `backend/certs/cert.pem`, signed for `localhost` and `127.0.0.1` only.

```bash
./start.sh             # all three (default)
./start.sh --backend   # nginx + Flask only (no Vite)
./start.sh --frontend  # Vite only
```

### Environment Variables

Two LLM modes — pick one:

**Option A — OpenAI-compatible API** (`LLM_PROVIDER=openai`, default)

```env
LLM_PROVIDER=openai
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

**Indian broker (live prices + F&O streaming):**

```env
INDMONEY_ACCESS_TOKEN=your_token   # INDstocks.com → API section
```

**Optional extras:**

```env
ZEP_API_KEY=          # Zep Cloud memory graph
QDRANT_URL=http://localhost:6333   # Semantic memory (+ ollama pull nomic-embed-text)
```

### Docker

```bash
cp .env.example .env
docker compose up -d
```

Exposes port `53847` (Vite) and `47291` (nginx HTTPS+HTTP/2). Flask runs on internal `47292` and is not exposed.

---

## API Reference

### Market (`/api/market`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ohlcv` | Standalone OHLCV — Kite candles first, yfinance fallback |
| GET | `/world-indices` | All world index prices (parallel fetch, TTL 10s) |
| GET | `/universe` | Asset universe metadata |
| GET | `/scan` | Smart scanner — ranked BUY/SELL signals |
| GET | `/signals/<ticker>` | Multi-source news + Reddit + FOMO |
| GET | `/search` | Ticker symbol/name search |
| GET | `/ai-predict/<ticker>` | Full-stack AI prediction |
| GET | `/fundamentals/<ticker>` | Screener.in / yfinance fundamentals (stocks only) |
| GET | `/invest-analysis-stream/<ticker>` | SSE — streams each Cerebrum agent result live |
| GET | `/budget` | Token usage + estimated cost per agent |
| POST | `/budget/reset` | Reset the budget ledger |
| GET | `/processes` | Recent agent execution history |

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
| GET | `/tracked` | List manually-pinned positions |
| POST | `/tracked` | Pin a position (when user clicks "I entered" on a ticket) |
| DELETE | `/tracked/<id>` | Unpin a position |
| GET | `/tracked/alerts/stream` | SSE — server-side SL/T1/T2/theta/time-exit alerts for pinned positions |
| GET | `/tracked/alerts/state` | Latest alert state per pinned position |
| GET | `/option-plan` | Build a single executable option ticket — delta-targeted strike, Greeks, BS premium SL/T1/T2 |
| GET | `/option-chain` | Return ATM ± N strikes around current spot with broker contract metadata (security_id, lot_size, expiry) so the browser can BS-reprice the entire chain on every tick. Query: `underlying`, `strikes` (default 11), `min_dte`, `max_dte`. |
| GET | `/live-status` | Per-position decision snapshot from the last evaluated tick |

### Kite Connect (`/api/kite`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/login-url` | Kite OAuth login URL |
| GET | `/status` | Connection status + WebSocket state |
| GET | `/tick/<ticker>` | Live LTP — WebSocket cache → REST fallback |
| GET | `/ohlc/<ticker>` | Intraday OHLC candles (5-minute) |
| GET | `/stream/<ticker>` | SSE live price stream (WebSocket-backed) |

---

## Market Hours & F&O Trading Windows

| Window | Time (IST) | Purpose |
|---|---|---|
| Market open | 09:15 | NSE market open |
| Safe trading open | 09:15 | Scanner emits signals from market open |
| Theta danger zone | 13:00 (expiry day) | tracked_monitor fires `theta_zone` alert on pinned CE/PE positions on their expiry date |
| Time-exit alert | 15:00 | tracked_monitor fires `time_exit` alert — 20 min before broker auto-square-off |
| Market close | 15:30 | NSE close |
| Broker auto-square-off | 15:20 | Zerodha MIS auto-square-off — exit by 15:00 to avoid this |

**Tuesday rule:** No buying CE/PE on Tuesday morning — weekly NIFTY expiry, theta destroys premium by afternoon.

---

## F&O Knowledge — What the CIO Knows

The CIO agent is trained (via prompt) on these rules:

- **Delta 0.30–0.45** — slightly OTM for best risk/reward (CE: +1–3%, PE: −1–3% from current price)
- **Deep OTM avoid** — delta < 0.15 = lottery ticket, avoid unless very high conviction
- **Premium SL** — 40–50% of premium paid; tracked_monitor flags this as an alert
- **FUT SL** — ATR-based, underlying price level
- **VIX guard** — avoid new longs if VIX > 20 unless it's a PE hedge
- **Expiry selection** — weekly if move expected in 2–3 days, monthly otherwise
- **Time exit 15:00** — alert fires before broker auto-square-off
- **Tuesday expiry** — no CE/PE buying on Tuesday morning

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
| All `/api/*` requests stuck `(pending)` | nginx not running or listening on wrong port | `lsof -nP -iTCP:47291` should show nginx LISTEN; if not, restart `./start.sh` |
| `nginx: [emerg] bind() to 0.0.0.0:47291 failed (98: Address already in use)` | Previous nginx not killed cleanly | `pkill -f nginx` then re-run |
| Multiple SSE rows pinging the same URL in DevTools | HMR orphaned old streams | Hard-refresh (`Cmd+Shift+R`); window-pinned cleanup will close stale ones automatically next save |
| `Cerebrum failed (litellm.APIConnectionError: cannot switch to a different thread)` | Stale gevent monkey-patch from a previous version | Ensure `PHOENIX_USE_GEVENT` is unset; gevent has been removed from deps |
| Watch button click does nothing | Vite saved a file mid-click and HMR aborted the in-flight POST | Don't edit code while clicking; the click handler shows an alert if the symbol is already tracked / in-flight |
| Flask reloads constantly on file save | Werkzeug auto-reloader was enabled | We disabled `use_reloader=False` in `run.py` — if you re-enable it, the SSE generators will be killed every save |

---

## Acknowledgments

- Market data: [yfinance](https://github.com/ranaroussi/yfinance) (fundamentals fallback)
- Live data: [pykiteconnect](https://github.com/zerodha/pykiteconnect) (Zerodha Kite Connect v3)
- TA indicators inspired by: [tradingview-mcp](https://github.com/atilaahmettaner/tradingview-mcp)
- Indian fundamentals: [Screener.in](https://www.screener.in)
- Simulation engine: [OASIS](https://github.com/camel-ai/oasis) by CAMEL-AI
- Graphs: [D3.js](https://d3js.org)
