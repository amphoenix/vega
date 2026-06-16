"""
INDmoney (INDstocks) broker integration.

REST base:      https://api.indstocks.com
WebSocket feed: wss://ws-prices.indstocks.com/api/v1/ws/prices
Order updates:  wss://ws-order-updates.indstocks.com

Auth: Bearer token from indstocks.com dashboard → API section.
      Set INDMONEY_ACCESS_TOKEN in .env (no OAuth flow needed).

Endpoints:
  GET  /api/indmoney/status               — connection status
  GET  /api/indmoney/profile              — account profile + funds
  GET  /api/indmoney/tick/<ticker>        — single live LTP (WebSocket cache → REST fallback)
  GET  /api/indmoney/stream/<ticker>      — SSE backed by WebSocket price feed
  GET  /api/indmoney/quote/<ticker>       — full quote (OHLC, depth, 52W, circuit limits)
  GET  /api/indmoney/positions            — open positions
  GET  /api/indmoney/holdings            — long-term holdings
  GET  /api/indmoney/order-book           — today's orders
  POST /api/indmoney/order               — place order (BUY/SELL)
  POST /api/indmoney/order/cancel        — cancel order
"""

import os
import json
import time
import queue as _queue
import threading
import requests as _requests
from datetime import datetime
from flask import current_app, request, jsonify, Response

from . import indmoney_bp
from ..utils.logger import get_logger

logger = get_logger('vega.api.indmoney')

# ── Helpers ───────────────────────────────────────────────────────────────────

_SSE_MAX_MINUTES = 30                        # hard cap on SSE connection duration

def _safe_error(e: Exception) -> str:
    """Sanitize exception for API response — hide internal paths and tracebacks."""
    msg = str(e)
    # Strip file paths
    import re
    msg = re.sub(r'(?:/[^\s:]+/)+[^\s:]+\.py', '<internal>', msg)
    # Truncate to a sane length
    if len(msg) > 200:
        msg = msg[:200] + '...'
    return msg

# ── Config ────────────────────────────────────────────────────────────────────
# Token is read fresh on every use so editing .env + restarting just the
# scanner thread (or a SIGHUP) is enough — no full-process restart needed.
BASE_URL      = 'https://api.indstocks.com'
WS_PRICE_URL  = 'wss://ws-prices.indstocks.com/api/v1/ws/prices'
WS_ORDER_URL  = 'wss://ws-order-updates.indstocks.com/api/v1/ws/trades'


def _live_trading_enabled() -> bool:
    """True only when LIVE_TRADING_ENABLED=true in .env. Default: False (paper mode)."""
    return os.environ.get('LIVE_TRADING_ENABLED', 'false').strip().lower() == 'true'


def _access_token() -> str:
    """Always read fresh from env so .env edits + dotenv-reload pick up
    the new JWT without a full process restart."""
    return os.environ.get('INDMONEY_ACCESS_TOKEN', '')

# Backwards-compat alias — older code imports ACCESS_TOKEN as a module global.
# Keep it as a property-like object so reads always re-fetch from env.
class _TokenProxy:
    def __bool__(self):  return bool(_access_token())
    def __str__(self):   return _access_token()
    def __eq__(self, o): return _access_token() == o
    def __ne__(self, o): return _access_token() != o
    __hash__ = None  # unhashable — hash changes with token rotation, unsafe as dict key
    def startswith(self, p): return _access_token().startswith(p)
    def __format__(self, spec): return format(_access_token(), spec)
ACCESS_TOKEN = _TokenProxy()

def _headers():
    return {'Authorization': _access_token(), 'Content-Type': 'application/json'}

def _connected():
    return bool(_access_token())


# ── Instrument token cache: "SBIN.NS" → "NSE:2885" ───────────────────────────
# INDstocks uses numeric security_id / scrip-codes like "NSE_2885".
# We maintain a small cache populated on first lookup.
_scrip_cache: dict[str, str] = {}   # ticker → "NSE_2885" format
_scrip_lock  = threading.Lock()
_inst_master: dict = {}              # source → instrument list (fetched once per source)
_inst_lock   = threading.Lock()


def _load_instruments(source: str = 'equity') -> list[dict]:
    """
    Fetch instrument master CSV from IndStocks.
    source: 'equity' | 'fno' | 'index'
    CSV columns: EXCH, SECURITY_ID, TRADING_SYMBOL, CUSTOM_SYMBOL, SERIES, ...
    Cached in process memory per source.
    """
    global _inst_master
    # Fast path: return cached result without HTTP
    with _inst_lock:
        if not isinstance(_inst_master, dict):
            _inst_master = {}
        cached = _inst_master.get(source)
        if cached:  # non-empty list = valid cache hit
            return cached

    # Fetch OUTSIDE the lock so other threads aren't blocked for up to 20s
    try:
        import csv, io as _io
        r = _requests.get(
            f'{BASE_URL}/market/instruments',
            headers=_headers(),
            params={'source': source},
            timeout=20,
        )
        if not r.ok:
            logger.warning(f"Instruments({source}) HTTP {r.status_code}: {r.text[:200]}")
            rows = []
        else:
            reader = csv.DictReader(_io.StringIO(r.text))
            rows = list(reader)
            logger.info(f"Loaded {len(rows)} {source} instruments from IndStocks")
            if rows:
                logger.debug(f"Instrument CSV columns: {list(rows[0].keys())}")
    except Exception as e:
        logger.warning(f"Instrument load({source}) failed: {e}")
        rows = []

    # Store result; another thread may have beaten us — keep theirs if non-empty
    with _inst_lock:
        if not _inst_master.get(source):
            _inst_master[source] = rows
    return _inst_master.get(source, rows)


# ── Friendly label cache (broker TRADING_SYMBOL → IND CUSTOM_SYMBOL) ────────
# Pure passthrough from IndStocks' instrument-master CSV (CUSTOM_SYMBOL column).
# Not constructed here — we only look up what IND already shipped.
_display_cache: dict[str, str] = {}
_equity_sym_index: dict[str, str] = {}  # TRADING_SYMBOL.upper() → CUSTOM_SYMBOL


def _build_equity_sym_index() -> None:
    """Build a O(1) lookup dict from equity instrument master.
    Called lazily on first _display_symbol equity miss."""
    if _equity_sym_index:
        return  # already built
    rows = _load_instruments('equity')
    for inst in rows:
        t_sym = (inst.get('TRADING_SYMBOL') or inst.get('tradingsymbol') or '').strip().upper()
        if t_sym:
            _equity_sym_index[t_sym] = (
                inst.get('CUSTOM_SYMBOL') or inst.get('custom_symbol') or ''
            ).strip()


def _display_symbol(ticker: str) -> str:
    """Return IndStocks' friendly label for the given ticker, or '' if absent.

    Source per master (all values come straight from IndStocks):
      - F&O:    CUSTOM_SYMBOL  (e.g. 'NIFTY 28 JUL 23800 CE')
      - Equity: CUSTOM_SYMBOL  (e.g. 'RELIANCE INDUSTRIES LTD.')
      - Index:  SEGMENT        (e.g. 'NIFTY 50', 'SENSEX', 'BANK NIFTY')

    For indices, the yfinance code (^NSEI) is translated to IND's
    SECURITY_ID via _scrip_code first, then we look up the row's SEGMENT.

    Result is cached (process-local). Misses are NOT cached when the master
    fetch failed (so transient 503s don't pollute future lookups).
    """
    if not ticker:
        return ''
    sym = ticker.strip().upper()
    if sym in _display_cache:
        return _display_cache[sym]

    is_index = ticker.startswith('^') or sym in (
        'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX',
    )

    # ── Index path: SECURITY_ID lookup → SEGMENT field ──────────────────────
    if is_index:
        code = _scrip_code(ticker)            # e.g. 'NSE_40000001'
        idx_rows = _load_instruments('index')
        if not idx_rows:
            return ''                          # master fetch failed; don't cache
        if code:
            target_sid = code.split('_', 1)[-1]
            for inst in idx_rows:
                if str(inst.get('SECURITY_ID', '')).strip() == target_sid:
                    label = (inst.get('SEGMENT') or '').strip()
                    _display_cache[sym] = label
                    return label
        _display_cache[sym] = ''
        return ''

    # ── F&O path: same disambiguation rule as everywhere else ───────────────
    # Multiple weekly contracts share a TRADING_SYMBOL (e.g. NIFTY-May2026-
    # 24350-CE matches 12-May, 19-May and 26-May rows). _resolve_fo_instrument
    # picks the soonest future expiry — keep display in sync with that so the
    # chart title matches the price + scrip code.
    # Not cached: the "soonest future" answer changes after each weekly expiry.
    if any(tok in sym for tok in ('-CE', '-PE', '-FUT')):
        try:
            inst = _resolve_fo_instrument(sym)
        except Exception:
            inst = None
        if inst:
            return (inst.get('CUSTOM_SYMBOL') or inst.get('custom_symbol') or '').strip()
        return ''

    # ── Equity path: TRADING_SYMBOL → CUSTOM_SYMBOL (O(1) via prebuilt index)
    _build_equity_sym_index()
    bare = sym.replace('.NS', '').replace('.BO', '')
    for candidate in (sym, bare):
        cs = _equity_sym_index.get(candidate)
        if cs is not None:
            _display_cache[sym] = cs
            return cs

    # Only cache the miss if index was populated (master loaded successfully).
    if _equity_sym_index:
        _display_cache[sym] = ''
    return ''


