# BTST Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated paper-trading BTST scanner as a fixed right-hand sidebar inside the Scalp panel, using AI scoring and APScheduler-driven lifecycle management.

**Architecture:** Separate `btst_scanner.py` engine (mirrors scalp_scanner.py patterns) with a two-stage pipeline: Stage 1 technical pre-filter (no LLM), Stage 2 parallel LLM scoring via `chat_json()`. `BtstPanel.vue` renders as a Swing-style fixed sidebar (`clamp(340px, 24vw, 460px)`) mounted unconditionally inside `ScalpPanel.vue`.

**Tech Stack:** Python (FastAPI, APScheduler, yfinance, pandas, pandas_ta), Vue 3 (Composition API), SSE (EventSource), `LLMClient.chat_json()`

## Global Constraints

- Paper trading only — no live broker calls anywhere in BTST code
- All times in IST (`Asia/Kolkata` timezone)
- LLM timeout per call: 10 seconds; on timeout skip symbol and log warning
- Morning exit LLM failure: default to `exit_now` (conservative fallback)
- Max 3 overnight positions (configurable via `BTST_MAX_POSITIONS`)
- Entry window hard-closes 15:10 IST — no entries after regardless of signals
- SSE event shape: every event has `type` and `ts` keys
- Frontend uses `service` (axios wrapper) from `../index`, not raw axios
- EventSource URL uses `import.meta.env.VITE_API_BASE_URL` prefix (see scalp.js pattern)
- Reuse existing `.rs-panel`, `.rs-header` CSS classes from Swing sidebar

---

## File Map

| File | Status | Responsibility |
|------|--------|----------------|
| `backend/app/config.py` | Modify | Add 5 BTST config fields to `Settings` class |
| `backend/app/engines/btst_scanner.py` | Create | Full BTST engine: state, SSE, Stage 1, Stage 2, entry, exit, APScheduler |
| `backend/app/api/trade.py` | Modify | Add 8 BTST routes |
| `frontend/src/api/market/btst.js` | Create | Frontend API calls + SSE factory |
| `frontend/src/components/panels/BtstPanel.vue` | Create | BTST sidebar UI component |
| `frontend/src/components/panels/ScalpPanel.vue` | Modify | Wrap in flex row, mount BtstPanel as sidebar |

---

### Task 1: Config Keys

**Files:**
- Modify: `backend/app/config.py` (after the `scalp_enabled` line, around line 141)
- Test: `backend/tests/test_btst_config.py`

**Interfaces:**
- Produces: `settings.btst_enabled`, `settings.btst_universe`, `settings.btst_max_positions`, `settings.btst_scan_start`, `settings.btst_scan_end` — all accessible via `from ..config import settings`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_btst_config.py
from app.config import Settings


def test_btst_config_defaults():
    s = Settings()
    assert s.btst_enabled is True
    assert s.btst_universe == "nifty50+sensex"
    assert s.btst_max_positions == 3
    assert s.btst_scan_start == "14:30"
    assert s.btst_scan_end == "15:10"
```

- [ ] **Step 2: Run test — verify it fails**

```bash
cd backend && uv run pytest tests/test_btst_config.py -v
```

Expected: `AttributeError: 'Settings' object has no attribute 'btst_enabled'`

- [ ] **Step 3: Add fields to Settings class**

Open `backend/app/config.py`. Find the line `scalp_enabled: bool = True` (~line 141). Add after it:

```python
    # ── BTST ─────────────────────────────────────────────────────────────────
    btst_enabled: bool = True
    btst_universe: str = "nifty50+sensex"
    btst_max_positions: int = 3
    btst_scan_start: str = "14:30"
    btst_scan_end: str = "15:10"
```

- [ ] **Step 4: Run test — verify it passes**

```bash
cd backend && uv run pytest tests/test_btst_config.py -v
```

Expected: `PASSED`

---

### Task 2: Engine Scaffold — State, SSE, Config Schema, Start/Stop

**Files:**
- Create: `backend/app/engines/btst_scanner.py`
- Test: `backend/tests/test_btst_engine.py`

**Interfaces:**
- Produces: `subscribe_sse() → queue.Queue`, `unsubscribe_sse(q)`, `_broadcast(event: dict)`, `get_state() → dict`, `get_stats() → dict`, `get_all_config() → dict`, `update_config(updates: dict) → dict`, `start()`, `stop()`
- Produces: `BTST_CONFIG_SCHEMA: dict`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_btst_engine.py
import queue
import pytest


def test_subscribe_sse_returns_queue():
    from app.engines import btst_scanner as svc
    q = svc.subscribe_sse()
    assert isinstance(q, queue.Queue)
    svc.unsubscribe_sse(q)


def test_broadcast_delivers_to_subscriber():
    from app.engines import btst_scanner as svc
    q = svc.subscribe_sse()
    svc._broadcast({'type': 'log', 'message': 'test', 'ts': '2026-01-01T00:00:00'})
    msg = q.get(timeout=1)
    import json
    data = json.loads(msg)
    assert data['type'] == 'log'
    svc.unsubscribe_sse(q)


def test_get_state_has_required_keys():
    from app.engines import btst_scanner as svc
    state = svc.get_state()
    assert 'status' in state
    assert 'positions' in state
    assert 'signals' in state


def test_update_config_roundtrip():
    from app.engines import btst_scanner as svc
    original = svc.get_all_config()['BTST_MAX_POSITIONS']
    svc.update_config({'BTST_MAX_POSITIONS': 5})
    assert svc.get_all_config()['BTST_MAX_POSITIONS'] == 5
    svc.update_config({'BTST_MAX_POSITIONS': original})
```

- [ ] **Step 2: Run test — verify it fails**

```bash
cd backend && uv run pytest tests/test_btst_engine.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.engines.btst_scanner'`

- [ ] **Step 3: Create engine scaffold**

Create `backend/app/engines/btst_scanner.py`:

```python
from __future__ import annotations

import json
import queue
import threading
from typing import Any

# ── Config Schema ─────────────────────────────────────────────────────────────

BTST_CONFIG_SCHEMA: dict[str, dict] = {
    'BTST_MAX_POSITIONS':     {'default': 3,      'type': int,   'label': 'Max Positions',          'group': 'Limits'},
    'BTST_MIN_AI_SCORE':      {'default': 65,     'type': int,   'label': 'Min AI Score',           'group': 'Filters'},
    'BTST_NOTIONAL_PER_TRADE':{'default': 50000,  'type': float, 'label': 'Notional per Trade (₹)', 'group': 'Trade'},
    'BTST_VOLUME_RATIO_MIN':  {'default': 1.5,    'type': float, 'label': 'Volume Ratio Min',       'group': 'Filters'},
    'BTST_ADX_MIN':           {'default': 20,     'type': float, 'label': 'ADX Min',                'group': 'Filters'},
    'BTST_SCAN_START':        {'default': '14:30', 'type': str,  'label': 'Scan Start (IST)',       'group': 'Schedule'},
    'BTST_SCAN_END':          {'default': '15:10', 'type': str,  'label': 'Scan End (IST)',         'group': 'Schedule'},
    'BTST_MORNING_EXIT_TIME': {'default': '09:15', 'type': str,  'label': 'Morning Exit Time (IST)','group': 'Schedule'},
    'BTST_HARD_EXIT_BY':      {'default': '10:00', 'type': str,  'label': 'Hard Exit By (IST)',     'group': 'Schedule'},
    'BTST_PAPER_MODE':        {'default': True,   'type': bool,  'label': 'Paper Mode',            'group': 'Trade'},
}

# ── Module State ──────────────────────────────────────────────────────────────

_config: dict[str, Any] = {k: v['default'] for k, v in BTST_CONFIG_SCHEMA.items()}
_config_lock = threading.Lock()

_state: dict[str, Any] = {
    'status': 'IDLE',       # IDLE | SCANNING | HOLDING | MORNING_EXIT
    'signals': [],           # scored candidates from last scan (all, incl. rejected)
    'positions': [],         # current overnight positions
    'last_scan_ts': None,
    'candidates_found': 0,
    'stats': {
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl_abs': 0.0,
        'total_pnl_pct': 0.0,
    },
}
_state_lock = threading.Lock()

_running = False
_running_lock = threading.Lock()

# ── SSE ──────────────────────────────────────────────────────────────────────

_sse_subscribers: list[queue.Queue] = []
_sse_lock = threading.Lock()


def subscribe_sse() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_subscribers.append(q)
        # Replay current state to new subscriber
        with _state_lock:
            for sig in _state['signals']:
                try:
                    q.put_nowait(json.dumps({'type': 'signal', '_replay': True, **sig}, default=str))
                except queue.Full:
                    break
            for pos in _state['positions']:
                try:
                    q.put_nowait(json.dumps({'type': 'position_update', '_replay': True, **pos}, default=str))
                except queue.Full:
                    break
    return q


def unsubscribe_sse(q: queue.Queue) -> None:
    with _sse_lock:
        try:
            _sse_subscribers.remove(q)
        except ValueError:
            pass


def _broadcast(event: dict) -> None:
    msg = json.dumps(event, default=str)
    with _sse_lock:
        dead: list[queue.Queue] = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _sse_subscribers.remove(q)
            except ValueError:
                pass

# ── Log helper ────────────────────────────────────────────────────────────────

import logging
_logger = logging.getLogger('btst_scanner')


def _log(message: str, level: str = 'info') -> None:
    getattr(_logger, level, _logger.info)(f'[btst] {message}')
    from datetime import datetime
    import pytz
    ts = datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()
    _broadcast({'type': 'log', 'message': message, 'level': level, 'ts': ts})

# ── Public API ────────────────────────────────────────────────────────────────

def get_state() -> dict:
    with _state_lock:
        return {
            'status': _state['status'],
            'positions_count': len(_state['positions']),
            'signals_count': len(_state['signals']),
            'last_scan_ts': _state['last_scan_ts'],
            'candidates_found': _state['candidates_found'],
            'positions': list(_state['positions']),
            'signals': list(_state['signals']),
        }


def get_stats() -> dict:
    with _state_lock:
        stats = dict(_state['stats'])
    total = stats['wins'] + stats['losses']
    stats['win_rate'] = round(stats['wins'] / total * 100, 1) if total else 0.0
    stats['total_pnl_abs'] = round(stats['total_pnl_abs'], 2)
    stats['total_pnl_pct'] = round(stats['total_pnl_pct'], 2)
    return stats


def get_all_config() -> dict:
    with _config_lock:
        return dict(_config)


def update_config(updates: dict) -> dict:
    valid = {k: v for k, v in updates.items() if k in BTST_CONFIG_SCHEMA}
    with _config_lock:
        for k, v in valid.items():
            _config[k] = BTST_CONFIG_SCHEMA[k]['type'](v)
    return get_all_config()


def start() -> None:
    global _running
    with _running_lock:
        _running = True
    _log('BTST scanner started')


def stop() -> None:
    global _running
    with _running_lock:
        _running = False
    _log('BTST scanner stopped')


def is_running() -> bool:
    with _running_lock:
        return _running
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd backend && uv run pytest tests/test_btst_engine.py -v
```

