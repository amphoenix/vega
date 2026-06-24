"""Forex market data API — FastAPI router.

Broker-first for INR pairs (USDINR, EURINR, GBPINR, JPYINR) via FUTCUR.
Yahoo Finance fallback for global pairs (EURUSD, GBPUSD, etc.).
"""
from __future__ import annotations

import asyncio
import json
import time as _time

from ..shared.time import clock

import requests
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..shared.logger import get_logger
from ..dependencies import get_broker

logger = get_logger('api.forex')
router = APIRouter(prefix='/api/forex', tags=['forex'])

_YF_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart'

_UNIVERSE = [
    'EURUSD=X',
    'GBPUSD=X',
    'USDJPY=X',
    'USDINR=X',
    'GBPINR=X',
    'EURINR=X',
    'AUDUSD=X',
    'USDCAD=X',
    'NZDUSD=X',
]

# Yahoo ticker → CDS base name (only INR pairs go through broker)
_PAIR_TO_CDS: dict[str, str] = {
    'USDINR=X': 'USDINR',
    'EURINR=X': 'EURINR',
    'GBPINR=X': 'GBPINR',
    'JPYINR=X': 'JPYINR',
}

# interval → (yf_interval, yf_range)
_INTERVAL_MAP = {
    '1m':  ('1m',  '1d'),
    '5m':  ('5m',  '1mo'),
    '15m': ('15m', '1mo'),
    '1h':  ('1h',  '3mo'),
    '4h':  ('60m', '3mo'),  # YF has no 4h; use 1h/3mo and relabel
    '1d':  ('1d',  '1y'),
}

# interval → broker days
_BROKER_DAYS = {
    '1m': 1, '5m': 7, '15m': 15, '1h': 60, '4h': 60, '1d': 365,
}

_cache: dict[str, tuple[float, object]] = {}

# Contract cache — reuse across ticker/ohlcv/stream calls
_contract_cache: dict[str, dict] = {}
_contract_cache_ts: float = 0


def _hit(key: str, ttl: float = 3.0) -> object | None:
    e = _cache.get(key)
    return e[1] if e and clock() - e[0] < ttl else None


def _set(key: str, val: object) -> None:
    _cache[key] = (clock(), val)


def _is_broker_pair(pair: str) -> bool:
    return pair in _PAIR_TO_CDS


# ── Contract resolution (cached 1hr) ──────────────────────────────────────────

def _resolve_contract(cds_base: str) -> dict | None:
    """Resolve nearest FUTCUR contract — cached 1 hour."""
    global _contract_cache_ts
    now = _time.time()
    if cds_base in _contract_cache and (now - _contract_cache_ts) < 3600:
        return _contract_cache[cds_base]
    try:
        from ..engines.forex_scanner import _resolve_cur_future
        result = _resolve_cur_future(cds_base)
    except Exception as e:
        logger.warning('Contract resolve %s: %s', cds_base, e)
        result = None
    if result:
        _contract_cache[cds_base] = result
        _contract_cache_ts = now
    return result


# ── Broker data helpers ───────────────────────────────────────────────────────

def _broker_ticker(pair: str) -> dict | None:
    """Fetch ticker for an INR pair via broker LTP on its FUTCUR contract."""
    cds_base = _PAIR_TO_CDS.get(pair)
    if not cds_base:
        return None
    contract = _resolve_contract(cds_base)
    if not contract:
        return None
    try:
        broker = get_broker()
        sym = contract['trading_symbol']
        sec_id = contract['security_id']
        ltp = broker.get_ltp(sym, exchange='CUR', security_id=sec_id)
        if not ltp:
            return None
        spread_pct = 0.0002  # synthetic spread for display
        out = {
            'symbol':     pair,
            'exchange':   'broker',
            'last':       ltp,
            'bid':        round(ltp * (1 - spread_pct / 2), 4),
            'ask':        round(ltp * (1 + spread_pct / 2), 4),
            'spread':     round(ltp * spread_pct, 4),
            'high':       ltp,
            'low':        ltp,
            'volume':     0,
            'change_pct': 0.0,
            'prev_close': ltp,
            'ts':         int(clock() * 1000),
        }
        return out
    except Exception as e:
        logger.debug('Broker ticker %s: %s', pair, e)
        return None


