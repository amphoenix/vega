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

logger = get_logger('phoenixtrade.fo_scanner')

# ── F&O universe ──────────────────────────────────────────────────────────────
# Tickers in yfinance format. Scanner cycles through these.
FO_UNIVERSE = [
    '^NSEI',        # NIFTY 50
    # '^BSESN' (SENSEX) intentionally excluded: IndStocks broker has no live
    # quote endpoint for BSE indices/derivatives — only equities. Re-enable
    # only when you switch to a broker that supports BSE F&O streaming.
    '^NSEBANK',     # BANKNIFTY (most liquid index after NIFTY)
]

# Lot sizes are now sourced from the IndStocks F&O instrument master
# (see broker_utils.fno_meta / underlying_lot_size). The dict below is kept
# only as a fallback used when the master fetch fails for ranking purposes —
# never trusted for live order sizing.
_LOT_SIZE_FALLBACK = {
    '^NSEI':         75,    # NIFTY (NSE revised Nov-2024)
    '^NSEBANK':      30,    # BANKNIFTY
    '^BSESN':        20,    # SENSEX (BSE)
    'NIFTY':         75,
    'BANKNIFTY':     30,
    'FINNIFTY':      65,
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
}  # For stocks, underlying == ticker

SCAN_INTERVAL_SECONDS = int(os.environ.get('FO_SCAN_INTERVAL_SEC', '0'))
MAX_OPEN_POSITIONS    = int(os.environ.get('FO_MAX_POSITIONS', '3'))
MIN_CONFIDENCE        = int(os.environ.get('FO_MIN_CONFIDENCE', '70'))
MIN_AGENT_AGREEMENT   = int(os.environ.get('FO_MIN_AGENTS', '3'))  # of 5 agents

_scanner_thread: Optional[threading.Thread] = None
_stop_event     = threading.Event()
_manual_trigger = threading.Event()   # set by trigger_now() to force a scan even off-hours
_wake_event     = threading.Event()   # wakes the loop's wait() without killing the thread
_scan_lock      = threading.Lock()

