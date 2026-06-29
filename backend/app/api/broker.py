"""
Broker API — broker-agnostic FastAPI router.

All endpoints talk to whichever BrokerAdapter is active (config/brokers.yaml).
Switch from INDmoney to Dhan (or any future broker) = ONE yaml change.
Zero code changes here.

Prefix: /api/broker/*
"""

from __future__ import annotations

import asyncio
import json
import queue as _queue
import threading as _threading
from concurrent.futures import ThreadPoolExecutor

from ..shared.time import clock, datetime, now_ist

# Dedicated pool for blocking broker REST calls (get_ltp, etc.)
# Keeps them off the default executor so SSE streams don't starve API requests.
_broker_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix='broker-ltp')

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from ..dependencies import get_broker
from ..shared.logger import get_logger

logger = get_logger('api.broker')
router = APIRouter(prefix='/api/broker', tags=['broker'])



# ── GET /status ──────────────────────────────────────────────────────────────

def _status_impl():
    b = get_broker()
    data = {'broker': b.broker_name, 'token_configured': b.is_configured}
    # Probe connectivity: try get_available_cash (works for all brokers)
    try:
        cash = b.get_available_cash()
        data['connected'] = cash is not None
        data['available_cash'] = cash
    except Exception:
        data['connected'] = False
    return {'success': True, 'data': data}


@router.get('/status')
def status():
    return _status_impl()


# ── GET /profile ─────────────────────────────────────────────────────────────

def _profile_impl():
    b = get_broker()
    cash = b.get_available_cash()
    positions = b.get_positions()
    holdings = b.get_holdings()
    return {
        'success': True,
        'data': {
            'broker': b.broker_name,
            'available_cash': cash,
            'open_positions': len(positions),
            'holdings_count': len(holdings),
        },
    }


@router.get('/profile')
def profile():
    return _profile_impl()


# ── GET /tick/{ticker} ───────────────────────────────────────────────────────

def _tick_impl(ticker: str):
    b = get_broker()
    ltp = b.get_ltp(ticker)
    if ltp is None:
        return {'success': False, 'error': f'No price for {ticker}'}
    return {'success': True, 'data': {'ticker': ticker, 'ltp': ltp}}


@router.get('/tick/{ticker:path}')
def tick(ticker: str):
    return _tick_impl(ticker)


# ── GET /quote/{ticker} ─────────────────────────────────────────────────────

def _quote_impl(ticker: str):
    b = get_broker()
    q = b.get_quote(ticker)
    if not q:
        return {'success': False, 'error': f'No quote for {ticker}'}
    return {
        'success': True,
        'data': {
            'symbol': q.symbol,
            'ltp': q.ltp,
            'open': q.open,
            'high': q.high,
            'low': q.low,
            'close': q.close,
            'volume': q.volume,
            'bid': getattr(q, 'bid', 0),
            'ask': getattr(q, 'ask', 0),
        },
    }


@router.get('/quote/{ticker:path}')
def quote(ticker: str):
    return _quote_impl(ticker)


# ── GET /positions ───────────────────────────────────────────────────────────

def _positions_impl():
    b = get_broker()
    pos = b.get_positions()
    return {
        'success': True,
        'data': [
            {
                'symbol': p.symbol,
                'qty': p.qty,
                'avg_price': p.avg_price,
                'ltp': p.ltp,
                'pnl': p.pnl,
                'realized_pnl': p.realized_pnl,
                'unrealized_pnl': p.unrealized_pnl,
                'security_id': p.security_id,
                'exchange': p.exchange,
                'product_type': p.product_type,
            }
            for p in pos
        ],
    }


@router.get('/positions')
def positions():
    return _positions_impl()


# ── GET /holdings ────────────────────────────────────────────────────────────

def _holdings_impl():
    b = get_broker()
    hl = b.get_holdings()
    return {
        'success': True,
        'data': [
            {
                'symbol': h.symbol,
                'qty': h.qty,
                'avg_price': h.avg_price,
                'ltp': h.ltp,
                'pnl': h.pnl,
                'security_id': h.security_id,
            }
            for h in hl
        ],
    }


@router.get('/holdings')
def holdings():
    return _holdings_impl()


# ── GET /order-book ──────────────────────────────────────────────────────────

