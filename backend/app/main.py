"""
Vega — FastAPI application with lifespan hooks.

Startup:  wire event bus, start scheduler, log config
Shutdown: stop scheduler, clean up
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .dependencies import get_event_bus, get_scheduler, get_kill_switch, get_supervisor
from .domain.events.events import PositionClosed, DayRolled
from .application.handlers.trading_handlers import (
    make_position_closed_handler, make_day_rolled_handler,
)
from .shared.logger import setup_logger, get_logger

# ── Bootstrap logger before anything else ────────────────────────────────────
setup_logger()
logger = get_logger('main')


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown hooks."""
    bus = get_event_bus()
    scheduler = get_scheduler()

    # ── Startup ──────────────────────────────────────────────────────────
    # Set thread pool on the ACTUAL running loop (not import-time throwaway).
    # All sync `def` endpoints run through this pool — 100 threads handles
    # market-hour burst (yfinance + broker calls are 1-4s each).
    from concurrent.futures import ThreadPoolExecutor
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=100, thread_name_prefix='ph-worker')
    )

    from .infrastructure.db.state_store import init_db
    init_db()
    logger.info('Vega starting on port %d', settings.v2_port)
    logger.info('Live trading: %s | Auto-trade: %s | Scalp auto: %s',
                settings.live_trading_enabled,
                settings.auto_trading_enabled,
                settings.scalp_auto_trade)

    # Validate LLM config
    llm_errors = settings.validate_llm()
    if llm_errors:
        for err in llm_errors:
            logger.warning('LLM config: %s', err)

    # Wire event handlers
    ks = get_kill_switch()
    sv = get_supervisor()
    bus.subscribe(PositionClosed, make_position_closed_handler(ks, sv))
    bus.subscribe(DayRolled, make_day_rolled_handler(ks, sv))

    # Schedule midnight reset via DayRolled event
    from .shared.time import today_ist_str

    def _midnight_reset() -> None:
        old = today_ist_str()
        bus.publish(DayRolled(old_date=old, new_date=today_ist_str()))

    scheduler.daily('00:00', _midnight_reset, name='midnight-reset')
    scheduler.start()

    # Wire public WS stream manager to the running event loop
    from .infrastructure.exchange.public_ws import get_stream_manager
    get_stream_manager().set_loop(asyncio.get_running_loop())

    # Start crypto EMA-crossover scanner
    from .domain.strategies.crypto_scanner import get_crypto_scanner
    get_crypto_scanner().start()

    # Auto-start F&O scanner (matches Flask __init__.py behaviour)
    try:
        from .engines.fo_scanner import start as _fo_start
        _fo_start()
        logger.info('F&O scanner auto-started')
    except Exception as _e:
        logger.warning('F&O scanner auto-start failed: %s', _e)

    # Auto-start scalp scanner only when enabled (matches Flask __init__.py)
    try:
        from .engines.scalp_scanner import scalp_enabled as _scalp_enabled, start as _scalp_start
        if _scalp_enabled():
            _scalp_start()
            logger.info('Scalp scanner auto-started')
        else:
            logger.info('Scalp scanner disabled (SCALP_ENABLED != true)')
    except Exception as _e:
        logger.warning('Scalp scanner auto-start failed: %s', _e)

    # Auto-start forex scanner (always on)
    try:
        from .engines.forex_scanner import start as _forex_start
        _forex_start()
        logger.info('Forex scanner auto-started')
    except Exception as _e:
        logger.warning('Forex scanner auto-start failed: %s', _e)

    # Auto-start crypto F&O (Deribit derivatives) scanner
    try:
        if settings.crypto_fo_enabled:
            from .engines.crypto_fo_scanner import get_crypto_fo_scanner
            get_crypto_fo_scanner().start()
            logger.info('Crypto F&O scanner auto-started (testnet=%s)', settings.deribit_testnet)
        else:
            logger.info('Crypto F&O scanner disabled in config')
    except Exception as _e:
        logger.warning('Crypto F&O scanner auto-start failed: %s', _e)

    # Auto-start BTST scanner (arms the 15:20 auto-scan; no manual Start needed)
    try:
        from .engines.btst_scanner import start as _btst_start
        _btst_start()
        logger.info('BTST scanner auto-started (auto-scan @ 15:20 IST)')
    except Exception as _e:
        logger.warning('BTST scanner auto-start failed: %s', _e)

    # Sync tracked positions watcher for positions pinned before this boot
    try:
        from .engines.monitor import tracked_monitor as _tm
        _tm.sync()
    except Exception as _e:
        logger.warning('tracked_monitor.sync() at boot failed: %s', _e)

    logger.info('Event bus ready (%d handlers)', bus.handler_count)
    logger.info('Scheduler started')

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    logger.info('Vega shutting down')
    try:
        from .engines.fo_scanner import stop as _fo_stop
        _fo_stop()
    except Exception:
        pass
    try:
        from .engines.scalp_scanner import stop as _scalp_stop
        _scalp_stop()
    except Exception:
        pass
    try:
        from .engines.forex_scanner import stop as _forex_stop
        _forex_stop()
    except Exception:
        pass
    try:
        from .engines.crypto_fo_scanner import get_crypto_fo_scanner
        get_crypto_fo_scanner().stop()
    except Exception:
        pass
    scheduler.stop()
    bus.clear()


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title='Vega',
    version='0.1.0',
    lifespan=lifespan,
)

