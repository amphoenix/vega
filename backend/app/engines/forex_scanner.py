"""
Forex (Currency Derivatives) Auto-Scanner — background service.

Mirrors the fo_scanner two-stage pipeline for NSE CDS currency pairs:
  Stage 1 — Technical pre-filter (Supertrend, ADX, RSI, EMA, MACD)
  Stage 2 — Cerebrum LLM confirmation (optional)

Instruments: FUTCUR (currency futures) on NSE CDS via Dhan.
Pairs: USDINR, EURINR, GBPINR, JPYINR

Market hours: 09:00 – 17:00 IST (NSE CDS segment)

Safety gates (mirror fo_scanner):
  1. CDS market hours only
  2. confidence_to_trade >= 60
  3. short_term_action == "BUY NOW" or "SELL NOW"
  4. No existing open position in same pair
  5. Sufficient cash
  6. Daily loss limit
"""
from __future__ import annotations

import os
import queue
import threading
import json
from datetime import timedelta
from typing import Optional

from ..shared.logger import get_logger
from ..shared import time as _mkt
from ..shared.time import now_ist
from ..dependencies import get_broker

logger = get_logger('vega.forex_scanner')

# ── Currency universe ────────────────────────────────────────────────────────
# Yahoo tickers for data → Dhan CDS base names for trading
_YAHOO_TO_CDS = {
    'USDINR=X': 'USDINR',
    'EURINR=X': 'EURINR',
    'GBPINR=X': 'GBPINR',
    'JPYINR=X': 'JPYINR',
}

_forex_universe_cache: list | None = None


def _get_forex_universe() -> list[str]:
    global _forex_universe_cache
    if _forex_universe_cache is None:
        from ..config import settings as _cfg
        raw = _cfg.forex_universe
        _forex_universe_cache = [t.strip() for t in raw.split(',') if t.strip()]
        logger.info(f"Forex universe: {_forex_universe_cache}")
    return _forex_universe_cache


class _UniverseProxy(list):
    def __iter__(self): return iter(_get_forex_universe())
    def __len__(self): return len(_get_forex_universe())
    def __getitem__(self, i): return _get_forex_universe()[i]
    def __contains__(self, v): return v in _get_forex_universe()
    def index(self, v, *a): return _get_forex_universe().index(v, *a)


FOREX_UNIVERSE = _UniverseProxy()

# Currency futures lot sizes (from Dhan / NSE CDS specification)
_LOT_SIZE_FALLBACK = {
    'USDINR': 1000,
    'EURINR': 1000,
    'GBPINR': 1000,
    'JPYINR': 100000,
}

# Strike step for currency options (future use)
_STRIKE_STEP = {
    'USDINR': 0.25,
    'EURINR': 0.25,
    'GBPINR': 0.25,
    'JPYINR': 0.25,
}

def _scan_interval():
    from ..config import settings as _cfg
    return _cfg.forex_scan_interval_sec

def _min_confidence():
    from ..config import settings as _cfg
    return _cfg.forex_min_confidence

def _max_risk_pct():
    from ..config import settings as _cfg
    return _cfg.forex_max_risk_pct

_scanner_thread: Optional[threading.Thread] = None
_stop_event     = threading.Event()
_manual_trigger = threading.Event()
_wake_event     = threading.Event()
_scan_lock      = threading.Lock()

# Ticket cache: lock entry price to first signal per pair per day
_ticket_cache: dict = {}

# Scan state (for status endpoint)
_state = {
    'running':       False,
    'last_scan':     None,
    'next_scan':     None,
    'scanned':       [],
    'signals':       [],
    'errors':        [],
    'latest_by_under': {},
}

# SSE broadcast
_sse_subscribers: list = []
_sse_lock = threading.Lock()