def _scrip_code(ticker: str, source: str = 'equity') -> str | None:
    """
    Convert Yahoo-style ticker (SBIN.NS, SBIN.BO, ^NSEI) → IndStocks scrip code.
    Returns "NSE_3045" format (exchange_security_id), or None if not found.
    Index tickers (^NSEI, ^NSEBANK) auto-switch to source='index'.
    """
    # Normalize common caret-prefixed misnomers (^BANKNIFTY → ^NSEBANK)
    from .market import _INDEX_ALIASES
    ticker = _INDEX_ALIASES.get(ticker.upper().strip(), ticker)
    is_index = ticker.startswith('^') or ticker.upper() in (
        'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX'
    )

    cache_key = f"{'index' if is_index else source}:{ticker}"
    with _scrip_lock:
        if cache_key in _scrip_cache:
            return _scrip_cache[cache_key]

    # ── Index fast-path ────────────────────────────────────────────────────────
    # Index master (source='index') only has EXCH/SEGMENT/SECURITY_ID — no
    # TRADING_SYMBOL. We maintain a known map and supplement it from the live
    # index master so new indices are picked up automatically.
    _INDEX_SCRIP_KNOWN = {
        '^NSEI':      'NSE_40000001',   # NIFTY 50
        '^NSEBANK':   'NSE_40000003',   # BANK NIFTY
        '^CNXFIN':    'NSE_40000100',   # Nifty Financial Services
        '^NSEMDCP50': 'NSE_40000017',   # Nifty Midcap 50
        '^INDIAVIX':  'NSE_40000107',   # India VIX
        '^BSESN':     'BSE_40000006',   # SENSEX
        '^CNXIT':     'NSE_40000004',   # NIFTY IT
        '^CNXPHARMA': 'NSE_40000005',   # NIFTY PHARMA
        'NIFTY':      'NSE_40000001',
        'BANKNIFTY':  'NSE_40000003',
        'FINNIFTY':   'NSE_40000100',
        'SENSEX':     'BSE_40000006',
    }
    if is_index:
        code = _INDEX_SCRIP_KNOWN.get(ticker.upper())
        if code:
            with _scrip_lock:
                _scrip_cache[cache_key] = code
            logger.debug(f"Resolved index {ticker} → {code}")
            return code
        # Try live index master (has EXCH + SECURITY_ID only)
        idx_rows = _load_instruments('index')
        for row in idx_rows:
            exch   = row.get('EXCH', 'NSE').strip()
            sec_id = row.get('SECURITY_ID', '').strip()
            if sec_id:
                candidate = f"{exch}_{sec_id}"
                if candidate == ticker.upper():
                    with _scrip_lock:
                        _scrip_cache[cache_key] = candidate
                    return candidate
        logger.warning(f"Unknown index ticker {ticker} — not in known map or index master")
        return None

    # ── Equity / F&O lookup ───────────────────────────────────────────────────
    sym      = ticker.upper().replace('.NS', '').replace('.BO', '').lstrip('^')
    exchange = 'BSE' if ticker.upper().endswith('.BO') else 'NSE'

    # Yahoo Finance NSE symbol → IndStocks TRADING_SYMBOL overrides
    # Tata Motors renamed its internal trading symbol post-demerger
    _INDSTOCKS_SYM = {
        'TATAMOTORS': 'TMCV',
    }
    sym = _INDSTOCKS_SYM.get(sym, sym)

    instruments = _load_instruments(source)
    best = None   # prefer SERIES=EQ, accept any series as fallback
    for inst in instruments:
        t_sym  = inst.get('TRADING_SYMBOL', '').strip().upper()
        t_exch = inst.get('EXCH', '').strip().upper()
        sec_id = inst.get('SECURITY_ID', '').strip()
        series = inst.get('SERIES', '').strip().upper()

        if t_sym == sym and t_exch == exchange and sec_id:
            code = f"{exchange}_{sec_id}"
            if series == 'EQ':
                best = code
                break
            if best is None:
                best = code

    if best:
        with _scrip_lock:
            _scrip_cache[cache_key] = best
        logger.debug(f"Resolved {ticker} → {best}")
        return best

    # ── F&O fallback ──────────────────────────────────────────────────────────
    # If equity master had no match, the ticker may be an option / futures
    # contract symbol like "NIFTY-APR2026-23700-PE" or "NIFTY-APR2026-FUT".
    # Resolve via the F&O instrument master.
    fno_inst = _resolve_fo_instrument(ticker)
    if fno_inst:
        code = _fo_scrip_code(fno_inst)
        if code:
            with _scrip_lock:
                _scrip_cache[cache_key] = code
            logger.debug(f"Resolved F&O {ticker} → {code}")
            return code

    logger.warning(f"No IndStocks instrument found for {ticker} (total={len(instruments)})")
    return None


def _security_id(ticker: str, source: str = 'equity') -> str | None:
    """Return the numeric security_id for a ticker."""
    code = _scrip_code(ticker, source)
    return code.split('_', 1)[1] if code else None


def _exchange(ticker: str) -> str:
    return 'BSE' if ticker.upper().endswith('.BO') else 'NSE'


# ── WebSocket price feed ──────────────────────────────────────────────────────
_ws_thread:    threading.Thread | None = None
_ws_instance                           = None
_ws_lock       = threading.Lock()

_tick_cache:   dict[str, dict] = {}   # scrip_code → latest tick
_tick_lock     = threading.Lock()

_subscribers:  dict[str, set]  = {}   # scrip_code → set of Queue
_sub_lock      = threading.Lock()

# Tick callbacks — fired synchronously on every received tick. Keep handlers
# fast (μs-ms), spawn threads for any heavy work to avoid blocking the WS reader.
_tick_callbacks: dict[str, list] = {}   # scrip_code → [callable(tick_dict)]
_callback_lock = threading.Lock()


def register_tick_callback(scrip_code_or_secid: str, fn) -> None:
    """
    Subscribe `fn(tick)` to live ticks for a security.

    Accepts either:
      • bare security_id e.g. '3045'
      • full scrip code  e.g. 'NSE_3045' / 'NFO_56789'
      • trading symbol — resolved via _scrip_code()
    """
    code = scrip_code_or_secid
    if '_' not in code and not code.isdigit():
        code = _scrip_code(code) or ''
    if not code and scrip_code_or_secid.isdigit():
        # Allow plain security_id without exchange prefix; auto-prefix NSE
        code = f"NSE_{scrip_code_or_secid}"
    if not code:
        return
    with _callback_lock:
        _tick_callbacks.setdefault(code, []).append(fn)
    # Also ensure WS is subscribed so ticks actually arrive
    try:
        _ws_subscribe([code])
    except Exception:
        pass


def unregister_tick_callback(scrip_code_or_secid: str, fn=None) -> None:
    # Mirror the same resolution logic as register_tick_callback
    code = scrip_code_or_secid
    if '_' not in code and not code.isdigit():
        code = _scrip_code(code) or ''
    if not code and scrip_code_or_secid.isdigit():
        code = f"NSE_{scrip_code_or_secid}"
    if not code:
        return
    with _callback_lock:
        if code not in _tick_callbacks:
            return
        if fn is None:
            _tick_callbacks.pop(code, None)
        else:
            try: _tick_callbacks[code].remove(fn)
            except ValueError: pass

_ws_msg_count  = 0                    # total messages received (for debug)
_ws_stats_lock = threading.Lock()     # protects _ws_msg_count, _ws_last_tick_at, _ws_last_real_tick_at


def _ws_on_message(ws, message):
    global _ws_msg_count, _ws_last_tick_at, _ws_last_real_tick_at
    try:
        now = time.time()
        with _ws_stats_lock:
            _ws_msg_count += 1
            _ws_last_tick_at      = now      # any source (used by /status liveness)
            _ws_last_real_tick_at = now      # WS only (used by REST-poll suspend check)
        # IndStocks double-encodes: the WebSocket frame is a JSON string whose value
        # is itself a JSON object string. Decode twice.
        parsed = json.loads(message)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)   # second decode

        # Normalise to a list of ticks
        if isinstance(parsed, list):
            raw_ticks = parsed
        elif isinstance(parsed, dict):
            raw_ticks = [parsed]
        else:
            return

        # Actual format: {"mode":"ltp","instrument":"3045","timestamp":...,"data":{"ltp":1012.7}}
        # instrument = bare security_id (no exchange prefix). Resolve via subscriber map.
        ticks = []
        for t in raw_ticks:
            sec_id = str(t.get('instrument') or t.get('scripCode') or t.get('scrip_code') or '')
            if not sec_id:
                continue

            # Price is nested under "data" key
            d = t.get('data') or {}
            price = (d.get('ltp') or d.get('price') or d.get('last_price')
                     or t.get('ltp') or t.get('price') or t.get('last_price'))

            # Resolve bare security_id → "NSE_3045" scrip code
            # Check subscribers first (fastest), then tick_cache keys
            code = None
            with _sub_lock:
                for k in _subscribers:
                    if k.endswith(f'_{sec_id}'):
                        code = k
                        break
            if not code:
                # Try all known scrip codes — covers index IDs like 40000001
                for k in list(_tick_cache.keys()):
                    if k.endswith(f'_{sec_id}'):
                        code = k
                        break
            if not code:
                # Last resort: assume NSE
                code = f'NSE_{sec_id}'

            tick = {
                '_normalised_code': code,
                'ltp':     price,
                'open':    d.get('open')  or d.get('o'),
                'high':    d.get('high')  or d.get('h'),
                'low':     d.get('low')   or d.get('l'),
                'close':   d.get('close') or d.get('c') or d.get('prev_close'),
                'volume':  d.get('volume') or d.get('v'),
                'change':  d.get('net_change') or d.get('change'),
                'change_pct': d.get('change_percent'),
                'timestamp': t.get('timestamp'),
            }
            ticks.append(tick)

        with _tick_lock:
            for t in ticks:
                code = t['_normalised_code']
                _tick_cache[code] = t
                _ws_last_tick_at_per_code[code] = now

        # Fan out to registered tick callbacks (synchronous, fast handlers only).
        with _callback_lock:
            cb_snapshot = {c: list(fs) for c, fs in _tick_callbacks.items()}
        for t in ticks:
            code = t['_normalised_code']
            for fn in cb_snapshot.get(code, ()):
                try:
                    fn(t)
                except Exception as e:
                    logger.warning(f"tick_callback({code}) error: {e}")

        # Fan out to SSE subscribers — ticks already normalised above
        with _sub_lock:
            for t in ticks:
                code = t['_normalised_code']
                if code in _subscribers:
                    payload = json.dumps({
                        "price":      t.get('ltp'),
                        "open":       t.get('open'),
                        "high":       t.get('high'),
                        "low":        t.get('low'),
                        "close":      t.get('close'),
                        "volume":     t.get('volume'),
                        "change":     t.get('change'),
                        "change_pct": t.get('change_pct'),
                        "timestamp":  datetime.now().isoformat(),
                    })
                    dead = set()
                    for q in _subscribers[code]:
                        try:
                            q.put_nowait(payload)
                        except _queue.Full:
                            dead.add(q)
                    _subscribers[code] -= dead
    except Exception as e:
        logger.warning(f"WS message parse error: {e} | raw={message[:200]}")


def _ws_on_open(ws):
    logger.info("INDmoney WebSocket connected")
    # Re-subscribe to all active scrip codes
    with _sub_lock:
        codes = list(_subscribers.keys())
    if codes:
        _ws_subscribe(codes)


def _ws_on_close(ws, code, reason):
    logger.warning(f"INDmoney WebSocket closed: {code} {reason}")


def _ws_on_close_status(ws, code, reason):
    """Reset auth-failure counter on a clean close (got at least one tick).
    Used by `_start_ws` reconnect loop to know if the token works."""
    global _ws_auth_failures
    if _ws_msg_count > 0 or _ws_last_tick_at > 0:
        _ws_auth_failures = 0   # we got data → token is fine
    _ws_on_close(ws, code, reason)