# CORS origins injected by start.sh via CORS_ORIGINS env — no hardcoded ports
_cors_origins = [
    o.strip() for o in os.environ.get('CORS_ORIGINS', '').split(',') if o.strip()
] or ['*']

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


# ── Pretty request logger ────────────────────────────────────────────────────

import time as _time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Req
from starlette.responses import Response as _Resp

# ANSI colors
_G = '\033[32m'; _Y = '\033[33m'; _R = '\033[31m'; _C = '\033[36m'
_DIM = '\033[2m'; _B = '\033[1m'; _RST = '\033[0m'

_METHOD_COLOR = {
    'GET': _G, 'POST': _Y, 'PUT': _Y, 'PATCH': _Y, 'DELETE': _R,
}

# Paths to skip (noisy health polls etc.)
_SKIP = {'/health', '/favicon.ico'}


class _RequestLogger(BaseHTTPMiddleware):
    async def dispatch(self, request: _Req, call_next) -> _Resp:
        path = request.url.path
        if path in _SKIP:
            return await call_next(request)

        method = request.method
        t0 = _time.monotonic()
        response = await call_next(request)
        ms = (_time.monotonic() - t0) * 1000
        status = response.status_code

        # Color by status
        if status < 300:
            sc = _G
        elif status < 400:
            sc = _C
        elif status < 500:
            sc = _Y
        else:
            sc = _R

        mc = _METHOD_COLOR.get(method, _C)
        proto = f"H/{request.scope.get('http_version', '?')}"

        # SSE streams show as "SSE" instead of time
        ct = response.headers.get('content-type', '')
        timing = f'{_DIM}SSE{_RST}' if 'text/event-stream' in ct else f'{_DIM}{ms:6.0f}ms{_RST}'

        # Clean path: strip /api/ prefix for readability
        short = path[4:] if path.startswith('/api') else path

        ts = _time.strftime('%H:%M:%S')
        print(f'  {_DIM}{ts}{_RST}  {sc}{status}{_RST}  {mc}{_B}{method:6s}{_RST}  {short:50s}  {timing}  {_DIM}[{proto}]{_RST}')
        return response


app.add_middleware(_RequestLogger)


# ── Register API routers ────────────────────────────────────────────────────

from .api.broker import router as broker_router
from .api.trade import router as trade_router
from .api.market import router as market_router
from .api.crypto import router as crypto_router
from .api.poly import router as poly_router
from .api.forex import router as forex_router

app.include_router(broker_router)
app.include_router(trade_router)
app.include_router(market_router)
app.include_router(crypto_router)
app.include_router(poly_router)
app.include_router(forex_router)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {
        'status': 'ok',
        'version': 'v2',
        'live_trading': settings.live_trading_enabled,
        'auto_trading': settings.auto_trading_enabled,
    }


# ── Paper trading status ──────────────────────────────────────────────────────

@app.get('/paper/status')
def paper_status():
    """Live view of all 3 paper accounts — mode, capital, auto-trade."""
    from .dependencies import get_broker, get_crypto_exchange, get_polymarket_exchange
    broker = get_broker()
    crypto = get_crypto_exchange()
    poly   = get_polymarket_exchange()
    return {
        'fo': {
            'mode': settings.fo_mode,
            'auto_trade': settings.fo_auto_trade,
            'capital': settings.swing_capital_inr,
            'paper': getattr(broker, '_paper_mode', False),
        },
        'crypto': {
            'mode': settings.crypto_mode,
            'exchange': settings.crypto_exchange,
            'auto_trade': settings.crypto_auto_trade,
            'capital': settings.crypto_capital_usd,
            'paper': getattr(crypto, '_paper_mode', False),
        },
        'polymarket': {
            'mode': settings.polymarket_mode,
            'auto_trade': settings.polymarket_auto_trade,
            'capital': settings.polymarket_capital_usd,
            'paper': getattr(poly, '_paper_mode', False),
        },
    }
