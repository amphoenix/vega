"""
F&O Auto-Scanner — background service that scans the F&O universe every N minutes.

Two-stage pipeline per ticker:
  Stage 1 — Technical pre-filter (pure Python, instant, no LLM):
    Checks Supertrend direction, ADX strength, RSI, EMA stack.
    If technicals are clearly directional (strong signal), generates a
    technical-only CIO dict with confidence 70-90. Skips tickers in ranging/weak markets.

  Stage 2 — Cerebrum confirmation (optional, LLM):
    Only runs on tickers that passed Stage 1.
    The LLM result can boost or reduce the technical confidence.
    If Cerebrum crashes, Stage 1 result is used as fallback.

Safety gates (ALL must pass before any trade):
  1. Market hours: 09:15 – 15:00 IST only
  2. confidence_to_trade >= 70 (from Stage 1 OR Stage 2)
  3. short_term_action == "BUY NOW" or "SELL NOW"
  4. At least 2 of 5 agents agree (for options) or 3 (for FUT)
  5. No existing open position in same underlying
  6. Sufficient cash (cost of 1 lot must fit in available cash)
  7. Max 3 open positions at a time (configurable)
"""
from __future__ import annotations

import os
import queue
import threading
import time
import json
from datetime import datetime
from typing import Callable, Optional

from ..utils.logger import get_logger
from . import broker_utils as bu

logger = get_logger('vega.fo_scanner')

# ── F&O universe ──────────────────────────────────────────────────────────────
# Configurable via FO_UNIVERSE env var (comma-separated tickers in yfinance format).
# Default: NIFTY 50 and SENSEX only.
_DEFAULT_UNIVERSE = '^NSEI,^BSESN'
_fo_universe_cache: list | None = None

def _get_fo_universe() -> list[str]:
    global _fo_universe_cache
    if _fo_universe_cache is None:
        raw = os.environ.get('FO_UNIVERSE', _DEFAULT_UNIVERSE)
        _fo_universe_cache = [t.strip() for t in raw.split(',') if t.strip()]
        logger.info(f"F&O universe: {_fo_universe_cache}")
    return _fo_universe_cache

# Keep FO_UNIVERSE as a property-like accessor for backward compat
class _UniverseProxy(list):
    """Lazy list that resolves from env on first access."""
    def __iter__(self): return iter(_get_fo_universe())
    def __len__(self): return len(_get_fo_universe())
    def __getitem__(self, i): return _get_fo_universe()[i]
    def __contains__(self, v): return v in _get_fo_universe()
    def index(self, v, *a): return _get_fo_universe().index(v, *a)

FO_UNIVERSE = _UniverseProxy()

# Lot sizes are now sourced from the IndStocks F&O instrument master
# (see broker_utils.fno_meta / underlying_lot_size). The dict below is kept
# only as a fallback used when the master fetch fails for ranking purposes —
# never trusted for live order sizing.
_LOT_SIZE_FALLBACK = {
    '^NSEI':         65,    # NIFTY (Jan-2026 series revision)
    '^NSEBANK':      30,    # BANKNIFTY
    '^BSESN':        20,    # SENSEX (BSE)
    'NIFTY':         65,
    'BANKNIFTY':     30,
    'FINNIFTY':      60,
}


def _lot_size_for(ticker: str) -> Optional[int]:
    """Resolve lot size from broker master; fall back to a small static map."""
    try:
        lot = bu.underlying_lot_size(ticker)
        if lot and lot > 0:
            return lot
    except Exception:
        pass
    return _LOT_SIZE_FALLBACK.get(ticker)

# Underlying ticker map (for position monitoring)
UNDERLYING_MAP = {
    '^NSEI':    '^NSEI',
    '^NSEBANK': '^NSEBANK',
    '^BSESN':   '^BSESN',
}  # For stocks, underlying == ticker

SCAN_INTERVAL_SECONDS = int(os.environ.get('FO_SCAN_INTERVAL_SEC', '0'))
MIN_CONFIDENCE        = int(os.environ.get('FO_MIN_CONFIDENCE', '70'))
MIN_AGENT_AGREEMENT   = int(os.environ.get('FO_MIN_AGENTS', '3'))  # of 5 agents
SIGNAL_MIN_DTE        = int(os.environ.get('FO_SIGNAL_MIN_DTE', '1'))  # 0 = allow all days
MAX_RISK_PCT          = float(os.environ.get('FO_MAX_RISK_PCT', '2.0'))  # max loss per trade as % of available capital

_scanner_thread: Optional[threading.Thread] = None
_stop_event     = threading.Event()
_manual_trigger = threading.Event()   # set by trigger_now() to force a scan even off-hours
_wake_event     = threading.Event()   # wakes the loop's wait() without killing the thread
_scan_lock      = threading.Lock()

# ── Ticket cache: lock entry price to first signal per underlying per day ──
# Prevents re-pricing when auto-entry retries on subsequent scan cycles.
_ticket_cache: dict = {}   # underlying → ticket (cleared on daily reset)

# ── Cross-index correlation: latest Supertrend direction per ticker ─────────
# SENSEX won't trade opposite to NIFTY — prevents confusing PE-vs-CE divergence.
_latest_st_dir: dict = {}   # ticker → 1 (bull) or -1 (bear)

# Correlated pairs: if ticker A disagrees with ticker B, skip A's signal
_CORRELATED_PAIRS = {
    '^BSESN': '^NSEI',    # SENSEX must agree with NIFTY
}

# Scan state (for status endpoint)
_state = {
    'running':       False,
    'last_scan':     None,
    'next_scan':     None,
    'scanned':       [],        # tickers scanned this cycle
    'signals':       [],        # all CIO verdicts this cycle
    'errors':        [],
    # Latest signal per underlying — survives scan-cycle boundaries so SSE
    # subscribers reconnecting after a browser refresh get the most recent
    # ticket card for each index.
    'latest_by_under': {},
}

# SSE broadcast for scan events
_sse_subscribers: list = []
_sse_lock = threading.Lock()


