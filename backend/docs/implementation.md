# Vega — Final Implementation Plan

> DDD Lite + Event-Driven + FastAPI.
> Full rewrite in `backend_v2/` alongside existing code.
> Clean Architecture with DDD Lite, event-driven internals,
> Strategy/Adapter/Observer/State Machine patterns.
> No microservices, no 500-file enterprise bloat.

---

## Core Principles

### Build From Scratch (Your Edge)

These components create trading alpha and must be owned entirely by Vega:

- Regime Engine
- Trade Supervisor
- Risk Engine
- Strategy Plugins
- AI Swing Engine
- Portfolio Engine
- Analytics Engine

### Do NOT Reinvent

Use existing battle-tested solutions:

- INDMoney SDK/API
- CCXT
- Polymarket py-clob-client
- SQLite
- FastAPI
- Pydantic

---

## Asset Classes

```python
class AssetClass(Enum):
    INDIAN_OPTION      = "indian_option"
    CRYPTO_PERP        = "crypto_perp"
    CRYPTO_OPTION      = "crypto_option"
    PREDICTION_MARKET  = "prediction_market"
```

**Supported:**

- NIFTY
- BANKNIFTY
- FINNIFTY
- SENSEX
- Stock Options

**Future:**

- BTC Perps
- ETH Perps
- SOL Perps
- Crypto Options
- Polymarket Markets

---

## High-Level Architecture

```
Vega
├── Edge Layer
├── Infrastructure Layer
├── Research Layer
├── API Layer
├── Runtime Layer
├── Ops Layer
└── Asset Layer
```

---

## Edge Layer

### Regime Engine

Determine current market state.

