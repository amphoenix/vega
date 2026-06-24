# Vega — Architecture

> One trading brain, multiple exchanges underneath.

## High-Level Architecture

```
Vega

├── Edge Layer (your moat — build from scratch)
│   ├── Regime Engine          — VIX/ADX/RSI-based market classification
│   ├── Trade Supervisor       — 6 gates: budget, streak, max trades, cooldown, correlation, kill switch
│   ├── Risk Engine            — Budget, SL, re-entry, exposure, regime, position sizing
│   ├── Strategy Plugins       — Freqtrade-inspired ABC (on_tick → Signal)
│   ├── AI Swing Engine        — LLM-assisted swing trade decisions
│   ├── Portfolio Engine       — (future: Exposure Engine, Capital Allocator, Portfolio Risk)
│   └── Analytics Engine       — Production analytics: win rate, drawdown, profit factor, regime perf
│
├── Infrastructure Layer (commodity — don't reinvent)
│   ├── INDMoney Adapter       — Indian F&O broker (existing BrokerAdapter)
│   ├── CCXT Adapter           — Crypto exchanges
│   │   ├── Binance
│   │   ├── Bybit
│   │   └── OKX
│   ├── Polymarket Adapter     — Prediction markets (py-clob-client)
│   ├── Event Bus              — Lightweight in-process pub/sub (no Kafka/Redis)
│   ├── Market Data Providers  — Tick/quote/OHLCV feeds
│   └── Storage (SQLite WAL)   — PnL ledger, state store
│
├── Research Layer (future)
│   ├── Replay Engine          — Historical trade replay for backtesting
│   ├── Backtesting            — Strategy backtest harness
│   ├── Analytics              — Research-grade analytics (different from production)
│   └── Experiment Tracking    — Strategy parameter experiments
│
└── Asset Classes
    ├── INDIAN_OPTION           → INDMoney
    ├── CRYPTO_PERP             → CCXT (Binance/Bybit/OKX)
    ├── CRYPTO_OPTION           → CCXT (future — Deribit-style)
    └── PREDICTION_MARKET       → Polymarket
```

## Core Flow

```
Strategy.on_tick(data)
    ↓ Signal
Risk Engine (6 gates)
    ↓ TradeApproved event
Trade Supervisor
    ↓ permission granted
ExchangeAdapter.place_order()
    ↓ OrderFilled event
Trade FSM: PENDING → OPEN → PARTIAL → CLOSED
    ↓ PositionClosed event
PnL recording + Analytics
```

## Key Design Decisions

1. **Strategy → AssetClass → Exchange** — no `if nifty: / if crypto:` scattered everywhere
2. **Domain layer is pure** — no I/O, no HTTP, no DB imports
3. **Single time source** — `shared/time.py` (IST-aware, NSE holidays)
4. **Single logger** — `shared/logger.py` (root `vega` logger, child loggers via `get_logger()`)
5. **Domain exceptions** — structured hierarchy under `VegaError`
6. **Event Bus** — lightweight in-process pub/sub, no external dependencies
7. **Trade FSM** — explicit state machine with validated transitions
8. **Existing BrokerAdapter** kept for F&O, **ExchangeAdapter** is the new unified layer

## Directory Structure

```
backend_v2/
├── app/
│   ├── domain/                    # Pure domain logic (your edge)
│   │   ├── entities/              # Trade (FSM), Position, Signal
│   │   ├── value_objects/         # Money, OptionLeg, Instrument, Market, AssetClass
│   │   ├── services/              # brokerage_calc, greeks
│   │   ├── regime/                # RegimeEngine
│   │   ├── supervisor/            # TradeSupervisor
│   │   ├── risk/                  # Budget, SL, ReEntry, Exposure, Regime, PositionSizer
│   │   ├── strategies/            # StrategyBase ABC
│   │   ├── analytics/             # AnalyticsEngine (production metrics)
│   │   ├── events/                # Domain events
│   │   └── exceptions.py          # VegaError hierarchy
│   │
│   ├── infrastructure/            # I/O, adapters, storage
│   │   ├── broker/                # BrokerAdapter (F&O), PaperBroker
│   │   ├── exchange/              # ExchangeAdapter, CCXTAdapter, PolymarketAdapter
│   │   ├── db/                    # SQLite state store
│   │   ├── market_data/           # Ticker stub
│   │   ├── llm/                   # LLM client
│   │   └── notifications/         # SSE
│   │
│   ├── application/               # Orchestration
│   │   └── event_bus.py           # In-process pub/sub
│   │
│   ├── shared/                    # Cross-cutting
│   │   ├── time.py                # Single time source (IST, UTC, holidays)
│   │   ├── logger.py              # Single logger
│   │   └── scheduler.py           # Task scheduler
│   │
│   ├── config.py
│   ├── dependencies.py
│   └── main.py                    # FastAPI app
│
├── tests/
│   ├── domain/                    # Unit tests
│   └── integration/               # Integration tests
│
└── docs/                          # This directory
```

## What's Frozen

These patterns are locked. Don't redesign unless a real production problem appears:

- **Strategy Plugin Pattern** — StrategyBase ABC
- **Adapter Pattern** — BrokerAdapter / ExchangeAdapter
- **State Machine** — Trade FSM (PENDING → OPEN → PARTIAL → CLOSED / CANCELLED)
- **Regime Engine** — VIX/ADX/RSI classification
- **Trade Supervisor** — 6-gate permission system
- **Risk Engine** — modular guards
- **Event Bus** — in-process pub/sub

## What's NOT Built (and shouldn't be)

- Custom Binance/Bybit/OKX clients → use **CCXT**
- Custom Polymarket SDK → use **py-clob-client**
- Message queues (Kafka/RabbitMQ/Redis Streams) → overkill for single-user system
- Custom exchange abstraction from scratch → CCXT already does this