def _ws_on_error(ws, error):
    global _ws_auth_failures
    err_str = str(error)
    # Detect IndStocks auth-rejection signatures so we can back off properly.
    if ('513' in err_str or '401' in err_str
        or 'Cannot authenticate' in err_str
        or 'Missing/Incorrect Authorization' in err_str):
        _ws_auth_failures += 1
        logger.error(
            f"INDmoney WS auth rejected (attempt {_ws_auth_failures}/5) — "
            f"token may be expired. Refresh INDMONEY_ACCESS_TOKEN in .env."
        )
    else:
        logger.error(f"INDmoney WebSocket error: {error}")


def _to_ws_format(code: str) -> str:
    """Translate an internal REST-format scrip code (NSE_3045 / BSE_500325 /
    NFO_<id> / BFO_<id>) into the IndStocks WebSocket subscribe format
    (NSE:3045 / NIDX:40000001 / BIDX:40000006 / NFO:<id> / BFO:<id>).

    Indices use a different segment prefix on WS (NIDX/BIDX) than equities
    (NSE/BSE). Detected by ID range: IndStocks index IDs start at 40000000;
    equity IDs are well below that. See api-docs.indstocks.com/Websockets.
    """
    if ':' in code:
        return code            # already in WS format
    if '_' not in code:
        return code            # bare ID — leave as-is
    seg, sid = code.split('_', 1)
    seg = seg.upper()
    # Index range — IndStocks reserves IDs ≥ 40000000 for indices.
    is_index = sid.isdigit() and int(sid) >= 40000000
    if seg == 'NSE':
        return f"{'NIDX' if is_index else 'NSE'}:{sid}"
    if seg == 'BSE':
        return f"{'BIDX' if is_index else 'BSE'}:{sid}"
    # NFO / BFO / others: just swap the separator.
    return f"{seg}:{sid}"


def _ws_subscribe(codes: list[str]):
    """Send subscribe message for a list of scrip codes (REST format).
    Codes are translated to IndStocks WS format (SEGMENT:TOKEN) before send.
    """
    with _ws_lock:
        ws = _ws_instance
    if not ws:
        return
    instruments = [_to_ws_format(c) for c in codes]
    try:
        ws.send(json.dumps({
            "action":      "subscribe",
            "mode":        "ltp",
            "instruments": instruments,
        }))
    except Exception as e:
        logger.warning(f"WS subscribe failed: {e}")


def _ws_unsubscribe(codes: list[str]):
    with _ws_lock:
        ws = _ws_instance
    if not ws:
        return
    instruments = [_to_ws_format(c) for c in codes]
    try:
        ws.send(json.dumps({"action": "unsubscribe", "instruments": instruments}))
    except Exception:
        pass


# Last tick timestamp — ANY source (WS or REST poll). Used by /status.
_ws_last_tick_at: float = 0.0
# Last tick timestamp — WS ONLY. Used by REST-poll's suspend check so it
# doesn't mistake its own ingested ticks as "WS resumed".
_ws_last_real_tick_at: float = 0.0
# Per-code last WS tick — lets REST-poll selectively cover codes whose WS
# feed has gone silent (e.g. BSE indices) while NSE codes keep ticking.
# Without this, the global liveness flag stays true on NSE traffic and
# silent BSE codes never get a REST refresh.
_ws_last_tick_at_per_code: dict[str, float] = {}
# Track auth-failure reconnect attempts so we can back off and finally give up
# instead of hammering IndStocks (and triggering Cloudflare ratelimits) forever.
_ws_auth_failures: int = 0
# True once we've concluded the loaded token is permanently bad and stopped
# retrying. Cleared whenever the .env token changes.
_ws_auth_dead: bool   = False
_ws_dead_token: str   = ''   # the token value that was rejected

def _start_ws():
    """Start the INDmoney WebSocket price feed in a background thread."""
    global _ws_instance, _ws_thread
    if not _access_token():
        return
    with _ws_lock:
        if _ws_thread and _ws_thread.is_alive():
            return  # already running

    try:
        import websocket as _ws_lib

        def _run():
            global _ws_instance, _ws_auth_failures, _ws_auth_dead, _ws_dead_token
            backoff = 5
            while True:  # auto-reconnect loop
                # Re-read token EVERY iteration so a .env edit takes effect on
                # the next reconnect — no full process restart needed.
                tok = _access_token()
                if not tok:
                    time.sleep(10); continue
                # If a previous attempt confirmed this exact token is invalid,
                # don't keep hammering — wait for the user to update .env.
                if _ws_auth_dead and tok == _ws_dead_token:
                    time.sleep(30); continue
                if tok != _ws_dead_token:
                    _ws_auth_dead = False  # new token, give it a chance
                    _ws_auth_failures = 0
                    backoff = 5
                try:
                    ws = _ws_lib.WebSocketApp(
                        WS_PRICE_URL,
                        header=[f'Authorization: {tok}'],
                        on_open=_ws_on_open,
                        on_message=_ws_on_message,
                        on_close=_ws_on_close_status,
                        on_error=_ws_on_error,
                    )
                    with _ws_lock:
                        _ws_instance = ws
                    ws.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as e:
                    logger.error(f"INDmoney WS crashed: {e}")
                finally:
                    with _ws_lock:
                        _ws_instance = None

                # Exponential backoff up to 5 min on consecutive auth failures
                # so we don't trigger Cloudflare ratelimits.
                if _ws_auth_failures >= 5:
                    _ws_auth_dead  = True
                    _ws_dead_token = tok
                    logger.error(
                        f"INDmoney WS gave up after {_ws_auth_failures} auth "
                        f"rejections — token appears invalid. Update "
                        f"INDMONEY_ACCESS_TOKEN in .env (no restart needed)."
                    )
                    time.sleep(30); continue
                time.sleep(min(backoff, 300))
                backoff = min(backoff * 2, 300)

        t = threading.Thread(target=_run, daemon=True, name='INDmoneyWS')
        with _ws_lock:
            _ws_thread = t
        t.start()
        logger.info("INDmoney WebSocket thread started")
    except ImportError:
        logger.warning("websocket-client not installed — INDmoney WebSocket unavailable. Run: uv add websocket-client")


# ── Order-updates WebSocket ────────────────────────────────────────────────────
# Connects to wss://ws-order-updates.indstocks.com to receive real-time order
# fill, rejection, and modification events.  Events are broadcast via SSE
# through the /api/indmoney/order-events/stream endpoint so the frontend can
# show toast/snackbar notifications for order status changes.

_order_ws_thread: threading.Thread | None = None
_order_ws_lock   = threading.Lock()

_order_subs_lock = threading.Lock()
_order_subs: set[_queue.Queue] = set()      # SSE listeners for order events
_order_event_log: list[dict] = []           # last N events for replay on reconnect
_ORDER_LOG_MAX   = 50


def order_subscribe_sse() -> _queue.Queue:
    """Frontend opens an SSE connection for order updates."""
    q: _queue.Queue = _queue.Queue(maxsize=200)
    with _order_subs_lock:
        _order_subs.add(q)
    # No replay — snackbars are ephemeral, replaying old events on
    # reconnect/refresh causes stale toasts (SL EXIT, DAILY LOSS, etc.)
    return q


def order_unsubscribe_sse(q: _queue.Queue) -> None:
    with _order_subs_lock:
        _order_subs.discard(q)


def _order_broadcast(payload: dict) -> None:
    """Fan-out an order event to every connected SSE listener."""
    with _order_subs_lock:
        _order_event_log.append(payload)
        if len(_order_event_log) > _ORDER_LOG_MAX:
            del _order_event_log[: len(_order_event_log) - _ORDER_LOG_MAX]
        dead = set()
        for q in _order_subs:
            try: q.put_nowait(payload)
            except _queue.Full: dead.add(q)
        _order_subs.difference_update(dead)


def _order_ws_on_open(ws):
    logger.info("INDmoney order-updates WebSocket connected")
    try:
        ws.send(json.dumps({"action": "subscribe", "mode": "order_updates"}))
        logger.info("INDmoney order-updates WebSocket subscribed")
    except Exception as e:
        logger.error(f"order-ws subscribe failed: {e}")


def _order_ws_on_message(ws, message):
    try:
        parsed = json.loads(message)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)

        # Normalise to list
        events = parsed if isinstance(parsed, list) else [parsed]

        for evt in events:
            if not isinstance(evt, dict):
                continue
            # Build a clean event payload for the frontend
            status    = (evt.get('status') or evt.get('order_status') or '').upper()
            order_id  = evt.get('order_id') or evt.get('id') or ''
            symbol    = (evt.get('trading_symbol') or evt.get('symbol')
                         or evt.get('scrip_name') or '')
            txn_type  = (evt.get('txn_type') or evt.get('transaction_type') or '').upper()
            qty       = evt.get('qty') or evt.get('quantity') or 0
            price     = evt.get('price') or evt.get('avg_price') or evt.get('trade_price') or 0
            message_  = evt.get('status_message') or evt.get('rejection_reason') or ''

            payload = {
                'type':       'order_update',
                'order_id':    order_id,
                'status':      status,
                'symbol':      symbol,
                'txn_type':    txn_type,
                'qty':         qty,
                'price':       price,
                'message':     message_,
                'timestamp':   datetime.now().isoformat(),
                'raw':         evt,
            }

            # Classify for frontend toast severity
            if status in ('COMPLETE', 'COMPLETED', 'TRADED', 'FILLED'):
                payload['severity'] = 'success'
                payload['title']    = f"Order filled: {txn_type} {symbol}"
            elif status in ('REJECTED', 'CANCELLED', 'CANCELED', 'FAILED'):
                payload['severity'] = 'error'
                payload['title']    = f"Order {status.lower()}: {txn_type} {symbol}"
            elif status in ('OPEN', 'PENDING', 'TRIGGER_PENDING', 'AFTER_MARKET_ORDER_REQ_RECEIVED'):
                payload['severity'] = 'info'
                payload['title']    = f"Order pending: {txn_type} {symbol}"
            else:
                payload['severity'] = 'info'
                payload['title']    = f"Order update: {status} {symbol}"

            logger.info(f"[order-ws] {payload['title']} qty={qty} @ ₹{price} "
                        f"({message_})" if message_ else f"[order-ws] {payload['title']} qty={qty} @ ₹{price}")
            _order_broadcast(payload)

    except Exception as e:
        logger.debug(f"order-ws message parse error: {e}")


def _order_ws_on_close(ws, close_status_code, close_msg):
    logger.info(f"INDmoney order-updates WebSocket closed: {close_status_code} {close_msg}")


def _order_ws_on_error(ws, error):
    logger.warning(f"INDmoney order-updates WebSocket error: {error}")