Expected: all 4 tests `PASSED`

---

### Task 3: Universe + Stage 1 Technical Pre-Filter

**Files:**
- Modify: `backend/app/engines/btst_scanner.py` (append to file)
- Test: `backend/tests/test_btst_engine.py` (append tests)

**Interfaces:**
- Produces: `NIFTY50_SENSEX_UNIVERSE: list[str]`, `BEARISH_FO_INSTRUMENTS: list[str]`
- Produces: `_fetch_ohlcv(symbol: str) → pd.DataFrame | None`
- Produces: `_stage1_filter(symbol: str, df: pd.DataFrame, direction: str) → bool`
- Produces: `_run_stage1() → tuple[list[str], list[str]]` — (bull_candidates, bear_candidates)

- [ ] **Step 1: Check pandas_ta is available**

```bash
cd backend && uv run python -c "import pandas_ta; print('ok')"
```

If `ModuleNotFoundError`: run `uv add pandas_ta` then re-run.

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_btst_engine.py`:

```python
import pandas as pd
import numpy as np


def _make_mock_df(close_in_top_range=True, high_volume=True, trending=True) -> pd.DataFrame:
    """Build a 25-row OHLCV DataFrame with controllable characteristics."""
    n = 25
    base = 1000.0
    dates = pd.date_range('2026-01-01', periods=n, freq='B')
    closes = [base + i * 2 for i in range(n)]
    df = pd.DataFrame({
        'Open':   [c - 5 for c in closes],
        'High':   [c + 10 for c in closes],
        'Low':    [c - 10 for c in closes],
        'Close':  closes,
        'Volume': [1_000_000 * (2.0 if high_volume else 0.5)] * n,
    }, index=dates)

    if close_in_top_range:
        # Close near day high (bullish: top 10% of range)
        df.loc[df.index[-1], 'Close'] = df.loc[df.index[-1], 'High'] - 1.0
    else:
        # Close near day low (bearish signal)
        df.loc[df.index[-1], 'Close'] = df.loc[df.index[-1], 'Low'] + 1.0

    if not trending:
        # Flat ADX (no trend) — zigzag closes
        for i in range(n):
            df.iloc[i, df.columns.get_loc('Close')] = base + (5 if i % 2 == 0 else -5)

    return df


def test_stage1_filter_bullish_pass():
    from app.engines import btst_scanner as svc
    df = _make_mock_df(close_in_top_range=True, high_volume=True, trending=True)
    # Ensure last close is near 20-day high
    df.loc[df.index[-1], 'Close'] = df['High'].iloc[-21:-1].max() * 0.997
    df.loc[df.index[-1], 'High'] = df.loc[df.index[-1], 'Close'] + 2
    result = svc._stage1_filter('TEST.NS', df, 'bull')
    assert result is True


def test_stage1_filter_low_volume_fails():
    from app.engines import btst_scanner as svc
    df = _make_mock_df(close_in_top_range=True, high_volume=False, trending=True)
    result = svc._stage1_filter('TEST.NS', df, 'bull')
    assert result is False


def test_stage1_filter_insufficient_data_fails():
    from app.engines import btst_scanner as svc
    df = pd.DataFrame({'Open': [1], 'High': [2], 'Low': [0], 'Close': [1], 'Volume': [100]})
    result = svc._stage1_filter('TEST.NS', df, 'bull')
    assert result is False
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
cd backend && uv run pytest tests/test_btst_engine.py::test_stage1_filter_bullish_pass -v
```

Expected: `AttributeError: module has no attribute '_stage1_filter'`

- [ ] **Step 4: Append universe constants + Stage 1 implementation to btst_scanner.py**

```python
# ── Universe ──────────────────────────────────────────────────────────────────

NIFTY50_SENSEX_UNIVERSE: list[str] = [
    # Nifty 50
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
    "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS", "SUNPHARMA.NS",
    "TITAN.NS", "BAJFINANCE.NS", "HCLTECH.NS", "WIPRO.NS", "NTPC.NS",
    "NESTLEIND.NS", "JSWSTEEL.NS", "POWERGRID.NS", "ULTRACEMCO.NS", "TECHM.NS",
    "TATAMOTORS.NS", "HINDALCO.NS", "ONGC.NS", "TATASTEEL.NS", "ADANIENT.NS",
    "GRASIM.NS", "CIPLA.NS", "DRREDDY.NS", "BAJAJFINSV.NS", "INDUSINDBK.NS",
    "COALINDIA.NS", "DIVISLAB.NS", "HEROMOTOCO.NS", "M&M.NS", "EICHERMOT.NS",
    "TATACONSUM.NS", "SBILIFE.NS", "HDFCLIFE.NS", "APOLLOHOSP.NS", "BPCL.NS",
    "BRITANNIA.NS", "ADANIPORTS.NS", "SHRIRAMFIN.NS", "BEL.NS", "BAJAJ-AUTO.NS",
    # Additional Sensex-only stocks
    "BAJAJHFL.NS", "ETERNAL.NS",
]

# Bearish F&O: always pass Stage 1, go straight to LLM
BEARISH_FO_INSTRUMENTS: list[str] = ["^NSEI", "^BSESN"]


# ── Stage 1: OHLCV Fetch + Technical Pre-filter ───────────────────────────────

import pandas as pd


def _fetch_ohlcv(symbol: str) -> 'pd.DataFrame | None':
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(period='25d', interval='1d')
        if df is None or df.empty or len(df) < 21:
            return None
        return df
    except Exception as e:
        _log(f'OHLCV fetch failed for {symbol}: {e}', 'warn')
        return None