def subscribe_sse():
    import queue
    q = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_subscribers.append(q)
        # Replay the latest signal per underlying so a browser refresh
        # immediately restores the LIVE ticket cards (otherwise they sit empty
        # until the next scan cycle).
        for sig in _state.get('latest_by_under', {}).values():
            try:
                q.put_nowait(json.dumps(
                    {'type': 'scan_signal', '_replay': True, **sig},
                    default=str,
                ))
            except Exception:
                break
    return q


def unsubscribe_sse(q):
    with _sse_lock:
        try:
            _sse_subscribers.remove(q)
        except ValueError:
            pass


def _broadcast(event: dict):
    msg = json.dumps(event, default=str)
    with _sse_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                _sse_subscribers.remove(q)
            except ValueError:
                pass


def get_state() -> dict:
    with _scan_lock:
        s = dict(_state)
    # Effective interval seconds (matches the wait() logic in _scanner_loop).
    s['interval_seconds'] = SCAN_INTERVAL_SECONDS or 300
    s['universe']         = list(FO_UNIVERSE)
    s['min_confidence']   = MIN_CONFIDENCE
    return s


# ── Signal builder from CIO output ────────────────────────────────────────────

def _nse_base(ticker: str) -> str:
    from . import broker_utils as _bu
    return _bu._nse_base(ticker)


def _resolve_option_contract(ticker: str, option_type: str, strike: float) -> dict | None:
    from . import broker_utils as _bu
    return _bu._resolve_option_contract(ticker, option_type, strike)


def _build_trading_symbol(ticker: str, cio: dict, price: float) -> str:
    """
    Build NSE trading symbol from CIO instrument recommendation.
    e.g. NIFTY + CE + 24500 + 10APR25 → NIFTY25APR24500CE
    """
    itype  = cio.get('instrument_type', 'EQ')
    expiry = cio.get('expiry', '')
    strike = cio.get('strike_price', 0)

    if itype == 'EQ':
        return ticker.replace('.NS', '').replace('.BO', '').replace('^', '')

    # Base symbol
    base = ticker.upper().replace('.NS', '').replace('.BO', '')
    base = base.replace('^NSEI', 'NIFTY').replace('^NSEBANK', 'BANKNIFTY')

    if itype == 'FUT':
        # e.g. NIFTY25APR FUT
        return f"{base}{expiry}FUT" if expiry else f"{base}FUT"

    if itype in ('CE', 'PE'):
        strike_int = int(round(strike / 50) * 50) if 'NIFTY' in base else int(strike)
        return f"{base}{expiry}{strike_int}{itype}" if expiry else f"{base}{strike_int}{itype}"

    return base


# ── Stage 1: pure technical pre-filter (no LLM) ──────────────────────────────

