"""Per-request identity for tracing: a request id and who is calling, visible to every log line."""

from __future__ import annotations

import contextvars
import logging
import re
import uuid

request_id: contextvars.ContextVar[str] = contextvars.ContextVar('vega_request_id', default='')
caller: contextvars.ContextVar[str] = contextvars.ContextVar('vega_caller', default='')

_SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')


def new_request_id(inbound: str | None = None) -> str:
    """Accept a sane caller-supplied id (for tracing across systems), otherwise make one."""
    if inbound and _SAFE_ID.match(inbound):
        return inbound
    return uuid.uuid4().hex[:16]


class TraceFilter(logging.Filter):
    """Adds ` rid=… by=…` to log lines written while serving a request. Other lines are unchanged."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id.get()
        if rid:
            who = caller.get()
            record.trace = f' rid={rid}' + (f' by={who}' if who else '')
        else:
            record.trace = ''
        return True