def _normalise_order(o: dict) -> dict:
    """Broker-agnostic order normalization — standard field names for frontend."""
    return {
        'order_id':       str(o.get('orderId', '') or o.get('order_id', '')),
        'symbol':         o.get('tradingSymbol', '') or o.get('trading_symbol', '') or o.get('symbol', ''),
        'side':           (o.get('transactionType', '') or o.get('transaction_type', '') or o.get('side', '')).upper(),
        'status':         (o.get('orderStatus', '') or o.get('order_status', '') or o.get('status', '')).upper(),
        'qty':            int(o.get('filledQty', 0) or o.get('filled_qty', 0) or o.get('quantity', 0) or o.get('qty', 0) or 0),
        'avg_price':      float(o.get('averageTradedPrice', 0) or o.get('average_traded_price', 0) or o.get('avg_price', 0) or o.get('price', 0) or 0),
        'product':        (o.get('productType', '') or o.get('product_type', '') or o.get('product', '')).upper(),
        'exchange':       (o.get('exchangeSegment', '') or o.get('exchange_segment', '') or o.get('exchange', '')).upper(),
        'order_type':     (o.get('orderType', '') or o.get('order_type', '')).upper(),
        'time':           o.get('exchangeTime', '') or o.get('exchange_timestamp', '') or o.get('time', ''),
    }


def _order_book_impl():
    b = get_broker()
    orders = b.get_order_list()
    return {'success': True, 'data': [_normalise_order(o) for o in orders]}


@router.get('/order-book')
def order_book():
    return _order_book_impl()


# ── POST /order ──────────────────────────────────────────────────────────────

async def _place_order_impl(request: Request):
    body = await request.json()
    b = get_broker()
    result = b.place_order(
        symbol=body.get('symbol', ''),
        side=body.get('side', body.get('txn_type', 'BUY')),
        qty=int(body.get('qty', body.get('quantity', 1))),
        order_type=body.get('order_type', 'MARKET'),
        price=float(body.get('price', body.get('limit_price', 0))),
        exchange=body.get('exchange', 'NFO'),
        security_id=body.get('security_id', ''),
        trigger_price=float(body.get('trigger_price', 0)),
        product_type=body.get('product', body.get('product_type', '')),
        tag=body.get('tag', ''),
    )
    return {
        'success': result.success,
        'data': {
            'order_id': result.order_id,
            'status': result.status,
        } if result.success else None,
        'error': result.message if not result.success else None,
    }


@router.post('/order')
async def place_order(request: Request):
    return await _place_order_impl(request)


# ── POST /order/cancel ──────────────────────────────────────────────────────

async def _cancel_order_impl(request: Request):
    body = await request.json()
    b = get_broker()
    ok = b.cancel_order(body.get('order_id', ''))
    return {'success': ok}


@router.post('/order/cancel')
async def cancel_order(request: Request):
    return await _cancel_order_impl(request)


# ── POST /order/modify ────────────────────────────────────────────────────

async def _modify_order_impl(request: Request):
    body = await request.json()
    b = get_broker()
    result = b.modify_order(
        order_id=body.get('order_id', ''),
        qty=int(body.get('qty', 0)),
        price=float(body.get('price', 0)),
        order_type=body.get('order_type', ''),
        trigger_price=float(body.get('trigger_price', 0)),
    )
    return {
        'success': result.success,
        'data': {'order_id': result.order_id, 'status': result.status} if result.success else None,
        'error': result.message if not result.success else None,
    }


@router.post('/order/modify')
async def modify_order(request: Request):
    return await _modify_order_impl(request)


# ── GET /order/{order_id} ────────────────────────────────────────────────────

@router.get('/order/{order_id}')
def order_status(order_id: str):
    b = get_broker()
    data = b.get_order_status(order_id)
    return {'success': bool(data), 'data': data}


# ── GET /trade-book ──────────────────────────────────────────────────────────

@router.get('/trade-book')
def trade_book():
    b = get_broker()
    try:
        resp = b._client.get_trade_book() if hasattr(b, '_client') and b._client else {}
        trades = resp.get('data', [])
        return {'success': True, 'data': trades if isinstance(trades, list) else []}
    except Exception as e:
        logger.error('trade_book failed: %s', e)
        return {'success': False, 'data': [], 'error': str(e)}


# ── POST /margin-calculator ──────────────────────────────────────────────────

@router.post('/margin-calculator')
async def margin_calculator(request: Request):
    body = await request.json()
    b = get_broker()
    try:
        resp = b._client.margin_calculator(
            security_id=body.get('security_id', ''),
            exchange_segment=body.get('exchange_segment', 'NSE_FNO'),
            transaction_type=body.get('transaction_type', 'BUY'),
            quantity=int(body.get('quantity', 1)),
            product_type=body.get('product_type', 'INTRADAY'),
            price=float(body.get('price', 0)),
            trigger_price=float(body.get('trigger_price', 0)),
        ) if hasattr(b, '_client') and b._client else {}
        return {'success': True, 'data': resp.get('data', {})}
    except Exception as e:
        logger.error('margin_calculator failed: %s', e)
        return {'success': False, 'error': str(e)}