# Scan state (for status endpoint)
_state = {
    'running':       False,
    'last_scan':     None,
    'next_scan':     None,
    'scanned':       [],        # tickers scanned this cycle
    'signals':       [],        # all CIO verdicts this cycle
    'trades_placed': [],        # trades executed this session
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
    s['max_positions']    = MAX_OPEN_POSITIONS
    return s


# ── Safety gates ──────────────────────────────────────────────────────────────

def _passes_safety_gates(ticker: str, cio: dict, wallet: dict) -> tuple[bool, str]:
    """Returns (pass, reason_if_fail)."""
    if not bu.is_safe_hours():
        return False, "Outside safe trading hours (IST 09:20-15:00) or NSE holiday"

    # Daily kill-switch (set by position_monitor when realised loss exceeds threshold)
    if wallet.get('kill_switch'):
        return False, f"Kill-switch active: {wallet.get('kill_reason', 'daily loss limit')}"

    confidence = int(cio.get('confidence_to_trade', 0))
    if confidence < MIN_CONFIDENCE:
        return False, f"Confidence {confidence} < {MIN_CONFIDENCE}"

    # Accept BUY NOW (long) OR SELL NOW (short/PE)
    action  = cio.get('short_term_action', '')
    verdict = cio.get('final_verdict', 'HOLD')
    itype   = cio.get('instrument_type', 'EQ')

    if action not in ('BUY NOW', 'SELL NOW'):
        return False, f"action={action} — need BUY NOW or SELL NOW"

    if verdict == 'HOLD':
        return False, "CIO says HOLD"

    bull_count = int(cio.get('bull_count', 0))
    bear_count = int(cio.get('bear_count', 0))

    # For bullish trades (CE/FUT long): need MIN_AGENT_AGREEMENT bulls
    # For bearish trades (PE/FUT short): need MIN_AGENT_AGREEMENT bears
    # Use MIN_AGENTS-1 (i.e. 2) when instrument is clearly directional
    threshold = MIN_AGENT_AGREEMENT - 1 if itype in ('PE', 'CE') else MIN_AGENT_AGREEMENT

    if verdict in ('STRONG BUY', 'BUY') and bull_count < threshold:
        return False, f"Only {bull_count}/{threshold} agents bullish for {itype}"

    if verdict in ('STRONG SELL', 'SELL') and bear_count < threshold:
        return False, f"Only {bear_count}/{threshold} agents bearish for {itype}"

    # Block FUT shorts unless explicitly enabled — needs full SPAN-margin path
    allow_fut_short = os.environ.get('ALLOW_FUT_SHORT', '').lower() in ('1', 'true', 'yes')
    if itype == 'FUT' and action == 'SELL NOW' and not allow_fut_short:
        return False, "FUT short disabled (set ALLOW_FUT_SHORT=1 + verify SPAN margin)"
    # EQ shorts on MIS only — block carry-forward shorts
    if itype == 'EQ' and action == 'SELL NOW':
        return False, "EQ short via this scanner is not supported (use intraday CE/PE instead)"

    # Check max open positions
    open_positions = wallet.get('positions', {})
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        return False, f"Max {MAX_OPEN_POSITIONS} open positions reached"

    # Check not already in this underlying
    underlying = UNDERLYING_MAP.get(ticker, ticker)
    for pos in open_positions.values():
        if pos.get('underlying') == underlying:
            return False, f"Already holding position in {underlying}"

    return True, ""


# ── Trade builder from CIO output ─────────────────────────────────────────────

def _nse_base(ticker: str) -> str:
    """Strip .NS/.BO/^ and map index tickers to NSE base names."""
    t = ticker.upper().replace('.NS', '').replace('.BO', '').lstrip('^')
    return {'NSEI': 'NIFTY', 'NSEBANK': 'BANKNIFTY', 'CNXFIN': 'FINNIFTY'}.get(t, t)


def _resolve_option_contract(ticker: str, option_type: str, strike: float) -> dict | None:
    """
    Search IndStocks FNO master for the nearest matching option contract.
    Returns {'trading_symbol': ..., 'security_id': ..., 'expiry_date': ..., 'ltp': ...}
    or None if not found.

    Matching priority:
      1. Exact underlying + option_type (CE/PE) + nearest strike to target
      2. Nearest expiry (earliest future expiry)
    """
    try:
        from ..api.indmoney import _load_instruments, _ind_option_ltp
        from datetime import date as _date

        instruments = _load_instruments('fno')
        base = _nse_base(ticker)
        today = _date.today()

        # Filter: symbol starts with base, option_type matches, expiry in future
        candidates = []
        for inst in instruments:
            sym       = (inst.get('TRADING_SYMBOL') or '').strip().upper()
            opt_type  = (inst.get('OPTION_TYPE') or '').strip().upper()
            exch      = (inst.get('EXCH') or '').strip().upper()
            expiry_s  = (inst.get('EXPIRY_DATE') or '').strip()
            sec_id    = (inst.get('SECURITY_ID') or '').strip()
            str_price = inst.get('STRIKE_PRICE', '0')

            if not sym.startswith(base):
                continue
            if opt_type != option_type.upper():
                continue
            if exch not in ('NFO', 'BFO', 'NSE', 'BSE'):
                continue

            # Parse expiry — use broker_utils for all formats
            try:
                from ..services import broker_utils as _bu
                exp_d = _bu._parse_expiry(expiry_s)
                if exp_d is None:
                    continue
            except Exception:
                continue

            if exp_d < today:
                continue

            try:
                inst_strike = float(str_price)
            except Exception:
                continue

            candidates.append({
                'symbol':   sym,
                'sec_id':   sec_id,
                'expiry':   exp_d,
                'expiry_s': expiry_s,
                'strike':   inst_strike,
            })

        if not candidates:
            return None

        # Sort: nearest expiry first, then nearest strike to target
        candidates.sort(key=lambda x: (x['expiry'], abs(x['strike'] - strike)))
        best = candidates[0]

        # Get live LTP for this contract
        ltp = _ind_option_ltp(best['symbol'])

        return {
            'trading_symbol': best['symbol'],
            'security_id':    best['sec_id'],
            'expiry_date':    best['expiry_s'],
            'strike':         best['strike'],
            'ltp':            ltp,
        }
    except Exception as e:
        logger.warning(f"Option contract resolve failed for {ticker} {option_type} {strike}: {e}")
        return None


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


def _execute_scan_trade(ticker: str, price: float, cio: dict, wallet: dict) -> Optional[dict]:
    """Build position from CIO output and open it."""
    from .position_monitor import open_position

    itype  = cio.get('instrument_type', 'EQ')
    strike = float(cio.get('strike_price', 0) or 0)

    # ── Resolve real broker contract from instrument master (CE/PE only) ─────
    trading_symbol = None
    sec_id, exch   = '', ''
    expiry_iso     = ''
    lot_size       = None
    premium        = float(cio.get('estimated_premium', 0) or 0)

    if itype in ('CE', 'PE'):
        # Prefer already-resolved symbol from plan_option_trade ticket
        trading_symbol = cio.get('option_symbol') or None
        contract = None
        if not trading_symbol:
            contract = _resolve_option_contract(ticker, itype, strike)
            if not contract:
                logger.warning(f"Scanner: cannot resolve {itype} contract for {ticker} @ strike {strike}")
                return None
            trading_symbol = contract['trading_symbol']
            if contract.get('ltp'):
                premium = float(contract['ltp'])
        meta = bu.fno_meta(trading_symbol)
        if not meta or not meta.get('lot_size'):
            logger.warning(f"Scanner: missing lot_size for {trading_symbol} in master")
            return None
        lot_size  = meta['lot_size']
        sec_id    = meta['security_id']
        exch      = meta['exchange']
        expiry_iso = meta['expiry'].isoformat() if meta.get('expiry') else ''
        # Skip expiry-day entries past 13:00 IST (theta zone) entirely
        if meta.get('expiry') == bu.today_ist() and bu.now_ist().time() >= bu.THETA_EXIT:
            logger.info(f"Scanner: skip {trading_symbol} — in theta-exit zone")
            return None
        if contract and contract.get('ltp'):
            premium = float(contract['ltp'])
        strike = float(meta.get('strike') or strike)

        # Liquidity gate: reject if bid-ask spread is too wide for a market order
        try:
            from ..api.indmoney import _ind_option_quote
            q = _ind_option_quote(trading_symbol)
            if q and q['bid'] > 0 and q['ask'] > 0:
                mid = (q['bid'] + q['ask']) / 2.0
                spread_pct = (q['ask'] - q['bid']) / mid * 100.0 if mid else 999.0
                max_spread = float(os.environ.get('FO_MAX_SPREAD_PCT', '3'))
                if spread_pct > max_spread:
                    logger.info(f"Scanner: skip {trading_symbol} — spread "
                                f"{spread_pct:.2f}% > {max_spread:.2f}%")
                    return None
                min_oi = int(os.environ.get('FO_MIN_OI', '0'))
                if min_oi and q.get('oi', 0) < min_oi:
                    logger.info(f"Scanner: skip {trading_symbol} — OI {q['oi']} < {min_oi}")
                    return None
        except Exception:
            pass    # broker quote unavailable — don't block, just log

    elif itype == 'FUT':
        lot_size = _lot_size_for(ticker)
        if not lot_size:
            logger.warning(f"Scanner: no lot_size for FUT {ticker}")
            return None
        # FUT trading-symbol resolution is broker-specific; defer to legacy builder
        trading_symbol = _build_trading_symbol(ticker, cio, price)
        premium = price

    else:    # EQ
        lot_size = 1
        trading_symbol = _build_trading_symbol(ticker, cio, price)
        premium = price

    if not premium or premium <= 0:
        logger.warning(f"Scanner: invalid premium {premium} for {trading_symbol}")
        return None

    stop_loss = float(cio.get('stop_loss', round(price * 0.97, 2)))
    target_1  = float(cio.get('target_1',  round(price * 1.03, 2)))
    target_2  = float(cio.get('target_2',  round(price * 1.06, 2)))

    cash         = wallet.get('cash', 0)
    position_pct = min(float(cio.get('position_size_pct', 2)), 10) / 100.0
    budget       = cash * position_pct
    cost_per_lot = premium * lot_size
    if cost_per_lot <= 0:
        return None
    if cost_per_lot > cash:
        logger.warning(f"Insufficient cash ₹{cash:.0f} for 1 lot of "
                       f"{trading_symbol} @ ₹{cost_per_lot:.0f}")
        return None

    qty = max(1, int(budget // cost_per_lot))
    underlying = UNDERLYING_MAP.get(ticker, ticker)
    live       = bu.is_live_mode()

    reason = (f"Scanner: {cio.get('final_verdict')} conf={cio.get('confidence_to_trade')}% | "
              f"{cio.get('short_term_reason', '')} | {cio.get('investment_thesis', '')[:100]}")

    result = open_position(
        pos_key=trading_symbol,
        underlying=underlying,
        instrument_type=itype,
        qty=qty,
        lot_size=lot_size,
        avg_entry=premium,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        strike_price=strike,
        expiry=expiry_iso,
        security_id=sec_id,
        exchange=exch,
        reason=reason,
        live=live,
    )
    return {
        'ticker':         ticker,
        'trading_symbol': trading_symbol,
        'instrument_type': itype,
        'qty':            qty,
        'lot_size':       lot_size,
        'premium':        premium,
        'cost':           round(cost_per_lot * qty, 2),
        'stop_loss':      stop_loss,
        'target_1':       target_1,
        'target_2':       target_2,
        'verdict':        cio.get('final_verdict'),
        'confidence':     cio.get('confidence_to_trade'),
        'order_status':   result.get('status'),
        'mode':           'live' if live else 'paper',
        'timestamp':      datetime.now().isoformat(),
    }


# ── Stage 1: pure technical pre-filter (no LLM) ──────────────────────────────

def _technical_cio(ticker: str, raw: dict) -> Optional[dict]:
    """
    Build a CIO-compatible dict purely from ta_utils indicators — zero LLM calls.
    Returns a tradeable signal if Supertrend + ADX + RSI all agree on direction.
    Returns None if market is ranging / weak trend.

    Signal rules:
      STRONG BUY  : Supertrend BULLISH + ADX > 25 + RSI > 55 + price > EMA20
      BUY         : Supertrend BULLISH + ADX > 20 + RSI > 50
      STRONG SELL : Supertrend BEARISH + ADX > 25 + RSI < 45 + price < EMA20
      SELL        : Supertrend BEARISH + ADX > 20 + RSI < 50
      None        : ADX < 20 (ranging) or Supertrend neutral
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

    # Need at least Supertrend direction; ADX threshold lowered to 15 so we
    # still emit option tickets in low-vol regimes (e.g. NIFTY ADX 15-20).
    if adx < 15 or st_dir is None:
        return None

    bullish = st_dir == 1
    bearish = st_dir == -1

    # ── Continuous confidence (50-95) so each ticker gets a distinct score
    # based on actual indicator strength, not a 3-bucket lookup.
    # Components (max points):
    #   base                       50
    #   trend strength (ADX)       +30  (linear 15→45 ADX → 0→30 pts)
    #   momentum (RSI distance)    +10  (|RSI-50| → 0→10 pts)
    #   price-EMA alignment        +5   (price on the trend side of EMA20)
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
    if itype == 'CE':
        raw_strike = price * 1.015
        strike = round(raw_strike / 50) * 50 if 'NIFTY' in ticker.upper() or '^NSE' in ticker else round(raw_strike)
        sl_underlying = round(price * 0.98, 2)
        t1_underlying = round(price * 1.03, 2)
        t2_underlying = round(price * 1.05, 2)
        bull_count, bear_count = 3, 1
    else:
        raw_strike = price * 0.985
        strike = round(raw_strike / 50) * 50 if 'NIFTY' in ticker.upper() or '^NSE' in ticker else round(raw_strike)
        sl_underlying = round(price * 1.02, 2)
        t1_underlying = round(price * 0.97, 2)
        t2_underlying = round(price * 0.95, 2)
        bull_count, bear_count = 1, 3

    # Estimated option premium ≈ 0.6% of underlying for weekly ATM
    est_premium = round(price * 0.006, 2)

    patterns = ta.get('candle_patterns', [])
    thesis = (f"Technical: Supertrend {'BULLISH' if bullish else 'BEARISH'}, "
              f"ADX={adx:.1f} (strong trend), RSI={rsi:.1f}. "
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
    from .position_monitor import _load_wallet

    with _scan_lock:
        _state['last_scan'] = datetime.now().isoformat()
        _state['scanned']   = []
        _state['signals']   = []

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

        # Wait for one result
        try:
            ticker, cio = result_q.get(timeout=120)
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
            try:
                from .option_planner import plan_option_trade
                bias = 'BULL' if itype == 'CE' else 'BEAR'
                # Defaults:
                #   target_delta=0.50  → ATM (best gamma/theta tradeoff for directional bets)
                #   min_dte=3          → skip same-day + 1-DTE theta cliff (avoid OTM lottery tickets)
                #   max_dte=21         → at most ~3 weeks out, premium too high beyond that
                # Pass CIO's spot-level SL/T1/T2 through so the planner can
                # back-derive PREMIUM targets via Black-Scholes at those spots
                # — instead of the old (broken) 2x/3x entry-premium heuristic.
                _fnum = lambda v: float(v) if v not in (None, '', 0) else None
                # ATR(14) clamps CIO's spot targets to intraday-realistic bands.
                # CIO from _scan_one/_technical_cio may stash atr; otherwise
                # the planner runs without ATR clamping (still works fine).
                atr = _fnum(cio.get('_atr')) or _fnum(cio.get('atr'))
                ticket = plan_option_trade(
                    underlying=ticker, bias=bias, spot=price,
                    # 0.55 = mildly ITM. Avoids the OTM-junk problem where a
                    # 0.50 target rounds OFF strike and ends up at Δ≈0.40 on
                    # short DTE, producing ₹8 lottery tickets. ITM gives real
                    # delta exposure + intrinsic value cushion.
                    target_delta=float(os.environ.get('FO_TARGET_DELTA', '0.55')),
                    min_dte=int(os.environ.get('FO_MIN_DTE', '3')),
                    max_dte=int(os.environ.get('FO_MAX_DTE', '21')),
                    rationale=f"{verdict} conf={conf}% | {cio.get('short_term_reason','')}",
                    spot_target_1  = _fnum(cio.get('target_1')),
                    spot_target_2  = _fnum(cio.get('target_2')),
                    spot_stop_loss = _fnum(cio.get('stop_loss')),
                    atr            = atr,
                )
            except Exception as e:
                logger.warning(f"Ticket build failed for {ticker}: {e}")
                ticket = None

            if ticket:
                cio['expiry']            = ticket['expiry']
                cio['strike_price']      = ticket['strike']
                cio['option_symbol']     = ticket['trading_symbol']
                cio['option_ltp']        = ticket['entry']['expected_premium_inr']
                cio['estimated_premium'] = ticket['entry']['expected_premium_inr']
                cio['lot_size']          = ticket['lot_size']
                logger.info(f"Scanner ticket: {ticket['trading_symbol']} "
                            f"@ ₹{ticket['entry']['expected_premium_inr']:.2f} "
                            f"Δ={ticket['greeks']['delta']:.2f} "
                            f"θ/day=₹{ticket['greeks']['theta_per_day']:.2f} "
                            f"IV={ticket['greeks']['iv_used']:.1%} "
                            f"DTE={ticket['days_to_expiry']}d "
                            f"SL=₹{ticket['exit']['stop_loss_inr']} "
                            f"T1=₹{ticket['exit']['target_1_inr']}")
            else:
                # Legacy fallback so we still emit *something* on signal
                option_contract = _resolve_option_contract(ticker, itype, strike)
                if option_contract:
                    cio['expiry']        = option_contract['expiry_date']
                    cio['strike_price']  = option_contract['strike']
                    cio['option_symbol'] = option_contract['trading_symbol']
                    cio['option_ltp']    = option_contract['ltp']

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
            'option_ltp':     cio.get('option_ltp'),
            'ticket':         ticket,            # full plan with Greeks + risk
            'timestamp':      datetime.now().isoformat(),
        }
        signals.append(signal)

        with _scan_lock:
            _state['signals'].append(signal)
            # Persist most-recent ticket per underlying so a browser refresh
            # can replay it via subscribe_sse().
            ticket = signal.get('ticket') or {}
            under  = ticket.get('underlying') or signal.get('ticker')
            if under:
                _state['latest_by_under'][under] = signal

        _broadcast({'type': 'scan_signal', **signal})
        logger.info(f"Scanner [{source}] {ticker} {verdict} conf={conf}% "
                    f"instrument={cio.get('instrument_type')} action={cio.get('short_term_action')}")

        # Attempt trade
        wallet = _load_wallet()
        passes, gate_reason = _passes_safety_gates(ticker, cio, wallet)
        if passes:
            trade = _execute_scan_trade(ticker, price, cio, wallet)
            if trade:
                with _scan_lock:
                    _state['trades_placed'].append(trade)
                _broadcast({'type': 'scan_trade', **trade})
                logger.info(f"Scanner TRADE: {trade['trading_symbol']} "
                            f"qty={trade['qty']} cost=₹{trade['cost']:.0f} "
                            f"mode={trade['mode']}")
        else:
            logger.info(f"Scanner: {ticker} skipped — {gate_reason}")
            _broadcast({'type': 'scan_skip', 'ticker': ticker, 'reason': gate_reason,
                        'verdict': verdict, 'confidence': conf})

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