def _broker_quote_ticker(pair: str) -> dict | None:
    """Secondary broker fallback: use get_quote (quote_data API) instead of ticker_data."""
    cds_base = _PAIR_TO_CDS.get(pair)
    if not cds_base:
        return None
    contract = _resolve_contract(cds_base)
    if not contract:
        return None
    try:
        broker = get_broker()
        sym = contract['trading_symbol']
        sec_id = contract['security_id']
        quote = broker.get_quote(sym, exchange='CUR', security_id=sec_id)
        if not quote or not quote.ltp:
            return None
        ltp = float(quote.ltp)
        return {
            'symbol':     pair,
            'exchange':   'broker_quote',
            'last':       ltp,
            'bid':        float(quote.bid) if quote.bid else round(ltp * 0.9999, 4),
            'ask':        float(quote.ask) if quote.ask else round(ltp * 1.0001, 4),
            'spread':     round(abs((quote.ask or ltp) - (quote.bid or ltp)), 4),
            'high':       float(quote.high) if quote.high else ltp,
            'low':        float(quote.low) if quote.low else ltp,
            'volume':     int(quote.volume) if quote.volume else 0,
            'change_pct': 0.0,
            'prev_close': float(quote.close) if quote.close else ltp,
            'ts':         int(clock() * 1000),
        }
    except Exception as e:
        logger.debug('Broker quote ticker %s: %s', pair, e)
        return None


def _broker_ohlcv(pair: str, interval: str, limit: int) -> list | None:
    """Fetch OHLCV for an INR pair via broker candles on its FUTCUR contract."""
    cds_base = _PAIR_TO_CDS.get(pair)
    if not cds_base:
        return None
    contract = _resolve_contract(cds_base)
    if not contract:
        return None
    try:
        broker = get_broker()
        sym = contract['trading_symbol']
        sec_id = contract['security_id']
        days = _BROKER_DAYS.get(interval, 7)
        broker_interval = '1d' if interval == '1d' else interval.replace('4h', '1h')
        raw = broker.get_candles(
            sym, interval=broker_interval, days=days,
            exchange='CUR', security_id=sec_id,
        ) or []
        if len(raw) < 5:
            return None
        candles = []
        for c in raw:
            try:
                ts_str = getattr(c, 'date', '') or ''
                # Try parsing date to epoch ms
                ts_ms = _parse_candle_ts(ts_str)
                candles.append({
                    'time_ms': ts_ms,
                    'open':    float(c.open),
                    'high':    float(c.high),
                    'low':     float(c.low),
                    'close':   float(c.close),
                    'volume':  int(c.volume or 0),
                })
            except Exception:
                continue
        candles.sort(key=lambda x: x['time_ms'])
        return candles[-limit:] if candles else None
    except Exception as e:
        logger.debug('Broker OHLCV %s: %s', pair, e)
        return None


def _parse_candle_ts(ts_str: str) -> int:
    """Convert broker candle date string to epoch milliseconds."""
    from datetime import datetime, timezone
    if not ts_str:
        return int(clock() * 1000)
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d', '%d-%m-%Y %H:%M', '%d-%m-%Y'):
        try:
            dt = datetime.strptime(ts_str.strip(), fmt)
            return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    # Fallback: try parsing as epoch seconds
    try:
        f = float(ts_str)
        if f > 1_000_000_000_000:
            return int(f)
        if f > 1_000_000_000:
            return int(f * 1000)
    except (ValueError, TypeError):
        pass
    return int(clock() * 1000)


# ── Yahoo Finance fallback ───────────────────────────────────────────────────

