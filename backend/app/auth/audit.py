"""
Audit trail: one JSON line per security-relevant event, in logs/audit-YYYY-MM-DD.log.

Records who (identity name, client address), what (method + path — never the query string, never headers,
never a key) and the outcome. Written separately from the application log so it can be kept and reviewed
on its own. Files older than the retention period are deleted at startup.
"""

from __future__ import annotations

import json
import os
import threading

from ..shared.logger import LOG_DIR
from ..shared.time import clock, now_ist, today_ist_str

_lock = threading.Lock()
_dir: str = LOG_DIR


def configure(directory: str) -> None:
    global _dir
    _dir = directory


def path_for(date: str) -> str:
    return os.path.join(_dir, f'audit-{date}.log')


def write(event: str, **fields: object) -> None:
    """Append one audit event. Never raises: auditing must not take the API down."""
    record = {'ts': now_ist().isoformat(timespec='milliseconds'), 'event': event, **fields}
    try:
        line = json.dumps(record, default=str, separators=(',', ':'))
        with _lock:
            os.makedirs(_dir, exist_ok=True)
            with open(path_for(today_ist_str()), 'a', encoding='utf-8') as fh:
                fh.write(line + '\n')
    except Exception:
        pass


def purge_old(retention_days: int) -> int:
    """Delete audit files older than `retention_days`. Returns how many were removed."""
    if retention_days <= 0:
        return 0
    cutoff = clock() - retention_days * 86400
    removed = 0
    try:
        for name in os.listdir(_dir):
            if name.startswith('audit-') and name.endswith('.log'):
                full = os.path.join(_dir, name)
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
                    removed += 1
    except OSError:
        pass
    return removed