Outputs: `TRENDING_BULL`, `TRENDING_BEAR`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`

All strategies must pass through this layer.

### Trade Supervisor

Permission layer before execution. 8 gates:

1. Daily Loss Limit (kill switch)
2. Consecutive Loss Limit
3. Max Trades Per Day
4. Instrument Cooldown
5. Correlation Limits
6. Session Rules
7. Max Open Trades
8. Duplicate Position Detection

Output: `APPROVE` or `REJECT`. No sizing logic.

### Risk Engine

Components:

- **PositionSizer** — how much to risk
- **StopManager** — SL clamping + target management
- **CapitalAllocator** — cross-asset capital distribution (future)
- **ExposureGuard** — net directional exposure limits

### Strategy Plugins

**Scalp Strategy** — No AI. Input: Market Data + Regime. Output: BUY / SELL / NO_TRADE. Must remain low-latency.

**Swing Strategy** — AI-assisted. Input: Market Data + Regime + AI Analysis. Output: BUY / SELL / NO_TRADE.

### AI Swing Engine

Responsibilities: Chart Analysis, Trade Thesis Validation, News/Sentiment (future), Confidence Scoring.

**Never:** Place Orders, Manage Risk, Manage Positions. AI produces opinions only.

### Portfolio Engine

Responsibilities: Cross Asset Exposure, Portfolio Risk, Capital Allocation, Exposure Aggregation.

Future: Multi-strategy portfolio management.

### Analytics Engine

Responsibilities: Win Rate, Profit Factor, Expectancy, Drawdown, Regime Attribution, Strategy Attribution, Trade Review.

Answers: *"Why did I lose money today?"*

### Kill Switch Engine

System-level safety — independent from Trade Supervisor's daily loss gate.

Triggers:

- Broker disconnected
- PnL < catastrophic threshold (-₹10000)
- Market data stale (no ticks for N seconds)
- Too many order failures in window
- AI malfunction (hallucinated confidence)

Action: System stops trading. All scanners halt. No new orders.

Location: `domain/safety/kill_switch.py`

### Audit Engine

Every trade decision gets a structured log entry:

```json
{
  "trade_id": "T184",
  "signal": "BUY",
  "regime": "TRENDING_BULL",
  "confidence": 8.2,
  "supervisor": "APPROVED",
  "risk_size": 2,
  "latency_ms": 12,
  "timestamp": "2025-06-18T10:30:00+05:30"
}
```

Answers: *"Why did trade #184 happen?"*

Location: `domain/audit/decision_log.py`

---

## Runtime Layer

### Scalp Runtime

```
Market Data → Regime Engine → Scalp Strategy → Trade Supervisor → Risk Engine → Execution → Position Manager
```

No AI.

### Swing Runtime

```
Market Data → Regime Engine → AI Swing Engine → Swing Strategy → Trade Supervisor → Risk Engine → Execution → Position Manager
```

---

## Real-Time Communication

### WebSockets

Use for: Market Data, Order Updates, Position Updates.

Sources: INDMoney, Binance, Bybit, OKX, Polymarket. Mandatory for scalping.

### REST

Use for: Commands, Queries, Settings.

### SSE

Use for Dashboard: PnL Stream, Trade Feed, Regime Changes, Alerts, Supervisor Events. Frontend only.

---

## Directory Structure

```
backend_v2/
│
├── main.py                          # FastAPI app, lifespan (startup/shutdown hooks)
├── config.py                        # Pydantic Settings — single source of truth
├── dependencies.py                  # DI wiring (get_broker, get_event_bus, etc.)
│
├── domain/                          # Pure domain — ZERO framework imports
│   │
│   ├── entities/
│   │   ├── trade.py                 # Trade (FSM: PENDING→OPEN→PARTIAL→CLOSED→CANCELLED)
│   │   ├── position.py              # Position (tracked, with alert status)
│   │   └── signal.py                # Signal (from scanner, with confidence + direction)
│   │
│   ├── value_objects/
│   │   ├── money.py                 # Money, PnL, Brokerage (immutable, typed)
│   │   ├── option_leg.py            # Strike, Expiry, OptionType, LotSize
│   │   ├── underlying.py            # Underlying enum (NIFTY, SENSEX) + lot sizes
│   │   └── levels.py                # SL, T1, T2, EntryPrice — validated
│   │
│   ├── services/                    # Domain services — pure logic, no I/O
│   │   ├── brokerage_calc.py        # Fee model (STT, exchange, SEBI, GST, stamp)
│   │   └── greeks.py                # Black-Scholes, IV, delta
│   │
│   ├── strategies/                  # Strategy pattern
│   │   ├── base.py                  # TradingStrategy ABC (generate_signal, should_exit)
│   │   ├── swing.py                 # SwingStrategy (technical + LLM confirmation)
│   │   └── scalp.py                 # ScalpStrategy (momentum, pure technical, no LLM)
│   │
│   ├── regime/                      # Market regime classification
│   │   └── regime_engine.py         # Outputs: TRENDING_BULL, TRENDING_BEAR, RANGING,
│   │                                #          HIGH_VOLATILITY, LOW_VOLATILITY
│   │                                # Inputs: VIX, ADX, EMA slope, breadth
│   │                                # Feeds into: strategies + regime_guard
│   │
│   ├── supervisor/                  # Trade supervisor — sits between Strategy and Risk
│   │   └── trade_supervisor.py      # Permission gate. Enforces: consecutive_losses,
│   │                                #   instrument_cooldown, max_trades_per_day,
│   │                                #   correlation_limit, session_rules (no entry
│   │                                #   first 5min / last 30min)
│   │
│   ├── risk/                        # Risk domain — pure, testable
│   │   ├── budget_guard.py          # Daily loss limit, headroom, kill switch logic
│   │   ├── sl_enforcer.py           # SL clamping per underlying (NIFTY 15pt, SENSEX 50pt)
│   │   ├── reentry_guard.py         # Direction blocks, re-entry limits, cooldowns
│   │   ├── exposure_guard.py        # Net exposure tracking: 4 CE trades = 1 bullish view.
│   │   │                            #   Max exposure per direction, per underlying,
│   │   │                            #   and combined. Prevents concentrated bets.
│   │   ├── regime_guard.py          # Blocks or adjusts based on regime:
│   │   │                            #   HIGH_VOL → reduce size / widen SL / skip scalp
│   │   │                            #   RANGING → skip swing entries
│   │   │                            #   TRENDING → allow full size
│   │   └── position_sizer.py        # How much to risk. Kelly / fixed-fraction / regime-aware.
│   │                                #   Supervisor = permission, Risk = sizing.
│   │
│   ├── safety/                      # System-level kill switch (independent of supervisor)
│   │   └── kill_switch.py           # Broker disconnect, stale data, catastrophic loss
│   │
│   ├── audit/                       # Decision audit trail
│   │   └── decision_log.py          # Structured log of every trade decision
│   │
│   ├── features/                    # FUTURE: reusable computed features (VWAP, ATR, PCR)
│   │
│   └── events/                      # Domain events (dataclasses, no side effects)
│       └── events.py                # SignalGenerated, TradeApproved, TradeRejected,
│                                    # OrderPlaced, OrderFilled, PositionClosed,
│                                    # PnLUpdated, DayRolled, KillSwitchTriggered,
│                                    # AlertStatusChanged, RegimeChanged, ExposureLimitHit
│
├── application/                     # Use cases — orchestrates domain + infra
│   │
│   ├── commands/                    # Write operations (things that change state)
│   │   ├── place_entry.py           # PlaceEntryCommand → validates risk → places order
│   │   ├── place_exit.py            # PlaceExitCommand → closes position → records P&L
│   │   ├── force_exit.py            # ForceExitCommand → manual/time exit
│   │   └── reset_daily.py           # ResetDailyCommand → midnight/manual reset
│   │
│   ├── queries/                     # Read operations (no side effects)
│   │   ├── pnl_summary.py           # DailySummary, MonthlySummary, TradeHistory
│   │   ├── executor_status.py       # Current config, P&L, kill switch, ledger
│   │   ├── scanner_state.py         # Scanner running/paused, last signal, stats
│   │   └── position_status.py       # All tracked positions + alert states
│   │
│   ├── handlers/                    # Event handlers (react to domain events)
│   │   ├── on_signal.py             # SignalGenerated → attempt entry
│   │   ├── on_order_filled.py       # OrderFilled → pin position, start monitoring
│   │   ├── on_position_closed.py    # PositionClosed → record P&L, update risk state
│   │   ├── on_pnl_updated.py        # PnLUpdated → check kill switch
│   │   ├── on_day_rolled.py         # DayRolled → reset all daily state
│   │   └── on_regime_changed.py     # RegimeChanged → adjust strategy params, notify UI
│   │
│   └── event_bus.py                 # Simple sync pub/sub (Observer pattern)
│                                    # bus.publish(event) → all registered handlers fire
│
├── engines/                         # Long-running loops (background threads)
│   │
│   ├── swing/
│   │   ├── scanner.py               # Scan loop: TA pre-filter → LLM → emit SignalGenerated
│   │   └── state.py                 # Daily ledger, direction blocks, exit tracking
│   │
│   ├── scalp/
│   │   ├── scanner.py               # Momentum detection loop (no LLM, every tick)
│   │   ├── state.py                 # Entry count, streak tracking, peak P&L
│   │   └── config.py                # Scalp-specific tunable config (hot-reloadable)
│   │
│   ├── monitor/
│   │   └── position_monitor.py      # Poll spot → reprice → detect SL/T1/T2/time
│   │                                # Emits AlertStatusChanged, triggers exits
│   │
│   └── portfolio/                   # FUTURE: capital allocation, cross-strategy exposure,
│                                    # portfolio-level risk. Placeholder — not built in v2.0.
│
├── infrastructure/                  # Adapters — replaceable, no domain logic
│   │
│   ├── broker/
│   │   ├── base.py                  # BrokerAdapter ABC (place_order, ltp, positions, cash)
│   │   ├── indmoney.py              # INDmoney/INDstocks implementation
│   │   └── paper.py                 # Paper trading mock (logs, no real orders)
│   │
│   ├── db/
│   │   ├── sqlite.py                # Connection pool, WAL mode, thread safety
│   │   └── state_store.py           # Key-value persistence (trading_state table)
│   │
│   ├── market_data/
│   │   ├── ticker.py                # Real-time tick stream (INDstocks WebSocket)
│   │   └── historical.py            # OHLCV via yfinance / INDstocks candles
│   │
│   ├── llm/
│   │   ├── client.py                # Unified LLM client (OpenAI/Bedrock/Ollama)
│   │   └── key_pool.py              # Round-robin key rotation + 429 cooldown
│   │
│   ├── config/
│   │   └── config_service.py        # YAML config loader + hot-reload + Pydantic validation
│   │
│   ├── security/
│   │   └── secrets_manager.py       # Credential management (INDMoney, OpenAI, CCXT keys)
│   │
│   └── notifications/
│       ├── sse.py                   # Single SSEManager (channels: scanner, alerts, scalp, pnl)
│       ├── telegram.py              # Telegram bot notifications
│       └── discord.py               # Discord webhook notifications
│
├── api/                             # FastAPI routers — thin, delegates to application layer
│   ├── trade.py                     # /trade/* (tracked positions, executor control)
│   ├── market.py                    # /market/* (analysis, OHLCV, signals, fundamentals)
│   ├── scanner.py                   # /scanner/* (swing + scalp start/stop/status)
│   ├── pnl.py                       # /pnl/* (summary, trades, export)
│   ├── broker.py                    # /broker/* (INDmoney proxy: orders, positions, holdings)
│   ├── streams.py                   # All SSE endpoints (single implementation)
│   └── options.py                   # /options/* (chain, plan, greeks)
│
├── shared/                          # Cross-cutting, no domain logic
│   ├── logger.py                    # Structured logging (same rotating file setup)
│   ├── time.py                      # IST helpers, market hours, NSE holidays, is_trading_day
│   ├── scheduler.py                 # Cron-like: midnight reset, market open/close hooks
│   └── indicators.py                # TA functions (Supertrend, ADX, RSI, EMA, MACD, ATR)
│                                    # FUTURE: split into indicators/{trend,momentum,volatility,volume}.py
│                                    # when this exceeds ~800 LOC. Not today.
│
├── monitoring/                      # Ops layer — health + performance
│   ├── health_monitor.py            # Feed delay, broker connectivity, API failures
│   ├── websocket_monitor.py         # WS disconnect tracking + auto-reconnect
│   └── latency_monitor.py           # Per-trade latency: signal → supervisor → risk → order
│
├── config/                          # YAML config files (not code)
│   ├── trading.yaml                 # Trade modes, session hours, defaults
│   ├── risk.yaml                    # Daily limits, SL rules, sizing params
│   ├── regime.yaml                  # VIX thresholds, ADX bands, regime rules
│   └── exchanges.yaml               # Endpoints, rate limits, fees
│
├── knowledge/                       # Trading knowledge base
│   ├── smc_ict_wyckoff.md
│   └── trading_knowledge_base.md
│
└── tests/
    ├── domain/                      # Unit tests — pure logic, no I/O, fast
    ├── application/                 # Handler + command tests with mocked infra
    ├── engines/                     # Scanner/monitor integration tests
    └── api/                         # Endpoint contract tests (httpx)