def _technical_cio(ticker: str, raw: dict) -> Optional[dict]:
    """
    Build a CIO-compatible dict purely from ta_utils indicators — zero LLM calls.
    Returns a tradeable signal with confidence penalised when leading indicators
    (+DI/-DI crossover and MACD) contradict Supertrend.
    Returns None only if market is ranging (ADX < 15) or Supertrend is neutral.

    Signal flow:
      1. Supertrend direction decides candidate CE (bullish) or PE (bearish)
      2. Leading indicators confirm/contradict → confidence adjusted (+5/−8 each)
      3. Confidence (50-95) scored from ADX, RSI, EMA alignment, DI/MACD
      4. Verdict tier: ≥80 STRONG, ≥60 actionable, <60 WAIT
      None : ADX < 15 (ranging) or Supertrend neutral
    """
    ta      = raw.get('technicals', {})
    price   = float(raw.get('price', 0))
    company = raw.get('company_name', ticker)

    st_dir  = ta.get('supertrend_dir')    # 1=bullish, -1=bearish
    adx     = ta.get('adx') or 0
    rsi     = ta.get('rsi') or 50
    ema20   = ta.get('ema20') or price
    ema50   = ta.get('ema50') or price
    atr     = ta.get('atr') or price * 0.01

    # Leading indicators — used to CONFIRM Supertrend direction and
    # reject stale/lagging signals when the trend is already reversing.
    plus_di    = ta.get('adx_plus_di') or 0
    minus_di   = ta.get('adx_minus_di') or 0
    macd_cross = ta.get('macd_cross', 'NONE')   # 'BULLISH' | 'BEARISH' | 'NONE'
    macd_hist  = ta.get('macd_hist') or 0

    # Need at least Supertrend direction; ADX threshold lowered to 15 so we
    # still emit option tickets in low-vol regimes (e.g. NIFTY ADX 15-20).
    if adx < 15 or st_dir is None:
        return None

    bullish = st_dir == 1
    bearish = st_dir == -1

    # Store latest Supertrend direction for cross-index correlation
    _latest_st_dir[ticker] = st_dir

    # ── Cross-index correlation guard ──────────────────────────────────────
    # If this ticker is correlated (e.g. SENSEX → NIFTY), skip if directions
    # disagree. Prevents confusing PE-vs-CE divergence between indices.
    _ref_ticker = _CORRELATED_PAIRS.get(ticker)
    if _ref_ticker:
        _ref_dir = _latest_st_dir.get(_ref_ticker)
        if _ref_dir is not None and _ref_dir != st_dir:
            logger.warning(f"Scanner: {ticker} {'BULL' if bullish else 'BEAR'} "
                           f"DISAGREES with {_ref_ticker} ({'BULL' if _ref_dir == 1 else 'BEAR'}) "
                           f"— skipping (correlation guard)")
            return None

    # ── Leading indicator confirmation ────────────────────────────────────
    # +DI > -DI = bullish momentum; MACD histogram > 0 = bullish momentum.
    # If BOTH contradict Supertrend → heavy confidence penalty (−16 pts).
    # Signal still emits but with WAIT action if confidence drops below 60.
    di_bullish  = plus_di > minus_di
    di_bearish  = minus_di > plus_di
    macd_bull   = macd_cross == 'BULLISH' or macd_hist > 0
    macd_bear   = macd_cross == 'BEARISH' or macd_hist < 0

    if bullish:
        confirms    = int(di_bullish) + int(macd_bull)
        contradicts = int(di_bearish) + int(macd_bear)
    else:
        confirms    = int(di_bearish) + int(macd_bear)
        contradicts = int(di_bullish) + int(macd_bull)

    if contradicts == 2 and confirms == 0:
        logger.info(f"Scanner: {ticker} Supertrend {'BULL' if bullish else 'BEAR'} "
                    f"WEAK — both +DI/-DI and MACD contradict "
                    f"(+DI={plus_di:.1f} -DI={minus_di:.1f} MACD_hist={macd_hist:.2f})")

    # ── Continuous confidence (50-95) so each ticker gets a distinct score
    # based on actual indicator strength, not a 3-bucket lookup.
    # Components (max points):
    #   base                       50
    #   trend strength (ADX)       +30  (linear 15→45 ADX → 0→30 pts)
    #   momentum (RSI distance)    +10  (|RSI-50| → 0→10 pts)
    #   price-EMA alignment        +5   (price on the trend side of EMA20)
    #   leading confirmation       +5/−8 per confirming/contradicting indicator
    def _clamp(v, lo, hi): return max(lo, min(hi, v))
    trend_score = _clamp((adx - 15) * 1.0, 0, 30)
    if bullish:
        rsi_score = _clamp((rsi - 50) * 0.5, 0, 10)
        ema_score = 5 if price > ema20 else 0
        ema50_bonus = 3 if price > ema50 else 0
    else:
        rsi_score = _clamp((50 - rsi) * 0.5, 0, 10)
        ema_score = 5 if price < ema20 else 0
        ema50_bonus = 3 if price < ema50 else 0
    conf = int(round(50 + trend_score + rsi_score + ema_score + ema50_bonus))

    # Leading indicator adjustment: +5 per confirming, −8 per contradicting
    conf += confirms * 5
    conf -= contradicts * 8

    # CPR adjustment (+3 narrow = trending day, -3 wide = choppy day)
    cpr_type = ta.get('cpr_type')
    if cpr_type == 'narrow':
        conf += 3
    elif cpr_type == 'wide':
        conf -= 3

    conf = _clamp(conf, 50, 95)

    # Verdict tier from confidence band
    if bullish:
        if conf >= 80:    verdict, action = 'STRONG BUY', 'BUY NOW'
        elif conf >= 60:  verdict, action = 'BUY',        'BUY NOW'
        else:             verdict, action = 'BUY',        'WAIT'   # weak bull
        itype = 'CE'
    elif bearish:
        if conf >= 80:    verdict, action = 'STRONG SELL', 'SELL NOW'
        elif conf >= 60:  verdict, action = 'SELL',        'SELL NOW'
        else:             verdict, action = 'SELL',        'WAIT'
        itype = 'PE'
    else:
        return None   # Mixed / unclear

    lot_size = _lot_size_for(ticker) or 1

    # Strike: CE = price + 1.5%, PE = price - 1.5% (delta ~0.35)
    # Round to exchange-mandated strike interval: NIFTY/BANKNIFTY → 50, SENSEX → 100, stocks → int
    def _round_strike(raw, tkr):
        t = tkr.upper()
        if 'NIFTY' in t or '^NSE' in t:
            return round(raw / 50) * 50
        if 'SENSEX' in t or 'BSESN' in t:
            return round(raw / 100) * 100
        return round(raw)

    if itype == 'CE':
        raw_strike = price * 1.015
        strike = _round_strike(raw_strike, ticker)
        sl_underlying = round(price * 0.98, 2)
        t1_underlying = round(price * 1.03, 2)
        t2_underlying = round(price * 1.05, 2)
        bull_count, bear_count = 3, 1
    else:
        raw_strike = price * 0.985
        strike = _round_strike(raw_strike, ticker)
        sl_underlying = round(price * 1.02, 2)
        t1_underlying = round(price * 0.97, 2)
        t2_underlying = round(price * 0.95, 2)
        bull_count, bear_count = 1, 3

    # Estimated option premium ≈ 0.6% of underlying for weekly ATM
    est_premium = round(price * 0.006, 2)

    patterns = ta.get('candle_patterns', [])
    _conf_tag = (f"+DI={'>' if di_bullish else '<'}-DI "
                f"MACD_hist={'↑' if macd_bull else '↓' if macd_bear else '—'} "
                f"({confirms}/2 confirm)")
    thesis = (f"Technical: Supertrend {'BULLISH' if bullish else 'BEARISH'}, "
              f"ADX={adx:.1f}, RSI={rsi:.1f}, {_conf_tag}. "
              f"Pattern: {patterns[0] if patterns else 'N/A'}.")

    return {
        'final_verdict':       verdict,
        'consensus_score':     conf if bullish else -conf,
        'bull_count':          bull_count,
        'bear_count':          bear_count,
        'neutral_count':       1,
        'entry_price':         price,
        'stop_loss':           sl_underlying,
        'target_1':            t1_underlying,
        'target_2':            t2_underlying,
        'target_3':            round(t2_underlying * (1.02 if bullish else 0.98), 2),
        'time_horizon':        'intraday',
        'investment_thesis':   thesis,
        'bull_case':           thesis if bullish else '',
        'bear_case':           thesis if bearish else '',
        'key_risks':           [f"ADX may weaken if {adx:.0f} drops below 20"],
        'position_size_pct':   2,
        'short_term_action':   action,
        'short_term_reason':   thesis,
        'instrument_type':     itype,
        'expiry':              None,
        'strike_price':        float(strike),
        'lot_size':            lot_size,
        'estimated_premium':   est_premium,
        'confidence_to_trade': conf,
        '_ticker':             ticker,
        '_price':              price,
        '_company':            company,
        '_source':             'technical',   # marks this as Stage 1 only
        '_atr':                atr,           # for downstream ticket build (option_planner)
    }


# ── Stage 2: Cerebrum LLM confirmation ───────────────────────────────────────