def _start_order_ws():
    """Start the INDmoney order-updates WebSocket in a background thread."""
    global _order_ws_thread
    if not _access_token():
        return
    with _order_ws_lock:
        if _order_ws_thread and _order_ws_thread.is_alive():
            return

    try:
        import websocket as _ws_lib

        def _run():
            backoff = 5
            last_tok = None
            while True:
                tok = _access_token()
                if not tok:
                    time.sleep(10); continue
                # Reset backoff when token changes (fresh credential)
                if tok != last_tok:
                    backoff = 5
                    last_tok = tok
                try:
                    ws = _ws_lib.WebSocketApp(
                        WS_ORDER_URL,
                        header=[f'Authorization: {tok}'],
                        on_open=_order_ws_on_open,
                        on_message=_order_ws_on_message,
                        on_close=_order_ws_on_close,
                        on_error=_order_ws_on_error,
                    )
                    ws.run_forever(ping_interval=30, ping_timeout=10)
                    # If run_forever returned cleanly after receiving data,
                    # reset backoff — it was a transient disconnect, not a bad token
                    backoff = 5
                except Exception as e:
                    logger.error(f"INDmoney order-WS crashed: {e}")
                time.sleep(min(backoff, 120))
                backoff = min(backoff * 2, 120)

        t = threading.Thread(target=_run, daemon=True, name='INDmoneyOrderWS')
        t.start()
        _order_ws_thread = t
        logger.info("INDmoney order-updates WebSocket thread started")
    except ImportError:
        logger.warning("websocket-client not installed — order updates WS unavailable")


# ── REST polling fallback ─────────────────────────────────────────────────────
# IndStocks WebSocket sometimes accepts the connection + auth but never delivers
# ticks (silent server-side gating, e.g. for accounts without streaming
# entitlement). When that happens we fall back to polling /market/quotes/ltp
# every 2s for every subscribed scrip code and inject the result into the same
# tick-cache / SSE-queue plumbing the WS would have used. From the rest of the
# app's perspective there's no difference — same `_tick_cache`, same SSE fan-out.
_rest_poll_thread: threading.Thread | None = None
_rest_poll_active: bool = False           # True while we're actually polling
_rest_poll_log_at: float = 0.0            # rate-limit fallback-mode log lines

def _ws_is_delivering() -> bool:
    """True if the WebSocket itself is currently receiving ticks (last < 30s).
    Uses the WS-only timestamp so REST-poll's own ingested ticks don't count."""
    if not _ws_last_real_tick_at:
        return False
    return (time.time() - _ws_last_real_tick_at) < 30


def _ingest_rest_tick(code: str, ltp: float):
    """Push a REST-polled price into the same plumbing as a WS tick.
    Updates _tick_cache, fires tick_callbacks, fans out to SSE subscribers."""
    global _ws_last_tick_at
    if ltp <= 0:
        return
    tick = {
        'ltp':              float(ltp),
        'last_price':       float(ltp),
        '_normalised_code': code,
        '_source':          'rest_poll',
    }
    with _tick_lock:
        _tick_cache[code] = tick
    with _ws_stats_lock:
        _ws_last_tick_at = time.time()

    # Fire registered tick callbacks (used by tracked_monitor, etc.)
    with _callback_lock:
        callbacks = list(_tick_callbacks.get(code, []))
    for fn in callbacks:
        try:
            fn(tick)
        except Exception as e:
            logger.warning(f"tick_callback({code}) REST-fallback error: {e}")

    # Fan out to SSE subscribers — frontend live-price streams
    payload = json.dumps({
        "price":     ltp,
        "ltp":       ltp,
        "source":    "rest_poll",
        "timestamp": datetime.now().isoformat(),
    })
    with _sub_lock:
        queues = list(_subscribers.get(code, ()))
    dead = set()
    for q in queues:
        try:
            q.put_nowait(payload)
        except _queue.Full:
            dead.add(q)
    if dead:
        with _sub_lock:
            if code in _subscribers:
                _subscribers[code] -= dead


def _start_rest_poll():
    """Start the REST-poll fallback thread. Idempotent — safe to call multiple times."""
    global _rest_poll_thread
    if _rest_poll_thread and _rest_poll_thread.is_alive():
        return

    def _loop():
        global _rest_poll_active, _rest_poll_log_at
        backoff_404: dict[str, float] = {}   # scrip → next-allowed-time for known-bad codes
        while True:
            try:
                time.sleep(2)
                tok = _access_token()
                if not tok:
                    _rest_poll_active = False; continue

                with _sub_lock:
                    all_codes = list(_subscribers.keys())
                if not all_codes:
                    _rest_poll_active = False; continue

                # Per-code liveness: only poll codes whose WS feed has been
                # silent for ≥30s. Codes still ticking over WS are skipped so
                # we don't double-count or waste quota. NSE indices keep
                # streaming; BSE indices that go quiet get covered here.
                now = time.time()
                codes = [c for c in all_codes
                         if (now - _ws_last_tick_at_per_code.get(c, 0)) >= 30]

                if not codes:
                    if _rest_poll_active:
                        logger.info("WS delivering ticks for all subscribed codes — REST poll suspended")
                    _rest_poll_active = False
                    continue

                if not _rest_poll_active:
                    logger.info(
                        f"WS silent for {len(codes)}/{len(all_codes)} codes — "
                        f"REST poll fallback ACTIVE on those (every 2s)"
                    )
                    _rest_poll_active = True

                # Throttled log: re-announce every 5 min that fallback is on
                if now - _rest_poll_log_at > 300:
                    logger.info(f"REST poll active for {len(codes)}/{len(all_codes)} codes (WS-silent only)")
                    _rest_poll_log_at = now

                # IndStocks REST rejects batch requests (`scrip-codes=A,B`) so
                # we fetch each code separately. Use ThreadPoolExecutor to
                # parallelize up to 4 concurrent requests (reduces total
                # cycle time from N*150ms to ~N/4*150ms).
                from concurrent.futures import ThreadPoolExecutor, as_completed

                poll_codes = [c for c in codes
                              if not (backoff_404.get(c, 0) and now < backoff_404.get(c, 0))]

                def _fetch_one(code):
                    try:
                        r = _requests.get(f'{BASE_URL}/market/quotes/ltp',
                                          headers=_headers(),
                                          params={'scrip-codes': code},
                                          timeout=4)
                        return code, r
                    except Exception:
                        return code, None

                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {pool.submit(_fetch_one, c): c for c in poll_codes}
                    for fut in as_completed(futures):
                        code, r = fut.result()
                        if r is None:
                            continue
                        if r.ok:
                            backoff_404.pop(code, None)
                            data = r.json().get('data', {}) or {}
                            entry = data.get(code) if isinstance(data, dict) else None
                            if isinstance(entry, dict):
                                ltp = entry.get('live_price') or entry.get('ltp') or entry.get('last_price')
                                if ltp:
                                    _ingest_rest_tick(code, float(ltp))
                        elif r.status_code == 400:
                            backoff_404[code] = now + 300
                        elif r.status_code == 429:
                            time.sleep(2)
            except Exception as e:
                logger.warning(f"REST poll loop error: {e}")
                time.sleep(5)

    t = threading.Thread(target=_loop, daemon=True, name='INDmoneyRESTPoll')
    t.start()
    _rest_poll_thread = t
    logger.info("INDmoney REST-poll fallback thread started")


# Start WebSocket on import if token is configured
if _access_token():
    _start_ws()
    _start_rest_poll()
    # Order-updates WS disabled — endpoint returns 404.
    # Uncomment when IndStocks enables wss://ws-order-updates.indstocks.com
    # _start_order_ws()


# ── Ticker normaliser: Yahoo-style → NSE/BSE symbol ──────────────────────────
_INDEX_MAP = {
    '^NSEI':      'NIFTY 50',    # used for display; actual scrip is 'NIFTY'
    '^NSEBANK':   'BANKNIFTY',
    '^CNXFIN':    'FINNIFTY',
    '^NSEMDCP50': 'MIDCPNIFTY',
}

def _norm(ticker: str) -> str:
    """Return plain NSE trading symbol from any Yahoo/caret notation."""
    t = ticker.upper()
    if t in _INDEX_MAP:
        return _INDEX_MAP[t]
    return t.replace('.NS', '').replace('.BO', '')


# ── Public helper functions (importable by market.py, position_monitor.py) ───

_cash_cache_lock = threading.Lock()
_cash_cache: tuple[float, float] | None = None   # (timestamp, value)


def _ind_available_cash() -> float | None:
    """
    Fetch available cash (free margin) from IndMoney /funds API.
    Returns the available balance in ₹, or None if unavailable.
    Cached for 60s to avoid hammering the API on every scan cycle.
    """
    global _cash_cache
    if not _access_token():
        return None
    now = time.time()
    with _cash_cache_lock:
        if _cash_cache and now - _cash_cache[0] < 60:
            return _cash_cache[1]
    try:
        r = _requests.get(f'{BASE_URL}/funds', headers=_headers(), timeout=5)
        if not r.ok:
            logger.warning(f"IndMoney /funds API returned {r.status_code}")
            return None
        data = r.json().get('data') or {}
        # IndStocks /funds response structure:
        #   sod_balance, funds_added, withdrawal_balance,
        #   detailed_avl_balance: { eq_cnc, option_buy, future, ... }
        # For F&O option buying, the usable cash is option_buy from
        # detailed_avl_balance. Fallback: sod_balance + funds_added.
        avl = data.get('detailed_avl_balance') or {}
        cash = avl.get('option_buy')
        if cash is None or float(cash) == 0:
            # Fallback: total available = SOD + added - withdrawn
            sod   = float(data.get('sod_balance', 0) or 0)
            added = float(data.get('funds_added', 0) or 0)
            drawn = float(data.get('funds_withdrawn', 0) or 0)
            cash  = sod + added - drawn
        cash = float(cash)
        with _cash_cache_lock:
            _cash_cache = (now, cash)
        return cash
    except Exception as e:
        logger.warning(f"IndMoney /funds error: {e}")
        return None


def _invalidate_cash_cache():
    """Clear the funds cache so next call to _ind_available_cash() hits the API."""
    global _cash_cache
    with _cash_cache_lock:
        _cash_cache = None


def _ind_broker_positions(segment: str = 'derivative', product: str = 'margin') -> list | None:
    """Return list of open F&O positions from broker, or None on error/no auth."""
    if not ACCESS_TOKEN:
        return None
    try:
        r = _requests.get(f'{BASE_URL}/portfolio/positions', headers=_headers(), timeout=5,
                          params={'segment': segment, 'product': product})
        if not r.ok:
            return None
        data = r.json().get('data')
        return data if isinstance(data, list) else None
    except Exception:
        return None


