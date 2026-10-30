"""Persisted runtime settings. Reads are served from memory; writes go through to SQLite."""

from __future__ import annotations

import json
import threading
from typing import Any

from ..shared.time import now_ist
from . import db

_cache: dict[str, Any] = {}
_lock = threading.Lock()
_MISSING = object()


def get(key: str, default: Any = None) -> Any:
    with _lock:
        if key in _cache:
            cached = _cache[key]
            return default if cached is _MISSING else cached
    row = db.connect().execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    value = json.loads(row['value']) if row else _MISSING
    with _lock:
        _cache[key] = value
    return default if value is _MISSING else value


def set(key: str, value: Any) -> None:  # noqa: A001 - mirrors state_store.set_state
    conn = db.connect()
    with conn:
        conn.execute(
            'INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
            (key, json.dumps(value), now_ist().isoformat()),
        )
    with _lock:
        _cache[key] = value


def get_bool(key: str, default: bool) -> bool:
    return bool(get(key, default))


def clear_cache() -> None:
    """Forget in-memory values (tests, or after the database file changes)."""
    with _lock:
        _cache.clear()