def _stage1_filter(symbol: str, df: 'pd.DataFrame', direction: str) -> bool:
    if df is None or len(df) < 21:
        return False

    try:
        import pandas_ta as ta  # type: ignore

        # ADX check — trend must be present
        adx_df = df.ta.adx(length=14)
        if adx_df is None or adx_df.empty:
            return False
        adx_col = [c for c in adx_df.columns if c.startswith('ADX_')]
        if not adx_col:
            return False
        adx = float(adx_df[adx_col[0]].iloc[-1])
        if pd.isna(adx) or adx < _config['BTST_ADX_MIN']:
            return False

        # Volume ratio check
        avg_vol = df['Volume'].iloc[-21:-1].mean()
        today_vol = float(df['Volume'].iloc[-1])
        if avg_vol <= 0 or (today_vol / avg_vol) < _config['BTST_VOLUME_RATIO_MIN']:
            return False

        # Day range position
        day_high = float(df['High'].iloc[-1])
        day_low = float(df['Low'].iloc[-1])
        close = float(df['Close'].iloc[-1])
        day_range = day_high - day_low
        if day_range <= 0:
            return False
        range_position = (close - day_low) / day_range

        # 20-day high/low
        rolling_high = float(df['High'].iloc[-21:-1].max())
        rolling_low = float(df['Low'].iloc[-21:-1].min())

        if direction == 'bull':
            if range_position < 0.90:
                return False
            if close < rolling_high * 0.995:
                return False
        else:  # bear
            if range_position > 0.10:
                return False
            if close > rolling_low * 1.005:
                return False

        return True

    except Exception as e:
        _log(f'Stage 1 filter error for {symbol}: {e}', 'warn')
        return False


def _run_stage1() -> tuple[list[str], list[str]]:
    """Returns (bull_candidates, bear_candidates)."""
    bull: list[str] = []
    for symbol in NIFTY50_SENSEX_UNIVERSE:
        df = _fetch_ohlcv(symbol)
        if df is not None and _stage1_filter(symbol, df, 'bull'):
            bull.append(symbol)
    # Bear: F&O instruments always pass Stage 1
    bear = list(BEARISH_FO_INSTRUMENTS)
    with _state_lock:
        _state['candidates_found'] = len(bull)
    _log(f'Stage 1 complete: {len(bull)} bull candidates, {len(bear)} bear instruments')
    return bull, bear
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd backend && uv run pytest tests/test_btst_engine.py -v
```

Expected: all tests `PASSED`

---

### Task 4: Stage 2 — LLM Scoring (Parallel)

**Files:**
- Modify: `backend/app/engines/btst_scanner.py` (append)
- Test: `backend/tests/test_btst_engine.py` (append)

**Interfaces:**
- Consumes: `LLMClient` (from `..infrastructure.llm.client`), `llm_client.chat_json(messages, temperature, max_tokens) → dict`
- Produces: `_build_score_prompt(symbol, df, direction, nifty_chg, sensex_chg) → str`
- Produces: `_score_candidate(symbol, df, direction, llm_client) → dict | None`
- Produces: `_run_stage2(bull_candidates, bear_instruments, llm_client) → list[dict]` — sorted by score desc

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_btst_engine.py`:

```python
from unittest.mock import MagicMock, patch


def _make_llm_client(score: int = 80):
    client = MagicMock()
    client.chat_json.return_value = {
        'score': score,
        'direction': 'bull',
        'rationale': 'Strong breakout on high volume',
        'target_pct': 1.8,
        'risk_note': 'Gap-down risk if market weak',
    }
    return client


def test_build_score_prompt_contains_symbol():
    from app.engines import btst_scanner as svc
    df = _make_mock_df()
    prompt = svc._build_score_prompt('RELIANCE.NS', df, 'bull', 0.5, 0.4)
    assert 'RELIANCE.NS' in prompt
    assert 'bull' in prompt.lower()


def test_score_candidate_above_threshold():
    from app.engines import btst_scanner as svc
    df = _make_mock_df()
    llm = _make_llm_client(score=80)
    result = svc._score_candidate('RELIANCE.NS', df, 'bull', llm)
    assert result is not None
    assert result['score'] == 80
    assert result['rejected'] is False


def test_score_candidate_below_threshold_marked_rejected():
    from app.engines import btst_scanner as svc
    df = _make_mock_df()
    llm = _make_llm_client(score=50)
    result = svc._score_candidate('RELIANCE.NS', df, 'bull', llm)
    assert result is not None
    assert result['rejected'] is True


def test_run_stage2_sorts_by_score():
    from app.engines import btst_scanner as svc
    df = _make_mock_df()
    llm = MagicMock()
    scores = [70, 90, 60]
    call_count = {'n': 0}
    def side_effect(**kwargs):
        s = scores[call_count['n'] % len(scores)]
        call_count['n'] += 1
        return {'score': s, 'direction': 'bull', 'rationale': 'test', 'target_pct': 1.0, 'risk_note': ''}
    llm.chat_json.side_effect = side_effect

    with patch.object(svc, '_fetch_ohlcv', return_value=df):
        results = svc._run_stage2(['A.NS', 'B.NS', 'C.NS'], [], llm)

    accepted = [r for r in results if not r['rejected']]
    scores_out = [r['score'] for r in accepted]
    assert scores_out == sorted(scores_out, reverse=True)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd backend && uv run pytest tests/test_btst_engine.py::test_build_score_prompt_contains_symbol -v
```

Expected: `AttributeError: module has no attribute '_build_score_prompt'`

- [ ] **Step 3: Append Stage 2 implementation to btst_scanner.py**

```python
# ── Stage 2: LLM Scoring ──────────────────────────────────────────────────────

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')


def _now_ist() -> datetime:
    return datetime.now(IST)


def _build_score_prompt(
    symbol: str,
    df: 'pd.DataFrame',
    direction: str,
    nifty_chg: float,
    sensex_chg: float,
) -> str:
    recent = df.tail(5)[['Open', 'High', 'Low', 'Close', 'Volume']].round(2)
    ohlcv_table = recent.to_string()
    close = float(df['Close'].iloc[-1])
    day_high = float(df['High'].iloc[-1])
    day_low = float(df['Low'].iloc[-1])
    avg_vol = float(df['Volume'].iloc[-21:-1].mean())
    today_vol = float(df['Volume'].iloc[-1])
    vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0.0

    return (
        f"Symbol: {symbol}\n"
        f"Evaluate for BTST {'BUY' if direction == 'bull' else 'SHORT'} — hold overnight, exit next morning.\n"
        f"Current close: ₹{close:.2f}  Day range: ₹{day_low:.2f}–₹{day_high:.2f}\n"
        f"Volume ratio vs 20-day avg: {vol_ratio:.1f}x\n"
        f"Market today: Nifty {nifty_chg:+.2f}%  Sensex {sensex_chg:+.2f}%\n"
        f"Last 5 trading days OHLCV:\n{ohlcv_table}\n\n"
        f"Score 0–100 for overnight BTST {direction} potential. "
        f"Score ≥65 = viable. Consider gap-up/down risk, momentum, and market breadth.\n"
        f"Respond ONLY with JSON matching this exact schema:\n"
        f'{{"score": <int 0-100>, "direction": "{direction}", '
        f'"rationale": "<one sentence>", "target_pct": <float>, "risk_note": "<one sentence>"}}'
    )


def _get_market_context() -> tuple[float, float]:
    """Fetch today's Nifty and Sensex % change for prompt context."""
    try:
        import yfinance as yf
        nifty = yf.Ticker('^NSEI').history(period='2d', interval='1d')
        sensex = yf.Ticker('^BSESN').history(period='2d', interval='1d')
        nifty_chg = float((nifty['Close'].iloc[-1] - nifty['Close'].iloc[-2]) / nifty['Close'].iloc[-2] * 100)
        sensex_chg = float((sensex['Close'].iloc[-1] - sensex['Close'].iloc[-2]) / sensex['Close'].iloc[-2] * 100)
        return nifty_chg, sensex_chg
    except Exception:
        return 0.0, 0.0


def _score_candidate(
    symbol: str,
    df: 'pd.DataFrame',
    direction: str,
    llm_client: Any,
) -> 'dict | None':
    try:
        nifty_chg, sensex_chg = _get_market_context()
        prompt = _build_score_prompt(symbol, df, direction, nifty_chg, sensex_chg)
        result = llm_client.chat_json(
            messages=[
                {
                    'role': 'system',
                    'content': (
                        'You are a BTST trading analyst for Indian equity markets. '
                        'Respond only with valid JSON matching the requested schema.'
                    ),
                },
                {'role': 'user', 'content': prompt},
            ],
            temperature=0.2,
            max_tokens=200,
        )
        if not isinstance(result, dict):
            return None
        required = {'score', 'direction', 'rationale', 'target_pct', 'risk_note'}
        if not required.issubset(result.keys()):
            return None
        score = int(result['score'])
        rejected = score < int(_config['BTST_MIN_AI_SCORE'])
        ts = _now_ist().isoformat()
        return {
            'symbol': symbol,
            'direction': direction,
            'score': score,
            'rejected': rejected,
            'rationale': str(result.get('rationale', '')),
            'target_pct': float(result.get('target_pct', 0.0)),
            'risk_note': str(result.get('risk_note', '')),
            'ts': ts,
        }
    except Exception as e:
        _log(f'LLM scoring failed for {symbol}: {e}', 'warn')
        return None


def _run_stage2(
    bull_candidates: list[str],
    bear_instruments: list[str],
    llm_client: Any,
) -> list[dict]:
    """Score all candidates in parallel. Returns full list sorted by score desc."""
    tasks: list[tuple[str, str]] = (
        [(sym, 'bull') for sym in bull_candidates]
        + [(sym, 'bear') for sym in bear_instruments]
    )
    results: list[dict] = []

    def _score_task(symbol: str, direction: str) -> 'dict | None':
        df = _fetch_ohlcv(symbol)
        if df is None:
            _log(f'No OHLCV data for {symbol}, skipping', 'warn')
            return None
        return _score_candidate(symbol, df, direction, llm_client)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_score_task, sym, direction): (sym, direction) for sym, direction in tasks}
        for future in as_completed(futures, timeout=120):
            sym, direction = futures[future]
            try:
                result = future.result(timeout=15)
                if result:
                    results.append(result)
                    _broadcast({'type': 'signal', **result})
            except Exception as e:
                _log(f'Scoring task failed for {sym}/{direction}: {e}', 'warn')

    with _state_lock:
        _state['signals'] = results

    return sorted(results, key=lambda x: x['score'], reverse=True)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd backend && uv run pytest tests/test_btst_engine.py -v
```