```

~50 files total. Not 500. Not 10. Right-sized.

---

## Design Patterns Used

### 1. Strategy Pattern — Trading Strategies

```python
class TradingStrategy(ABC):
    @abstractmethod
    def generate_signal(self, data: MarketData) -> Signal | None: ...

    @abstractmethod
    def should_exit(self, position: Position, tick: Tick) -> ExitReason | None: ...

# class SwingStrategy(TradingStrategy):   # TA pre-filter + LLM confirmation
# class ScalpStrategy(TradingStrategy):   # Pure momentum, no LLM
```

### 2. Adapter Pattern — Broker

```python
class BrokerAdapter(ABC):
    @abstractmethod
    def place_order(self, order: Order) -> OrderResult: ...

    @abstractmethod
    def ltp(self, symbol: str) -> float: ...

    @abstractmethod
    def positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def available_cash(self) -> float: ...

# class INDMoneyBroker(BrokerAdapter): ...
# class PaperBroker(BrokerAdapter): ...
# Future: class ZerodhaBroker(BrokerAdapter): ...
```

### 3. Observer / Event Bus

```python
class EventBus:
    def subscribe(self, event_type: type, handler: Callable): ...
    def publish(self, event: DomainEvent): ...

# Wiring (in main.py lifespan):
# bus.subscribe(SignalGenerated,  on_signal_handler)
# bus.subscribe(OrderFilled,     on_order_filled_handler)
# bus.subscribe(PositionClosed,  on_position_closed_handler)
# bus.subscribe(PnLUpdated,      on_pnl_updated_handler)
# bus.subscribe(DayRolled,       on_day_rolled_handler)
# bus.subscribe(RegimeChanged,   on_regime_changed_handler)
```

### 4. State Machine — Trade Lifecycle

```python
class TradeState(Enum):
    PENDING   = "pending"
    OPEN      = "open"
    PARTIAL   = "partial"      # T1 hit, 50% exited
    CLOSED    = "closed"
    CANCELLED = "cancelled"