def _yf_get(symbol: str, interval: str, range_: str) -> dict | None:
    url = f'{_YF_BASE}/{symbol}'
    params = {'interval': interval, 'range': range_}
    try:
        r = requests.get(url, params=params, timeout=8,
                         headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        data = r.json()
        results = data.get('chart', {}).get('result', [])
        if not results:
            return None
        return results[0]
    except Exception as e:
        logger.error('Yahoo Finance %s %s/%s: %s', symbol, interval, range_, e)
        return None


def _extract_ticker(symbol: str) -> dict | None:
    """Fetch current price — broker only for INR pairs, Yahoo for global."""
    key = f'fx:ticker:{symbol}'
    if h := _hit(key, ttl=3.0):
        return h

    # Broker-first for INR pairs (Dhan REST primary, Yahoo fallback)
    if _is_broker_pair(symbol):
        out = _broker_ticker(symbol)
        if out:
            _set(key, out)
            return out
        # Secondary: try broker quote_data (different API endpoint)
        out = _broker_quote_ticker(symbol)
        if out:
            _set(key, out)
            return out
        logger.debug('Broker ticker+quote both failed for %s — falling back to Yahoo', symbol)
        # Fall through to Yahoo v8 / yfinance below as safety net

    # Yahoo v8 chart API (global pairs + INR fallback when broker is down)
    result = _yf_get(symbol, '1m', '1d')
    if result:
        try:
            return _parse_v8_ticker(symbol, result)
        except Exception as e:
            logger.warning('v8 ticker parse %s: %s — falling back to yfinance', symbol, e)

    # Fallback: yfinance library
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        hist = tk.history(period='1d', interval='1m')
        if hist.empty:
            hist = tk.history(period='5d', interval='1d')
        if hist.empty:
            return None

        last_close = float(hist['Close'].iloc[-1])
        day_high = float(hist['High'].max())
        day_low = float(hist['Low'].min())
        volume = int(hist['Volume'].sum())

        info = tk.fast_info
        prev_close = getattr(info, 'previous_close', None) or last_close
        change_pct = ((last_close - prev_close) / prev_close * 100) if prev_close else 0.0

        spread_pct = 0.0002
        bid = last_close * (1 - spread_pct / 2)
        ask = last_close * (1 + spread_pct / 2)

        out = {
            'symbol':     symbol,
            'exchange':   'yahoo',
            'last':       last_close,
            'bid':        round(bid, 6),
            'ask':        round(ask, 6),
            'spread':     round(ask - bid, 6),
            'high':       day_high,
            'low':        day_low,
            'volume':     volume,
            'change_pct': round(change_pct, 4),
            'prev_close': prev_close,
            'ts':         int(clock() * 1000),
        }
        _set(key, out)
        return out
    except Exception as e:
        logger.error('yfinance ticker fallback %s: %s', symbol, e)
        return None


def _parse_v8_ticker(symbol: str, result: dict) -> dict | None:
    """Parse Yahoo v8 chart API response into ticker dict."""
    ts_list = result.get('timestamp', [])
    quotes  = result.get('indicators', {}).get('quote', [{}])[0]
    closes  = quotes.get('close', [])
    highs   = quotes.get('high', [])
    lows    = quotes.get('low', [])
    volumes = quotes.get('volume', [])

    valid_closes = [(i, c) for i, c in enumerate(closes) if c is not None]
    if not valid_closes:
        return None

    last_idx, last_close = valid_closes[-1]

    meta = result.get('meta', {})
    prev_close = meta.get('chartPreviousClose') or meta.get('previousClose')
    change_pct = 0.0
    if prev_close and prev_close != 0:
        change_pct = ((last_close - prev_close) / prev_close) * 100

    valid_highs = [v for v in highs if v is not None]
    valid_lows  = [v for v in lows if v is not None]
    valid_vols  = [v for v in volumes if v is not None]

    day_high = max(valid_highs) if valid_highs else last_close
    day_low  = min(valid_lows) if valid_lows else last_close
    volume   = sum(valid_vols) if valid_vols else 0

    spread_pct = 0.0002
    bid = last_close * (1 - spread_pct / 2)
    ask = last_close * (1 + spread_pct / 2)

    key = f'fx:ticker:{symbol}'
    out = {
        'symbol':     symbol,
        'exchange':   'yahoo',
        'last':       last_close,
        'bid':        round(bid, 6),
        'ask':        round(ask, 6),
        'spread':     round(ask - bid, 6),
        'high':       day_high,
        'low':        day_low,
        'volume':     int(volume),
        'change_pct': round(change_pct, 4),
        'prev_close': prev_close,
        'ts':         ts_list[last_idx] * 1000 if last_idx < len(ts_list) else int(clock() * 1000),
    }
    _set(key, out)
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────
# IMPORTANT: Fixed-path routes MUST come before {pair:path} catch-all routes,
# otherwise FastAPI matches /scanner/status as pair="scanner/status".

@router.get('/universe')
def get_universe():
    return {'success': True, 'data': _UNIVERSE}


# ── Status / auto-trading toggle ─────────────────────────────────────────────

@router.get('/status')
def get_status():
    from ..infrastructure.db import state_store
    auto = state_store.get_state('forex_auto_trading_enabled', 'true') == 'true'
    return {
        'success': True,
        'data': {
            'mode':       'paper',
            'auto_trade': auto,
            'exchange':   'forex',
        },
    }


@router.post('/auto-trading')
async def toggle_auto_trading(request: Request):
    from ..infrastructure.db import state_store
    body = await request.json()
    enabled = bool(body.get('enabled', False))
    state_store.set_state('forex_auto_trading_enabled', str(enabled).lower())
    return {'success': True, 'data': {'auto_trading_enabled': enabled}}


# ── Scanner endpoints ────────────────────────────────────────────────────────

@router.post('/scanner/start')
def scanner_start():
    from ..engines import forex_scanner as fs
    fs.start()
    return {'success': True, 'data': {'status': 'started'}}


@router.post('/scanner/stop')
def scanner_stop():
    from ..engines import forex_scanner as fs
    fs.stop()
    return {'success': True, 'data': {'status': 'stopped'}}


@router.post('/scanner/trigger')
def scanner_trigger():
    from ..engines import forex_scanner as fs
    fs.trigger_now()
    return {'success': True, 'data': {'status': 'triggered'}}


@router.get('/scanner/status')
def scanner_status():
    from ..engines import forex_scanner as fs
    return {'success': True, 'data': fs.get_state()}


@router.get('/scanner/stream')
async def scanner_stream(request: Request):
    from ..engines import forex_scanner as fs

    q = fs.subscribe_sse()

    async def _gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get(timeout=0.25)
                    yield {'event': 'message', 'data': msg}
                except Exception:
                    yield {'event': 'ping', 'data': '{}'}
                    await asyncio.sleep(1)
        finally:
            fs.unsubscribe_sse(q)

    return EventSourceResponse(
        _gen(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Catch-all pair routes (MUST be last — {pair:path} is greedy) ─────────────

@router.get('/ticker/{pair:path}')
def get_ticker(pair: str):
    d = _extract_ticker(pair)
    if not d:
        return JSONResponse({'success': False, 'error': 'fetch failed'}, status_code=502)
    return {'success': True, 'data': d}


@router.get('/ohlcv/{pair:path}')
def get_ohlcv(
    pair: str,
    interval: str = Query('1h', description='1m 5m 15m 1h 4h 1d'),
    limit: int = Query(200, le=1000),
):
    key = f'fx:ohlcv:{pair}:{interval}'
    if h := _hit(key, ttl=30.0):
        return {'success': True, 'data': h, '_cached': True}

    # Broker-first for INR pairs
    if _is_broker_pair(pair):
        candles = _broker_ohlcv(pair, interval, limit)
        if candles:
            _set(key, candles)
            return {'success': True, 'data': candles, 'symbol': pair, 'interval': interval, 'source': 'broker'}
        logger.debug('Broker OHLCV failed for %s — trying Yahoo', pair)

    # Yahoo fallback
    yf_interval, yf_range = _INTERVAL_MAP.get(interval, ('1h', '3mo'))
    result = _yf_get(pair, yf_interval, yf_range)
    if not result:
        return {'success': True, 'data': []}

    try:
        timestamps = result.get('timestamp', [])
        quotes     = result.get('indicators', {}).get('quote', [{}])[0]
        opens_     = quotes.get('open',   [])
        highs_     = quotes.get('high',   [])
        lows_      = quotes.get('low',    [])
        closes_    = quotes.get('close',  [])
        volumes_   = quotes.get('volume', [])

        candles = []
        for i, ts in enumerate(timestamps):
            o = opens_[i]  if i < len(opens_)  else None
            h = highs_[i]  if i < len(highs_)  else None
            l = lows_[i]   if i < len(lows_)   else None
            c = closes_[i] if i < len(closes_) else None
            v = volumes_[i] if i < len(volumes_) else 0

            if o is None or h is None or l is None or c is None:
                continue

            candles.append({
                'time_ms': int(ts) * 1000,
                'open':    o,
                'high':    h,
                'low':     l,
                'close':   c,
                'volume':  int(v) if v else 0,
            })

        candles = candles[-limit:]
        _set(key, candles)
        return {'success': True, 'data': candles, 'symbol': pair, 'interval': interval, 'source': 'yahoo'}
    except Exception as e:
        logger.error('OHLCV parse %s: %s', pair, e)
        return {'success': True, 'data': []}


@router.get('/stream/{pair:path}')
async def stream_forex_ticker(pair: str, request: Request):
    """SSE — broker LTP for INR pairs, Yahoo fallback for global. Polls every 5s."""

    async def _gen():
        while True:
            if await request.is_disconnected():
                break
            try:
                loop = asyncio.get_running_loop()
                d = await loop.run_in_executor(None, _extract_ticker, pair)
                if d:
                    yield {
                        'event': 'tick',
                        'data': json.dumps({
                            'symbol':     d['symbol'],
                            'last':       d['last'],
                            'change_pct': d['change_pct'],
                            'high':       d['high'],
                            'low':        d['low'],
                            'volume':     d['volume'],
                            'bid':        d['bid'],
                            'ask':        d['ask'],
                            'spread':     d['spread'],
                            'ts':         d['ts'],
                        }),
                    }
                else:
                    yield {'event': 'heartbeat', 'data': json.dumps({'symbol': pair})}
            except Exception as e:
                logger.warning('Forex SSE %s: %s', pair, e)
                yield {'event': 'heartbeat', 'data': json.dumps({'symbol': pair})}
            await asyncio.sleep(5)

    return EventSourceResponse(
        _gen(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