Expected: all tests `PASSED`

---

### Task 5: Entry Execution + Persistence

**Files:**
- Modify: `backend/app/engines/btst_scanner.py` (append)
- Test: `backend/tests/test_btst_engine.py` (append)

**Interfaces:**
- Produces: `_enter_paper_position(signal: dict) → dict` — the created position record
- Produces: `_save_positions()` — writes `backend/btst_positions.json`
- Produces: `_load_positions()` — reads and restores state from `backend/btst_positions.json`
- Produces: `trigger_now()` — manual trigger for testing outside market hours

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_btst_engine.py`:

```python
import os
import tempfile


def test_enter_paper_position_records_position():
    from app.engines import btst_scanner as svc
    with _state_lock_ctx(svc):
        svc._state['positions'] = []

    signal = {
        'symbol': 'RELIANCE.NS',
        'direction': 'bull',
        'score': 85,
        'rejected': False,
        'rationale': 'Strong breakout',
        'target_pct': 1.8,
        'risk_note': 'Gap-down risk',
        'ts': '2026-06-22T14:35:00+05:30',
    }
    pos = svc._enter_paper_position(signal)
    assert pos['symbol'] == 'RELIANCE.NS'
    assert pos['direction'] == 'bull'
    assert 'entry_price' in pos
    assert 'qty' in pos
    assert pos['status'] == 'HOLDING'


def test_save_and_load_positions(tmp_path, monkeypatch):
    from app.engines import btst_scanner as svc
    positions_file = tmp_path / 'btst_positions.json'
    monkeypatch.setattr(svc, 'POSITIONS_FILE', str(positions_file))

    with svc._state_lock:
        svc._state['positions'] = [{
            'symbol': 'TCS.NS', 'direction': 'bull', 'entry_price': 3500.0,
            'qty': 14, 'status': 'HOLDING', 'rationale': 'test',
            'ts': '2026-06-22T14:40:00+05:30',
        }]
        svc._state['status'] = 'HOLDING'

    svc._save_positions()
    assert positions_file.exists()

    # Reset state and reload
    with svc._state_lock:
        svc._state['positions'] = []
        svc._state['status'] = 'IDLE'

    svc._load_positions()
    with svc._state_lock:
        assert len(svc._state['positions']) == 1
        assert svc._state['positions'][0]['symbol'] == 'TCS.NS'


# Helper context manager for tests that mutate _state
import contextlib

@contextlib.contextmanager
def _state_lock_ctx(svc):
    with svc._state_lock:
        yield
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd backend && uv run pytest tests/test_btst_engine.py::test_enter_paper_position_records_position -v
```

Expected: `AttributeError: module has no attribute '_enter_paper_position'`

- [ ] **Step 3: Append entry + persistence implementation**

```python
# ── Entry Execution + Persistence ─────────────────────────────────────────────

import os
import pathlib

POSITIONS_FILE = str(pathlib.Path(__file__).parent.parent.parent / 'btst_positions.json')


def _get_current_price(symbol: str) -> float:
    """Fetch last close price for paper entry."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period='1d', interval='1d')
        if hist.empty:
            return 0.0
        return float(hist['Close'].iloc[-1])
    except Exception:
        return 0.0


def _enter_paper_position(signal: dict) -> dict:
    entry_price = _get_current_price(signal['symbol'])
    notional = float(_config['BTST_NOTIONAL_PER_TRADE'])
    qty = max(1, int(notional // entry_price)) if entry_price > 0 else 1
    position = {
        'symbol': signal['symbol'],
        'direction': signal['direction'],
        'entry_price': entry_price,
        'qty': qty,
        'notional': round(entry_price * qty, 2),
        'rationale': signal.get('rationale', ''),
        'target_pct': signal.get('target_pct', 0.0),
        'risk_note': signal.get('risk_note', ''),
        'ai_score': signal.get('score', 0),
        'status': 'HOLDING',
        'ts': _now_ist().isoformat(),
        'exit_price': None,
        'exit_ts': None,
        'pnl_pct': None,
        'pnl_abs': None,
        'exit_reason': None,
    }
    with _state_lock:
        _state['positions'].append(position)
    _broadcast({'type': 'position_update', **position})
    _log(f"Paper entry: {position['direction'].upper()} {position['symbol']} @ ₹{entry_price:.2f} qty={qty}")
    return position


def _save_positions() -> None:
    try:
        with _state_lock:
            data = {
                'status': _state['status'],
                'positions': list(_state['positions']),
                'date': _now_ist().strftime('%Y-%m-%d'),
            }
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(data, f, default=str, indent=2)
    except Exception as e:
        _log(f'Failed to save positions: {e}', 'error')


def _load_positions() -> None:
    if not os.path.exists(POSITIONS_FILE):
        return
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        today = _now_ist().strftime('%Y-%m-%d')
        if data.get('date') != today:
            _log('Positions file is from a previous day — skipping restore')
            return
        with _state_lock:
            _state['positions'] = data.get('positions', [])
            saved_status = data.get('status', 'IDLE')
            # Only restore HOLDING state; don't restore terminal states
            if saved_status in ('HOLDING', 'MORNING_EXIT'):
                _state['status'] = saved_status
        _log(f"Restored {len(_state['positions'])} positions from disk (state={saved_status})")
    except Exception as e:
        _log(f'Failed to load positions: {e}', 'error')


def trigger_now() -> None:
    """Manual trigger for testing outside market hours."""
    _log('Manual trigger fired — starting scan cycle')
    import threading
    t = threading.Thread(target=_scan_cycle, daemon=True)
    t.start()
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd backend && uv run pytest tests/test_btst_engine.py -v
```

Expected: all tests `PASSED`

---

### Task 6: APScheduler Jobs + Scan Cycle + Morning Exit

**Files:**
- Modify: `backend/app/engines/btst_scanner.py` (append)
- Test: `backend/tests/test_btst_engine.py` (append)

**Interfaces:**
- Consumes: `_run_stage1()`, `_run_stage2()`, `_enter_paper_position()`, `_save_positions()`, `_load_positions()`
- Produces: `_scan_cycle()` — full afternoon scan orchestration
- Produces: `_close_entry_window()` — transitions SCANNING → HOLDING
- Produces: `_build_exit_prompt(pos, gap_pct, candle) → str`
- Produces: `_exit_position(pos, llm_client)` — calls LLM, records exit P&L
- Produces: `_morning_exit_check()` — iterates positions, calls _exit_position for each

- [ ] **Step 1: Check APScheduler is available**

```bash
cd backend && uv run python -c "from apscheduler.schedulers.background import BackgroundScheduler; print('ok')"
```

If `ModuleNotFoundError`: run `uv add apscheduler` then re-run.

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_btst_engine.py`:

```python
def test_close_entry_window_sets_holding():
    from app.engines import btst_scanner as svc
    with svc._state_lock:
        svc._state['status'] = 'SCANNING'
        svc._state['positions'] = [{'symbol': 'TEST.NS'}]
    svc._close_entry_window()
    with svc._state_lock:
        assert svc._state['status'] == 'HOLDING'


def test_exit_position_records_pnl():
    from app.engines import btst_scanner as svc
    pos = {
        'symbol': 'RELIANCE.NS',
        'direction': 'bull',
        'entry_price': 2400.0,
        'qty': 20,
        'notional': 48000.0,
        'rationale': 'Breakout',
        'status': 'HOLDING',
        'ts': '2026-06-22T14:40:00',
        'exit_price': None,
        'pnl_pct': None,
        'pnl_abs': None,
        'exit_reason': None,
    }
    llm = MagicMock()
    llm.chat_json.return_value = {'action': 'exit_now', 'reason': 'Gap held'}

    with svc._state_lock:
        svc._state['positions'] = [pos]
        svc._state['stats'] = {
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'total_pnl_abs': 0.0, 'total_pnl_pct': 0.0,
        }

    candle = {'open': 2450.0, 'high': 2460.0, 'low': 2440.0, 'close': 2455.0}
    with patch.object(svc, '_get_current_price', return_value=2450.0):
        svc._exit_position(pos, llm, gap_pct=2.08, candle=candle)

    with svc._state_lock:
        updated = next(p for p in svc._state['positions'] if p['symbol'] == 'RELIANCE.NS')
    assert updated['exit_price'] == 2450.0
    assert updated['status'] == 'EXITED'
    assert updated['pnl_pct'] is not None
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
cd backend && uv run pytest tests/test_btst_engine.py::test_close_entry_window_sets_holding -v
```

Expected: `AttributeError: module has no attribute '_close_entry_window'`

- [ ] **Step 4: Append scan cycle + morning exit implementation**

```python
# ── Scan Cycle ────────────────────────────────────────────────────────────────

def _scan_cycle() -> None:
    """Afternoon scan: Stage 1 → Stage 2 → entries. Runs at 14:30 IST."""
    if not is_running():
        return

    with _state_lock:
        if _state['status'] not in ('IDLE', 'SCANNING'):
            _log(f"Scan skipped — state is {_state['status']}")
            return
        _state['status'] = 'SCANNING'

    _log('Starting afternoon scan cycle')
    _broadcast({'type': 'stats', **get_stats(), 'ts': _now_ist().isoformat()})

    try:
        from ..config import settings as _cfg
        from ..infrastructure.llm.client import LLMClient
        llm_client = LLMClient.from_settings(_cfg)

        bull_candidates, bear_instruments = _run_stage1()
        all_scored = _run_stage2(bull_candidates, bear_instruments, llm_client)

        accepted = [s for s in all_scored if not s['rejected']]
        max_pos = int(_config['BTST_MAX_POSITIONS'])

        with _state_lock:
            existing_count = len(_state['positions'])

        for signal in accepted[:max(0, max_pos - existing_count)]:
            _enter_paper_position(signal)

        with _state_lock:
            _state['last_scan_ts'] = _now_ist().isoformat()

        _log(f'Scan complete: {len(accepted)} accepted, {len(_state["positions"])} positions taken')
        _broadcast({'type': 'stats', **get_stats(), 'ts': _now_ist().isoformat()})

    except Exception as e:
        _log(f'Scan cycle error: {e}', 'error')
        with _state_lock:
            _state['status'] = 'IDLE'


def _close_entry_window() -> None:
    """Closes entry window at 15:10 IST. Transitions SCANNING → HOLDING."""
    with _state_lock:
        if _state['status'] == 'SCANNING':
            _state['status'] = 'HOLDING'
    _save_positions()
    _log(f"Entry window closed. {len(_state['positions'])} positions held overnight.")
    _broadcast({'type': 'stats', **get_stats(), 'ts': _now_ist().isoformat()})


# ── Morning Exit ───────────────────────────────────────────────────────────────

def _fetch_opening_candle(symbol: str) -> 'dict | None':
    """Fetch first 5-min candle of the day."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period='1d', interval='5m')
        if hist.empty:
            return None
        first = hist.iloc[0]
        return {
            'open': float(first['Open']),
            'high': float(first['High']),
            'low': float(first['Low']),
            'close': float(first['Close']),
        }
    except Exception as e:
        _log(f'Opening candle fetch failed for {symbol}: {e}', 'warn')
        return None