class Trade:
    state: TradeState
    def fill(self) -> None:         # PENDING → OPEN
    def partial_exit(self) -> None: # OPEN → PARTIAL
    def close(self) -> None:        # OPEN/PARTIAL → CLOSED
    def cancel(self) -> None:       # PENDING → CANCELLED
    # Invalid transitions raise InvalidStateTransition
```

### 5. Factory — Strategy Selection

```python
class StrategyFactory:
    @staticmethod
    def create(mode: TradeMode, config: Settings) -> TradingStrategy:
        match mode:
            case TradeMode.SWING: return SwingStrategy(config)
            case TradeMode.SCALP: return ScalpStrategy(config)
```

### Patterns NOT Used

| Pattern | Why Not |
|---------|---------|
| Repository | SQLite isn't changing. Direct queries are fine. |
| CQRS | Overkill. Commands/queries are separated by convention, not infrastructure. |
| Event Sourcing | Not needed. State is mutable, events are fire-and-forget. |
| Service Locator | Avoided. FastAPI `Depends()` handles DI. |
| Full DDD | No aggregates, no bounded contexts, no saga. DDD Lite only. |

---

## Data Structures

| Structure | Where | Why |
|-----------|-------|-----|
| `deque(maxlen=N)` | Tick buffer, recent exits | Ring buffer — O(1) append, auto-evict old |
| `heapq` | Signal priority queue | Best signal first, O(log n) insert |
| `functools.lru_cache` | Option chains, historical data | Avoid repeated API calls |
| `dict` | Ledgers, config cache, position lookup | O(1) everything |
| Sliding window | EMA, VWAP, ATR, RSI | Streaming indicators over tick/candle data |
| FSM (enum + transitions) | Trade lifecycle | No more `if status == ...` scattered everywhere |

---

## Data Flow

```
Market Data (WebSocket ticks)
        │
        ▼
