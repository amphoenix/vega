"""Connection to data/server_state.db (per-thread, WAL) and its schema."""

from __future__ import annotations

import os
import sqlite3
import threading

_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'server_state.db'),
)
_path: str = os.environ.get('VEGA_STATE_DB') or _DEFAULT_PATH
_local = threading.local()

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    symbol   TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    item     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signal_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    segment    TEXT NOT NULL,
    ticker     TEXT NOT NULL DEFAULT '',
    verdict    TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_log_segment ON signal_log (segment, id);
'''


def connect() -> sqlite3.Connection:
    """Return this thread's connection, creating the file and schema on first use."""
    conn = getattr(_local, 'conn', None)
    if conn is None or getattr(_local, 'path', None) != _path:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        conn = sqlite3.connect(_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(_SCHEMA)
        _local.conn, _local.path = conn, _path
    return conn


def configure(path: str) -> None:
    """Point at a different database file (tests). Drops this thread's connection."""
    global _path
    close()
    _path = path


def close() -> None:
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        conn.close()
    _local.conn = None