# ── GET /stream/{ticker} (SSE) ──────────────────────────────────────────────
#
# WebSocket-backed: creates a per-client queue in the broker adapter
# that receives ticks directly from the WS price feed in real-time.
# Falls back to 1s REST polling only when WS isn't available.
# Same pattern as old backend/app/api/indmoney.py → stream()

_SSE_MAX_MINUTES = 30  # hard cap on SSE connection duration


async def _stream_ticker_impl(ticker: str, request: Request):
    b = get_broker()
    loop = asyncio.get_running_loop()
    # create_tick_queue may do sync WS setup — run off event loop
    code, q = await loop.run_in_executor(_broker_pool, b.create_tick_queue, ticker)

    async def event_generator():
        try:
            # Emit initial LTP immediately so chart doesn't wait
            ltp = await loop.run_in_executor(_broker_pool, b.get_ltp, ticker)
            if ltp is not None and ltp > 0:
                yield {'data': json.dumps({
                    'price': ltp, 'ticker': ticker,
                    'timestamp': now_ist().isoformat(),
                })}

            deadline = clock() + _SSE_MAX_MINUTES * 60
            last_emit = 0.0

            while clock() < deadline:
                if await request.is_disconnected():
                    break

                # Drain WS queue
                try:
                    payload = q.get_nowait()
                    yield {'data': payload}
                    last_emit = clock()
                except _queue.Empty:
                    if clock() - last_emit > 5.0:
                        yield {'data': json.dumps({'heartbeat': True})}
                        last_emit = clock()
                    await asyncio.sleep(0.1)

            yield {'data': json.dumps({
                'type': 'timeout',
                'message': 'stream expired, please reconnect',
            })}
        except asyncio.CancelledError:
            pass
        finally:
            # remove_tick_queue acquires threading locks — MUST run off
            # the event loop or a WS-thread lock hold blocks the server.
            loop.run_in_executor(None, b.remove_tick_queue, code, q)

    return EventSourceResponse(
        event_generator(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.get('/stream/{ticker:path}')
async def stream_ticker(ticker: str, request: Request):
    return await _stream_ticker_impl(ticker, request)


# ── GET /order-events/stream (SSE) ──────────────────────────────────────────

_oe_clients: list[_queue.Queue] = []
_oe_lock = _threading.Lock()
_oe_subscribed = False


def _oe_fan_out(event) -> None:
    """Push an order_update event to every connected SSE client."""
    data = getattr(event, 'data', event) if not isinstance(event, dict) else event
    with _oe_lock:
        dead = []
        for q in _oe_clients:
            try:
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for q in dead:
            _oe_clients.remove(q)


def _ensure_oe_subscription():
    """Subscribe to the event bus once (idempotent)."""
    global _oe_subscribed
    if _oe_subscribed:
        return
    try:
        from ..application.event_bus import event_bus
        from ..domain.events.events import DomainEvent
        event_bus.subscribe(DomainEvent, _oe_fan_out)
        _oe_subscribed = True
        logger.info('[broker] order-events SSE subscribed to event_bus')
    except Exception as e:
        logger.warning(f'[broker] order-events subscribe failed: {e}')


@router.get('/order-events/stream')
async def order_events_stream(request: Request):
    _ensure_oe_subscription()
    q: _queue.Queue = _queue.Queue(maxsize=200)
    with _oe_lock:
        _oe_clients.append(q)

    async def event_generator():
        try:
            # Send a connected marker so frontend knows it's live
            yield {'event': 'message', 'data': json.dumps({'type': 'order_events_connected', 'ts': clock()})}
            while True:
                if await request.is_disconnected():
                    break
                sent = False
                while True:
                    try:
                        payload = q.get_nowait()
                        yield {'event': 'message', 'data': json.dumps(payload, default=str)}
                        sent = True
                    except _queue.Empty:
                        break
                if not sent:
                    yield {'event': 'heartbeat', 'data': json.dumps({'ts': clock()})}
                await asyncio.sleep(1)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            with _oe_lock:
                try:
                    _oe_clients.remove(q)
                except ValueError:
                    pass

    return EventSourceResponse(
        event_generator(),
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


