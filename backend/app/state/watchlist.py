"""The watchlist, owned by the server so every device shows the same one."""

from __future__ import annotations

import json
from typing import Any

from . import db

MAX_ITEMS = 200


def _clean(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise: upper-case unique symbols, keep order, cap the length."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in items:
        symbol = str(raw.get('symbol', '')).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append({**raw, 'symbol': symbol})
        if len(out) >= MAX_ITEMS:
            break
    return out


def get_watchlist() -> list[dict[str, Any]]:
    rows = db.connect().execute('SELECT item FROM watchlist ORDER BY position').fetchall()
    return [json.loads(r['item']) for r in rows]


def set_watchlist(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the whole list atomically; returns what was stored."""
    clean = _clean(items)
    conn = db.connect()
    with conn:
        conn.execute('DELETE FROM watchlist')
        conn.executemany(
            'INSERT INTO watchlist (symbol, position, item) VALUES (?, ?, ?)',
            [(it['symbol'], i, json.dumps(it)) for i, it in enumerate(clean)],
        )
    return clean
