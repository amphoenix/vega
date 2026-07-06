"""
Structured logging — console + rotating file.

USAGE: every module should do ONE of:
    from app.shared.logger import get_logger
    logger = get_logger(__name__)

Never call logging.getLogger() directly — always go through get_logger()
so handlers are wired once and consistently.
"""

from __future__ import annotations

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

from .time import today_ist_str


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
_ROOT_NAME = 'vega'
_initialized = False


def setup_logger(name: str = _ROOT_NAME, level: int = logging.DEBUG) -> logging.Logger:
    """Bootstrap the root 'vega' logger with file + console handlers.

    Call ONCE at app startup (main.py). All child loggers ('vega.trade',
    'vega.supervisor', etc.) inherit handlers automatically.
    """
    global _initialized
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)
    root.propagate = False

    if root.handlers:
        _initialized = True
        return logging.getLogger(name)

    detailed = logging.Formatter(
        '[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    simple = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    # Rotating file handler — 10 MB, 5 backups
    log_file = os.path.join(LOG_DIR, today_ist_str() + '.log')
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(detailed)

    # Console handler — INFO+
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(simple)

    root.addHandler(fh)
    root.addHandler(ch)

    _silence_dead_connection_errors()

    _initialized = True
    return logging.getLogger(name)


def _silence_dead_connection_errors() -> None:
    """Drop hypercorn's benign 'Error in ASGI Framework' tracebacks that fire
    when a client disconnects (or the server shuts down) mid-write on an H/2 /
    SSE connection — the SSL transport is already gone, so hypercorn hits
    AttributeError: 'NoneType' has no attribute '_write_appdata' (and friends:
    ConnectionResetError, BrokenPipeError). These are per-dead-connection teardown
    noise, not app faults. Suppress just those; keep all other hypercorn errors.
    """
    _benign = ('_write_appdata', 'ConnectionResetError', 'BrokenPipeError',
               'StreamClosed', 'connectionreset', 'brokenpipe')

    class _DeadConnFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            exc = record.exc_info[1] if record.exc_info else None
            blob = f'{record.getMessage()} {exc!r}'
            if 'Error in ASGI Framework' in record.getMessage() or exc is not None:
                if any(m.lower() in blob.lower() for m in _benign):
                    return False
            return True

    logging.getLogger('hypercorn.error').addFilter(_DeadConnFilter())


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a child logger under the 'vega' namespace.

    Usage:
        logger = get_logger(__name__)           # → vega.app.domain.entities.trade
        logger = get_logger('supervisor')       # → vega.supervisor

    If the root logger hasn't been set up yet (tests, scripts), bootstraps it
    with console-only output so nothing crashes silently.
    """
    if not _initialized:
        setup_logger()

    if name is None:
        return logging.getLogger(_ROOT_NAME)

    # If caller passes __name__ like 'app.domain.entities.trade', prefix it
    if name.startswith('app.') or name.startswith('app'):
        return logging.getLogger(f'{_ROOT_NAME}.{name}')
    # If already prefixed
    if name.startswith(f'{_ROOT_NAME}.'):
        return logging.getLogger(name)
    # Short name like 'supervisor', 'trade', etc.
    return logging.getLogger(f'{_ROOT_NAME}.{name}')