def _scan_one(ticker: str, llm_client) -> Optional[dict]:
    """
    Two-stage scan:
      1. Quick technical pre-filter (instant, no LLM)
      2. Cerebrum confirmation (LLM) — only if Stage 1 passes

    If Cerebrum crashes (nested executor etc.), Stage 1 result is used as fallback.
    If Stage 1 fails (ranging market), skip entirely.
    """
    from ..api.market import _fetch_market_data

    logger.info(f"Scanner: analysing {ticker}")
    try:
        raw = _fetch_market_data(ticker)
    except Exception as e:
        logger.error(f"Data fetch failed for {ticker}: {e}")
        return None

    price   = float(raw.get('price', 0))
    company = raw.get('company_name', ticker)

    # Stage 1: pure technical check
    tech_cio = _technical_cio(ticker, raw)
    if tech_cio is None:
        logger.info(f"Scanner: {ticker} pre-filter SKIP (ADX weak / no trend)")
        return {
            'final_verdict': 'HOLD', 'confidence_to_trade': 10,
            'short_term_action': 'AVOID', 'instrument_type': 'EQ',
            '_ticker': ticker, '_price': price, '_company': company,
            '_source': 'prefilter',
        }

    logger.info(f"Scanner: {ticker} Stage-1 → {tech_cio['final_verdict']} "
                f"conf={tech_cio['confidence_to_trade']}% ({tech_cio['instrument_type']})")

    # Stage 2: Cerebrum LLM (confirmation — runs in current thread, avoids nested executor crash)
    try:
        from .cerebrum.runner import AnalysisRunner

        def _noop_emit(evt, payload): pass

        result = AnalysisRunner().run(
            ticker=ticker,
            emit_fn=_noop_emit,
            llm_client=llm_client,
            market_data_fn=lambda t: raw,
        )
        cio = result.get('cio', {})
        cio['_ticker']  = ticker
        cio['_price']   = result.get('price', price)
        cio['_company'] = result.get('company_name', company)
        cio['_source']  = 'cerebrum'

        c_verdict = cio.get('final_verdict', 'HOLD')
        t_verdict = tech_cio['final_verdict']
        t_bullish = t_verdict in ('BUY', 'STRONG BUY')
        c_bullish = c_verdict in ('BUY', 'STRONG BUY')
        t_bearish = t_verdict in ('SELL', 'STRONG SELL')
        c_bearish = c_verdict in ('SELL', 'STRONG SELL')
        t_actionable = t_bullish or t_bearish
        c_actionable = c_bullish or c_bearish
        t_conf       = int(tech_cio.get('confidence_to_trade', 0))

        if (t_bullish and c_bullish) or (t_bearish and c_bearish):
            blended = min(95, int(cio.get('confidence_to_trade', 50)) + 10)
            cio['confidence_to_trade'] = blended
            logger.info(f"Scanner: {ticker} Cerebrum CONFIRMS Stage-1 → conf {blended}%")
        elif t_actionable and t_conf >= 62 and not c_actionable:
            # Stage 1 says BUY/SELL strongly; Cerebrum waters down to HOLD.
            # Trust deterministic technicals — LLM is systematically over-cautious.
            # Subtract a fixed penalty (5) without flooring — lets the user
            # distinguish "Cerebrum mildly disagrees" from "everything is 60%".
            cio['final_verdict']       = t_verdict
            cio['confidence_to_trade'] = max(t_conf - 5, 40)
            cio['instrument_type']     = tech_cio['instrument_type']
            cio['strike_price']        = cio.get('strike_price') or tech_cio['strike_price']
            cio['estimated_premium']   = cio.get('estimated_premium') or tech_cio['estimated_premium']
            cio['short_term_action']   = tech_cio['short_term_action']
            # Restore Stage-1 agent agreement counts. Without this the safety
            # gate would see Cerebrum's HOLD-time counts (often 0/0) and
            # always reject with "Only 0/2 agents bearish for PE".
            cio['bull_count']          = tech_cio['bull_count']
            cio['bear_count']          = tech_cio['bear_count']
            cio['neutral_count']       = tech_cio.get('neutral_count', 1)
            logger.info(f"Scanner: {ticker} Stage-1 actionable + Cerebrum HOLD → "
                        f"trust Stage-1 ({t_verdict} conf={cio['confidence_to_trade']}%)")
        elif (t_bullish and c_bearish) or (t_bearish and c_bullish):
            cio['confidence_to_trade'] = int(cio.get('confidence_to_trade', 0)) // 2
            logger.info(f"Scanner: {ticker} Stage-1 vs Cerebrum DISAGREE → conf halved")
        elif cio.get('confidence_to_trade', 0) < t_conf:
            cio['confidence_to_trade'] = t_conf
            cio['instrument_type']    = cio.get('instrument_type') or tech_cio['instrument_type']
            cio['strike_price']       = cio.get('strike_price') or tech_cio['strike_price']
            cio['estimated_premium']  = cio.get('estimated_premium') or tech_cio['estimated_premium']
            cio['short_term_action']  = cio.get('short_term_action') or tech_cio['short_term_action']

        # Always use Stage-1 instrument type (CE/PE) — Cerebrum's prompt biases toward FUT
        # on clear trends, but we want option strikes for the user.
        if tech_cio['instrument_type'] in ('CE', 'PE'):
            cio['instrument_type'] = tech_cio['instrument_type']
            cio['strike_price']    = cio.get('strike_price') or tech_cio['strike_price']

        return cio

    except Exception as e:
        logger.warning(f"Cerebrum failed for {ticker} ({e}) — using Stage-1 technical result")
        return tech_cio