def subscribe_sse():
    q = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_subscribers.append(q)
        for sig in _state.get('latest_by_under', {}).values():
            try:
                q.put_nowait(json.dumps(
                    {'type': 'forex_scan_signal', '_replay': True, **sig},
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
    s['interval_seconds'] = _scan_interval()
    s['universe']         = list(FOREX_UNIVERSE)
    s['min_confidence']   = _min_confidence()
    return s


# ── Data fetching ────────────────────────────────────────────────────────────

def _fetch_forex_data(ticker: str) -> dict:
    """Fetch OHLCV + compute technicals for a currency pair.

    Data sources:
      1. Dhan broker for contract resolution + LTP
      2. yfinance for OHLCV candle data (Dhan chart API doesn't support FUTCUR)
    """
    from ..shared.indicators import CandleData, ema, rsi, atr, adx_full, supertrend, macd
    import yfinance as yf

    cds_base = _YAHOO_TO_CDS.get(ticker, ticker.replace('=X', ''))
    broker = get_broker()

    # ── Resolve nearest FUTCUR contract for LTP ───────────────────────────
    contract = _resolve_cur_future_cached(cds_base)
    sec_id = contract['security_id'] if contract else ''
    sym    = contract['trading_symbol'] if contract else cds_base

    # ── Live price via broker LTP ─────────────────────────────────────────
    ltp = None
    if contract:
        ltp = broker.get_ltp(sym, exchange='CUR', security_id=sec_id)

    # ── OHLCV candles: Dhan broker first, yfinance fallback ────────────────
    candles = []
    # 1) Try Dhan broker candles (FUTCUR daily)
    if contract:
        try:
            raw = broker.get_candles(sym, interval='1d', days=90,
                                    exchange='CUR', security_id=sec_id) or []
            for c in raw:
                candles.append({
                    'open': float(c.open), 'high': float(c.high),
                    'low': float(c.low), 'close': float(c.close),
                    'volume': int(c.volume or 0),
                })
        except Exception as e:
            logger.debug(f"Dhan OHLCV fetch failed for {cds_base}: {e}")

    # 2) Fallback to yfinance if Dhan didn't return enough candles
    if len(candles) < 20:
        yf_ticker = ticker if '=' in ticker else f"{cds_base}=X"
        try:
            df = yf.download(yf_ticker, period='60d', interval='1d', progress=False, auto_adjust=True)
            if df is not None and len(df) >= 20:
                # Flatten multi-level columns from yfinance (e.g. ('Open', 'USDINR=X') → 'Open')
                if hasattr(df.columns, 'droplevel') and df.columns.nlevels > 1:
                    df.columns = df.columns.droplevel(1)
                candles = []
                for _, row in df.iterrows():
                    candles.append({
                        'open': float(row['Open']), 'high': float(row['High']),
                        'low': float(row['Low']), 'close': float(row['Close']),
                        'volume': int(row.get('Volume', 0) or 0),
                    })
        except Exception as e:
            logger.warning(f"yfinance OHLCV fallback failed for {cds_base}: {e}")

    if len(candles) < 20:
        raise ValueError(f"Not enough candles for {cds_base}: {len(candles)}")

    price = ltp or candles[-1]['close']
    close_list = [c['close'] for c in candles]
    cd = [CandleData(c['open'], c['high'], c['low'], c['close']) for c in candles]

    # Compute technicals
    st    = supertrend(cd, period=10, multiplier=3.0)
    adx_r = adx_full(cd, period=14)
    macd_r = macd(close_list)
    rsi_v  = rsi(close_list, 14)
    ema20_v = ema(close_list, 20)
    ema50_v = ema(close_list, 50)
    atr_v   = atr(cd, 14)

    technicals = {
        'supertrend_dir': st['direction'] if st else None,
        'adx':           adx_r['adx']      if adx_r else 0,
        'adx_plus_di':   adx_r['plus_di']  if adx_r else 0,
        'adx_minus_di':  adx_r['minus_di'] if adx_r else 0,
        'rsi':    rsi_v or 50,
        'ema20':  ema20_v,
        'ema50':  ema50_v,
        'atr':    atr_v,
        'macd_hist':  macd_r['histogram'] if macd_r else 0,
        'macd_cross': macd_r['cross']     if macd_r else 'NONE',
    }

    return {
        'price':        price,
        'company_name': cds_base,
        'technicals':   technicals,
        'candles':      candles[-75:],
        'cds_base':     cds_base,
    }


# ── Cached contract resolver (avoid re-scanning instrument master every tick) ─

_contract_cache: dict = {}
_contract_cache_ts: float = 0


def _resolve_cur_future_cached(cds_base: str) -> Optional[dict]:
    """Cached wrapper — refreshes instrument lookup at most once per hour."""
    import time
    global _contract_cache_ts
    now = time.time()
    if cds_base in _contract_cache and (now - _contract_cache_ts) < 3600:
        return _contract_cache[cds_base]
    result = _resolve_cur_future(cds_base)
    if result:
        _contract_cache[cds_base] = result
        _contract_cache_ts = now
    return result


# ── Stage 1: pure technical pre-filter ───────────────────────────────────────

def _technical_cio(ticker: str, raw: dict) -> Optional[dict]:
    """
    Build a CIO-compatible dict from technicals — zero LLM.
    Adapted from fo_scanner._technical_cio for currency pairs.
    Currency-specific adjustments:
      - Lower ADX threshold (12 vs 15): currencies trend subtly
      - Tighter SL/TP: currencies move ~0.3-0.8% daily vs 1-3% for indices
    """
    ta      = raw.get('technicals', {})
    price   = float(raw.get('price', 0))
    company = raw.get('company_name', ticker)
    cds_base = raw.get('cds_base', ticker.replace('=X', ''))

    st_dir  = ta.get('supertrend_dir')
    adx     = ta.get('adx') or 0
    rsi_val = ta.get('rsi') or 50
    ema20   = ta.get('ema20') or price
    ema50   = ta.get('ema50') or price
    atr_val = ta.get('atr') or price * 0.005

    plus_di    = ta.get('adx_plus_di') or 0
    minus_di   = ta.get('adx_minus_di') or 0
    macd_cross = ta.get('macd_cross', 'NONE')
    macd_hist  = ta.get('macd_hist') or 0

    # Currency ADX threshold is lower — they trend more subtly
    if adx < 12 or st_dir is None:
        return None

    bullish = st_dir == 1
    bearish = st_dir == -1

    # Leading indicator confirmation
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

    # Continuous confidence (50-95)
    def _clamp(v, lo, hi): return max(lo, min(hi, v))
    trend_score = _clamp((adx - 12) * 1.0, 0, 30)
    if bullish:
        rsi_score = _clamp((rsi_val - 50) * 0.5, 0, 10)
        ema_score = 5 if price > ema20 else 0
        ema50_bonus = 3 if price > ema50 else 0
    else:
        rsi_score = _clamp((50 - rsi_val) * 0.5, 0, 10)
        ema_score = 5 if price < ema20 else 0
        ema50_bonus = 3 if price < ema50 else 0

    conf = int(round(50 + trend_score + rsi_score + ema_score + ema50_bonus))
    conf += confirms * 5
    conf -= contradicts * 8
    conf = _clamp(conf, 50, 95)

    # Verdict
    if bullish:
        if conf >= 80:    verdict, action = 'STRONG BUY', 'BUY NOW'
        elif conf >= 60:  verdict, action = 'BUY',        'BUY NOW'
        else:             verdict, action = 'BUY',        'WAIT'
        direction = 'LONG'
    elif bearish:
        if conf >= 80:    verdict, action = 'STRONG SELL', 'SELL NOW'
        elif conf >= 60:  verdict, action = 'SELL',        'SELL NOW'
        else:             verdict, action = 'SELL',        'WAIT'
        direction = 'SHORT'
    else:
        return None

    lot_size = _LOT_SIZE_FALLBACK.get(cds_base, 1000)

    # Currency SL/TP: tighter than equity indices
    # SL = 0.3% of spot, T1 = 0.5%, T2 = 0.8%
    if bullish:
        sl  = round(price * 0.997, 4)
        t1  = round(price * 1.005, 4)
        t2  = round(price * 1.008, 4)
    else:
        sl  = round(price * 1.003, 4)
        t1  = round(price * 0.995, 4)
        t2  = round(price * 0.992, 4)

    _conf_tag = (f"+DI={'>' if di_bullish else '<'}-DI "
                f"MACD_hist={'↑' if macd_bull else '↓' if macd_bear else '—'} "
                f"({confirms}/2 confirm)")
    thesis = (f"Technical: Supertrend {'BULLISH' if bullish else 'BEARISH'}, "
              f"ADX={adx:.1f}, RSI={rsi_val:.1f}, {_conf_tag}.")

    return {
        'final_verdict':       verdict,
        'consensus_score':     conf if bullish else -conf,
        'bull_count':          3 if bullish else 1,
        'bear_count':          1 if bullish else 3,
        'neutral_count':       1,
        'entry_price':         price,
        'stop_loss':           sl,
        'target_1':            t1,
        'target_2':            t2,
        'target_3':            round(t2 * (1.003 if bullish else 0.997), 4),
        'time_horizon':        'intraday',
        'investment_thesis':   thesis,
        'key_risks':           [f"ADX may weaken if {adx:.0f} drops below 15"],
        'position_size_pct':   2,
        'short_term_action':   action,
        'short_term_reason':   thesis,
        'instrument_type':     'FUT',
        'direction':           direction,
        'lot_size':            lot_size,
        'confidence_to_trade': conf,
        '_ticker':             ticker,
        '_price':              price,
        '_company':            company,
        '_cds_base':           cds_base,
        '_source':             'technical',
        '_atr':                atr_val,
    }


# ── Stage 2: Cerebrum LLM confirmation ───────────────────────────────────────

def _scan_one(ticker: str, llm_client) -> Optional[dict]:
    """Two-stage scan: technical pre-filter + optional LLM confirmation."""
    logger.info(f"Forex scanner: analysing {ticker}")
    try:
        raw = _fetch_forex_data(ticker)
    except Exception as e:
        logger.error(f"Forex data fetch failed for {ticker}: {e}")
        return None

    price   = float(raw.get('price', 0))
    company = raw.get('company_name', ticker)

    # Stage 1: pure technical check
    tech_cio = _technical_cio(ticker, raw)
    if tech_cio is None:
        logger.info(f"Forex scanner: {ticker} pre-filter SKIP (ADX weak / no trend)")
        return {
            'final_verdict': 'HOLD', 'confidence_to_trade': 10,
            'short_term_action': 'AVOID', 'instrument_type': 'FUT',
            '_ticker': ticker, '_price': price, '_company': company,
            '_cds_base': raw.get('cds_base', ''),
            '_source': 'prefilter',
        }

    logger.info(f"Forex scanner: {ticker} Stage-1 → {tech_cio['final_verdict']} "
                f"conf={tech_cio['confidence_to_trade']}% ({tech_cio['direction']})")

    # Technical-only mode — no LLM needed for currency derivatives
    return tech_cio


# ── Resolve currency futures contract from Dhan master ───────────────────────

def _resolve_cur_future(cds_base: str) -> Optional[dict]:
    """Find the nearest-month FUTCUR contract for a currency pair via Dhan master."""
    try:
        broker = get_broker()
        instruments = broker.load_instruments('fno') or []
    except Exception as e:
        logger.warning(f"Forex scanner: failed to load instruments: {e}")
        return None

    from datetime import date as _date
    today = _mkt.today_ist()
    best = None

    for inst in instruments:
        sym = str(inst.get('SEM_TRADING_SYMBOL', inst.get('TRADING_SYMBOL', ''))).strip().upper()
        inst_name = str(inst.get('SEM_INSTRUMENT_NAME', inst.get('INSTRUMENT_NAME', ''))).strip().upper()

        if inst_name != 'FUTCUR':
            continue
        if not sym.startswith(cds_base.upper()):
            continue

        # Parse expiry
        expiry_raw = inst.get('SEM_EXPIRY_DATE', inst.get('EXPIRY_DATE', ''))
        if not expiry_raw:
            continue
        expiry_str = str(expiry_raw).strip()
        if 'T' in expiry_str:
            expiry_str = expiry_str.split('T')[0]
        if ' ' in expiry_str and ':' in expiry_str:
            expiry_str = expiry_str.split(' ')[0]

        exp_date = None
        for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d %b %Y'):
            try:
                exp_date = __import__('datetime').datetime.strptime(expiry_str.upper(), fmt).date()
                break
            except Exception:
                continue
        if not exp_date:
            continue

        dte = (exp_date - today).days
        if dte < 0:
            continue  # expired

        # Dhan SEM_LOT_UNITS=1 for CDS means "1 lot", not "1 unit".
        # Use NSE-defined lot sizes: 1000 for USD/EUR/GBP, 100000 for JPY.
        lot = _LOT_SIZE_FALLBACK.get(cds_base, 1000)

        sec_id = str(inst.get('SEM_SMST_SECURITY_ID', inst.get('SECURITY_ID', ''))).strip()

        candidate = {
            'trading_symbol': sym,
            'security_id':    sec_id,
            'expiry':         exp_date.isoformat(),
            'dte':            dte,
            'lot_size':       lot,
            'exchange':       'CUR',
            'cds_base':       cds_base,
        }

        if best is None or dte < best['dte']:
            best = candidate

    return best


# ── Build trade ticket ───────────────────────────────────────────────────────

def _build_forex_ticket(cio: dict) -> Optional[dict]:
    """Build an executable trade ticket for a currency futures position."""
    cds_base = cio.get('_cds_base', '')
    if not cds_base:
        return None

    contract = _resolve_cur_future(cds_base)
    if not contract:
        logger.warning(f"Forex scanner: no FUTCUR contract found for {cds_base}")
        return None

    price = float(cio.get('_price', 0))
    direction = cio.get('direction', 'LONG')
    atr_val = float(cio.get('_atr', 0) or price * 0.005)

    # SL/TP based on ATR: SL = 1.5×ATR, T1 = 2×ATR, T2 = 3×ATR
    if direction == 'LONG':
        sl  = round(price - atr_val * 1.5, 4)
        t1  = round(price + atr_val * 2.0, 4)
        t2  = round(price + atr_val * 3.0, 4)
    else:
        sl  = round(price + atr_val * 1.5, 4)
        t1  = round(price - atr_val * 2.0, 4)
        t2  = round(price - atr_val * 3.0, 4)

    # SL in points for risk calculation
    sl_points = abs(price - sl)
    lot_size = contract['lot_size']
    max_loss = sl_points * lot_size

    ticket = {
        'underlying':        cds_base,
        'trading_symbol':    contract['trading_symbol'],
        'display_symbol':    f"{cds_base} FUT",
        'security_id':       contract['security_id'],
        'exchange':          'CUR',
        'instrument_type':   'FUTCUR',
        'option_type':       '',
        'direction':         direction,
        'expiry':            contract['expiry'],
        'days_to_expiry':    contract['dte'],
        'lot_size':          lot_size,
        'trade_mode':        'forex',
        'entry': {
            'expected_premium_inr': price,
            'window_ist':           'CDS 09:05-16:30',
        },
        'exit': {
            'stop_loss_inr': sl,
            'target_1_inr':  t1,
            'target_2_inr':  t2,
        },
        'risk': {
            'sl_points':    round(sl_points, 4),
            'max_loss_inr': round(max_loss, 2),
        },
        'greeks': {
            'delta': 1.0 if direction == 'LONG' else -1.0,
            'theta_per_day': 0,
            'iv_used': 0,
        },
    }
    return ticket


# ── Scan cycle ───────────────────────────────────────────────────────────────

def _run_scan_cycle(llm_client):
    """One full scan cycle across all forex pairs."""
    with _scan_lock:
        _state['last_scan'] = now_ist().isoformat()
        _state['scanned']   = []
        _state['signals']   = []

    # Clear ticket cache at start of new trading day
    _now = now_ist()
    if _now.hour == 9 and _now.minute < (_scan_interval() // 60 + 4):
        _ticket_cache.clear()
        logger.info("Forex scanner: ticket cache cleared (new trading day)")

    _broadcast({'type': 'forex_scan_start', 'universe': list(FOREX_UNIVERSE),
                'timestamp': now_ist().isoformat()})

    result_q: queue.Queue = queue.Queue()

    def _worker(ticker: str):
        try:
            if _stop_event.is_set():
                result_q.put((ticker, None))
                return
            idx = FOREX_UNIVERSE.index(ticker) + 1
            _broadcast({'type': 'forex_scan_analysing', 'ticker': ticker,
                        'ticker_clean': ticker.replace('=X', ''),
                        'index': idx, 'total': len(FOREX_UNIVERSE),
                        'timestamp': now_ist().isoformat()})
            with _scan_lock:
                _state['scanned'].append(ticker)
            cio = _scan_one(ticker, llm_client)
            result_q.put((ticker, cio))
        except Exception as exc:
            logger.error(f"Forex worker error {ticker}: {exc}")
            result_q.put((ticker, None))

    # Launch tickers in parallel (max 4)
    signals = []
    pending = list(FOREX_UNIVERSE)
    active  = []

    while (pending or active) and not _stop_event.is_set():
        while pending and len(active) < 4:
            t = pending.pop(0)
            th = threading.Thread(target=_worker, args=(t,), daemon=True)
            th.start()
            active.append(th)

        try:
            ticker, cio = result_q.get(timeout=120)
        except queue.Empty:
            break

        active = [th for th in active if th.is_alive()]

        if not cio:
            continue

        price     = float(cio.get('_price', 0))
        verdict   = cio.get('final_verdict', 'HOLD')
        conf      = int(cio.get('confidence_to_trade', 0))
        source    = cio.get('_source', 'technical')
        direction = cio.get('direction', 'LONG')
        cds_base  = cio.get('_cds_base', '')

        # Build trade ticket
        ticket = None
        if conf >= 60 and verdict not in ('HOLD',):
            _cache_key = f"{cds_base}:{direction}"
            cached = _ticket_cache.get(_cache_key)
            if cached:
                ticket = cached
            else:
                ticket = _build_forex_ticket(cio)
                if ticket:
                    _ticket_cache[_cache_key] = ticket

        signal = {
            'ticker':         ticker,
            'company':        cio.get('_company', ticker),
            'cds_base':       cds_base,
            'verdict':        verdict,
            'confidence':     conf,
            'action':         cio.get('short_term_action'),
            'direction':      direction,
            'instrument':     'FUT',
            'expiry':         ticket['expiry'] if ticket else None,
            'lot_size':       ticket['lot_size'] if ticket else _LOT_SIZE_FALLBACK.get(cds_base, 1000),
            'entry':          price,
            'stop_loss':      ticket['exit']['stop_loss_inr'] if ticket else cio.get('stop_loss'),
            'target_1':       ticket['exit']['target_1_inr'] if ticket else cio.get('target_1'),
            'target_2':       ticket['exit']['target_2_inr'] if ticket else cio.get('target_2'),
            'thesis':         cio.get('investment_thesis', cio.get('short_term_reason', ''))[:200],
            'source':         source,
            'ticket':         ticket,
            'cio':            cio,
            'timestamp':      now_ist().isoformat(),
        }
        signals.append(signal)

        with _scan_lock:
            _state['signals'].append(signal)
            if cds_base:
                _state['latest_by_under'][cds_base] = signal

        # SSE broadcast (strip heavy fields)
        _bcast = {k: v for k, v in signal.items() if k != 'cio'}
        _broadcast({'type': 'forex_scan_signal', **_bcast})
        logger.info(f"Forex scanner [{source}] {ticker} {verdict} conf={conf}% "
                    f"direction={direction}")

        # ── Auto-entry via order executor ────────────────────────────────
        if ticket and conf >= _min_confidence():
            try:
                _try_forex_auto_entry(signal)
            except Exception as e:
                logger.warning(f"Forex scanner: auto-entry failed for {ticker}: {e}")

    _broadcast({'type': 'forex_scan_complete', 'signals': len(signals),
                'timestamp': now_ist().isoformat()})
    logger.info(f"Forex scan cycle complete: {len(signals)} signals across "
                f"{len(FOREX_UNIVERSE)} pairs")


# ── Auto-entry for forex ─────────────────────────────────────────────────────

def _try_forex_auto_entry(signal: dict) -> Optional[dict]:
    """Place a currency futures entry if all gates pass."""
    from ..config import settings as _cfg

    if not _cfg.forex_auto_trade:
        logger.info(f"[forex] AUTO-TRADE DISABLED — skipping {signal.get('cds_base')}")
        return None

    ticket = signal.get('ticket')
    if not ticket:
        return None

    conf = int(signal.get('confidence', 0))
    sym = ticket.get('trading_symbol', '').strip().upper()
    direction = ticket.get('direction', 'LONG')
    cds_base = signal.get('cds_base', '')

    if not sym:
        return None

    # Check not already tracking this pair
    from ..infrastructure.db import tracked_positions as tp
    existing = tp.list_tracked()
    for rec in existing:
        t = (rec.get('ticket') or {})
        if t.get('underlying', '') == cds_base and t.get('trade_mode') == 'forex':
            logger.info(f"[forex] Skip {cds_base} — already tracking forex position")
            return None

    # Capital check — use forex-specific capital from config
    from ..config import settings as _cfg
    _paper = _cfg.forex_mode == 'paper'
    if _paper:
        avail = float(_cfg.forex_capital_inr)
    else:
        try:
            avail = get_broker().get_available_cash() or 0
        except Exception:
            avail = 0

    max_loss = float(ticket.get('risk', {}).get('max_loss_inr', 0))
    risk_limit = avail * (_max_risk_pct() / 100.0)
    if max_loss > risk_limit:
        logger.warning(f"[forex] CAPITAL BLOCK {sym} — max_loss ₹{max_loss:.0f} > "
                       f"limit ₹{risk_limit:.0f}")
        return None

    # Place BUY order for currency futures
    lot_size = int(ticket.get('lot_size', 1000))
    from ..config import settings as _cfg
    num_lots = _cfg.forex_max_lots_per_trade
    qty = lot_size * num_lots

    logger.info(f"[forex] AUTO-ENTRY: {direction} {sym} qty={qty} conf={conf}%")

    from .order_executor import _place_cur_buy, _place_cur_sell, _order_broadcast

    _sec_id = str(ticket.get('security_id', '') or '')
    if direction == 'LONG':
        order_result = _place_cur_buy(sym, qty, security_id=_sec_id)
    else:
        order_result = _place_cur_sell(sym, qty, security_id=_sec_id)

    if order_result and order_result.get('success'):
        record = tp.add_tracked(ticket, qty=qty,
                                notes=f"Forex auto-entry conf={conf}% | {cds_base}")
        _entry_evt = {
            'type': 'order_update', 'severity': 'success',
            'title': f"Forex {direction}: {ticket.get('display_symbol', sym)}",
            'status': 'ENTRY_PLACED', 'symbol': sym,
            'trade_mode': 'forex',
            'txn_type': 'BUY' if direction == 'LONG' else 'SELL',
            'qty': qty,
            'message': f"Confidence {conf}% — SL ₹{ticket['exit']['stop_loss_inr']:.4f}",
            'timestamp': _mkt.now_ist().isoformat(),
        }
        _broadcast(_entry_evt)
        _order_broadcast(_entry_evt)
        logger.info(f"[forex] Entry success: {sym} tracked as {record.get('id')}")
        return record
    else:
        err = (order_result or {}).get('error', 'Unknown error')
        logger.error(f"[forex] Entry failed: {sym} — {err}")
        return None


# ── Fast reversal check ──────────────────────────────────────────────────────

REVERSAL_CHECK_INTERVAL = 30

def _fast_reversal_check():
    """Lightweight Stage-1-only check for direction flips on open forex positions."""
    try:
        from ..infrastructure.db import tracked_positions as tp
        from .order_executor import try_auto_exit
        positions = tp.list_tracked()
    except Exception:
        return

    if not positions:
        return

    for rec in positions:
        ticket = rec.get('ticket') or {}
        if ticket.get('trade_mode') != 'forex':
            continue

        underlying = ticket.get('underlying', '')
        pos_dir = ticket.get('direction', 'LONG')

        # Find the Yahoo ticker for this pair
        yahoo_ticker = None
        for yt, base in _YAHOO_TO_CDS.items():
            if base == underlying:
                yahoo_ticker = yt
                break
        if not yahoo_ticker:
            continue

        try:
            raw = _fetch_forex_data(yahoo_ticker)
            tech = _technical_cio(yahoo_ticker, raw)
        except Exception:
            continue

        if tech is None:
            continue

        tech_dir = tech.get('direction', 'LONG')
        tech_conf = int(tech.get('confidence_to_trade', 0))

        # Direction flip
        is_flip = (
            (pos_dir == 'LONG' and tech_dir == 'SHORT' and tech_conf >= 60) or
            (pos_dir == 'SHORT' and tech_dir == 'LONG' and tech_conf >= 60)
        )
        if not is_flip:
            continue

        # Get current price for exit
        current_price = float(raw.get('price', 0))
        logger.warning(f"[forex reversal] FLIP {underlying}: holding {pos_dir} but "
                       f"Stage-1 says {tech_dir} conf={tech_conf}%. Exiting.")
        _broadcast({
            'type': 'order_update', 'severity': 'warning',
            'title': f"Forex FLIP: {ticket.get('display_symbol', underlying)}",
            'status': 'THESIS_FLIP', 'symbol': ticket.get('trading_symbol', ''),
            'txn_type': 'SELL',
            'message': f"Direction flipped {pos_dir}→{tech_dir} conf={tech_conf}%. Exiting.",
            'timestamp': now_ist().isoformat(),
        })
        try_auto_exit(rec['id'], 'thesis_flip', rec, current_price)


# ── Scanner loop ─────────────────────────────────────────────────────────────

def _scanner_loop():
    logger.info(f"Forex scanner started (technical-only, "
                f"sleep={_scan_interval()}s between cycles, "
                f"universe={len(FOREX_UNIVERSE)} pairs)")

    with _scan_lock:
        _state['running'] = True

    llm_client = None

    while not _stop_event.is_set():
        manual = _manual_trigger.is_set()
        if manual or _mkt.is_cds_hours():
            if manual:
                _manual_trigger.clear()
            try:
                _run_scan_cycle(llm_client)
            except Exception as e:
                logger.error(f"Forex scan cycle error: {e}", exc_info=True)

            _sleep_sec = _scan_interval() or 180
            with _scan_lock:
                _state['next_scan'] = (now_ist() + timedelta(seconds=_sleep_sec)).isoformat()

            # Sleep with periodic reversal checks
            _remaining = _sleep_sec
            while _remaining > 0 and not _stop_event.is_set():
                chunk = min(_remaining, REVERSAL_CHECK_INTERVAL)
                _wake_event.clear()
                _wake_event.wait(chunk)
                _remaining -= chunk
                if _wake_event.is_set() or _stop_event.is_set():
                    break
                if _remaining > 0 and _mkt.is_cds_hours():
                    try:
                        _fast_reversal_check()
                    except Exception as e:
                        logger.debug(f"[forex reversal] check error: {e}")
        else:
            # CDS closed — sleep until next open
            secs_to_open = _mkt.seconds_until_cds_open()
            sleep_for = min(max(secs_to_open, 1), 1800)
            with _scan_lock:
                _state['next_scan'] = (now_ist() + timedelta(seconds=sleep_for)).isoformat()
            _wake_event.clear()
            _wake_event.wait(sleep_for)

    with _scan_lock:
        _state['running'] = False
    logger.info("Forex scanner stopped")


# ── Public API ───────────────────────────────────────────────────────────────

def start():
    global _scanner_thread
    if _scanner_thread and _scanner_thread.is_alive():
        logger.info("Forex scanner already running")
        return
    _stop_event.clear()
    _scanner_thread = threading.Thread(
        target=_scanner_loop, name='ForexScanner', daemon=True)
    _scanner_thread.start()
    logger.info("Forex scanner thread started")


def stop():
    _stop_event.set()
    _wake_event.set()
    if _scanner_thread:
        _scanner_thread.join(timeout=15)


def trigger_now():
    """Force an immediate scan cycle (manual trigger via API)."""
    _manual_trigger.set()
    if _scanner_thread and _scanner_thread.is_alive():
        _wake_event.set()
    else:
        start()