def _build_exit_prompt(pos: dict, gap_pct: float, candle: 'dict | None') -> str:
    candle_str = (
        f"O={candle['open']:.2f} H={candle['high']:.2f} L={candle['low']:.2f} C={candle['close']:.2f}"
        if candle else 'unavailable'
    )
    entry = float(pos['entry_price'])
    current = _get_current_price(pos['symbol'])
    pnl_pct = ((current - entry) / entry * 100) if pos['direction'] == 'bull' else ((entry - current) / entry * 100)
    return (
        f"BTST exit decision for {pos['symbol']} ({pos['direction'].upper()})\n"
        f"Entry: ₹{entry:.2f}  Current: ₹{current:.2f}  P&L: {pnl_pct:+.2f}%\n"
        f"Gap at open: {gap_pct:+.2f}%\n"
        f"First 5-min candle: {candle_str}\n"
        f"Original rationale: {pos.get('rationale', 'N/A')}\n\n"
        f"Decide whether to exit now, wait 5 more minutes, or schedule hard exit at 10:00.\n"
        f"Respond ONLY with JSON: "
        f'{{"action": "<exit_now|wait|exit_by_1000>", "reason": "<one sentence>"}}'
    )


def _exit_position(pos: dict, llm_client: Any, gap_pct: float = 0.0, candle: 'dict | None' = None) -> None:
    try:
        prompt = _build_exit_prompt(pos, gap_pct, candle)
        result = llm_client.chat_json(
            messages=[
                {
                    'role': 'system',
                    'content': 'You are a BTST exit analyst. Respond only with valid JSON.',
                },
                {'role': 'user', 'content': prompt},
            ],
            temperature=0.1,
            max_tokens=100,
        )
        action = result.get('action', 'exit_now') if isinstance(result, dict) else 'exit_now'
        reason = result.get('reason', 'LLM decision') if isinstance(result, dict) else 'fallback exit'
    except Exception as e:
        _log(f'Exit LLM failed for {pos["symbol"]}: {e} — defaulting to exit_now', 'warn')
        action = 'exit_now'
        reason = 'LLM error — conservative exit'

    if action == 'exit_now':
        _record_exit(pos, reason)
    elif action == 'wait':
        _schedule_recheck(pos, llm_client, retry_count=0)
    else:  # exit_by_1000
        _log(f"{pos['symbol']}: scheduled hard exit at 10:00 — {reason}")
        import threading
        now = _now_ist()
        hard_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
        delay = max(0, (hard_time - now).total_seconds())
        t = threading.Timer(delay, _record_exit, args=(pos, 'hard exit at 10:00'))
        t.daemon = True
        t.start()


def _record_exit(pos: dict, reason: str) -> None:
    exit_price = _get_current_price(pos['symbol'])
    entry = float(pos['entry_price'])
    qty = int(pos.get('qty', 1))
    if pos['direction'] == 'bull':
        pnl_pct = (exit_price - entry) / entry * 100
    else:
        pnl_pct = (entry - exit_price) / entry * 100
    pnl_abs = round(pnl_pct / 100 * entry * qty, 2)
    pnl_pct = round(pnl_pct, 2)

    with _state_lock:
        for p in _state['positions']:
            if p['symbol'] == pos['symbol'] and p['status'] == 'HOLDING':
                p['exit_price'] = exit_price
                p['exit_ts'] = _now_ist().isoformat()
                p['pnl_pct'] = pnl_pct
                p['pnl_abs'] = pnl_abs
                p['exit_reason'] = reason
                p['status'] = 'EXITED'
                break
        stats = _state['stats']
        stats['total_trades'] += 1
        stats['total_pnl_abs'] += pnl_abs
        stats['total_pnl_pct'] += pnl_pct
        if pnl_pct >= 0:
            stats['wins'] += 1
        else:
            stats['losses'] += 1

    _broadcast({'type': 'exit', 'symbol': pos['symbol'], 'exit_price': exit_price,
                'pnl_pct': pnl_pct, 'pnl_abs': pnl_abs, 'reason': reason,
                'ts': _now_ist().isoformat()})
    _log(f"Exit: {pos['symbol']} @ ₹{exit_price:.2f} P&L={pnl_pct:+.2f}% ({reason})")
    _save_positions()
    _broadcast({'type': 'stats', **get_stats(), 'ts': _now_ist().isoformat()})


def _schedule_recheck(pos: dict, llm_client: Any, retry_count: int) -> None:
    import threading
    now = _now_ist()
    hard_exit_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= hard_exit_time or retry_count > 8:
        _record_exit(pos, 'hard exit — max retries or 10:00 AM reached')
        return
    _log(f"{pos['symbol']}: waiting 5 min before re-evaluating exit (retry {retry_count + 1})")

    def recheck():
        candle = _fetch_opening_candle(pos['symbol'])
        entry = float(pos['entry_price'])
        current = _get_current_price(pos['symbol'])
        gap_pct = (current - entry) / entry * 100 if pos['direction'] == 'bull' else (entry - current) / entry * 100
        _exit_position(pos, llm_client, gap_pct=gap_pct, candle=candle)

    t = threading.Timer(300, recheck)
    t.daemon = True
    t.start()