def _ind_ltp(ticker: str) -> float | None:
    """
    Get live last-traded-price for any NSE/BSE symbol.
    WebSocket cache → REST fallback → None if unavailable.
    """
    if not ACCESS_TOKEN:
        return None
    code = _scrip_code(ticker)
    if code:
        with _tick_lock:
            cached = _tick_cache.get(code)
        if cached:
            p = cached.get('ltp') or cached.get('last_price')
            if p:
                return float(p)
    # F&O contracts (NFO_*/BFO_*) use a different REST endpoint than equities;
    # delegate to the option-aware helper so option-stream SSE actually emits
    # live ticks instead of heartbeats forever.
    if code and (code.startswith('NFO_') or code.startswith('BFO_')):
        try:
            p = _ind_option_ltp(ticker)
            if p:
                return float(p)
        except Exception:
            pass
    # REST fallback — IndStocks API uses NSE_3045 (underscore) format,
    # response is {"data": {"NSE_3045": {"live_price": ...}}}
    scrip = code or f"{_exchange(ticker)}_{_norm(ticker)}"
    try:
        r = _requests.get(f'{BASE_URL}/market/quotes/ltp',
                          headers=_headers(),
                          params={'scrip-codes': scrip}, timeout=4)
        if r.ok:
            data = r.json().get('data') or {}
            entry = data.get(scrip) if isinstance(data, dict) else None
            if isinstance(entry, dict):
                p = entry.get('live_price') or entry.get('ltp') or entry.get('last_price')
                if p:
                    return float(p)
            elif isinstance(data, list) and data:
                p = (data[0] or {}).get('ltp') or (data[0] or {}).get('last_price')
                if p:
                    return float(p)
    except Exception:
        pass

    # yfinance last-resort fallback — covers symbols IndStocks lists but can't
    # quote (notably ^BSESN / SENSEX, which 400s on /market/quotes/ltp despite
    # appearing in their index master). 15-min delayed but better than null.
    try:
        import yfinance as _yf
        info = _yf.Ticker(ticker).fast_info
        for k in ('last_price', 'lastPrice', 'regularMarketPrice'):
            v = info.get(k) if hasattr(info, 'get') else getattr(info, k, None)
            if v:
                return float(v)
    except Exception:
        pass
    return None


_candles_404_cache: dict[str, float] = {}   # "scrip|interval" → next-allowed-time
_CANDLES_404_MAX = 500                      # evict oldest entries beyond this
_candles_404_lock = threading.Lock()

def _ind_candles(ticker: str, interval: str = '5m', days: int = 7) -> list[dict]:
    """
    Fetch OHLCV candles from IndMoney historical API.
    interval: '1m','5m','15m','30m','1h','1d','1w'
    Returns list of {date, open, high, low, close, volume} sorted oldest→newest.

    Some scrip codes (e.g. NSE_40000003 for BANK NIFTY, NSE_40000107 for India
    VIX) are listed in the IndStocks index master but rejected by the
    historical endpoint with HTTP 400 "Invalid scrip codes". For these we
    cache the failure for 1 hour and skip silently — the caller (e.g.
    `_fetch_market_data` in market.py) already falls back to yfinance.
    """
    if not _access_token():
        return []
    code = _scrip_code(ticker)
    if not code:
        return []
    # Skip if we recently learned this code is unsupported on /market/historical
    with _candles_404_lock:
        cooldown_until = _candles_404_cache.get(f"{code}|{interval}", 0)
    if cooldown_until and time.time() < cooldown_until:
        return []

    from datetime import datetime as _dt, timedelta as _td
    to_dt   = _dt.now()
    from_dt = to_dt - _td(days=days)
    from_ts = int(from_dt.timestamp() * 1000)
    to_ts   = int(to_dt.timestamp()   * 1000)

    # Historical endpoint uses NSE_3045 (underscore) — NOT NSE:3045 (colon)
    scrip_param = code   # already "NSE_3045" format

    # IndStocks canonical interval names (from api-docs.indstocks.com)
    # Max ranges: 1minute=7d, 5minute/15minute/30minute/60minute=14d, 1day/1week/1month=1yr
    _IV_MAP = {
        '1m':  '1minute',  '3m':  '3minute',  '5m':  '5minute',
        '10m': '10minute', '15m': '15minute', '30m': '30minute',
        '1h':  '60minute', '2h':  '120minute','4h':  '240minute',
        '1d':  '1day',     '1w':  '1week',    '1M':  '1month',
    }
    ind_interval = _IV_MAP.get(interval, interval)

    try:
        r = _requests.get(
            f'{BASE_URL}/market/historical/{ind_interval}',
            headers=_headers(),
            params={
                'scrip-codes': scrip_param,   # hyphenated, underscore format
                'start_time':  from_ts,        # epoch milliseconds
                'end_time':    to_ts,
            },
            timeout=15,
        )
        if not r.ok:
            # 400 "Invalid scrip codes" means IndStocks doesn't support
            # candles for this scrip — common for some indices (BANK NIFTY,
            # India VIX). Cache for 1h and silence the log so we don't spam.
            if r.status_code == 400 and 'Invalid scrip' in r.text:
                with _candles_404_lock:
                    _candles_404_cache[f"{code}|{interval}"] = time.time() + 3600
                    if len(_candles_404_cache) > _CANDLES_404_MAX:
                        now = time.time()
                        expired = [k for k, v in _candles_404_cache.items() if v < now]
                        for k in expired:
                            del _candles_404_cache[k]
                logger.debug(
                    f"IndMoney candles {ticker} {interval}: not supported on "
                    f"historical endpoint — using yfinance fallback for 1h"
                )
            else:
                logger.warning(f"IndMoney candles {ticker} {interval}: {r.status_code} — {r.text[:200]}")
            return []

        # Response: {"success": true, "data": {"NSE_3045": {"candles": [{ts, o, h, l, c, v}]}}}
        # ts is epoch SECONDS (not ms). o/h/l/c/v are the OHLCV fields.
        raw_data = r.json().get('data') or {}
        raw: list = []
        if isinstance(raw_data, dict):
            for v in raw_data.values():
                if isinstance(v, dict) and 'candles' in v:
                    raw = v['candles']
                    break
                elif isinstance(v, list):
                    raw = v
                    break
        elif isinstance(raw_data, list):
            raw = raw_data

        candles = []
        for c in raw:
            if isinstance(c, list) and len(c) >= 6:
                ts_s = c[0]   # epoch seconds
                o, h, l, cls, vol = float(c[1]), float(c[2]), float(c[3]), float(c[4]), int(c[5])
            elif isinstance(c, dict):
                ts_s  = c.get('ts') or c.get('timestamp') or c.get('t') or 0
                o     = float(c.get('o', c.get('open',   0)))
                h     = float(c.get('h', c.get('high',   0)))
                l     = float(c.get('l', c.get('low',    0)))
                cls   = float(c.get('c', c.get('close',  0)))
                vol   = int(c.get('v',   c.get('volume', 0)))
            else:
                continue
            dt_str = _dt.fromtimestamp(ts_s).strftime('%Y-%m-%d %H:%M:%S')  # ts is seconds
            candles.append({'date': dt_str, 'open': o, 'high': h, 'low': l, 'close': cls, 'volume': vol})

        # Ensure oldest→newest order — some IndMoney endpoints return
        # newest-first which inverts every downstream indicator (Supertrend,
        # MACD, RSI) and causes CE/PE signal inversion.
        if len(candles) >= 2 and candles[0]['date'] > candles[-1]['date']:
            logger.warning(f"IndMoney candles {ticker} {interval}: API returned NEWEST-FIRST — reversing to fix indicator direction")
        candles.sort(key=lambda x: x['date'])

        logger.debug(f"IndMoney candles {ticker} {interval}: {len(candles)} bars")
        return candles
    except Exception as e:
        logger.warning(f"IndMoney candles error {ticker}: {e}")
        return []


_fno_sym_index: dict[str, list[dict]] = {}  # TRADING_SYMBOL/CUSTOM_SYMBOL → [inst rows]


def _build_fno_sym_index() -> None:
    """Build O(1) lookup dict keyed by TRADING_SYMBOL and CUSTOM_SYMBOL."""
    if _fno_sym_index:
        return
    rows = _load_instruments('fno')
    for inst in rows:
        for key_field in ('TRADING_SYMBOL', 'tradingsymbol', 'CUSTOM_SYMBOL', 'custom_symbol'):
            val = (inst.get(key_field) or '').strip().upper()
            if val:
                _fno_sym_index.setdefault(val, []).append(inst)


def _parse_expiry(exp_raw: str):
    """Parse IndStocks expiry date string → datetime or None."""
    from datetime import datetime as _dt
    exp_raw = exp_raw.strip()
    try:
        return _dt.strptime(exp_raw, '%m/%d/%Y %H:%M')
    except Exception:
        try:
            return _dt.strptime(exp_raw[:10], '%m/%d/%Y')
        except Exception:
            return None


def _resolve_fo_instrument(symbol: str) -> dict | None:
    """
    Resolve an F&O trading symbol to a single instrument-master row.
    Matches by TRADING_SYMBOL first, then CUSTOM_SYMBOL (display name like
    'NIFTY 9 JUN 23500 CE').  Multiple weekly contracts share the same
    TRADING_SYMBOL — pick the one whose EXPIRY_DATE is the soonest still
    in the future.  Returns the master row dict, or None.
    """
    _build_fno_sym_index()
    sym = symbol.upper()
    from datetime import datetime as _dt
    now = _dt.now()

    matches = _fno_sym_index.get(sym)
    if not matches:
        return None

    candidates = []
    for inst in matches:
        exp_dt = _parse_expiry(inst.get('EXPIRY_DATE') or '')
        if exp_dt and exp_dt >= now:
            candidates.append((exp_dt, inst))

    if not candidates:
        # Fall back: any match with parseable expiry, even if past (settlement day)
        # Sort by expiry descending so we return the most recent contract
        fallback = []
        for inst in matches:
            exp_dt = _parse_expiry(inst.get('EXPIRY_DATE') or '') or _dt.min
            fallback.append((exp_dt, inst))
        fallback.sort(key=lambda x: x[0], reverse=True)
        return fallback[0][1]

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _fo_scrip_code(inst: dict) -> str:
    """Return NFO_<sec_id> or BFO_<sec_id> for an F&O instrument-master row.
    The master's EXCH field shows 'NSE' for NSE-segment derivatives but the
    quote API requires the segment code (NFO/BFO)."""
    sec_id = (inst.get('SECURITY_ID') or '').strip()
    seg    = (inst.get('SEGMENT') or '').strip().upper()
    exch   = (inst.get('EXCH') or '').strip().upper()
    # SEGMENT is 'D' for derivatives in IndStocks master
    if exch.startswith('B') or seg == 'BFO':
        return f'BFO_{sec_id}'
    return f'NFO_{sec_id}'