┌─────────────┐
│ Indicators  │  Supertrend, ADX, RSI, EMA, MACD, VIX (shared/indicators.py)
└──────┬──────┘
       │
       ▼
┌──────────────┐
│ Regime       │  VIX + ADX + EMA slope + breadth
│ Engine       │  → TRENDING_BULL / BEAR / RANGING / HIGH_VOL / LOW_VOL
│              │  → emits RegimeChanged (on transition)
└──────┬───────┘
       │ regime context
       ▼
┌──────────────┐
│ Strategy     │  SwingStrategy (+ LLM) or ScalpStrategy (pure TA)
│ Engine       │  Receives regime → adjusts signals accordingly
│              │  → emits SignalGenerated
└──────┬───────┘
       │ event
       ▼
┌──────────────┐
│ Trade        │  Sits BETWEEN strategy and risk. Permission gate.
│ Supervisor   │  Checks: consecutive_losses, instrument_cooldown,
│              │  max_trades, correlation_limit, session_rules
│              │  → approves or rejects (does NOT size)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Risk Engine  │  BudgetGuard + SLEnforcer + ReentryGuard
│              │  + ExposureGuard + RegimeGuard + PositionSizer
│              │  → approves or rejects, sets final size
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Execution    │  PlaceEntryCommand → BrokerAdapter.place_order()
│ Engine       │  → emits OrderFilled
└──────┬───────┘
       │ event
       ▼
┌──────────────┐
│ Position     │  Pins position, starts monitoring
│ Engine       │  Monitor reprices → detects SL/T1/T2/time
│              │  → emits PositionClosed
└──────┬───────┘
       │ event
       ▼
┌──────────────┐
│ PnL Engine   │  Records trade, calcs brokerage
│              │  → emits PnLUpdated
└──────┬───────┘
       │ event
       ▼
