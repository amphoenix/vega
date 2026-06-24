# BTST Scanner — Design Spec
**Date:** 2026-06-22  
**Status:** Approved  
**Scope:** New BTST scanner as fixed right-hand sidebar inside Scalp panel (always visible, Swing-style layout)

---

## 1. Overview

Fully automated paper-trading BTST (Buy Today Sell Tomorrow) scanner. Scans Nifty 50 + Sensex stock universe for bullish overnight holds, and Nifty/Sensex index F&O for bearish overnight shorts. AI (LLM) scores candidates and decides morning exit timing. Paper trading only — no live order execution.

---

## 2. Architecture

### Approach
Separate BTST service following Approach B. Clean separation from scalp engine — BTST has a fundamentally different lifecycle (afternoon scan → overnight hold → morning exit) that would conflict with scalp's tick-driven intraday logic.

### Component Map

```
Frontend                          Backend
────────────────────────────      ─────────────────────────────────────
ScalpPanel.vue
  └─ BtstPanel.vue (always)  ←── SSE: /api/trade/btst-scanner/stream
       ├─ signal cards
       ├─ overnight positions
       └─ trade log

BtstPanel.vue ──────────────────► /api/trade/btst-scanner/start|stop
                                  /api/trade/btst-scanner/config (GET/PUT)
                                  /api/trade/btst-scanner/stats
                                  /api/trade/btst-scanner/status

                                  backend/app/engines/btst_scanner.py
                                    BTSTScanner (singleton)
                                      ├─ Stage 1: technical pre-filter
                                      ├─ Stage 2: parallel LLM scoring
                                      ├─ APScheduler: morning exit job
                                      └─ SSE pub/sub (same as scalp)
```

### New Files
| File | Purpose |
|------|---------|
| `frontend/src/components/panels/BtstPanel.vue` | BTST UI panel |
| `frontend/src/api/market/btst.js` | Frontend API calls + SSE |
| `backend/app/engines/btst_scanner.py` | Core scanner engine |
| `backend/btst_positions.json` | Paper position persistence (auto-created) |

### Modified Files
| File | Change |
|------|--------|
| `frontend/src/components/panels/ScalpPanel.vue` | Wrap in flex row, add `<BtstPanel />` as right sidebar |
| `backend/app/api/trade.py` | Add 7 BTST routes |
| `backend/app/config.py` | Add BTST config keys |

---

## 3. Backend Design

### State Machine
```
IDLE → SCANNING → HOLDING → MORNING_EXIT → IDLE
```
- `IDLE`: Outside scan window and no held positions
- `SCANNING`: 14:30–15:10, Stage 1 + Stage 2 running
- `HOLDING`: Positions locked overnight (15:10–09:15 next day)
- `MORNING_EXIT`: 09:15 AM, AI evaluates and exits each position

### Universe
- **Bullish (cash segment):** Nifty 50 + Sensex stocks (~80 symbols)
- **Bearish (F&O):** NIFTY and BANKNIFTY/SENSEX index futures only (2 instruments)

### Two-Stage Pipeline

**Stage 1 — Technical Pre-filter (fast, no LLM)**
Criteria (all must pass for bullish; inverse for bearish):
- Volume ratio > 1.5x 20-day average volume
- Price closing in top 10% of day's range (bullish) / bottom 10% (bearish)
- ADX > 20 (trend present)
- Price within 0.5% of 20-day high (bullish) / 20-day low (bearish)

Reduces ~80 stocks → 10–15 candidates. Bearish instruments (2 total) skip Stage 1 and go straight to Stage 2.

**Stage 2 — AI Scoring (parallel async)**
- All Stage 1 survivors scored concurrently via LLM (`LLMClient.from_settings()`)
- Input per symbol: 5-day OHLCV, today's volume ratio, sector, market breadth context
- LLM output schema:
  ```json
  {
    "score": 0-100,
    "direction": "bull" | "bear",
    "rationale": "one-line reason",
    "target_pct": 1.8,
    "risk_note": "gap-down on weak market"
  }
  ```
- Top 3 by score (across bull + bear combined) become paper positions
- Minimum score threshold: 65 (configurable)

### Entry Execution
- Hard entry window: 14:30–15:10 IST
- No new entries after 15:10 regardless of signals
- Paper buy/short recorded: symbol, direction, entry price, qty (fixed ₹50,000 notional), AI rationale, timestamp

### Morning Exit (APScheduler)
- APScheduler job fires at 09:15 IST next trading day
- For each held position: fetch opening price, compute gap%, fetch first 5-min candle
- LLM receives: entry price, gap%, first 5-min direction, original rationale
- LLM output:
  ```json
  {
    "action": "exit_now" | "wait" | "exit_by_1000",
    "reason": "gap held, momentum continuing"
  }
  ```
- `exit_now`: exit at current price immediately
- `wait`: re-check every 5 min (max until 10:00 AM)
- `exit_by_1000`: schedule hard exit at 10:00 AM
- All exits paper-simulated, P&L calculated and logged

### Persistence
- Positions written to `backend/btst_positions.json` on every state change
- Loaded on startup — survives process restart
- Schema: `{positions: [...], date: "YYYY-MM-DD", state: "HOLDING"}`

