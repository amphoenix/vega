"""
Crypto market data API — FastAPI router.

Binance: public REST (api.binance.com) + WebSocket → SSE
Bybit:   public REST (api.bybit.com)   + WebSocket → SSE
Account ops: CCXTAdapter (paper or live, key-optional)
"""
from __future__ import annotations

import asyncio
import json

import requests
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..infrastructure.exchange.public_ws import get_stream_manager
from ..shared.logger import get_logger
from ..shared.time import clock

logger = get_logger('api.crypto')
router = APIRouter(prefix='/api/crypto', tags=['crypto'])

_BINANCE   = 'https://api.binance.com'
_BINANCE_F = 'https://fapi.binance.com'
_BYBIT     = 'https://api.bybit.com'

_UNIVERSE = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
    'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT',
]

_cache: dict[str, tuple[float, object]] = {}


def _hit(key: str, ttl: float = 3.0) -> object | None:
    e = _cache.get(key)
    return e[1] if e and clock() - e[0] < ttl else None


def _set(key: str, val: object) -> None:
    _cache[key] = (clock(), val)


def _sym(pair: str) -> str:
    return pair.upper().replace('/', '')


def _bget(path: str, params: dict | None = None, futures: bool = False) -> dict | list:
    base = _BINANCE_F if futures else _BINANCE
    try:
        r = requests.get(f'{base}{path}', params=params, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error('Binance REST %s: %s', path, e)
        return {}


def _yget(path: str, params: dict | None = None) -> dict:
    try:
        r = requests.get(f'{_BYBIT}{path}', params=params, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug('Bybit REST %s: %s', path, e)
        return {}


# ── Binance REST ──────────────────────────────────────────────────────────────

@router.get('/ticker/{pair:path}')
def get_ticker(pair: str):
    sym = _sym(pair)
    if h := _hit(f'bnb:ticker:{sym}'):
        return {'success': True, 'data': h, '_cached': True}
    d = _bget('/api/v3/ticker/24hr', {'symbol': sym})
    if not d:
        return JSONResponse({'success': False, 'error': 'fetch failed'}, status_code=502)
    result = {
        'symbol': sym, 'exchange': 'binance',
        'last':         float(d.get('lastPrice', 0)),
        'bid':          float(d.get('bidPrice', 0)),
        'ask':          float(d.get('askPrice', 0)),
        'volume':       float(d.get('volume', 0)),
        'quote_volume': float(d.get('quoteVolume', 0)),
        'change_pct':   float(d.get('priceChangePercent', 0)),
        'high':         float(d.get('highPrice', 0)),
        'low':          float(d.get('lowPrice', 0)),
        'trades':       int(d.get('count', 0)),
    }
    _set(f'bnb:ticker:{sym}', result)
    return {'success': True, 'data': result}


@router.get('/ohlcv/{pair:path}')
def get_ohlcv(
    pair: str,
    interval: str = Query('1h', description='1m 5m 15m 1h 4h 1d'),
    limit: int = Query(200, le=1000),
):
    sym = _sym(pair)
    key = f'bnb:ohlcv:{sym}:{interval}:{limit}'
    if h := _hit(key, ttl=30.0):
        return {'success': True, 'data': h, '_cached': True}
    d = _bget('/api/v3/klines', {'symbol': sym, 'interval': interval, 'limit': limit})
    if not isinstance(d, list):
        return JSONResponse({'success': False, 'error': 'fetch failed'}, status_code=502)
    candles = [
        {
            'time': c[0], 'open': float(c[1]), 'high': float(c[2]),
            'low': float(c[3]), 'close': float(c[4]), 'volume': float(c[5]),
            'close_time': c[6], 'quote_volume': float(c[7]), 'trades': int(c[8]),
        }
        for c in d
    ]
    _set(key, candles)
    return {'success': True, 'data': candles, 'symbol': sym, 'interval': interval}


@router.get('/order-book/{pair:path}')
def get_order_book(pair: str, depth: int = Query(20, le=100)):
    sym = _sym(pair)
    d = _bget('/api/v3/depth', {'symbol': sym, 'limit': depth})
    if not d:
        return JSONResponse({'success': False, 'error': 'fetch failed'}, status_code=502)
    return {
        'success': True,
        'data': {
            'symbol': sym, 'exchange': 'binance',
            'bids': [[float(p), float(q)] for p, q in d.get('bids', [])],
            'asks': [[float(p), float(q)] for p, q in d.get('asks', [])],
            'last_update_id': d.get('lastUpdateId'),
        },
    }


@router.get('/funding/{pair:path}')
def get_funding(pair: str):
    sym = _sym(pair)
    if h := _hit(f'bnb:funding:{sym}', ttl=60.0):
        return {'success': True, 'data': h, '_cached': True}
    d = _bget('/fapi/v1/premiumIndex', {'symbol': sym}, futures=True)
    if not d:
        return JSONResponse({'success': False, 'error': 'fetch failed'}, status_code=502)
    result = {
        'symbol': sym, 'exchange': 'binance_futures',
        'mark_price':        float(d.get('markPrice', 0)),
        'index_price':       float(d.get('indexPrice', 0)),
        'funding_rate':      float(d.get('lastFundingRate', 0)),
        'next_funding_time': d.get('nextFundingTime'),
    }
    _set(f'bnb:funding:{sym}', result)
    return {'success': True, 'data': result}


# ── Bybit REST ────────────────────────────────────────────────────────────────

@router.get('/bybit/ticker/{pair:path}')
def get_bybit_ticker(pair: str, category: str = Query('linear')):
    """Futures ticker: try Bybit first, fall back to Binance Futures."""
    sym = _sym(pair)

    # ── Try Bybit ────────────────────────────────────────────────────
    d = _yget('/v5/market/tickers', {'category': category, 'symbol': sym})
    lst = d.get('result', {}).get('list', [])
    if lst:
        t = lst[0]
        return {
            'success': True,
            'data': {
                'symbol': sym, 'exchange': 'bybit', 'category': category,
                'last':              float(t.get('lastPrice', 0)),
                'mark_price':        float(t.get('markPrice', 0) or 0),
                'index_price':       float(t.get('indexPrice', 0) or 0),
                'bid':               float(t.get('bid1Price', 0)),
                'ask':               float(t.get('ask1Price', 0)),
                'volume':            float(t.get('volume24h', 0)),
                'quote_volume':      float(t.get('turnover24h', 0)),
                'change_pct':        float(t.get('price24hPcnt', 0)),
                'high':              float(t.get('highPrice24h', 0)),
                'low':               float(t.get('lowPrice24h', 0)),
                'prev_price_24h':    float(t.get('prevPrice24h', 0) or 0),
                'open_interest':     float(t.get('openInterest', 0) or 0),
                'open_interest_val': float(t.get('openInterestValue', 0) or 0),
                'funding_rate':      float(t.get('fundingRate', 0) or 0),
                'next_funding_time': t.get('nextFundingTime', ''),
            },
        }

    # ── Fallback: Binance Futures (works in India) ───────────────────
    try:
        pm = _bget('/fapi/v1/premiumIndex', {'symbol': sym}, futures=True)
        tk = _bget('/fapi/v1/ticker/24hr', {'symbol': sym}, futures=True)
        if pm and tk:
            return {
                'success': True,
                'data': {
                    'symbol': sym, 'exchange': 'binance_futures', 'category': category,
                    'last':              float(tk.get('lastPrice', 0)),
                    'mark_price':        float(pm.get('markPrice', 0) or 0),
                    'index_price':       float(pm.get('indexPrice', 0) or 0),
                    'bid':               float(tk.get('bidPrice', 0) or 0),
                    'ask':               float(tk.get('askPrice', 0) or 0),
                    'volume':            float(tk.get('volume', 0)),
                    'quote_volume':      float(tk.get('quoteVolume', 0)),
                    'change_pct':        float(tk.get('priceChangePercent', 0)) / 100,
                    'high':              float(tk.get('highPrice', 0)),
                    'low':               float(tk.get('lowPrice', 0)),
                    'prev_price_24h':    float(tk.get('prevClosePrice', 0) or 0),
                    'open_interest':     0,  # separate endpoint, skip for now
                    'open_interest_val': 0,
                    'funding_rate':      float(pm.get('lastFundingRate', 0) or 0),
                    'next_funding_time': pm.get('nextFundingTime', ''),
                },
            }
    except Exception as e:
        logger.debug('Binance Futures fallback failed for %s: %s', sym, e)

    return JSONResponse({'success': False, 'error': 'fetch failed'}, status_code=502)


@router.get('/bybit/orderbook/{pair:path}')
def get_bybit_orderbook(pair: str, depth: int = Query(25, le=200), category: str = Query('linear')):
    sym = _sym(pair)
    d = _yget('/v5/market/orderbook', {'category': category, 'symbol': sym, 'limit': depth})
    r = d.get('result', {})
    return {
        'success': True,
        'data': {
            'symbol': sym, 'exchange': 'bybit',
            'bids': [[float(p), float(q)] for p, q in r.get('b', [])],
            'asks': [[float(p), float(q)] for p, q in r.get('a', [])],
            'ts': r.get('ts'),
        },
    }


@router.get('/bybit/trades/{pair:path}')
def get_bybit_trades(pair: str, limit: int = Query(50, le=1000), category: str = Query('linear')):
    sym = _sym(pair)
    d = _yget('/v5/market/recent-trade', {'category': category, 'symbol': sym, 'limit': limit})
    trades = [
        {
            'time':     t.get('time'),
            'price':    float(t.get('price', 0)),
            'qty':      float(t.get('size', 0)),
            'side':     t.get('side', '').upper(),
            'trade_id': t.get('execId', ''),
        }
        for t in d.get('result', {}).get('list', [])
    ]
    return {'success': True, 'data': trades, 'symbol': sym}


@router.get('/bybit/open-interest/{pair:path}')
def get_bybit_oi(
    pair: str,
    interval: str = Query('1h', description='5min 15min 30min 1h 4h 1d'),
    limit: int = Query(50, le=200),
    category: str = Query('linear'),
):
    sym = _sym(pair)
    d = _yget('/v5/market/open-interest', {
        'category': category, 'symbol': sym,
        'intervalTime': interval, 'limit': limit,
    })
    oi = [
        {'oi': float(i.get('openInterest', 0)), 'time': i.get('timestamp')}
        for i in d.get('result', {}).get('list', [])
    ]
    return {'success': True, 'data': oi, 'symbol': sym}


# ── Account via CCXTAdapter ───────────────────────────────────────────────────

@router.get('/balances')
async def get_balances():
    from ..dependencies import get_crypto_exchange
    ex = get_crypto_exchange()
    if not ex.is_connected:
        await ex.connect()
    bals = await ex.get_balances()
    return {
        'success': True,
        'data': [
            {'currency': b.currency, 'free': b.free, 'used': b.used, 'total': b.total}
            for b in bals
        ],
    }


@router.get('/positions')
async def get_positions():
    from ..dependencies import get_crypto_exchange
    ex = get_crypto_exchange()
    if not ex.is_connected:
        await ex.connect()
    pos = await ex.get_positions()
    return {
        'success': True,
        'data': [
            {
                'symbol': p.symbol, 'side': p.side, 'qty': p.qty,
                'avg_entry': p.avg_entry, 'unrealized_pnl': p.unrealized_pnl,
            }
            for p in pos
        ],
    }


@router.post('/order')
async def place_order(payload: dict):
    from ..dependencies import get_crypto_exchange
    from ..domain.value_objects.instrument import Instrument
    from ..domain.value_objects.market import MarketType
    ex = get_crypto_exchange()
    if not ex.is_connected:
        await ex.connect()
    instrument = Instrument(symbol=_sym(payload.get('symbol', '')), market_type=MarketType.CRYPTO)
    order = await ex.place_order(
        instrument=instrument,
        side=payload.get('side', 'BUY').upper(),
        qty=float(payload.get('qty', 0)),
        order_type=payload.get('order_type', 'MARKET').upper(),
        price=float(payload.get('price', 0)),
    )
    return {
        'success': order.is_filled or order.status == 'PENDING',
        'data': {
            'order_id':   order.order_id,
            'status':     order.status,
            'fill_price': order.fill_price,
            'filled_qty': order.filled_qty,
        },
    }


@router.post('/order/cancel')
async def cancel_order(payload: dict):
    from ..dependencies import get_crypto_exchange
    ex = get_crypto_exchange()
    ok = await ex.cancel_order(payload.get('order_id', ''))
    return {'success': ok}


# ── Universe / search ─────────────────────────────────────────────────────────

@router.get('/universe')
def get_universe():
    return {'success': True, 'data': _UNIVERSE}


@router.get('/search')
def search_pairs(q: str = Query(...)):
    q_up = q.upper().replace('/', '')
    return {'success': True, 'data': [s for s in _UNIVERSE if q_up in s]}


# ── Crypto scanner ───────────────────────────────────────────────────────────

@router.get('/scanner/status')
def scanner_status():
    """Scanner running state, signal count, positions."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    sc = get_crypto_scanner()
    return {
        'success': True,
        'data': {
            'running':       sc.is_running(),
            'signal_count':  sc.signal_count(),
            'open_positions': len(sc.get_positions()),
        },
    }


@router.get('/scanner/signals')
def scanner_signals(limit: int = Query(30, le=100)):
    """Last N signals (most-recent first)."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    return {'success': True, 'data': get_crypto_scanner().get_signals(limit)}


@router.get('/scanner/positions')
def scanner_positions():
    """Open paper positions with SL/TP/confidence."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    return {'success': True, 'data': get_crypto_scanner().get_positions()}


@router.get('/scanner/pnl')
def scanner_pnl():
    """Today's P&L { realized, unrealized, net, daily, positions }."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    return {'success': True, 'data': get_crypto_scanner().get_pnl()}


@router.get('/scanner/config')
def scanner_config():
    """Current scanner configuration."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    return {'success': True, 'data': get_crypto_scanner().get_config()}


@router.post('/scanner/start')
def scanner_start():
    """Start the scanner background thread."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    get_crypto_scanner().start()
    return {'success': True, 'data': {'running': True}}


@router.post('/scanner/stop')
def scanner_stop():
    """Stop scanner."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    get_crypto_scanner().stop()
    return {'success': True, 'data': {'running': False}}


@router.post('/scanner/close/{symbol}')
def scanner_close_position(symbol: str):
    """Manually close a scanner position."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    result = get_crypto_scanner().manual_close(symbol.upper())
    if result is None:
        return JSONResponse({'success': False, 'error': f'No open position for {symbol}'}, status_code=404)
    return {'success': True, 'data': result}


@router.post('/scanner/reset-daily')
def scanner_reset_daily():
    """Reset daily P&L counter."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner
    get_crypto_scanner().reset_daily()
    return {'success': True}


@router.get('/scanner/stream')
async def scanner_signal_stream(request: Request):
    """SSE stream: polls scanner every 3s, emits new signals + position updates."""
    from ..domain.strategies.crypto_scanner import get_crypto_scanner

    async def _gen():
        sc = get_crypto_scanner()
        seen: set[str] = set()
        try:
            while True:
                if await request.is_disconnected():
                    break
                for sig in sc.get_signals():
                    if sig['id'] not in seen:
                        seen.add(sig['id'])
                        yield {
                            'event': 'signal',
                            'data': json.dumps(sig),
                        }
                # Also emit position and P&L updates
                yield {
                    'event': 'positions',
                    'data': json.dumps(sc.get_positions()),
                }
                yield {
                    'event': 'pnl',
                    'data': json.dumps(sc.get_pnl()),
                }
                await asyncio.sleep(3.0)
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(
        _gen(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Trade history ────────────────────────────────────────────────────────────

@router.get('/trades')
def get_trade_history(limit: int = Query(50, le=200)):
    """Recent closed crypto trades from the P&L ledger."""
    from ..infrastructure.db import state_store
    trades = state_store.recent_trades(limit=limit, market_type='crypto')
    return {'success': True, 'data': trades}


@router.get('/pnl/summary')
def get_pnl_summary():
    """Today's crypto P&L summary."""
    from ..infrastructure.db import state_store
    summary = state_store.daily_summary_by_segment()
    return {
        'success': True,
        'data': summary.get('crypto', {'trades': 0, 'gross': 0.0, 'brokerage': 0.0, 'net': 0.0}),
    }


# ── Manual quick-trade ──────────────────────────────────────────────────────

@router.post('/quick-trade')
async def quick_trade(request: Request):
    """Place a quick market order (paper or live).

    Body: { symbol, side: 'BUY'|'SELL', notional_usd: float }
    For paper: uses scanner's position tracker.
    For live: routes through CCXTAdapter.
    """
    body = await request.json()
    symbol = _sym(body.get('symbol', ''))
    side = body.get('side', 'BUY').upper()
    notional = float(body.get('notional_usd', 0))

    if not symbol or side not in ('BUY', 'SELL') or notional <= 0:
        return JSONResponse({'success': False, 'error': 'Invalid params'}, status_code=400)

    # Fetch current price
    d = _bget('/api/v3/ticker/price', {'symbol': symbol})
    price = float(d.get('price', 0))
    if price <= 0:
        return JSONResponse({'success': False, 'error': f'Cannot get price for {symbol}'}, status_code=502)

    qty = round(notional / price, 6)

    from ..config import settings as cfg
    if cfg.crypto_mode == 'paper':
        # Paper fill via scanner position tracker
        from ..domain.strategies.crypto_scanner import get_crypto_scanner
        sc = get_crypto_scanner()
        if side == 'BUY':
            signal_stub = {
                'confidence': 100, 'sl_dist': price * 0.02, 'tp_dist': price * 0.04,
            }
            sc._open_position(symbol, price, signal_stub)
            return {'success': True, 'data': {
                'action': 'BUY', 'symbol': symbol, 'price': price,
                'qty': qty, 'mode': 'paper',
            }}
        else:
            result = sc.manual_close(symbol)
            if result is None:
                return JSONResponse({'success': False, 'error': f'No position to close for {symbol}'}, status_code=404)
            return {'success': True, 'data': {**result, 'mode': 'paper'}}
    else:
        # Live via CCXT
        from ..dependencies import get_crypto_exchange
        from ..domain.value_objects.instrument import Instrument
        from ..domain.value_objects.market import MarketType
        ex = get_crypto_exchange()
        if not ex.is_connected:
            await ex.connect()
        instrument = Instrument(symbol=symbol, market_type=MarketType.CRYPTO)
        order = await ex.place_order(
            instrument=instrument, side=side, qty=qty,
            order_type='MARKET', price=0,
        )
        return {'success': True, 'data': {
            'order_id': order.order_id, 'status': order.status,
            'fill_price': order.fill_price, 'filled_qty': order.filled_qty,
            'mode': 'live',
        }}


# ── SSE streams ───────────────────────────────────────────────────────────────

@router.get('/status')
def get_status():
    from ..config import settings
    return {
        'success': True,
        'data': {
            'mode':       settings.crypto_mode,
            'auto_trade': settings.crypto_auto_trade,
            'exchange':   settings.crypto_exchange,
        },
    }


@router.post('/auto-trading')
async def toggle_auto_trading(request: Request):
    from ..config import settings
    body = await request.json()
    enabled = bool(body.get('enabled', False))
    settings.crypto_auto_trade = enabled
    return {'success': True, 'data': {'auto_trading_enabled': enabled}}


@router.get('/stream/{pair:path}')
async def stream_binance_ticker(pair: str, request: Request):
    """Real-time Binance 24hr ticker via public WebSocket → SSE."""
    sym = _sym(pair)
    mgr = get_stream_manager()
    channel = f'binance:{sym.lower()}:ticker'

    loop = asyncio.get_running_loop()

    async def _gen():
        q = await loop.run_in_executor(None, mgr.subscribe, channel)
        loop.run_in_executor(None, mgr.start_binance_ticker, sym)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield {
                        'event': 'tick',
                        'data': json.dumps({
                            'symbol': sym, 'exchange': 'binance',
                            'last':       float(data.get('c', 0)),
                            'bid':        float(data.get('b', 0)),
                            'ask':        float(data.get('a', 0)),
                            'volume':     float(data.get('v', 0)),
                            'change_pct': float(data.get('P', 0)),
                            'high':       float(data.get('h', 0)),
                            'low':        float(data.get('l', 0)),
                            'ts':         data.get('E', 0),
                        }),
                    }
                except TimeoutError:
                    yield {'event': 'heartbeat', 'data': json.dumps({'symbol': sym})}
        except asyncio.CancelledError:
            pass
        finally:
            loop.run_in_executor(None, mgr.unsubscribe, channel, q)

    return EventSourceResponse(
        _gen(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.get('/bybit/stream/{pair:path}')
async def stream_bybit_ticker(pair: str, request: Request):
    """Real-time Bybit linear ticker via public WebSocket → SSE."""
    sym = _sym(pair)
    mgr = get_stream_manager()
    channel = f'bybit:{sym}:ticker'

    loop = asyncio.get_running_loop()

    async def _gen():
        q = await loop.run_in_executor(None, mgr.subscribe, channel)
        loop.run_in_executor(None, mgr.start_bybit_ticker, sym)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    raw = await asyncio.wait_for(q.get(), timeout=30.0)
                    td = raw.get('data', {})
                    if isinstance(td, list):
                        td = td[0] if td else {}
                    yield {
                        'event': 'tick',
                        'data': json.dumps({
                            'symbol': sym, 'exchange': 'bybit',
                            'last':          float(td.get('lastPrice', 0)),
                            'bid':           float(td.get('bid1Price', 0)),
                            'ask':           float(td.get('ask1Price', 0)),
                            'volume':        float(td.get('volume24h', 0)),
                            'open_interest': float(td.get('openInterest', 0) or 0),
                            'funding_rate':  float(td.get('fundingRate', 0) or 0),
                            'ts':            raw.get('ts', 0),
                        }),
                    }
                except TimeoutError:
                    yield {'event': 'heartbeat', 'data': json.dumps({'symbol': sym})}
        except asyncio.CancelledError:
            pass
        finally:
            loop.run_in_executor(None, mgr.unsubscribe, channel, q)

    return EventSourceResponse(
        _gen(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Bot management (Freqtrade / OctoBot style) ──────────────────────────────

@router.get('/bots')
def list_bots():
    """List all crypto bots with status."""
    from ..domain.strategies.bot_manager import get_bot_manager
    return {'success': True, 'data': get_bot_manager().list_bots()}


@router.get('/bots/pnl')
def bots_aggregate_pnl():
    """Aggregate P&L across all bots."""
    from ..domain.strategies.bot_manager import get_bot_manager
    return {'success': True, 'data': get_bot_manager().aggregate_pnl()}


@router.post('/bots')
async def create_bot(request: Request):
    """Create a new crypto bot.

    Body: { name, symbols: ['BTCUSDT',...], strategy?: 'trend_v4', config?: {...} }
    """
    from ..domain.strategies.bot_manager import get_bot_manager
    body = await request.json()
    name = body.get('name', '').strip()
    if not name:
        return JSONResponse({'success': False, 'error': 'Bot name is required'}, status_code=400)
    symbols = body.get('symbols', [])
    if not symbols or not isinstance(symbols, list):
        return JSONResponse({'success': False, 'error': 'symbols must be a non-empty list'}, status_code=400)
    symbols = [s.upper().strip() for s in symbols if s.strip()]
    strategy = body.get('strategy', 'trend_v4')
    config = body.get('config', {})

    bot_id = get_bot_manager().create_bot(
        name=name, symbols=symbols, strategy=strategy, config=config,
    )
    return {'success': True, 'data': {'bot_id': bot_id, 'name': name, 'symbols': symbols}}


@router.post('/bots/start-all')
def start_all_bots():
    """Start all enabled bots."""
    from ..domain.strategies.bot_manager import get_bot_manager
    count = get_bot_manager().start_all()
    return {'success': True, 'data': {'started': count}}


@router.post('/bots/stop-all')
def stop_all_bots():
    """Stop all running bots."""
    from ..domain.strategies.bot_manager import get_bot_manager
    count = get_bot_manager().stop_all()
    return {'success': True, 'data': {'stopped': count}}


@router.get('/bots/{bot_id}')
def get_bot_status(bot_id: str):
    """Get a specific bot's full status."""
    from ..domain.strategies.bot_manager import get_bot_manager
    bot = get_bot_manager().get_bot(bot_id)
    if not bot:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    return {'success': True, 'data': bot.get_status()}


@router.put('/bots/{bot_id}')
async def update_bot(bot_id: str, request: Request):
    """Update bot config. Stop/start required for runtime changes.

    Body: { name?, symbols?, config?: {...} }
    """
    from ..domain.strategies.bot_manager import get_bot_manager
    body = await request.json()
    ok = get_bot_manager().update_bot(
        bot_id=bot_id,
        name=body.get('name'),
        symbols=body.get('symbols'),
        config=body.get('config'),
    )
    if not ok:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    return {'success': True}


@router.delete('/bots/{bot_id}')
def delete_bot(bot_id: str):
    """Stop and delete a bot permanently."""
    from ..domain.strategies.bot_manager import get_bot_manager
    ok = get_bot_manager().delete_bot(bot_id)
    if not ok:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    return {'success': True}


@router.post('/bots/{bot_id}/start')
def start_bot(bot_id: str):
    """Start a specific bot."""
    from ..domain.strategies.bot_manager import get_bot_manager
    ok = get_bot_manager().start_bot(bot_id)
    if not ok:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    return {'success': True, 'data': {'running': True}}


@router.post('/bots/{bot_id}/stop')
def stop_bot(bot_id: str):
    """Stop a specific bot."""
    from ..domain.strategies.bot_manager import get_bot_manager
    ok = get_bot_manager().stop_bot(bot_id)
    if not ok:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    return {'success': True, 'data': {'running': False}}


@router.get('/bots/{bot_id}/signals')
def bot_signals(bot_id: str, limit: int = Query(30, le=100)):
    """Get signals for a specific bot."""
    from ..domain.strategies.bot_manager import get_bot_manager
    bot = get_bot_manager().get_bot(bot_id)
    if not bot:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    return {'success': True, 'data': bot.get_signals(limit)}


@router.get('/bots/{bot_id}/positions')
def bot_positions(bot_id: str):
    """Get open positions for a specific bot."""
    from ..domain.strategies.bot_manager import get_bot_manager
    bot = get_bot_manager().get_bot(bot_id)
    if not bot:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    return {'success': True, 'data': bot.get_positions()}


@router.post('/bots/{bot_id}/close/{symbol}')
def bot_close_position(bot_id: str, symbol: str):
    """Manually close a position in a specific bot."""
    from ..domain.strategies.bot_manager import get_bot_manager
    bot = get_bot_manager().get_bot(bot_id)
    if not bot:
        return JSONResponse({'success': False, 'error': 'Bot not found'}, status_code=404)
    result = bot.manual_close(symbol.upper())
    if result is None:
        return JSONResponse({'success': False, 'error': f'No position for {symbol}'}, status_code=404)
    return {'success': True, 'data': result}


# ══════════════════════════════════════════════════════════════════════════════
# CRYPTO F&O (Deribit Derivatives — Options + Perpetuals)
# ══════════════════════════════════════════════════════════════════════════════

@router.get('/fno/state')
def crypto_fo_state():
    """Crypto F&O scanner state."""
    from ..engines.crypto_fo_scanner import get_crypto_fo_scanner
    return {'success': True, 'data': get_crypto_fo_scanner().get_state()}


@router.get('/fno/pnl')
def crypto_fo_pnl():
    """Crypto F&O P&L summary."""
    from ..engines.crypto_fo_scanner import get_crypto_fo_scanner
    return {'success': True, 'data': get_crypto_fo_scanner().get_pnl()}


@router.get('/fno/positions')
def crypto_fo_positions():
    """Crypto F&O open positions (options + perps)."""
    from ..engines.crypto_fo_scanner import get_crypto_fo_scanner
    return {'success': True, 'data': get_crypto_fo_scanner().get_positions()}


@router.get('/fno/signals')
def crypto_fo_signals(limit: int = Query(50, le=200)):
    """Crypto F&O signal log."""
    from ..engines.crypto_fo_scanner import get_crypto_fo_scanner
    return {'success': True, 'data': get_crypto_fo_scanner().get_signals()[-limit:]}


@router.post('/fno/start')
def crypto_fo_start():
    """Start crypto F&O scanner."""
    from ..engines.crypto_fo_scanner import get_crypto_fo_scanner
    s = get_crypto_fo_scanner()
    s.start()
    return {'success': True, 'data': s.get_state()}


@router.post('/fno/stop')
def crypto_fo_stop():
    """Stop crypto F&O scanner."""
    from ..engines.crypto_fo_scanner import get_crypto_fo_scanner
    s = get_crypto_fo_scanner()
    s.stop()
    return {'success': True, 'data': s.get_state()}


@router.post('/fno/flatten')
def crypto_fo_flatten():
    """Emergency flatten all crypto F&O positions."""
    from ..engines.crypto_fo_scanner import get_crypto_fo_scanner
    s = get_crypto_fo_scanner()
    s._flatten_all('manual')
    return {'success': True, 'data': s.get_pnl()}
