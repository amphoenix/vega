"""
Manual position tracker — pinned positions the user has entered at their broker.

When the user clicks "I entered" on a LIVE ticket card, the full ticket payload
is pinned here. The frontend continues to live-reprice the contract and fires
exit alerts (SL hit, T1 hit, theta zone, 15:00 force-exit) regardless of any
later AI verdict revisions.

Storage is a flat JSON file — no DB, no schema migration, easy to inspect/edit.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from typing import List, Optional

from ...shared.time import datetime, now_ist

# ── Storage ───────────────────────────────────────────────────────────────────
_DATA_DIR  = os.path.join(os.path.dirname(__file__), '..', '..', 'data')
_FILE_PATH = os.path.abspath(os.path.join(_DATA_DIR, 'tracked_positions.json'))
_lock      = threading.Lock()

# In-memory read cache: invalidated on every _write(), avoids ~20 disk reads/min.
_cache: Optional[List[dict]] = None


def _read() -> List[dict]:
    global _cache
    if _cache is not None:
        return list(_cache)
    if not os.path.exists(_FILE_PATH):
        _cache = []
        return []
    try:
        with open(_FILE_PATH, 'r') as f:
            data = json.load(f)
        _cache = data if isinstance(data, list) else []
        return list(_cache)
    except Exception:
        return []


def _write(items: List[dict]) -> None:
    global _cache
    os.makedirs(_DATA_DIR, exist_ok=True)
    # Atomic write: write to temp file, fsync, then rename over the target.
    # Prevents a corrupt/empty JSON file on crash mid-write.
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR, suffix='.tmp')
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            json.dump(items, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _FILE_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _cache = items


# ── Public API ────────────────────────────────────────────────────────────────
def list_tracked() -> List[dict]:
    """Return all tracked positions, newest first."""
    with _lock:
        items = _read()
    return sorted(items, key=lambda x: x.get('entered_at', ''), reverse=True)


def add_tracked(ticket: dict, qty: int = 1, notes: str = '') -> dict:
    """Pin a ticket as an entered position. Returns the saved record.
    Rejects duplicates: if a position with the same trading_symbol already
    exists, returns the existing record instead of creating another."""
    if not ticket or not ticket.get('trading_symbol'):
        raise ValueError("ticket with trading_symbol required")
    sym = str(ticket.get('trading_symbol') or '').strip().upper()
    record = {
        'id':          uuid.uuid4().hex[:12],
        'entered_at':  now_ist().isoformat(),
        'qty':         int(qty or 1),
        'notes':       (notes or '').strip(),
        'ticket':      ticket,
    }
    with _lock:
        items = _read()
        # Dedup by trading_symbol — guards against rapid double-clicks racing
        # the frontend's isTracked() check.
        for existing in items:
            t = (existing.get('ticket') or {}).get('trading_symbol') or ''
            if t.strip().upper() == sym:
                return existing
        items.append(record)
        _write(items)
    # Notify the backend watcher so it begins repricing this position on every
    # spot tick / poll cycle. Lazy import to avoid an import cycle at startup.
    try:
        from ...engines.monitor import tracked_monitor as _tm
        _tm.sync()
    except Exception:
        pass
    return record


def remove_tracked(track_id: str, exit_premium: Optional[float] = None,
                    exit_reason: str = '') -> Optional[dict]:
    """
    Remove a tracked position. Returns the removed record (with exit metadata
    appended) so the caller can show a final P&L summary.
    """
    with _lock:
        items = _read()
        idx = next((i for i, r in enumerate(items) if r.get('id') == track_id), -1)
        if idx < 0:
            return None
        rec = items.pop(idx)
        _write(items)

    rec['exited_at']    = now_ist().isoformat()
    rec['exit_premium'] = exit_premium
    rec['exit_reason']  = exit_reason or 'manual'
    # Re-sync the watcher so it stops monitoring this id (clears stale state).
    try:
        from ...engines.monitor import tracked_monitor as _tm
        _tm.sync()
    except Exception:
        pass
    return rec


def upsert(record: dict) -> Optional[dict]:
    """Update an existing tracked position in-place (e.g. trailing SL changes).
    Matches by record 'id'. Returns the updated record or None if not found."""
    track_id = record.get('id')
    if not track_id:
        return None
    with _lock:
        items = _read()
        for i, r in enumerate(items):
            if r.get('id') == track_id:
                items[i] = record
                _write(items)
                return record
    return None


def update_notes(track_id: str, notes: str) -> Optional[dict]:
    with _lock:
        items = _read()
        for r in items:
            if r.get('id') == track_id:
                r['notes'] = (notes or '').strip()
                _write(items)
                return r
    return None
