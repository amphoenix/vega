"""Append-only log of scanner signals, so any device can load the history it missed."""

from __future__ import annotations

import json
from typing import Any

from ..shared.logger import get_logger
from ..shared.time import now_ist
from . import db

logger = get_logger('state.signals')

KEEP_LAST = 5000
_PRUNE_EVERY = 500


def _verdict_of(signal: dict[str, Any]) -> tuple[str, str, float]:
    verdict = str(signal.get('verdict') or signal.get('direction') or signal.get('signal') or '')
    action = str(signal.get('action') or signal.get('short_term_action') or '')
    try:
        confidence = float(signal.get('confidence') or signal.get('score') or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return verdict, action, confidence


def record(segment: str, signal: dict[str, Any]) -> None:
    """Store one signal. Never raises: logging must not break a scan."""
    try:
        verdict, action, confidence = _verdict_of(signal)
        conn = db.connect()
        with conn:
            cur = conn.execute(
                'INSERT INTO signal_log (ts, segment, ticker, verdict, action, confidence, payload) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (now_ist().isoformat(), segment, str(signal.get('ticker') or signal.get('symbol') or ''),
                 verdict, action, confidence, json.dumps(signal, default=str)),
            )
            if cur.lastrowid % _PRUNE_EVERY == 0:
                conn.execute('DELETE FROM signal_log WHERE id <= ?', (cur.lastrowid - KEEP_LAST,))
    except Exception as e:
        logger.warning('signal log write failed (%s): %s', segment, e)


def history(segment: str | None = None, since_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Newest first. `since_id` returns only rows newer than that id (for catch-up)."""
    limit = max(1, min(int(limit), 500))
    sql = 'SELECT id, ts, segment, payload FROM signal_log WHERE id > ?'
    args: list[Any] = [int(since_id)]
    if segment:
        sql += ' AND segment = ?'
        args.append(segment)
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    rows = db.connect().execute(sql, args).fetchall()
    return [{**json.loads(r['payload']), 'id': r['id'], 'ts': r['ts'], 'segment': r['segment']} for r in rows]