def _morning_exit_check() -> None:
    """Fires at 09:15 IST. Evaluates and exits each held position."""
    with _state_lock:
        if _state['status'] != 'HOLDING':
            return
        _state['status'] = 'MORNING_EXIT'
        positions_to_exit = [p for p in _state['positions'] if p.get('status') == 'HOLDING']

    if not positions_to_exit:
        with _state_lock:
            _state['status'] = 'IDLE'
        return

    _log(f'Morning exit check: evaluating {len(positions_to_exit)} positions')

    try:
        from ..config import settings as _cfg
        from ..infrastructure.llm.client import LLMClient
        llm_client = LLMClient.from_settings(_cfg)
    except Exception as e:
        _log(f'LLM init failed at morning exit: {e} — force-exiting all', 'error')
        for pos in positions_to_exit:
            _record_exit(pos, 'LLM unavailable — conservative exit')
        with _state_lock:
            _state['status'] = 'IDLE'
        return

    for pos in positions_to_exit:
        try:
            candle = _fetch_opening_candle(pos['symbol'])
            entry = float(pos['entry_price'])
            current = _get_current_price(pos['symbol'])
            if pos['direction'] == 'bull':
                gap_pct = (current - entry) / entry * 100
            else:
                gap_pct = (entry - current) / entry * 100
            _exit_position(pos, llm_client, gap_pct=gap_pct, candle=candle)
        except Exception as e:
            _log(f'Exit failed for {pos["symbol"]}: {e} — force-exiting', 'error')
            _record_exit(pos, f'error during exit: {e}')

    # Check if all exited synchronously; if waits are scheduled, status updates later
    with _state_lock:
        all_exited = all(p.get('status') == 'EXITED' for p in _state['positions'])
        if all_exited:
            _state['status'] = 'IDLE'


# ── APScheduler Setup ─────────────────────────────────────────────────────────