┌──────────────┐
│ Risk Engine  │  Updates daily P&L, checks kill switch
│ (listener)   │  → may emit KillSwitchTriggered
└──────────────┘
```

---

## Scheduler

```python
# In main.py lifespan:
scheduler.daily("00:00", IST, reset_all_daily_state)     # midnight reset
scheduler.daily("09:10", IST, pre_warm_caches)            # before market
scheduler.daily("15:10", IST, force_exit_all_positions)   # post-market
scheduler.every(30, seconds, check_day_rollover)           # belt-and-suspenders
```

---

## API Contracts (preserved for frontend)

All existing endpoints keep the same paths and response shapes. Key routes:

| Current Path | Router | Notes |
|-------------|--------|-------|
| `/api/trade/tracked` | `api/trade.py` | GET/POST/DELETE positions |
| `/api/trade/tracked/alerts/stream` | `api/streams.py` | SSE alerts |
| `/api/trade/fo-scanner/*` | `api/scanner.py` | Swing scanner control |
| `/api/trade/scalp-scanner/*` | `api/scanner.py` | Scalp scanner control |
| `/api/trade/executor/*` | `api/trade.py` | Status, reset, block/unblock |
| `/api/trade/pnl/*` | `api/pnl.py` | Summary, trades |
| `/api/trade/option-chain` | `api/options.py` | Chain + plan |
| `/api/market/*` | `api/market.py` | OHLCV, signals, analysis, fundamentals |
| `/api/indmoney/*` | `api/broker.py` | Broker proxy (orders, positions, holdings) |

---

## Migration Phases

### Phase 0: Skeleton ✅

- [x] `backend_v2/` project setup with `uv`, FastAPI, uvicorn
- [x] `config.py` — Pydantic Settings (all .env vars, typed, validated)
- [x] `domain/events/events.py` — all domain event dataclasses
- [x] `application/event_bus.py` — simple sync pub/sub
- [x] `shared/` — logger, time helpers, scheduler
- [x] `main.py` — FastAPI app with lifespan, health check
- [x] Verify it starts on a different port alongside old backend

### Phase 1: Domain + Infrastructure ✅

- [x] `domain/entities/` — Trade (FSM), Signal (multi-market)
- [x] `domain/value_objects/` — Money (multi-currency), OptionLeg, Instrument, Market, AssetClass
- [x] `domain/services/` — brokerage_calc, greeks
- [x] `domain/regime/regime_engine.py` — regime classification (VIX + ADX + EMA slope)
- [x] `domain/supervisor/trade_supervisor.py` — 8 gates
- [x] `domain/risk/` — budget_guard, sl_enforcer, reentry_guard, exposure_guard, regime_guard, position_sizer
- [x] `domain/strategies/base.py` — StrategyBase ABC
- [x] `domain/analytics/engine.py` — production analytics (win rate, drawdown, regime perf, streaks)
- [x] `domain/exceptions.py` — VegaError hierarchy
- [x] `infrastructure/broker/` — BrokerAdapter ABC + DhanBroker (dhanhq SDK) + INDMoneyBroker + BrokerFactory (pluggable)
- [x] `infrastructure/exchange/` — ExchangeAdapter ABC + CCXTAdapter (real ccxt.async_support, derivatives/options) + PolymarketAdapter (real polymarket-client SDK)
- [x] `shared/indicators.py` — pure TA functions (EMA, ATR, VWAP, Donchian, ROC, RSI, ADX, Bollinger, Supertrend, MACD, ADX full)
- [x] `domain/strategies/scalp.py` — NiftyScalpStrategy (pure momentum, 4-gate detection, migrated from v1)
- [x] `domain/strategies/swing.py` — SwingAIStrategy (2-stage TA + LLM pipeline, migrated from v1 fo_scanner)
- [x] `domain/replay/replay_engine.py` — ReplayEngine (deterministic session replay, simulated trades, comparison)
- [x] `monitoring/health_monitor.py` — HealthMonitor (component heartbeats, status aggregation, active checks)
- [x] `monitoring/latency_tracker.py` — LatencyTracker (per-trade pipeline latency, rolling P50/P95/P99 stats)
- [x] `infrastructure/notifications/` — NotificationService + TelegramChannel + DiscordChannel (priority/category routing, rate limiting)
- [x] `application/paper_validator.py` — PaperTradingValidator (end-to-end pipeline: signal→supervisor→broker, session reports, validation assertions)
- [x] `infrastructure/exchange/ccxt_adapter.py` — CCXTAdapter (real async ccxt: spot, perps, futures, options via Deribit/Bybit/OKX, leverage, margin mode)
- [x] `infrastructure/exchange/polymarket_adapter.py` — PolymarketAdapter (real polymarket-client: SecureClient/PublicClient, limit/market orders, positions, balance, order book)
- [x] `infrastructure/config/config_service.py` — YAML config loader (config/*.yaml: brokers, strategies, exchanges, risk)
- [x] `domain/analytics/ab_testing.py` — A/B testing framework (variant routing, trade recording, comparative metrics, winner detection)
- [x] `config/brokers.yaml` — broker config (dhan, indmoney) with pluggable adapter pattern
- [x] `config/strategies.yaml` — strategy config + A/B test definitions
- [x] `config/exchanges.yaml` — crypto exchange config (Binance, Bybit, Deribit) + Polymarket
- [x] `config/risk.yaml` — risk/supervisor/kill-switch config
- [x] Unit tests — **732 passing**

### Phase 2: Application Layer ⬜

- [ ] `application/commands/` — place_entry, place_exit, force_exit, reset_daily
- [ ] `application/queries/` — pnl_summary, executor_status, scanner_state
- [ ] `application/handlers/` — all event handlers
- [x] `domain/strategies/swing.py` — SwingAIStrategy (TA + LLM)
- [x] `domain/strategies/scalp.py` — ScalpStrategy (pure momentum)
- [ ] Wire event bus in `main.py` lifespan
- [ ] Tests for commands + handlers with mocked broker

### Phase 3: Engines ⬜

- [ ] `engines/swing/` — scanner loop + daily state
- [ ] `engines/scalp/` — scanner loop + daily state + hot-reload config
- [ ] `engines/monitor/` — position monitor (poll → reprice → alert)
- [x] `shared/indicators.py` — pure TA functions
- [ ] Integration test: signal → entry → SL exit → P&L recorded → kill switch checked

### Phase 4: API Layer ⬜

- [ ] All routers (trade, market, scanner, pnl, broker, streams, options)
- [ ] Same URL paths, same JSON response shapes as old backend
- [ ] Frontend contract test suite (hit every endpoint, compare shapes)

### Phase 5: Cutover ⬜

- [ ] Run both backends, compare P&L outputs on same market day
- [ ] Update `start.sh` with `--v2` flag
- [ ] Switchover
- [ ] Old backend stays in repo for rollback

---

## Tech Stack

| What | Choice |
|------|--------|
| Framework | FastAPI |
| Server | uvicorn (ASGI) |
| Config | Pydantic Settings + YAML (config/*.yaml) |
| Validation | Pydantic v2 models |
| DB | sqlite3 (sync, WAL mode) — no ORM, no aiosqlite |
| SSE | sse-starlette |
| Testing | pytest + httpx |
| Deps | uv |
| Typing | strict — mypy-compatible |

---

## Infrastructure Layer (expanded)

```
Infrastructure
├── Exchange Adapters
├── Market Data Providers
├── Storage
├── Event Bus
├── Config Service
├── Secrets Manager
└── Notifications
```

### Config Service

No more scattered constants. Centralized, typed, hot-reloadable.

```
config/
    trading.yaml       # trade modes, session hours, defaults
    risk.yaml          # daily limits, SL rules, sizing
    regime.yaml        # VIX thresholds, ADX bands
    exchanges.yaml     # endpoints, rate limits, fees
```

Loaded via `ConfigService` → validated by Pydantic → injected via DI.

Location: `infrastructure/config/config_service.py`

### Secrets Manager

Today: 3 keys. Tomorrow: 10+.

Credentials: INDMoney, OpenAI, Bedrock, Binance, Bybit, OKX, Polymarket, Telegram, Discord.

Never in code. Never in `.env` in production.

Location: `infrastructure/security/secrets_manager.py`

### Notifications

Channels:

- Telegram
- Discord
- Email (future)

Events:

- Kill Switch activated
- Trade Opened / Closed
- Daily Summary
- Loss Limit Hit
- System errors

Location: `infrastructure/notifications/`

### Storage

SQLite WAL mode.

Tables:

- Trades
- Positions
- Orders
- Regimes
- Analytics
- **AuditLog** (decision trail)

No PostgreSQL until required.

---

## Research Layer

### Replay Engine

Most important research tool. Replay historical sessions, losing days, winning days.
System behaves as if live.

### Backtesting

Evaluate strategies, regime rules, supervisor rules.

### Experiment Tracking

Track parameters, results, metrics.

---

## Ops Layer

### Health Monitoring

Track:

- Feed delay (market data staleness)
- WebSocket disconnects + reconnects
- Execution latency (order → fill)
- API failure rates
- Broker connectivity

Location: `monitoring/health_monitor.py`, `websocket_monitor.py`, `latency_monitor.py`

### Performance Monitoring

Track per-trade:

- Signal generation latency
- Supervisor decision latency
- Risk check latency
- Execution latency
- Total decision latency (signal → order)

Critical for scalping where ms matter.

---

## Future-Proofing

### Feature Store

Reusable computed features across strategies:

- VWAP, ATR, PCR, OI, Funding Rate, Volatility
- Computed once, consumed by many strategies

Location: `domain/features/`

### Versioned Strategy Configs

Track strategy evolution:

- Strategy V1, V2, V3... with parameters, metrics, results
- Know which version worked and why
- Enables A/B testing between strategy versions

### Multi-Timeframe Support

Architecture must allow:

- 1m, 3m, 5m, 15m, 1h, Daily
- Without rewriting strategy code
- Strategy declares required timeframes, system provides them

---

## Testing Strategy

1. Unit Tests
2. Strategy Tests
3. Regime Tests
4. Replay Tests
5. Integration Tests
6. Paper Trading
7. Chaos Tests

**Highest Priority:**

- Regime Engine
- Trade Supervisor
- Risk Engine
- Replay Engine
- Kill Switch Engine
- Audit Engine

---

## Implementation Order (20 Phases)

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Freeze Architecture, Project Structure, Domain Models | ✅ |
| 2 | Regime Engine | ✅ |
| 3 | Trade Supervisor | ✅ |
| 4 | Risk Engine | ✅ |
| 5 | Position Manager + PnL Manager | ✅ |
| 6 | Audit Trail + Decision Log | ✅ |
| 7 | Kill Switch Engine (system-level safety) | ✅ |
| 8 | Config Service + Secrets Manager | ✅ |
| 9 | Analytics Engine | ✅ |
| 10 | Scalp Strategy Migration | ✅ |
| 11 | Swing Strategy Migration | ✅ |
| 12 | Replay Engine | ✅ |
| 13 | Health + Performance Monitoring | ✅ |
| 14 | Notifications (Telegram, Discord) | ✅ |
| 15 | Paper Trading Validation | ✅ |
| 16 | CCXT Integration (real ccxt.async_support + derivatives) | ✅ |
| 17 | Polymarket Integration (real polymarket-client SDK) | ✅ |
| 18 | AI Swing Enhancements | ✅ |
| 19 | Feature Store + Multi-Timeframe | ✅ |
| 20 | Versioned Strategy Configs | ✅ |

**ALL 20 PHASES COMPLETE** — 814 tests passing.

---

## Success Criteria

The architecture is considered complete when:

- New strategies can be added without touching execution code
- New exchanges can be added via adapters
- Scalp remains AI-free and low-latency
- Swing can leverage AI safely
- Trade Supervisor controls all risk permissions
- Regime Engine gates all strategies
- Replay Engine can reproduce any trading day
- Analytics can explain losses and wins automatically
- Crypto and Polymarket can be added without redesigning the system
- Every trade decision has a full audit trail
- System-level kill switch catches broker/data/AI failures
- Config changes don't require code deploys
- Notifications alert on critical events in real-time
- Latency is tracked end-to-end for every trade

---

## Maturity Model

| Level | What | Status |
|-------|------|--------|
| **95%** | Current plan (domain, risk, strategies, analytics) | ✅ |
| **99%** | + Audit Trail, Kill Switch, Config Layer, Monitoring, Notifications | ⬜ |
| **100%** | Discovered after 500+ live trades, broker outages, WebSocket failures, market crashes, AI hallucinations, real operational pain | 🔮 |