def _ind_option_ltp(symbol: str) -> float | None:
    """
    Get live LTP for an F&O trading symbol (e.g. NIFTY-May2026-24250-CE).
    """
    if not ACCESS_TOKEN:
        return None
    inst = _resolve_fo_instrument(symbol)
    if not inst:
        return None
    code = _fo_scrip_code(inst)
    with _tick_lock:
        cached = _tick_cache.get(code)
    if cached:
        p = cached.get('ltp') or cached.get('last_price') or cached.get('live_price')
        if p:
            return float(p)
    try:
        r = _requests.get(f'{BASE_URL}/market/quotes/ltp',
                          headers=_headers(),
                          params={'scrip-codes': code}, timeout=4)
        if r.ok:
            data = r.json().get('data') or {}
            entry = data.get(code) if isinstance(data, dict) else None
            if isinstance(entry, dict):
                p = entry.get('live_price') or entry.get('ltp') or entry.get('last_price')
                if p:
                    return float(p)
    except Exception:
        pass
    return None


def _ind_option_quotes_batch(scrip_codes: list[str]) -> dict[str, dict]:
    """Fetch full quotes (bid/ask/ltp/oi/volume) for many F&O contracts at once.
    Returns {scrip_code: {bid, ask, ltp, oi, volume}}. Empty dict on failure.

    IndStocks /market/quotes/full accepts comma-separated scrip-codes.
    Used by the option-chain endpoint to avoid 22*2 = 44 sequential REST calls.
    """
    if not ACCESS_TOKEN or not scrip_codes:
        return {}
    out: dict[str, dict] = {}
    # Chunk to stay below URL-length and rate limits — 20 per request is safe
    CHUNK = 20
    for i in range(0, len(scrip_codes), CHUNK):
        codes = scrip_codes[i:i + CHUNK]
        try:
            r = _requests.get(f'{BASE_URL}/market/quotes/full',
                              headers=_headers(),
                              params={'scrip-codes': ','.join(codes)},
                              timeout=6)
            if not r.ok:
                logger.debug(f"batch quote {len(codes)} codes: HTTP {r.status_code}")
                continue
            data = r.json().get('data') or {}
            if not isinstance(data, dict):
                continue
            for code in codes:
                d = data.get(code)
                if not isinstance(d, dict):
                    continue
                md     = d.get('market_depth') or {}
                md_in  = md.get(code) if isinstance(md, dict) else None
                depth  = (md_in or {}).get('depth') if isinstance(md_in, dict) else None
                bid = ask = 0.0
                if isinstance(depth, list) and depth:
                    top = depth[0] or {}
                    try:
                        bid = float((top.get('buy')  or {}).get('price') or 0)
                        ask = float((top.get('sell') or {}).get('price') or 0)
                    except Exception:
                        pass
                ltp = float(d.get('live_price') or d.get('ltp') or d.get('last_price') or 0)
                out[code] = {
                    'bid':    bid,
                    'ask':    ask,
                    'ltp':    ltp,
                    'oi':     int(float(d.get('oi') or d.get('open_interest') or 0)),
                    'volume': int(float(d.get('volume') or d.get('day_volume') or 0)),
                }
        except Exception as e:
            logger.debug(f"batch quote error: {e}")
    return out


def _ind_option_quote(symbol: str) -> dict | None:
    """
    Return a normalised quote for an F&O contract (bid, ask, ltp, oi, volume).
    Picks the nearest-expiry contract when multiple weeklies share the same
    TRADING_SYMBOL. Returns None on any failure so callers degrade gracefully.
    """
    if not ACCESS_TOKEN:
        return None
    sym  = symbol.upper()
    inst = _resolve_fo_instrument(sym)
    if not inst:
        return None
    code = _fo_scrip_code(inst)
    try:
        r = _requests.get(f'{BASE_URL}/market/quotes/full',
                          headers=_headers(),
                          params={'scrip-codes': code},
                          timeout=4)
        if not r.ok:
            return None
        data = r.json().get('data') or {}
        d    = data.get(code) if isinstance(data, dict) else None
        if not isinstance(d, dict):
            return None
        # market_depth.<code>.depth is a LIST of levels, each:
        # {"buy": {"price": "...", "quantity": "..."}, "sell": {...}}
        md     = d.get('market_depth') or {}
        md_in  = md.get(code) if isinstance(md, dict) else None
        depth  = (md_in or {}).get('depth') if isinstance(md_in, dict) else None
        bid = ask = 0.0
        if isinstance(depth, list) and depth:
            top = depth[0] or {}
            try:
                bid = float((top.get('buy')  or {}).get('price') or 0)
                ask = float((top.get('sell') or {}).get('price') or 0)
            except Exception:
                pass
        elif isinstance(depth, dict):
            # Legacy fallback: dict with buy/sell arrays
            bids = depth.get('buy')  or depth.get('bids') or []
            asks = depth.get('sell') or depth.get('asks') or []
            try:
                bid = float(((bids[0] or {}) if bids else {}).get('price') or 0)
                ask = float(((asks[0] or {}) if asks else {}).get('price') or 0)
            except Exception:
                pass
        ltp = float(d.get('live_price') or d.get('ltp') or d.get('last_price') or 0)
        return {
            'symbol': sym,
            'bid':    bid,
            'ask':    ask,
            'ltp':    ltp,
            'oi':     int(float(d.get('oi') or d.get('open_interest') or 0)),
            'volume': int(float(d.get('volume') or d.get('day_volume') or 0)),
            'expiry': (inst.get('EXPIRY_DATE') or '').strip(),
            'lot_size': int(float(inst.get('LOT_UNITS') or inst.get('LOT_SIZE') or 0)),
            'raw':    d,
        }
    except Exception:
        return None