def _init_scheduler() -> None:
    """Initialize APScheduler with BTST lifecycle jobs."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BackgroundScheduler(timezone='Asia/Kolkata')
        scheduler.add_job(
            _scan_cycle,
            CronTrigger(hour=14, minute=30, timezone='Asia/Kolkata'),
            id='btst_scan_cycle',
            replace_existing=True,
        )
        scheduler.add_job(
            _close_entry_window,
            CronTrigger(hour=15, minute=10, timezone='Asia/Kolkata'),
            id='btst_close_entry',
            replace_existing=True,
        )
        scheduler.add_job(
            _morning_exit_check,
            CronTrigger(hour=9, minute=15, timezone='Asia/Kolkata'),
            id='btst_morning_exit',
            replace_existing=True,
        )
        scheduler.start()
        _log('APScheduler started: scan@14:30, close@15:10, exit@09:15')
    except Exception as e:
        _log(f'APScheduler init failed: {e}', 'error')


# ── Module Init ───────────────────────────────────────────────────────────────

_load_positions()
_init_scheduler()
```

- [ ] **Step 5: Run all engine tests**

```bash
cd backend && uv run pytest tests/test_btst_engine.py -v
```

Expected: all tests `PASSED`

---

### Task 7: API Routes

**Files:**
- Modify: `backend/app/api/trade.py` (append after existing scalp routes, around line 540)
- Test: manual `curl` — no new pytest file needed (integration tested via frontend)

**Interfaces:**
- Consumes: `btst_scanner.subscribe_sse()`, `btst_scanner.unsubscribe_sse()`, `btst_scanner.get_state()`, etc.
- Produces: 8 REST endpoints under `/api/trade/btst-scanner/*`

- [ ] **Step 1: Add import + routes to trade.py**

Open `backend/app/api/trade.py`. After the existing `from ..engines import scalp_scanner as _scalp_svc` import line (~line 449), add:

```python
from ..engines import btst_scanner as _btst_svc
```

Then, after the last scalp route (`scalp_reset_killswitch`), append:

```python
# ── BTST Scanner Routes ───────────────────────────────────────────────────────

@router.get('/btst-scanner/status')
def btst_scanner_status():
    return {'success': True, 'data': _btst_svc.get_state()}


@router.get('/btst-scanner/stats')
def btst_scanner_stats():
    return {'success': True, 'data': _btst_svc.get_stats()}


@router.get('/btst-scanner/config')
def btst_scanner_config():
    return {'success': True, 'data': {'params': _btst_svc.get_all_config()}}


@router.put('/btst-scanner/config')
async def update_btst_config(request: Request):
    body = await request.json()
    if not body:
        return JSONResponse({'success': False, 'message': 'No params provided'}, status_code=400)
    valid = {k: v for k, v in body.items() if k in _btst_svc.BTST_CONFIG_SCHEMA}
    if not valid:
        return JSONResponse({
            'success': False,
            'message': f'No valid keys. Valid: {list(_btst_svc.BTST_CONFIG_SCHEMA.keys())}',
        }, status_code=400)
    updated = _btst_svc.update_config(valid)
    return {'success': True, 'data': {'params': updated}}


@router.post('/btst-scanner/start')
def btst_scanner_start():
    _btst_svc.start()
    return {'success': True, 'message': 'BTST scanner started'}


@router.post('/btst-scanner/stop')
def btst_scanner_stop():
    _btst_svc.stop()
    return {'success': True, 'message': 'BTST scanner stopped'}


@router.post('/btst-scanner/trigger')
def btst_scanner_trigger():
    _btst_svc.trigger_now()
    return {'success': True, 'message': 'BTST scan triggered manually'}


@router.get('/btst-scanner/stream')
async def btst_scanner_stream(request: Request):
    import queue as _queue
    loop = asyncio.get_running_loop()
    q = await loop.run_in_executor(None, _btst_svc.subscribe_sse)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await loop.run_in_executor(None, q.get, True, 1.0)
                    yield {'data': msg}
                except _queue.Empty:
                    yield {'data': json.dumps({'type': 'heartbeat'})}
        finally:
            await loop.run_in_executor(None, _btst_svc.unsubscribe_sse, q)

    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())
```

- [ ] **Step 2: Verify backend starts without errors**

```bash
cd backend && uv run python -c "from app.api.trade import router; print('routes ok')"
```

Expected: `routes ok`

- [ ] **Step 3: Verify status endpoint responds**

Start the backend (`uv run python -m app` or equivalent), then:

```bash
curl http://localhost:47293/api/trade/btst-scanner/status
```

Expected: `{"success": true, "data": {"status": "IDLE", ...}}`

---

### Task 8: Frontend API

**Files:**
- Create: `frontend/src/api/market/btst.js`

**Interfaces:**
- Consumes: `service` from `../index` (axios wrapper), `VITE_API_BASE_URL` env var
- Produces: 7 named exports consumed by BtstPanel.vue

- [ ] **Step 1: Create btst.js**

```javascript
// frontend/src/api/market/btst.js
import service from '../index'

export const startBtst         = () => service.post('/api/trade/btst-scanner/start')
export const stopBtst          = () => service.post('/api/trade/btst-scanner/stop')
export const triggerBtst       = () => service.post('/api/trade/btst-scanner/trigger')
export const getBtstStatus     = () => service.get('/api/trade/btst-scanner/status')
export const getBtstStats      = () => service.get('/api/trade/btst-scanner/stats')
export const getBtstConfig     = () => service.get('/api/trade/btst-scanner/config')
export const updateBtstConfig  = (params) => service.put('/api/trade/btst-scanner/config', params)

export const createBtstStream = () => {
  const base = import.meta.env.VITE_API_BASE_URL || ''
  return new EventSource(`${base}/api/trade/btst-scanner/stream`)
}
```

- [ ] **Step 2: Verify file is importable**

```bash
cd frontend && node -e "import('./src/api/market/btst.js').then(() => console.log('ok')).catch(e => console.error(e))"
```

Expected: `ok` (or a harmless `import.meta` parse warning in Node — acceptable)

---

### Task 9: BtstPanel.vue Component

**Files:**
- Create: `frontend/src/components/panels/BtstPanel.vue`

**Interfaces:**
- Consumes: all exports from `../../api/market/btst.js`
- Produces: a Vue component that renders BTST sidebar UI, auto-connects SSE on mount

- [ ] **Step 1: Create BtstPanel.vue**

```vue
<!-- frontend/src/components/panels/BtstPanel.vue -->
<template>
  <div class="btst-sidebar">
    <!-- Header -->
    <div class="rs-header btst-header">
      <div class="btst-title-row">
        <span class="btst-title">🌙 BTST</span>
        <span class="mode-badge paper">PAPER</span>
        <span class="scanner-pill" :class="statusClass">
          <span class="pill-dot"></span>
          {{ state.status }}
        </span>
      </div>
      <div class="btst-controls">
        <button class="btst-btn start" @click="handleStart" :disabled="state.status !== 'IDLE'">Start</button>
        <button class="btst-btn stop"  @click="handleStop"  :disabled="state.status === 'IDLE'">Stop</button>
        <button class="btst-btn trigger" @click="handleTrigger" title="Manual trigger for testing">⚡</button>
      </div>
    </div>

    <!-- Stats row -->
    <div class="rs-panel btst-stats-row">
      <div class="btst-stat">
        <span class="btst-stat-label">Candidates</span>
        <span class="btst-stat-val">{{ state.candidates_found }}</span>
      </div>
      <div class="btst-stat">
        <span class="btst-stat-label">Positions</span>
        <span class="btst-stat-val">{{ state.positions_count }}</span>
      </div>
      <div class="btst-stat" :class="stats.total_pnl_abs >= 0 ? 'up' : 'dn'">
        <span class="btst-stat-label">P&amp;L</span>
        <span class="btst-stat-val">{{ formatPnl(stats.total_pnl_abs) }}</span>
      </div>
      <div class="btst-stat">
        <span class="btst-stat-label">Win%</span>
        <span class="btst-stat-val">{{ stats.win_rate }}%</span>
      </div>
    </div>

    <!-- Overnight Positions -->
    <div class="rs-panel" v-if="positions.length">
      <div class="rs-panel-title">Positions 🌙</div>
      <div
        v-for="pos in positions"
        :key="pos.symbol + pos.ts"
        class="btst-pos-card"
        :class="pos.direction"
      >
        <div class="btst-card-top">
          <span class="btst-sym">{{ cleanSymbol(pos.symbol) }}</span>
          <span class="btst-status-badge" :class="pos.status.toLowerCase()">{{ pos.status }}</span>
        </div>
        <div class="btst-card-detail">
          <span>Entry ₹{{ pos.entry_price?.toFixed(2) }}</span>
          <span v-if="pos.pnl_pct !== null" :class="pos.pnl_pct >= 0 ? 'up' : 'dn'">
            {{ pos.pnl_pct >= 0 ? '+' : '' }}{{ pos.pnl_pct?.toFixed(2) }}%
          </span>
        </div>
        <div class="btst-card-rationale" v-if="pos.rationale">{{ pos.rationale }}</div>
      </div>
    </div>

    <!-- Signals -->
    <div class="rs-panel">
      <div class="rs-panel-title">Signals</div>
      <div v-if="!signals.length" class="btst-empty">No signals yet — scan runs at 14:30 IST</div>
      <div
        v-for="sig in signals"
        :key="sig.symbol + sig.ts"
        class="btst-signal-card"
        :class="[sig.direction, sig.rejected ? 'rejected' : '']"
      >
        <div class="btst-card-top">
          <span class="btst-sym">{{ cleanSymbol(sig.symbol) }}</span>
          <span class="btst-score-badge" :class="scoreClass(sig.score)">{{ sig.score }}</span>
          <span class="btst-dir-badge">{{ sig.direction === 'bull' ? '▲' : '▼' }}</span>
        </div>
        <div class="btst-card-rationale">{{ sig.rationale }}</div>
        <div class="btst-card-meta">
          <span>Target {{ sig.target_pct?.toFixed(1) }}%</span>
          <span class="btst-risk">⚠ {{ sig.risk_note }}</span>
        </div>
      </div>
    </div>

    <!-- Trade Log -->
    <div class="rs-panel btst-log-panel">
      <div class="rs-panel-title">Log</div>
      <div class="btst-log-feed">
        <div v-for="(entry, i) in logEntries" :key="i" class="log-entry" :class="entry.level">
          <span class="log-time">{{ formatTime(entry.ts) }}</span>
          <span class="log-msg">{{ entry.message }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  startBtst, stopBtst, triggerBtst,
  getBtstStatus, getBtstStats, createBtstStream,
} from '../../api/market/btst.js'

// ── State ─────────────────────────────────────────────────────────────────────

const state   = ref({ status: 'IDLE', positions_count: 0, candidates_found: 0, signals_count: 0 })
const stats   = ref({ total_pnl_abs: 0, total_pnl_pct: 0, win_rate: 0, total_trades: 0 })
const signals = ref([])    // scored candidates
const positions = ref([])  // overnight holds
const logEntries = ref([]) // trade log

let eventSource = null

// ── SSE ───────────────────────────────────────────────────────────────────────

function connectSSE() {
  if (eventSource) return
  eventSource = createBtstStream()

  eventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      handleSSEEvent(data)
    } catch {}
  }

  eventSource.onerror = () => {
    eventSource?.close()
    eventSource = null
    setTimeout(connectSSE, 5000)
  }
}

function handleSSEEvent(data) {
  if (data.type === 'heartbeat') return

  if (data.type === 'signal') {
    const idx = signals.value.findIndex(s => s.symbol === data.symbol && s.direction === data.direction)
    if (idx >= 0) signals.value.splice(idx, 1, data)
    else signals.value.unshift(data)
    signals.value = [...signals.value].sort((a, b) => b.score - a.score).slice(0, 50)
  }

  if (data.type === 'position_update') {
    const idx = positions.value.findIndex(p => p.symbol === data.symbol && p.ts === data.ts)
    if (idx >= 0) positions.value.splice(idx, 1, data)
    else positions.value.unshift(data)
  }

  if (data.type === 'exit') {
    const idx = positions.value.findIndex(p => p.symbol === data.symbol && p.status === 'HOLDING')
    if (idx >= 0) {
      positions.value[idx] = { ...positions.value[idx], status: 'EXITED', exit_price: data.exit_price, pnl_pct: data.pnl_pct }
    }
  }

  if (data.type === 'stats') {
    stats.value = { ...stats.value, ...data }
    if (data.status) state.value = { ...state.value, status: data.status }
  }

  if (data.type === 'log') {
    logEntries.value.unshift(data)
    if (logEntries.value.length > 30) logEntries.value.pop()
  }
}

// ── Actions ───────────────────────────────────────────────────────────────────

async function handleStart() {
  await startBtst()
  refreshStatus()
}

async function handleStop() {
  await stopBtst()
  refreshStatus()
}

async function handleTrigger() {
  await triggerBtst()
}

async function refreshStatus() {
  const [statusRes, statsRes] = await Promise.all([getBtstStatus(), getBtstStats()])
  if (statusRes.data?.data) {
    const d = statusRes.data.data
    state.value = { ...state.value, ...d }
    if (d.signals)   signals.value   = d.signals
    if (d.positions) positions.value = d.positions
  }
  if (statsRes.data?.data) stats.value = statsRes.data.data
}

// ── Computed ──────────────────────────────────────────────────────────────────

const statusClass = computed(() => ({
  running:       state.value.status === 'SCANNING',
  holding:       state.value.status === 'HOLDING',
  morning_exit:  state.value.status === 'MORNING_EXIT',
  idle:          state.value.status === 'IDLE',
}))

// ── Helpers ───────────────────────────────────────────────────────────────────

function cleanSymbol(sym) {
  return sym?.replace('.NS', '').replace('^', '') ?? sym
}

function scoreClass(score) {
  if (score >= 80) return 'score-high'
  if (score >= 65) return 'score-mid'
  return 'score-low'
}

function formatPnl(val) {
  if (val == null) return '₹0'
  const sign = val >= 0 ? '+' : ''
  return `${sign}₹${Math.abs(val).toFixed(0)}`
}

function formatTime(ts) {
  if (!ts) return ''
  try { return new Date(ts).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) }
  catch { return ts }
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  await refreshStatus()
  connectSSE()
})

onUnmounted(() => {
  eventSource?.close()
  eventSource = null
})
</script>

<style scoped>
.btst-sidebar {
  width: clamp(340px, 24vw, 460px);
  flex-shrink: 0;
  border-left: 1px solid #2a2a3a;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  background: var(--bg-panel, #13131f);
}

.btst-header {
  padding: 10px 12px 8px;
  border-bottom: 2px solid #7c3aed;
}

.btst-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.btst-title {
  font-size: 13px;
  font-weight: 700;
  color: #a78bfa;
  letter-spacing: 0.05em;
}

.btst-controls {
  display: flex;
  gap: 6px;
}

.btst-btn {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 4px;
  border: none;
  cursor: pointer;
  font-weight: 600;
}

.btst-btn.start   { background: #16a34a; color: #fff; }
.btst-btn.stop    { background: #dc2626; color: #fff; }
.btst-btn.trigger { background: #374151; color: #f3f4f6; }
.btst-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.btst-stats-row {
  display: flex;
  gap: 0;
  padding: 0;
}

.btst-stat {
  flex: 1;
  padding: 8px 6px;
  text-align: center;
  border-right: 1px solid #1e1e2e;
}

.btst-stat:last-child { border-right: none; }

.btst-stat-label {
  display: block;
  font-size: 9px;
  color: #6b7280;
  text-transform: uppercase;
  margin-bottom: 2px;
}

.btst-stat-val {
  display: block;
  font-size: 13px;
  font-weight: 700;
  color: #e5e7eb;
}

.btst-stat.up .btst-stat-val { color: #4ade80; }
.btst-stat.dn .btst-stat-val { color: #f87171; }

.rs-panel-title {
  font-size: 10px;
  font-weight: 700;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 6px 10px 4px;
  border-bottom: 1px solid #1e1e2e;
}

.btst-signal-card,
.btst-pos-card {
  padding: 8px 10px;
  border-bottom: 1px solid #1a1a2e;
  border-left: 3px solid transparent;
}

.btst-signal-card.bull  { border-left-color: #4ade80; }
.btst-signal-card.bear  { border-left-color: #f87171; }
.btst-signal-card.rejected { opacity: 0.45; }

.btst-pos-card.bull { border-left-color: #4ade80; background: rgba(74, 222, 128, 0.04); }
.btst-pos-card.bear { border-left-color: #f87171; background: rgba(248, 113, 113, 0.04); }

.btst-card-top {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.btst-sym {
  font-size: 12px;
  font-weight: 700;
  color: #e5e7eb;
}

.btst-score-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
}

.btst-score-badge.score-high { background: #14532d; color: #4ade80; }
.btst-score-badge.score-mid  { background: #1e3a5f; color: #60a5fa; }
.btst-score-badge.score-low  { background: #1f1f1f; color: #6b7280; }

.btst-dir-badge {
  font-size: 10px;
  color: #9ca3af;
}

.btst-card-rationale {
  font-size: 10px;
  color: #9ca3af;
  line-height: 1.4;
  margin-bottom: 2px;
}

.btst-card-meta {
  display: flex;
  gap: 10px;
  font-size: 9px;
  color: #6b7280;
}

.btst-risk { color: #f59e0b; }

.btst-card-detail {
  display: flex;
  gap: 10px;
  font-size: 10px;
  color: #9ca3af;
}

.up { color: #4ade80; }
.dn { color: #f87171; }

.btst-status-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
}

.btst-status-badge.holding { background: #1e3a5f; color: #60a5fa; }
.btst-status-badge.exited  { background: #14532d; color: #4ade80; }

.scanner-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 10px;
  text-transform: uppercase;
  background: #1f1f1f;
  color: #6b7280;
}

.scanner-pill.running { background: #14532d; color: #4ade80; }
.scanner-pill.holding { background: #1e3a5f; color: #60a5fa; }
.scanner-pill.morning_exit { background: #451a03; color: #fb923c; }

.pill-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.mode-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
}

.mode-badge.paper { background: #374151; color: #9ca3af; }

.btst-empty {
  font-size: 10px;
  color: #4b5563;
  padding: 12px 10px;
  text-align: center;
}

.btst-log-panel { flex: 1; min-height: 0; }

.btst-log-feed {
  max-height: 180px;
  overflow-y: auto;
  padding: 4px 0;
}

.log-entry {
  display: flex;
  gap: 6px;
  padding: 3px 10px;
  font-size: 10px;
  line-height: 1.4;
  border-bottom: 1px solid #111118;
}

.log-time { color: #4b5563; flex-shrink: 0; }
.log-msg  { color: #9ca3af; }
.log-entry.error .log-msg { color: #f87171; }
.log-entry.warn  .log-msg { color: #fbbf24; }
</style>
```

- [ ] **Step 2: Verify Vue file has no syntax errors**

```bash
cd frontend && npx vue-tsc --noEmit 2>&1 | grep BtstPanel || echo "no errors"
```

---

### Task 10: ScalpPanel Sidebar Integration

**Files:**
- Modify: `frontend/src/components/panels/ScalpPanel.vue`

**Interfaces:**
- Consumes: `BtstPanel` component from `./BtstPanel.vue`
- Change: wrap `.scalp-view` content in a flex row — existing `.scalp-header` + `.scalp-main` on the left, `<BtstPanel>` as fixed right sidebar

- [ ] **Step 1: Import BtstPanel in ScalpPanel.vue**

In `frontend/src/components/panels/ScalpPanel.vue`, find the `<script>` section. Add the import:

```javascript
import BtstPanel from './BtstPanel.vue'
```

And register it in `components:` if using Options API, or it auto-registers if using `<script setup>`.

> **Check:** Look at ScalpPanel.vue's `<script>` tag. If it says `<script setup>`, just add the import — no registration needed. If it uses `export default { components: {...} }`, add `BtstPanel` to the `components` object.

- [ ] **Step 2: Wrap template in flex container**

In `ScalpPanel.vue` template, find the outermost `<div class="scalp-view">`. The current structure is:

```html
<div class="scalp-view">
  <div class="scalp-header">...</div>
  <div class="scalp-main">...</div>
  ...settings modal...
</div>
```

Change it to:

```html
<div class="scalp-view">
  <!-- ═══ SCALP HEADER (full width) ════════════════════════════════════════ -->
  <div class="scalp-header">...</div>   <!-- UNCHANGED -->

  <!-- ═══ BODY: scalp content + BTST sidebar ══════════════════════════════ -->
  <div class="scalp-body-row">
    <div class="scalp-content">
      <div class="scalp-main">...</div>    <!-- UNCHANGED — move inside here -->
      <!-- any other existing scalp content below scalp-main goes here too -->
    </div>
    <BtstPanel />
  </div>

  ...settings modal...   <!-- UNCHANGED — leave outside the body row -->
</div>
```

- [ ] **Step 3: Add CSS for the body row wrapper**

In `frontend/src/styles/ScalpPanel.css` (or the `<style>` block inside ScalpPanel.vue — check which exists), add:

```css
.scalp-body-row {
  display: flex;
  flex-direction: row;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.scalp-content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
```

- [ ] **Step 4: Start the frontend dev server and verify**

```bash
cd frontend && npm run dev
```

Open browser → navigate to Scalp tab. Verify:
- Existing scalp content (charts, signals, positions) appears on left — unchanged
- BTST sidebar appears on right with `🌙 BTST` header
- "No signals yet" placeholder visible in signals section
- Start/Stop/⚡ buttons present and clickable
- Stats row shows 0s

- [ ] **Step 5: Verify SSE connection in browser console**

Open DevTools → Network → Filter `btst-scanner/stream`. Verify EventSource connection is established (status 200, type `eventsource`).

- [ ] **Step 6: Test manual trigger**

With backend running, click ⚡ button in BTST sidebar. Check backend logs for `[btst] Manual trigger fired`. Verify log entries appear in the BTST sidebar log feed.

---

## Self-Review

**Spec coverage check:**
- ✅ Two-stage pipeline (Stage 1 technical + Stage 2 LLM parallel) — Tasks 3, 4
- ✅ Bullish universe: Nifty 50 + Sensex stocks — Task 3 (`NIFTY50_SENSEX_UNIVERSE`)
- ✅ Bearish: Nifty/Sensex F&O instruments skip Stage 1 — Task 3 (`BEARISH_FO_INSTRUMENTS`)
- ✅ AI scoring via `chat_json()` — Task 4
- ✅ Fully automated entry, max 3 positions — Task 5, 6
- ✅ Paper mode only — Global Constraints + Task 5
- ✅ APScheduler at 14:30, 15:10, 09:15 — Task 6
- ✅ Morning exit: exit_now / wait (5-min re-check) / exit_by_1000 — Task 6
- ✅ Position persistence across restart — Task 5
- ✅ SSE events: signal, position_update, exit, log, stats — Tasks 2, 4, 5, 6
- ✅ Manual trigger endpoint for testing — Tasks 5, 7
- ✅ Swing-style fixed right sidebar — Task 9, 10
- ✅ Reuse `.rs-panel`, `.rs-header` CSS classes — Task 9
- ✅ Config schema with all 10 keys — Task 2
- ✅ 8 API routes — Task 7
- ✅ LLM timeout fallback (`exit_now` on error) — Task 6 `_exit_position`
- ✅ Holiday/no-data graceful skip — Task 3 (`_fetch_ohlcv` returns None → skipped)

**Type consistency check:**
- `_score_candidate` signature: `(symbol: str, df: pd.DataFrame, direction: str, llm_client: Any) → dict | None` — matches all callers ✅
- `_broadcast` called everywhere with `{'type': ..., 'ts': ..., ...}` — consistent ✅
- `_state['positions']` is `list[dict]` with keys: symbol, direction, entry_price, qty, status, rationale, ts, exit_price, pnl_pct, pnl_abs, exit_reason — all tasks use same schema ✅
- `subscribe_sse() → queue.Queue` — matches SSE route in Task 7 ✅

**Placeholder scan:** No TBDs, no "implement later", all code blocks complete ✅
