"""
Read-only access to the server's logs, for another machine.

    GET /api/logs?file=app|audit&date=YYYY-MM-DD&lines=200&level=ERROR&q=text&offset=N
    GET /api/logs/files

* Protected twice: by the access middleware (key required) and again here (a caller identity must be present).
* Credentials are redacted from every line before it leaves the server.
* `offset` makes following cheap: pass the previous `next_offset` and get only what was appended since.
* Every read is recorded in the audit trail with who asked and what for.
"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException, Query, Request

from ..auth import audit
from ..auth.api_key import current_secrets
from ..config import settings
from ..shared.logger import LOG_DIR
from ..shared.redact import redact
from ..shared.time import today_ist_str

router = APIRouter(prefix='/api/logs', tags=['logs'])

MAX_LINES = 2000
MAX_TAIL_BYTES = 2 * 1024 * 1024
MAX_FOLLOW_BYTES = 1024 * 1024
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
_LINE_LEVEL = re.compile(r'^\[[^\]]+\]\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b')


def _caller(request: Request) -> str:
    """Defence in depth: refuse unless the access middleware identified the caller."""
    identity = request.scope.get('vega_identity')
    if not identity and not settings.vega_auth_disabled:
        raise HTTPException(status_code=403, detail='forbidden')
    return identity or 'anonymous'


def _path(file: str, date: str) -> str:
    if file not in ('app', 'audit') or not _DATE.match(date):
        raise HTTPException(status_code=400, detail='file must be app|audit and date YYYY-MM-DD')
    path = audit.path_for(date) if file == 'audit' else os.path.join(LOG_DIR, f'{date}.log')
    root = os.path.realpath(os.path.dirname(path))
    if os.path.dirname(os.path.realpath(path)) != root:           # never leave the log directory
        raise HTTPException(status_code=400, detail='bad path')
    return path


def _filter(lines: list[str], level: str | None, q: str | None) -> list[str]:
    """Keep lines at or above `level` (continuation lines such as stack traces follow their parent) containing q."""
    if level:
        floor = _LEVELS.index(level)
        kept: list[str] = []
        current = len(_LEVELS)                     # lines before any header are kept
        for line in lines:
            m = _LINE_LEVEL.match(line)
            if m:
                current = _LEVELS.index(m.group(1))
            if current >= floor:
                kept.append(line)
        lines = kept
    if q:
        needle = q.lower()
        lines = [ln for ln in lines if needle in ln.lower()]
    return lines


def _read_tail(path: str) -> tuple[list[str], int]:
    size = os.path.getsize(path)
    with open(path, 'rb') as fh:
        start = max(0, size - MAX_TAIL_BYTES)
        fh.seek(start)
        data = fh.read()
    text = data.decode('utf-8', errors='replace')
    lines = text.split('\n')
    if start > 0:
        lines = lines[1:]                          # first line is cut off mid-way
    return [ln for ln in lines if ln != ''], size


def _read_from(path: str, offset: int) -> tuple[list[str], int, bool]:
    size = os.path.getsize(path)
    if offset > size:                              # file rotated or replaced: start over
        lines, size = _read_tail(path)
        return lines, size, True
    with open(path, 'rb') as fh:
        fh.seek(offset)
        data = fh.read(MAX_FOLLOW_BYTES)
    end = data.rfind(b'\n')
    if end < 0:
        return [], offset, False                   # nothing complete yet
    complete = data[: end + 1]
    lines = [ln for ln in complete.decode('utf-8', errors='replace').split('\n') if ln != '']
    return lines, offset + len(complete), False


@router.get('')
def read_logs(
    request: Request,
    file: str = Query('app', description='app | audit'),
    date: str | None = Query(None, description='YYYY-MM-DD (IST); default today'),
    lines: int = Query(200, ge=1, le=MAX_LINES),
    level: str | None = Query(None, description='minimum level: DEBUG|INFO|WARNING|ERROR|CRITICAL'),
    q: str | None = Query(None, max_length=100, description='only lines containing this text'),
    offset: int | None = Query(None, ge=0, description='byte offset from a previous call (follow mode)'),
):
    who = _caller(request)
    level = level.upper() if level else None
    if level and level not in _LEVELS:
        raise HTTPException(status_code=400, detail=f'level must be one of {", ".join(_LEVELS)}')
    date = date or today_ist_str()
    path = _path(file, date)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='no such log')

    reset = False
    if offset is None:
        raw, next_offset = _read_tail(path)
    else:
        raw, next_offset, reset = _read_from(path, offset)
    matched = _filter(raw, level if file == 'app' else None, q)
    truncated = len(matched) > lines
    out = matched[-lines:] if offset is None else matched[:lines]
    if truncated and offset is not None:
        # more matched than we return: resume right after the last line we are sending
        sent = sum(len(ln.encode('utf-8')) + 1 for ln in out)
        next_offset = (offset if not reset else 0) + sent if not reset else next_offset
    secrets_ = current_secrets()
    out = [redact(ln, secrets_) for ln in out]
    audit.write('logs_read', rid=request.scope.get('vega_request_id'), identity=who, file=file, date=date,
                lines=len(out), level=level, follow=offset is not None, q_len=len(q) if q else 0)
    return {'success': True, 'data': {
        'file': file, 'date': date, 'lines': out, 'next_offset': next_offset,
        'reset': reset, 'truncated': truncated, 'size': os.path.getsize(path),
    }}


@router.get('/files')
def list_log_files(request: Request):
    _caller(request)
    files = []
    try:
        for name in sorted(os.listdir(LOG_DIR), reverse=True):
            m = re.match(r'^(audit-)?(\d{4}-\d{2}-\d{2})\.log$', name)
            if m:
                files.append({'file': 'audit' if m.group(1) else 'app', 'date': m.group(2),
                              'size': os.path.getsize(os.path.join(LOG_DIR, name))})
    except OSError:
        pass
    return {'success': True, 'data': files[:200]}