def _nearest_nifty_future() -> dict | None:
    """
    Return the nearest-expiry NIFTY index futures instrument from IndStocks FNO master.
    This is the real GIFT Nifty / SGX Nifty equivalent — live futures price.
    Returns {'trading_symbol', 'security_id', 'expiry_date', 'ltp', 'scrip_code'} or None.
    """
    if not ACCESS_TOKEN:
        return None
    from datetime import date as _date, datetime as _datetime
    instruments = _load_instruments('fno')
    today = _date.today()
    candidates = []
    for inst in instruments:
        if inst.get('INSTRUMENT_NAME') not in ('FUTIDX',):
            continue
        sym = inst.get('TRADING_SYMBOL', '').upper()
        # Only NIFTY 50 futures (not BANKNIFTY, FINNIFTY, NIFTYNXT50 etc.)
        if not (sym.startswith('NIFTY-') and sym.endswith('-FUT')):
            continue
        exp_s = inst.get('EXPIRY_DATE', '')
        try:
            exp_d = _datetime.strptime(exp_s, '%m/%d/%Y %H:%M').date()
        except Exception:
            continue
        if exp_d < today:
            continue
        candidates.append((exp_d, inst))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    best    = candidates[0][1]
    sec_id  = best.get('SECURITY_ID', '').strip()
    code    = _fo_scrip_code(best)
    ltp     = None
    with _tick_lock:
        cached = _tick_cache.get(code)
    if cached:
        ltp = cached.get('ltp') or cached.get('last_price')
        ltp = float(ltp) if ltp else None
    if not ltp:
        try:
            r = _requests.get(f'{BASE_URL}/market/quotes/ltp',
                              headers=_headers(),
                              params={'scrip-codes': code}, timeout=4)
            if r.ok:
                raw = r.json().get('data') or {}
                if isinstance(raw, dict):
                    d = raw.get(code) or (next(iter(raw.values()), {}) if raw else {})
                elif isinstance(raw, list):
                    d = raw[0] if raw else {}
                else:
                    d = {}
                p = d.get('ltp') or d.get('last_price') or d.get('live_price')
                ltp = float(p) if p else None
        except Exception:
            pass
    return {
        'trading_symbol': best.get('TRADING_SYMBOL'),
        'security_id':    sec_id,
        'expiry_date':    best.get('EXPIRY_DATE'),
        'scrip_code':     code,
        'ltp':            ltp,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@indmoney_bp.route('/ws-debug', methods=['GET'])
def ws_debug():
    """
    DEV ONLY — show WebSocket state, raw message samples, and tick cache.
    Hit: GET /api/indmoney/ws-debug
    """
    if not current_app.debug:
        return jsonify({"error": "debug endpoints disabled in production"}), 403
    ws_alive = _ws_thread is not None and _ws_thread.is_alive()
    with _tick_lock:
        cache_snapshot = {k: v for k, v in list(_tick_cache.items())[:10]}
    with _sub_lock:
        subs = {k: len(v) for k, v in _subscribers.items()}
    return jsonify({
        "ws_connected":    ws_alive,
        "msg_count":       _ws_msg_count,
        "subscribers":     subs,
        "tick_cache_keys": list(_tick_cache.keys()),
        "tick_cache_sample": cache_snapshot,
    })


@indmoney_bp.route('/debug-candles', methods=['GET'])
def debug_candles():
    """
    DEV ONLY — try every known historical API format to find what IndStocks accepts.
    Hit: GET /api/indmoney/debug-candles?ticker=SBIN.NS
    """
    if not current_app.debug:
        return jsonify({"error": "debug endpoints disabled in production"}), 403
    if not _connected():
        return jsonify({"error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    ticker = request.args.get('ticker', 'SBIN.NS')
    code   = _scrip_code(ticker)
    if not code:
        return jsonify({"error": f"No scrip code for {ticker}"}), 404

    from datetime import datetime as _dt, timedelta as _td
    to_ts   = int(_dt.now().timestamp() * 1000)
    from_ts = int((_dt.now() - _td(days=7)).timestamp() * 1000)
    colon   = code.replace('_', ':', 1)

    # Confirmed from api-docs.indstocks.com: param is scrip-codes (underscore format), start_time/end_time in ms
    results = []
    candidates = [
        # Correct format per docs
        (f'{BASE_URL}/market/historical/1day',    {'scrip-codes': code, 'start_time': from_ts, 'end_time': to_ts}),
        (f'{BASE_URL}/market/historical/5minute', {'scrip-codes': code, 'start_time': from_ts, 'end_time': to_ts}),
        # Colon format variant
        (f'{BASE_URL}/market/historical/1day',    {'scrip-codes': colon, 'start_time': from_ts, 'end_time': to_ts}),
        # Older path variant
        (f'{BASE_URL}/market/historical/day',     {'scrip-codes': code, 'start_time': from_ts, 'end_time': to_ts}),
    ]
    for url, params in candidates:
        try:
            r = _requests.get(url, headers=_headers(), params=params, timeout=8)
            results.append({'url': url, 'params': params,
                            'status': r.status_code, 'body': r.text[:400]})
            if r.ok:
                break
        except Exception as e:
            results.append({'url': url, 'params': params, 'error': str(e)})

    return jsonify({'ticker': ticker, 'scrip': code, 'colon': colon, 'results': results})


@indmoney_bp.route('/debug-instruments', methods=['GET'])
def debug_instruments():
    """
    DEV ONLY — returns raw instrument master response so we can see actual field names.
    Hit: GET /api/indmoney/debug-instruments
    """
    if not current_app.debug:
        return jsonify({"error": "debug endpoints disabled in production"}), 403
    if not _connected():
        return jsonify({"error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    try:
        source = request.args.get('source', 'equity')
        r = _requests.get(f'{BASE_URL}/market/instruments', headers=_headers(),
                          params={'source': source}, timeout=15)
        if r.ok:
            import csv, io as _io
            reader = csv.DictReader(_io.StringIO(r.text))
            rows   = [row for row in reader]
            return jsonify({"status": r.status_code, "source": source,
                            "total": len(rows), "columns": list(rows[0].keys()) if rows else [],
                            "sample": rows[:3]})
        else:
            return jsonify({"status": r.status_code, "error": r.text[:500]})
    except Exception as e:
        return jsonify({"error": _safe_error(e)}), 500


@indmoney_bp.route('/debug-search', methods=['GET'])
def debug_search():
    """
    DEV ONLY — search instrument master for a partial symbol.
    Hit: GET /api/indmoney/debug-search?q=TATA&source=equity
    """
    if not current_app.debug:
        return jsonify({"error": "debug endpoints disabled in production"}), 403
    if not _connected():
        return jsonify({"error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    q      = request.args.get('q', '').upper()
    source = request.args.get('source', 'equity')
    instruments = _load_instruments(source)
    sample_cols = list(instruments[0].keys()) if instruments else []

    # For small masters (index=128) with no name cols, just show all records
    if len(instruments) <= 200 and not q:
        return jsonify({"query": q, "source": source, "total_master": len(instruments),
                        "columns": sample_cols, "all": instruments})

    matches = [
        inst for inst in instruments
        if q in inst.get('TRADING_SYMBOL', '').upper()
           or q in inst.get('CUSTOM_SYMBOL', '').upper()
           or q in inst.get('SYMBOL_NAME', '').upper()
           or q in inst.get('INSTRUMENT_NAME', '').upper()
    ]
    return jsonify({"query": q, "source": source, "total_master": len(instruments),
                    "columns": sample_cols, "matches": matches[:30]})


@indmoney_bp.route('/status', methods=['GET'])
def status():
    # Distinguish three liveness concepts:
    #   - ws_streaming:  WebSocket is the active source (lowest latency)
    #   - data_flowing:  ANY source (WS or REST poll) is delivering ticks
    #                    → this is what the frontend's "live" indicator cares about
    #   - source:        which one is active right now
    ws_thread_alive = _ws_thread is not None and _ws_thread.is_alive()
    last_tick_age   = (time.time() - _ws_last_tick_at) if _ws_last_tick_at else None
    ws_streaming    = bool(
        ws_thread_alive
        and _ws_msg_count > 0
        and last_tick_age is not None
        and last_tick_age < 60          # WS tick within last 60s
        and not _ws_auth_dead
    )
    data_flowing = bool(
        last_tick_age is not None and last_tick_age < 30
    )
    source = ('websocket' if ws_streaming
              else 'rest_poll' if (_rest_poll_active and data_flowing)
              else 'none')
    ws_healthy = data_flowing
    if not _connected():
        return jsonify({"success": True, "data": {
            "connected":         False,
            "token_configured":  False,
            "websocket":         False,
            "reason": "INDMONEY_ACCESS_TOKEN not set in .env",
        }})
    try:
        r = _requests.get(f'{BASE_URL}/user/profile', headers=_headers(), timeout=5)
        profile = r.json().get('data', {}) if r.ok else {}
        ws_reason = None
        if _ws_auth_dead:
            ws_reason = (
                f"Token rejected by IndStocks ({_ws_auth_failures} attempts). "
                f"Refresh INDMONEY_ACCESS_TOKEN in .env (no restart needed)."
            )
        elif source == 'rest_poll':
            ws_reason = "WS silent — REST-poll fallback delivering ticks every 2s."
        elif _ws_msg_count == 0 and ws_thread_alive:
            ws_reason = "WebSocket thread running but zero ticks received yet."
        elif last_tick_age is not None and last_tick_age >= 60:
            ws_reason = f"Last tick was {int(last_tick_age)}s ago — stream may be stalled."
        return jsonify({"success": True, "data": {
            "connected":         r.ok,
            "token_configured":  True,
            "websocket":         ws_healthy,           # any data source flowing
            "data_source":       source,               # 'websocket' | 'rest_poll' | 'none'
            "ws_streaming":      ws_streaming,         # true only when actual WS ticks
            "rest_poll_active":  _rest_poll_active,
            "ws_thread_alive":   ws_thread_alive,
            "ws_msg_count":      _ws_msg_count,
            "ws_last_tick_age":  round(last_tick_age, 1) if last_tick_age else None,
            "ws_auth_failures":  _ws_auth_failures,
            "ws_reason":         ws_reason,
            "name":              f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
            "email":             profile.get('email'),
            "demat_id":          profile.get('demat_id'),
            "available_cash":    _ind_available_cash(),
        }})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/candles/<path:ticker>', methods=['GET'])
def candles(ticker: str):
    """
    OHLCV candles for charting.
    Query params: interval (default 1d), days (default 200)
    """
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    interval = request.args.get('interval', '1d')
    try:
        days = int(request.args.get('days', 200))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "invalid 'days' parameter"}), 400
    data     = _ind_candles(ticker, interval, days)
    return jsonify({"success": True, "ticker": ticker, "interval": interval, "data": data})


@indmoney_bp.route('/profile', methods=['GET'])
def profile():
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    try:
        prof = _requests.get(f'{BASE_URL}/user/profile', headers=_headers(), timeout=5)
        funds = _requests.get(f'{BASE_URL}/funds', headers=_headers(), timeout=5)
        return jsonify({"success": True, "data": {
            "profile": prof.json().get('data') if prof.ok else {},
            "funds":   funds.json().get('data') if funds.ok else {},
        }})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/tick/<ticker>', methods=['GET'])
def tick(ticker: str):
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401

    code = _scrip_code(ticker)
    # display_symbol = IndStocks' CUSTOM_SYMBOL for this contract, looked up
    # from the cached instrument master. '' if not in the master.
    disp = _display_symbol(ticker)

    # WebSocket cache hit (fastest)
    if code:
        with _tick_lock:
            cached = _tick_cache.get(code)
        if cached:
            return jsonify({"success": True, "source": "websocket", "data": {
                "symbol":         ticker,
                "display_symbol": disp,
                "price":          cached.get('ltp') or cached.get('last_price'),
                "open":           cached.get('open'),
                "high":           cached.get('high'),
                "low":            cached.get('low'),
                "close":          cached.get('close') or cached.get('prev_close'),
                "volume":         cached.get('volume'),
                "change":         cached.get('net_change') or cached.get('change'),
                "change_pct":     cached.get('change_percent'),
                "timestamp":      datetime.now().isoformat(),
            }})

    # REST fallback — delegate to _ind_ltp (handles new scrip format + response shape).
    try:
        price = _ind_ltp(ticker)
        return jsonify({"success": True, "source": "rest", "data": {
            "symbol":         ticker,
            "display_symbol": disp,
            "price":          price,
            "timestamp":      datetime.now().isoformat(),
        }})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/stream/<ticker>', methods=['GET'])
def stream(ticker: str):
    """SSE backed by INDmoney WebSocket price feed."""
    if not _connected():
        def err():
            yield 'data: {"error": "INDMONEY_ACCESS_TOKEN not set"}\n\n'
        return Response(err(), mimetype='text/event-stream')

    code = _scrip_code(ticker)

    # WebSocket path
    if code:
        q = _queue.Queue(maxsize=50)
        with _sub_lock:
            if code not in _subscribers:
                _subscribers[code] = set()
            _subscribers[code].add(q)
        _ws_subscribe([code])

        def generate_ws():
            deadline = time.time() + _SSE_MAX_MINUTES * 60
            try:
                last_emit = 0.0
                def _emit_latest():
                    nonlocal last_emit
                    with _tick_lock:
                        cached = _tick_cache.get(code)
                    p = None
                    if cached:
                        p = cached.get('ltp') or cached.get('last_price') or cached.get('live_price')
                    if not p:
                        p = _ind_ltp(ticker)
                    if p and p != last_emit:
                        last_emit = p
                        return f'data: {json.dumps({"price": float(p), "timestamp": datetime.now().isoformat()})}\n\n'
                    return None

                first = _emit_latest()
                if first:
                    yield first

                while time.time() < deadline:
                    try:
                        payload = q.get(timeout=1.0)
                        yield f'data: {payload}\n\n'
                    except _queue.Empty:
                        update = _emit_latest()
                        if update:
                            yield update
                        else:
                            yield 'data: {"heartbeat": true}\n\n'
                yield f'data: {json.dumps({"type": "timeout", "message": "stream expired, please reconnect"})}\n\n'
            except GeneratorExit:
                pass
            finally:
                with _sub_lock:
                    if code in _subscribers:
                        _subscribers[code].discard(q)
                        if not _subscribers[code]:
                            del _subscribers[code]
                            _ws_unsubscribe([code])

        return Response(generate_ws(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # REST fallback — 1s polling
    scrip = f"{_exchange(ticker)}_{ticker.replace('.NS','').replace('.BO','').upper()}"
    logger.warning(f"No scrip code for {ticker}, falling back to REST polling")

    def generate_rest():
        deadline = time.time() + _SSE_MAX_MINUTES * 60
        while time.time() < deadline:
            try:
                # Use _ind_ltp helper which handles new scrip format + response shape
                p = _ind_ltp(ticker)
                yield f'data: {json.dumps({"price": p, "timestamp": datetime.now().isoformat()})}\n\n'
                time.sleep(1)
            except GeneratorExit:
                break
            except Exception as e:
                yield f'data: {json.dumps({"error": _safe_error(e)})}\n\n'
                time.sleep(5)
        yield f'data: {json.dumps({"type": "timeout", "message": "stream expired, please reconnect"})}\n\n'

    return Response(generate_rest(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@indmoney_bp.route('/quote/<ticker>', methods=['GET'])
def quote(ticker: str):
    """Full quote: OHLC, depth, 52W high/low, circuit limits, volume."""
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    code  = _scrip_code(ticker) or f"{_exchange(ticker)}_{ticker.replace('.NS','').replace('.BO','').upper()}"
    scrip = code
    try:
        r = _requests.get(f'{BASE_URL}/market/quotes/full',
                          headers=_headers(),
                          params={'scrip-codes': scrip}, timeout=5)
        body = r.json() if r.ok else {}
        raw  = body.get('data') or {}
        # New IndStocks shape: {"data": {"NSE_3045": {...}}}; legacy was [{…}]
        if isinstance(raw, dict):
            data = raw.get(scrip) or (next(iter(raw.values()), {}) if raw else {})
        elif isinstance(raw, list):
            data = raw[0] if raw else {}
        else:
            data = {}
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/positions', methods=['GET'])
def positions():
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    try:
        segment = request.args.get('segment', 'derivative')
        product = request.args.get('product', 'margin')
        r = _requests.get(f'{BASE_URL}/portfolio/positions',
                         headers=_headers(), timeout=5,
                         params={'segment': segment, 'product': product})
        return jsonify({"success": r.ok, "data": r.json().get('data') if r.ok else r.text})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/holdings', methods=['GET'])
def holdings():
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    try:
        r = _requests.get(f'{BASE_URL}/portfolio/holdings', headers=_headers(), timeout=5)
        return jsonify({"success": r.ok, "data": r.json().get('data') if r.ok else r.text})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/order-book', methods=['GET'])
def order_book():
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    try:
        r = _requests.get(f'{BASE_URL}/order-book', headers=_headers(), timeout=5)
        return jsonify({"success": r.ok, "data": r.json().get('data') if r.ok else r.text})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/order', methods=['POST'])
def place_order():
    """
    Place a BUY or SELL order (equity).
    Body: { ticker, txn_type, qty, order_type, product, limit_price? }
    Guarded by LIVE_TRADING_ENABLED env var.
    """
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401

    body       = request.get_json() or {}
    ticker     = body.get('ticker', '')
    txn_type   = body.get('txn_type', 'BUY').upper()
    qty        = body.get('qty', 1)
    order_type = body.get('order_type', 'MARKET').upper()
    product    = body.get('product', 'CNC').upper()
    limit_px   = body.get('limit_price')

    if not _live_trading_enabled():
        logger.info(f"[order] PAPER MODE — {txn_type} {ticker} qty={qty} (LIVE_TRADING_ENABLED=false)")
        _order_broadcast({
            'type': 'order_update', 'severity': 'warning',
            'title': f"Paper: {txn_type} {ticker}",
            'status': 'SIMULATED', 'symbol': ticker,
            'txn_type': txn_type, 'qty': qty,
            'message': 'LIVE_TRADING_ENABLED=false — order not sent to broker',
            'timestamp': datetime.now().isoformat(),
        })
        return jsonify({"success": True, "data": {"order_id": "PAPER", "status": "SIMULATED"}})

    sec_id = _security_id(ticker)
    if not sec_id:
        return jsonify({"success": False, "error": f"Instrument not found: {ticker}"}), 400

    exch = _exchange(ticker)
    _eq_product_map = {'NRML': 'CNC', 'MIS': 'INTRADAY', 'CNC': 'CNC',
                       'MARGIN': 'MARGIN', 'INTRADAY': 'INTRADAY'}
    api_product = _eq_product_map.get(product, 'CNC')
    algo_id = "9999999999999999" if exch == 'BSE' else "99999"

    payload = {
        "txn_type":   txn_type,
        "exchange":   exch,
        "segment":    "EQUITY",
        "product":    api_product,
        "order_type": order_type,
        "validity":   "DAY",
        "security_id": sec_id,
        "qty":        qty,
        "is_amo":     False,
        "algo_id":    algo_id,
    }
    if order_type == 'LIMIT' and limit_px:
        payload['limit_price'] = limit_px

    try:
        r = _requests.post(f'{BASE_URL}/order', headers=_headers(),
                           json=payload, timeout=10)
        return jsonify({"success": r.ok, "data": r.json()})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/order/cancel', methods=['POST'])
def cancel_order():
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401
    if not _live_trading_enabled():
        return jsonify({"success": True, "data": {"status": "SIMULATED", "message": "Paper mode — cancel not sent"}})
    body     = request.get_json() or {}
    order_id = body.get('order_id')
    segment  = body.get('segment', 'DERIVATIVE')
    if not order_id:
        return jsonify({"success": False, "error": "order_id required"}), 400
    try:
        r = _requests.post(f'{BASE_URL}/order/cancel', headers=_headers(),
                           json={"order_id": order_id, "segment": segment}, timeout=10)
        return jsonify({"success": r.ok, "data": r.json()})
    except Exception as e:
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/order/fo', methods=['POST'])
def place_fo_order():
    """
    Place an F&O (options/futures) order.
    Body: { ticker, txn_type, qty, order_type, product, limit_price? }
    ticker: trading symbol like 'NIFTY-JUN2026-23500-CE' or 'NIFTY-JUN2026-FUT'
    product: NRML (carry-forward) or MIS (intraday), default NRML
    Guarded by LIVE_TRADING_ENABLED env var.
    """
    if not _connected():
        return jsonify({"success": False, "error": "INDMONEY_ACCESS_TOKEN not set"}), 401

    body       = request.get_json() or {}
    ticker     = body.get('ticker', '')
    txn_type   = body.get('txn_type', 'BUY').upper()
    qty        = body.get('qty', 1)
    order_type = body.get('order_type', 'MARKET').upper()
    product    = body.get('product', 'NRML').upper()
    limit_px   = body.get('limit_price')

    if not _live_trading_enabled():
        display = ticker
        try:
            inst = _resolve_fo_instrument(ticker)
            if inst:
                display = (inst.get('CUSTOM_SYMBOL') or ticker).strip()
        except Exception:
            pass
        logger.info(f"[order/fo] PAPER MODE — {txn_type} {display} qty={qty} (LIVE_TRADING_ENABLED=false)")
        _order_broadcast({
            'type': 'order_update', 'severity': 'warning',
            'title': f"Paper: {txn_type} {display}",
            'status': 'SIMULATED', 'symbol': display,
            'txn_type': txn_type, 'qty': qty,
            'message': 'LIVE_TRADING_ENABLED=false — order not sent to broker',
            'timestamp': datetime.now().isoformat(),
        })
        return jsonify({"success": True, "data": {"order_id": "PAPER", "status": "SIMULATED"}})

    # Resolve F&O instrument from master
    inst = _resolve_fo_instrument(ticker)
    if not inst:
        _order_broadcast({
            'type': 'order_update', 'severity': 'error',
            'title': f"Instrument not found: {ticker}",
            'status': 'REJECTED', 'symbol': ticker,
            'txn_type': txn_type, 'message': 'Could not resolve F&O instrument',
            'timestamp': datetime.now().isoformat(),
        })
        return jsonify({"success": False, "error": f"F&O instrument not found: {ticker}"}), 400

    sec_id = (inst.get('SECURITY_ID') or '').strip()
    seg    = (inst.get('SEGMENT') or '').strip().upper()
    exch   = (inst.get('EXCH') or '').strip().upper()

    # Exchange: NSE or BSE based on instrument master
    if exch.startswith('B') or seg == 'BFO':
        exchange = 'BSE'
    else:
        exchange = 'NSE'

    # Map product: NRML/MIS → API enum (MARGIN/INTRADAY)
    _product_map = {'NRML': 'MARGIN', 'MIS': 'INTRADAY', 'CNC': 'CNC',
                    'MARGIN': 'MARGIN', 'INTRADAY': 'INTRADAY'}
    api_product = _product_map.get(product, 'MARGIN')

    # algo_id: "99999" for NSE, "9999999999999999" for BSE (per API docs)
    algo_id = "9999999999999999" if exchange == 'BSE' else "99999"

    payload = {
        "txn_type":    txn_type,
        "exchange":    exchange,
        "segment":     "DERIVATIVE",
        "product":     api_product,
        "order_type":  order_type,
        "validity":    "DAY",
        "security_id": sec_id,
        "qty":         qty,
        "is_amo":      False,
        "algo_id":     algo_id,
    }
    if order_type == 'LIMIT' and limit_px:
        payload['limit_price'] = limit_px

    display = (inst.get('CUSTOM_SYMBOL') or ticker).strip()
    logger.info(f"[order/fo] Placing {txn_type} {display} qty={qty} type={order_type} product={product}")

    try:
        r = _requests.post(f'{BASE_URL}/order', headers=_headers(),
                           json=payload, timeout=10)
        resp = r.json()
        # Broadcast immediate feedback
        if r.ok:
            _order_broadcast({
                'type': 'order_update', 'severity': 'success',
                'title': f"Order placed: {txn_type} {display}",
                'status': 'PLACED', 'symbol': display,
                'txn_type': txn_type, 'qty': qty,
                'order_id': resp.get('data', {}).get('order_id', ''),
                'message': f"qty={qty} {order_type} {product}",
                'timestamp': datetime.now().isoformat(),
            })
        else:
            err_msg = resp.get('message') or resp.get('error') or str(resp)
            _order_broadcast({
                'type': 'order_update', 'severity': 'error',
                'title': f"Order failed: {txn_type} {display}",
                'status': 'FAILED', 'symbol': display,
                'txn_type': txn_type, 'qty': qty,
                'message': err_msg,
                'timestamp': datetime.now().isoformat(),
            })
        return jsonify({"success": r.ok, "data": resp})
    except Exception as e:
        _order_broadcast({
            'type': 'order_update', 'severity': 'error',
            'title': f"Order error: {txn_type} {display}",
            'status': 'ERROR', 'symbol': display,
            'txn_type': txn_type, 'message': str(e),
            'timestamp': datetime.now().isoformat(),
        })
        return jsonify({"success": False, "error": _safe_error(e)}), 500


@indmoney_bp.route('/order-events/stream', methods=['GET'])
def order_events_stream():
    """
    SSE endpoint pushing real-time order status updates (fills, rejections, etc.).
    The frontend connects here and shows toast/snackbar notifications.
    """
    from flask import stream_with_context

    @stream_with_context
    def gen():
        q = order_subscribe_sse()
        deadline = time.time() + _SSE_MAX_MINUTES * 60
        try:
            yield 'data: {"type":"order_events_connected"}\n\n'
            while time.time() < deadline:
                try:
                    payload = q.get(timeout=15.0)
                    yield f'data: {json.dumps(payload, default=str)}\n\n'
                except Exception:
                    yield 'data: {"type":"heartbeat"}\n\n'
            yield f'data: {json.dumps({"type": "timeout", "message": "stream expired, please reconnect"})}\n\n'
        except GeneratorExit:
            pass
        finally:
            order_unsubscribe_sse(q)

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