### Config Schema (`BTST_CONFIG_SCHEMA`)
```python
{
  "scan_start": "14:30",
  "scan_end": "15:10",
  "max_positions": 3,
  "min_ai_score": 65,
  "notional_per_trade": 50000,
  "volume_ratio_min": 1.5,
  "adx_min": 20,
  "morning_exit_time": "09:15",
  "hard_exit_by": "10:00",
  "paper_mode": True,
}
```

### Config keys in `backend/app/config.py`
```python
btst_enabled: bool = True
btst_universe: str = "nifty50+sensex"
btst_max_positions: int = 3
btst_scan_start: str = "14:30"
btst_scan_end: str = "15:10"
```

### API Routes (added to `backend/app/api/trade.py`)
```
GET  /api/trade/btst-scanner/status    → {state, positions_count, last_scan}
GET  /api/trade/btst-scanner/stats     → {total_trades, win_rate, total_pnl, ...}
GET  /api/trade/btst-scanner/config    → BTST_CONFIG_SCHEMA values
PUT  /api/trade/btst-scanner/config    → update config keys
POST /api/trade/btst-scanner/start     → activate scanner
POST /api/trade/btst-scanner/stop      → deactivate (does not close positions)
GET  /api/trade/btst-scanner/stream    → SSE EventSource
```

### SSE Event Types
| Event | Payload |
|-------|---------|
| `signal` | `{symbol, direction, score, rationale, target_pct, risk_note, ts}` |
| `position_update` | `{symbol, direction, entry_price, current_pnl, state}` |
| `exit` | `{symbol, exit_price, pnl_pct, pnl_abs, reason, ts}` |
| `log` | `{message, level, ts}` |
| `stats` | `{total_pnl, win_rate, positions_held, ...}` |

---

## 4. Frontend Design

### Layout
Swing-style fixed right sidebar. `ScalpPanel.vue` wraps its existing content + `BtstPanel.vue` in a flex row:

```
ScalpPanel.vue  (display: flex; flex-direction: row)
┌─────────────────────────────┬──────────────────────┐
│  .scalp-main (existing)     │  BtstPanel.vue       │
│  grid: 1.6fr chart | 1fr    │  clamp(340px,24vw,   │
│  signals — unchanged        │  460px) fixed width  │
│                             │  flex-shrink: 0      │
│                             │  border-left         │
│                             │  overflow-y: auto    │
└─────────────────────────────┴──────────────────────┘
```

Reuses existing `.rs-panel` and `.rs-header` CSS classes from Swing sidebar (already in codebase). No new layout CSS needed beyond a wrapper flex row in ScalpPanel.

### UI Sections

**Header bar:** Scanner status pill (IDLE/SCANNING/HOLDING/MORNING_EXIT), Start/Stop buttons, Mode: Paper badge.

**Stats row:** Candidates scanned | Positions held | Total P&L

**Signals section:** Card list, sorted by AI score descending.
- Green left border = bullish
- Red left border = bearish
- Grey = rejected (score < threshold)
- Card shows: symbol, score badge, direction, entry price, rationale, target%, risk note

**Overnight Positions section:** Cards showing held positions with moon icon, entry price, current P&L (updated at open next day).

**Trade Log:** Reverse-chronological, latest 30 entries, same `.log-entry` pattern as ScalpPanel.

### Frontend API (`frontend/src/api/market/btst.js`)
```js
export const startBtst = () => axios.post('/api/trade/btst-scanner/start')
export const stopBtst = () => axios.post('/api/trade/btst-scanner/stop')
export const getBtstStatus = () => axios.get('/api/trade/btst-scanner/status')
export const getBtstStats = () => axios.get('/api/trade/btst-scanner/stats')
export const getBtstConfig = () => axios.get('/api/trade/btst-scanner/config')
export const updateBtstConfig = (cfg) => axios.put('/api/trade/btst-scanner/config', cfg)
export const createBtstStream = () => new EventSource('/api/trade/btst-scanner/stream')
```

---

## 5. Data Flow

```
14:30 IST
  │  APScheduler fires scan_cycle()
  │  Fetch OHLCV for all ~80 symbols
  │  Stage 1: filter → 10-15 candidates
  │  Stage 2: parallel LLM score all candidates
  │  SSE broadcast: signal events for each result
  │  Top 3 → paper entry → position_update SSE
  │
15:10 IST
  │  Entry window closes, state → HOLDING
  │  Positions persisted to btst_positions.json
  │
09:15 IST (next day)
  │  APScheduler fires morning_exit_check()
  │  For each position: fetch gap + 5-min candle
  │  LLM decides: exit_now / wait / exit_by_1000
  │  Execute paper exits, log P&L
  │  SSE broadcast: exit events
  │  State → IDLE
```

---

## 6. Error Handling

- LLM timeout per symbol: 10s, skip symbol on timeout (log warning)
- Market data fetch failure: skip symbol, log error
- Morning exit LLM failure: default to `exit_now` (conservative)
- Process restart mid-HOLDING: restore from `btst_positions.json`, resume HOLDING state
- Scan during market holiday: APScheduler detects no data, skips gracefully

---

## 7. Testing Plan

- Paper mode only — no live broker calls in initial version
- Manual trigger endpoint (POST `/api/trade/btst-scanner/trigger`) for testing outside market hours
- Verify SSE events flow to frontend correctly
- Verify position persistence across restart
- Verify morning exit fires correctly via APScheduler
- Back-test mode: replay historical dates (future enhancement, not in scope)