def _run_scan_cycle(llm_client):
    """
    One full scan cycle.
    Uses plain threading.Thread (NOT ThreadPoolExecutor) to avoid nested-executor
    crashes — Cerebrum internally uses ThreadPoolExecutor for agents + debate,
    so nesting executors causes RuntimeError in Python 3.12.
    Results flow back via a queue.Queue as each ticker completes.
    """
    with _scan_lock:
        _state['last_scan'] = datetime.now().isoformat()
        _state['scanned']   = []
        _state['signals']   = []

    # Clear ticket cache at start of new trading day (09:15 IST)
    _now = datetime.now()
    if _now.hour == 9 and _now.minute < (SCAN_INTERVAL_SECONDS // 60 + 4):
        _ticket_cache.clear()
        logger.info("Scanner: ticket cache cleared (new trading day)")

    _broadcast({'type': 'scan_start', 'universe': FO_UNIVERSE,
                'timestamp': datetime.now().isoformat()})

    result_q: queue.Queue = queue.Queue()
    MAX_PARALLEL = 4  # max concurrent Cerebrum pipelines

    def _worker(ticker: str):
        try:
            if _stop_event.is_set():
                result_q.put((ticker, None))
                return
            idx = FO_UNIVERSE.index(ticker) + 1
            _broadcast({'type': 'scan_analysing', 'ticker': ticker,
                        'ticker_clean': ticker.replace('.NS','').replace('.BO','').replace('^',''),
                        'index': idx, 'total': len(FO_UNIVERSE),
                        'timestamp': datetime.now().isoformat()})
            with _scan_lock:
                _state['scanned'].append(ticker)
            cio = _scan_one(ticker, llm_client)
            result_q.put((ticker, cio))
        except Exception as exc:
            logger.error(f"Worker error {ticker}: {exc}")
            result_q.put((ticker, None))

    # Launch tickers in batches of MAX_PARALLEL
    signals  = []
    pending  = list(FO_UNIVERSE)
    active   = []

    while (pending or active) and not _stop_event.is_set():
        # Fill up to MAX_PARALLEL active threads
        while pending and len(active) < MAX_PARALLEL:
            t = pending.pop(0)
            th = threading.Thread(target=_worker, args=(t,), daemon=True)
            th.start()
            active.append(th)

        # Wait for one result. 300s budget covers: 8 parallel agents per
        # ticker x up to 90s per agent on full LLM hang (3 retries x 30s
        # request timeout) + debate + CIO. Fits 3 tickers in parallel via
        # MAX_PARALLEL with comfortable margin.
        try:
            ticker, cio = result_q.get(timeout=300)
        except queue.Empty:
            break

        # Remove finished thread from active list
        active = [th for th in active if th.is_alive()]

        if not cio:
            continue

        price   = float(cio.get('_price', 0))
        verdict = cio.get('final_verdict', 'HOLD')
        conf    = cio.get('confidence_to_trade', 0)
        source  = cio.get('_source', 'cerebrum')
        itype   = cio.get('instrument_type', 'EQ')
        strike  = float(cio.get('strike_price', 0) or 0)

        # ── Build a precise, executable trade ticket (delta-targeted + Greeks)
        # Always build for CE/PE conf≥60 regardless of verdict — user needs to see the strike
        option_contract = None
        ticket = None
        if itype in ('CE', 'PE') and conf >= 60:
            # ── Reuse cached ticket if same underlying + same direction ──────
            # Locks entry price to the first signal shown in the UI.
            # Invalidate if direction flipped (e.g., was PE now CE).
            _cache_key = f"{ticker}:{itype}"
            _opposite  = f"{ticker}:{'CE' if itype == 'PE' else 'PE'}"
            if _opposite in _ticket_cache:
                del _ticket_cache[_opposite]
            bias = 'BULL' if itype == 'CE' else 'BEAR'
            _fnum = lambda v: float(v) if v not in (None, '', 0) else None
            atr = _fnum(cio.get('_atr')) or _fnum(cio.get('atr'))
            cached = _ticket_cache.get(_cache_key)
            if cached:
                ticket = cached
                logger.debug(f"Scanner: reusing cached ticket for {_cache_key} "
                             f"@ ₹{ticket['entry']['expected_premium_inr']:.2f}")
            else:
                try:
                    from .option_planner import plan_option_trade
                    ticket = plan_option_trade(
                        underlying=ticker, bias=bias, spot=price,
                        target_delta=float(os.environ.get('FO_TARGET_DELTA', '0.55')),
                        min_dte=int(os.environ.get('FO_MIN_DTE', '3')),
                        max_dte=int(os.environ.get('FO_MAX_DTE', '21')),
                        rationale=f"{verdict} conf={conf}% | {cio.get('short_term_reason','')}",
                        spot_target_1  = _fnum(cio.get('target_1')),
                        spot_target_2  = _fnum(cio.get('target_2')),
                        spot_stop_loss = _fnum(cio.get('stop_loss')),
                        atr            = atr,
                    )
                    if ticket:
                        _ticket_cache[_cache_key] = ticket
                        logger.info(f"Scanner: cached ticket for {_cache_key} "
                                    f"@ ₹{ticket['entry']['expected_premium_inr']:.2f}")
                except Exception as e:
                    logger.warning(f"Ticket build failed for {ticker}: {e}")
                    ticket = None

            # ── Generate alternative strikes at different deltas ──────────
            from .option_planner import plan_option_trade
            alt_tickets = []
            if ticket:
                _alt_deltas = [0.40, 0.45, 0.65]   # cheaper OTM + pricier ITM
                primary_strike = ticket.get('strike')
                # Use the primary ticket's DTE so alts match the same expiry
                # (avoids min_dte filter rejecting short-dated contracts like SENSEX 1DTE)
                _ticket_dte = ticket.get('days_to_expiry', 1) or 1
                _alt_min_dte = min(int(os.environ.get('FO_MIN_DTE', '3')), _ticket_dte)
                _alt_max_dte = max(int(os.environ.get('FO_MAX_DTE', '21')), _ticket_dte)
                for ad in _alt_deltas:
                    try:
                        alt = plan_option_trade(
                            underlying=ticker, bias=bias, spot=price,
                            target_delta=ad,
                            min_dte=_alt_min_dte,
                            max_dte=_alt_max_dte,
                            rationale=f"alt Δ={ad}",
                            spot_target_1=_fnum(cio.get('target_1')),
                            spot_target_2=_fnum(cio.get('target_2')),
                            spot_stop_loss=_fnum(cio.get('stop_loss')),
                            atr=atr,
                        )
                        if alt and alt.get('strike') != primary_strike:
                            alt_tickets.append(alt)
                    except Exception:
                        pass

            if ticket:
                cio['expiry']            = ticket['expiry']
                cio['strike_price']      = ticket['strike']
                cio['option_symbol']     = ticket['trading_symbol']
                cio['display_symbol']    = ticket.get('display_symbol') or ticket['trading_symbol']
                # Entry price = locked from first signal (cached ticket)
                cio['estimated_premium'] = ticket['entry']['expected_premium_inr']
                # option_ltp = live price for UI display (fetch current tick)
                try:
                    from ..api.indmoney import _ind_ltp
                    live_px = _ind_ltp(ticket['trading_symbol'])
                    cio['option_ltp'] = live_px if live_px else ticket['entry']['expected_premium_inr']
                except Exception:
                    cio['option_ltp'] = ticket['entry']['expected_premium_inr']
                cio['lot_size']          = ticket['lot_size']
                cio['premium_sl']        = ticket['exit']['stop_loss_inr']
                cio['premium_t1']        = ticket['exit']['target_1_inr']
                cio['premium_t2']        = ticket['exit']['target_2_inr']
                # Attach alternatives for UI display + affordable auto-entry
                if alt_tickets:
                    cio['alt_tickets'] = [{
                        'trading_symbol':  a['trading_symbol'],
                        'display_symbol':  a.get('display_symbol') or a['trading_symbol'],
                        'strike':          a['strike'],
                        'premium':         a['entry']['expected_premium_inr'],
                        'delta':           a['greeks']['delta'],
                        'theta_per_day':   a['greeks']['theta_per_day'],
                        'sl':              a['exit']['stop_loss_inr'],
                        't1':              a['exit']['target_1_inr'],
                        'lot_size':        a['lot_size'],
                        'max_loss':        a.get('risk', {}).get('max_loss_inr', 0),
                        '_full_ticket':    a,
                    } for a in alt_tickets]
                    alt_syms = ', '.join(f"{a['strike']}@₹{a['entry']['expected_premium_inr']:.0f}"
                                        for a in alt_tickets)
                    logger.info(f"Scanner alts for {ticker}: {alt_syms}")
                logger.info(f"Scanner ticket: {ticket['trading_symbol']} "
                            f"@ ₹{ticket['entry']['expected_premium_inr']:.2f} "
                            f"Δ={ticket['greeks']['delta']:.2f} "
                            f"θ/day=₹{ticket['greeks']['theta_per_day']:.2f} "
                            f"IV={ticket['greeks']['iv_used']:.1%} "
                            f"DTE={ticket['days_to_expiry']}d "
                            f"SL=₹{ticket['exit']['stop_loss_inr']} "
                            f"T1=₹{ticket['exit']['target_1_inr']}")

                # ── Capital-aware risk gate ────────────────────────────────
                # Fetch live available cash from broker and reject trades
                # where max_loss exceeds MAX_RISK_PCT of capital (default 2%).
                # This prevents a single trade from wiping out the account.
                # If capital cannot be fetched or is ≤ 0 → block the trade
                # (fail-safe: never trade blind on unknown capital).
                # In paper mode, use PAPER_CAPITAL_INR (default ₹100,000).
                try:
                    from ..api.indmoney import _ind_available_cash
                    _paper_mode = not (os.environ.get('LIVE_TRADING_ENABLED', 'false')
                                       .strip().lower() in ('true', '1', 'yes'))
                    if _paper_mode:
                        avail = float(os.environ.get('PAPER_CAPITAL_INR', '100000'))
                        logger.debug(f"Scanner: paper mode — using simulated capital ₹{avail:.0f}")
                    else:
                        avail = _ind_available_cash()
                    max_loss = float(ticket.get('risk', {}).get('max_loss_inr', 0))

                    if avail is None:
                        logger.warning(
                            f"Scanner: {ticker} REJECTED by capital gate — "
                            f"could not fetch available cash from broker")
                        cio['short_term_action'] = 'AVOID'
                        cio['confidence_to_trade'] = min(conf, 40)
                        cio['_risk_rejected'] = True
                        cio['_risk_reason'] = "Could not fetch available capital from broker"
                        ticket['risk']['capital_warning'] = cio['_risk_reason']
                        conf = cio['confidence_to_trade']
                    elif avail <= 0:
                        logger.warning(
                            f"Scanner: {ticker} REJECTED by capital gate — "
                            f"available cash ₹{avail:.0f} (zero or negative)")
                        cio['short_term_action'] = 'AVOID'
                        cio['confidence_to_trade'] = min(conf, 40)
                        cio['_risk_rejected'] = True
                        cio['_risk_reason'] = f"Available capital ₹{avail:.0f} — insufficient"
                        ticket['risk']['capital_warning'] = cio['_risk_reason']
                        conf = cio['confidence_to_trade']
                    else:
                        risk_limit = avail * (MAX_RISK_PCT / 100.0)
                        # Use enforced strict SL for max_loss (not planner's loose 50% SL)
                        # e.g. NIFTY: 15pts × 75 lot = ₹1,125 vs planner's ₹8,025
                        from .order_executor import sl_max_points
                        _strict_max_loss = sl_max_points(ticker) * float(ticket.get('lot_size', 1))
                        if _strict_max_loss > 0:
                            max_loss = _strict_max_loss
                            logger.debug(f"Scanner: {ticker} using strict SL max_loss ₹{max_loss:.0f} "
                                         f"(sl_max_pts={sl_max_points(ticker)} × lot={ticket.get('lot_size')})")
                        if max_loss > risk_limit:
                            # Check if any alt ticket fits within the limit
                            # Use strict SL (sl_max_points × lot) — same as primary
                            _has_affordable_alt = any(
                                sl_max_points(ticker) * float(a.get('lot_size', 1) or 1) <= risk_limit
                                for a in alt_tickets
                            ) if alt_tickets else False
                            if _has_affordable_alt:
                                # Let executor pick the cheaper alt — keep conf intact
                                logger.info(
                                    f"Scanner: {ticker} primary too expensive "
                                    f"(₹{max_loss:.0f} > ₹{risk_limit:.0f}) "
                                    f"but cheaper alt available — passing to executor")
                                cio['_risk_reason'] = (
                                    f"Primary ₹{max_loss:.0f} > limit ₹{risk_limit:.0f}, "
                                    f"executor will use cheaper alt")
                            else:
                                logger.warning(
                                    f"Scanner: {ticker} REJECTED by capital gate — "
                                    f"max_loss ₹{max_loss:.0f} > {MAX_RISK_PCT}% of "
                                    f"₹{avail:.0f} (limit ₹{risk_limit:.0f})")
                                cio['short_term_action'] = 'AVOID'
                                cio['confidence_to_trade'] = min(conf, 40)
                                cio['_risk_rejected'] = True
                                cio['_risk_reason'] = (
                                    f"Max loss ₹{max_loss:.0f} exceeds {MAX_RISK_PCT}% "
                                    f"of available capital ₹{avail:.0f} "
                                    f"(limit ₹{risk_limit:.0f})")
                                ticket['risk']['capital_warning'] = cio['_risk_reason']
                                conf = cio['confidence_to_trade']
                                verdict = f"{verdict} (⚠ CAPITAL)"
                        else:
                            logger.info(
                                f"Scanner: {ticker} capital gate OK — "
                                f"max_loss ₹{max_loss:.0f} ≤ {MAX_RISK_PCT}% of "
                                f"₹{avail:.0f} (limit ₹{risk_limit:.0f})")
                except Exception as e:
                    logger.warning(f"Capital gate check failed for {ticker}: {e}")
                    cio['short_term_action'] = 'AVOID'
                    cio['confidence_to_trade'] = min(conf, 40)
                    cio['_risk_rejected'] = True
                    cio['_risk_reason'] = f"Capital gate error: {e}"
                    conf = cio['confidence_to_trade']
                    verdict = f"{verdict} (⚠ CAPITAL)"

            else:
                # Legacy fallback so we still emit *something* on signal
                option_contract = _resolve_option_contract(ticker, itype, strike)
                if option_contract:
                    cio['expiry']         = option_contract['expiry_date']
                    cio['strike_price']   = option_contract['strike']
                    cio['option_symbol']  = option_contract['trading_symbol']
                    cio['display_symbol'] = option_contract.get('display_symbol') \
                                            or option_contract['trading_symbol']
                    cio['option_ltp']     = option_contract['ltp']

        signal = {
            'ticker':         ticker,
            'company':        cio.get('_company', ticker),
            'verdict':        verdict,
            'confidence':     conf,
            'action':         cio.get('short_term_action'),
            'instrument':     itype,
            'expiry':         cio.get('expiry'),
            'strike':         cio.get('strike_price'),
            'lot_size':       cio.get('lot_size'),
            'entry':          cio.get('entry_price'),
            'stop_loss':      cio.get('stop_loss'),
            'target_1':       cio.get('target_1'),
            'target_2':       cio.get('target_2'),
            'thesis':         cio.get('investment_thesis', '')[:200],
            'source':         source,
            'option_symbol':  cio.get('option_symbol'),
            'display_symbol': cio.get('display_symbol') or cio.get('option_symbol'),
            'option_ltp':     cio.get('option_ltp'),
            'ticket':         ticket,            # full plan with Greeks + risk
            'alt_tickets':    cio.get('alt_tickets', []),
            'cio':            cio,               # full CIO for executor alt-strike logic
            'timestamp':      datetime.now().isoformat(),
        }
        signals.append(signal)

        with _scan_lock:
            _state['signals'].append(signal)
            # Persist most-recent ticket per underlying so a browser refresh
            # can replay it via subscribe_sse().
            _sig_ticket = signal.get('ticket') or {}
            under  = _sig_ticket.get('underlying') or signal.get('ticker')
            if under:
                _state['latest_by_under'][under] = signal

        # Strip _full_ticket from alt_tickets before SSE broadcast (too heavy)
        _bcast_signal = {**signal}
        if _bcast_signal.get('alt_tickets'):
            _bcast_signal['alt_tickets'] = [
                {k: v for k, v in a.items() if k != '_full_ticket'}
                for a in _bcast_signal['alt_tickets']
            ]
        _bcast_signal.pop('cio', None)  # cio is internal, don't send to frontend
        _broadcast({'type': 'scan_signal', **_bcast_signal})
        logger.info(f"Scanner [{source}] {ticker} {verdict} conf={conf}% "
                    f"instrument={cio.get('instrument_type')} action={cio.get('short_term_action')}")

        # ── Thesis invalidation: exit opposite positions on direction flip ──
        # If scanner says BUY (CE) but we hold a PE for the same underlying
        # (or vice versa), the thesis is dead — auto-exit the old position.
        if itype in ('CE', 'PE') and conf >= 70:
            opposite_type = 'PE' if itype == 'CE' else 'CE'
            try:
                from . import tracked_positions as tp
                from .order_executor import try_auto_exit
                from ..api.indmoney import _order_broadcast, _ind_ltp
                for rec in tp.list_tracked():
                    t = rec.get('ticket') or {}
                    t_under = t.get('underlying', '')
                    t_opt = t.get('option_type', '').upper()
                    # Same underlying, opposite direction
                    if t_under == ticker and t_opt == opposite_type:
                        opt_sym = t.get('trading_symbol', '')
                        prem = _ind_ltp(opt_sym) or 0
                        logger.warning(f"Scanner: THESIS FLIP for {ticker} — "
                                       f"was {opposite_type}, now {itype} conf={conf}%. "
                                       f"Auto-exiting {opt_sym}")
                        _order_broadcast({
                            'type': 'order_update', 'severity': 'warning',
                            'title': f"⚡ THESIS FLIP: {t.get('display_symbol', opt_sym)}",
                            'status': 'THESIS_FLIP', 'symbol': opt_sym,
                            'txn_type': 'SELL',
                            'message': f"Direction flipped {opposite_type}→{itype} conf={conf}%. Exiting.",
                            'timestamp': datetime.now().isoformat(),
                        })
                        try_auto_exit(rec['id'], 'thesis_flip', rec, prem)
            except Exception as e:
                logger.warning(f"Scanner: thesis invalidation failed for {ticker}: {e}")

        # ── Auto-entry via order executor ────────────────────────────────
        if ticket and conf >= 60:
            try:
                from .order_executor import try_auto_entry
                result = try_auto_entry(signal)
                if result:
                    logger.info(f"Scanner: auto-entry placed for {ticker} → {result.get('id')}")
            except Exception as e:
                logger.warning(f"Scanner: auto-entry failed for {ticker}: {e}")
                from ..api.indmoney import _order_broadcast
                _order_broadcast({
                    'type': 'order_update', 'severity': 'error',
                    'title': f"Auto-entry error: {ticker}",
                    'status': 'ERROR', 'symbol': ticker,
                    'message': str(e),
                    'timestamp': datetime.now().isoformat(),
                })

    _broadcast({'type': 'scan_complete', 'signals': len(signals),
                'timestamp': datetime.now().isoformat()})
    logger.info(f"Scan cycle complete: {len(signals)} signals across {len(FO_UNIVERSE)} tickers")


def _pre_warm():
    """Warm caches/clients ~5 min before market open so the FIRST scan cycle
    at 09:15 doesn't pay cold-start penalties.

    Warms three things:
      1. F&O instrument master (large CSV; 2-5s to parse first time)
      2. IndMoney spot LTP cache for each universe ticker (TLS handshake +
         WebSocket subscription)
      3. LLM client TLS handshake (Bedrock / OpenAI-compat — first call adds
         100-500ms otherwise)

    Best-effort: failures are logged but do not abort the scanner.
    """
    logger.info("Scanner pre-warm starting (T-5min to market open)…")
    # 1. F&O master
    try:
        from ..api.indmoney import _load_instruments
        rows = _load_instruments('fno') or []
        logger.info(f"Pre-warm: F&O master loaded ({len(rows)} contracts)")
    except Exception as e:
        logger.warning(f"Pre-warm: F&O master load failed: {e}")
    # 2. Underlying spots
    try:
        from ..api.indmoney import _ind_ltp
        for ticker in FO_UNIVERSE:
            try:
                p = _ind_ltp(ticker)
                logger.info(f"Pre-warm: {ticker} LTP={p}")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Pre-warm: spot fetch failed: {e}")
    # 3. LLM TLS handshake
    try:
        from ..utils.llm_client import LLMClient
        LLMClient().chat(messages=[{'role': 'user', 'content': 'ping'}],
                         max_tokens=4)
        logger.info("Pre-warm: LLM client warm")
    except Exception as e:
        logger.warning(f"Pre-warm: LLM handshake failed: {e}")
    logger.info("Scanner pre-warm complete")


def _scanner_loop():
    from ..utils.llm_client import LLMClient

    logger.info(f"F&O scanner started (continuous, "
                f"sleep={SCAN_INTERVAL_SECONDS}s between cycles, "
                f"universe={len(FO_UNIVERSE)} tickers)")

    with _scan_lock:
        _state['running'] = True

    last_prewarm_date = None       # track date of last pre-warm to fire once/day
    PREWARM_LEAD_SEC  = 300        # 5 minutes before open

    while not _stop_event.is_set():
        # Manual trigger forces one cycle regardless of market hours (e.g. pre-market prep).
        manual = _manual_trigger.is_set()
        if manual or bu.is_market_hours():
            if manual:
                _manual_trigger.clear()
            try:
                llm = LLMClient()
                _run_scan_cycle(llm)
            except Exception as e:
                logger.error(f"Scan cycle error: {e}", exc_info=True)
            with _scan_lock:
                _state['next_scan'] = datetime.now().isoformat()
            _wake_event.clear()
            _wake_event.wait(SCAN_INTERVAL_SECONDS or 300)
        else:
            # Market closed — sleep precisely until next open instead of
            # polling every 60s. Inside a 5-min pre-warm window, run the
            # warm-up routine first (once per day) so the 09:15 first scan
            # is as fast as the steady-state ones.
            secs_to_open = bu.seconds_until_market_open()
            today        = bu.today_ist()

            if 0 < secs_to_open <= PREWARM_LEAD_SEC and last_prewarm_date != today:
                _pre_warm()
                last_prewarm_date = today

            # Sleep until open — but cap at 30 min so the loop can react to
            # config changes / stop signals reasonably soon, and re-check
            # market state in case the system clock jumped.
            sleep_for = min(max(secs_to_open, 1), 1800)
            with _scan_lock:
                _state['next_scan'] = datetime.now().isoformat()
            _wake_event.clear()
            _wake_event.wait(sleep_for)

    with _scan_lock:
        _state['running'] = False
    logger.info("F&O scanner stopped")


# ── Public API ────────────────────────────────────────────────────────────────

def start():
    global _scanner_thread
    if _scanner_thread and _scanner_thread.is_alive():
        logger.info("F&O scanner already running")
        return
    _stop_event.clear()
    _scanner_thread = threading.Thread(
        target=_scanner_loop, name='FOScanner', daemon=True)
    _scanner_thread.start()
    logger.info("F&O scanner thread started")


def stop():
    _stop_event.set()
    if _scanner_thread:
        _scanner_thread.join(timeout=15)


def trigger_now():
    """Force an immediate scan cycle (for manual trigger via API).
    Bypasses the market-hours guard so the user can run a pre-market /
    after-hours scan on the last available data.
    """
    _manual_trigger.set()
    if _scanner_thread and _scanner_thread.is_alive():
        # Wake the sleeping loop without killing the thread.
        _wake_event.set()
    else:
        start()
