"""
Access control for the whole API, in one ASGI layer (pure ASGI, so streaming responses are never buffered).

For every request, in this order:
  1. a request id is assigned (or a sane caller-supplied X-Request-ID is kept) and returned in the response;
  2. `/health` (bare "ok") and CORS preflight pass untouched;
  3. a client address that has sent too many wrong keys is locked out — nothing from it is evaluated;
  4. the key must match one of the configured keys (header `X-Vega-Key`, or `?key=` for browser streams);
     the match identifies WHO is calling (the entry's name) — wrong keys count toward the lockout;
  5. each person is limited to N requests per minute;
  6. security headers are added to every response and the outcome is written to the audit trail.

Keys: see keys.py. Weak keys (< 32 characters) are never accepted. With no strong key configured the API
is CLOSED (503) unless VEGA_AUTH_DISABLED=true is set explicitly for local development.
The key list is re-read from .env when that file changes, so rotation needs no restart.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import threading
from urllib.parse import parse_qs

from ..config import _ENV_FILE, settings
from ..shared.logger import get_logger
from ..shared.time import monotonic
from . import audit, context, keys
from .ratelimit import FailedAuthLimiter, RequestLimiter

logger = get_logger('auth')

HEADER = 'x-vega-key'
QUERY_PARAM = 'key'
OPEN_PATHS = frozenset({'/health'})
_LOOPBACK = frozenset({'127.0.0.1', '::1', '::ffff:127.0.0.1'})
_AUDITED_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
_SESSION_AUDIT_SEC = 3600.0       # first request of a person from an address is audited, then hourly
_NOISE_AUDIT_SEC = 60.0           # repeated lockout/limit events from one address: at most one audit line a minute

_KEY_LINE = re.compile(r'^\s*VEGA_API_KEY\s*=\s*(.*?)\s*$')
_lock = threading.Lock()
_cache: dict[str, object] = {'path': None, 'mtime': None, 'raw': ''}
_env_file: str = _ENV_FILE


def configure(env_file: str) -> None:
    """Read keys from a different .env file (tests)."""
    global _env_file
    with _lock:
        _env_file = env_file
        _cache.update(path=None, mtime=None, raw='')


def _raw_from_env_file() -> str | None:
    """VEGA_API_KEY as written in .env right now (re-read when the file changes); None if absent."""
    try:
        mtime = os.stat(_env_file).st_mtime_ns
    except OSError:
        return None
    with _lock:
        if _cache['path'] == _env_file and _cache['mtime'] == mtime:
            return _cache['raw'] or None      # type: ignore[return-value]
        raw = ''
        try:
            with open(_env_file, encoding='utf-8') as fh:
                for line in fh:
                    m = _KEY_LINE.match(line)
                    if m:
                        raw = m.group(1).strip().strip('"').strip("'")
        except OSError:
            raw = ''
        _cache.update(path=_env_file, mtime=mtime, raw=raw)
        return raw or None


def configured_raw() -> str:
    """The VEGA_API_KEY text in effect: .env wins (hot-reloaded), then the process environment."""
    raw = _raw_from_env_file()
    return raw if raw is not None else (settings.vega_api_key or '')


def current_entries() -> list[keys.Entry]:
    """Accepted keys: only strong ones."""
    return keys.usable_entries(configured_raw())


def current_secrets() -> list[str]:
    return [e.secret for e in current_entries()]


def match_identity(provided: str | None, entries: list[keys.Entry]) -> str | None:
    """Name of the person whose key matches, else None. Constant-time over every entry."""
    if not provided:
        return None
    given = provided.encode()
    found: str | None = None
    for entry in entries:
        if hmac.compare_digest(given, entry.secret.encode()):
            found = entry.name
    return found


def client_address(scope: dict) -> str:
    """The caller's address. Behind a local tunnel/proxy (peer is loopback) the forwarded address is used —
    only a process on this machine can be a loopback peer, so it cannot be spoofed from the internet."""
    peer = (scope.get('client') or ('unknown', 0))[0]
    if peer in _LOOPBACK:
        headers = {k: v for k, v in scope.get('headers') or []}
        forwarded = headers.get(b'cf-connecting-ip') or (headers.get(b'x-forwarded-for') or b'').split(b',')[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded.decode('ascii').strip()))
            except ValueError:
                pass
    return peer


def _header(scope: dict, name: bytes) -> str | None:
    for k, v in scope.get('headers') or []:
        if k == name:
            return v.decode('latin-1')
    return None


def _provided_key(scope: dict) -> str | None:
    value = _header(scope, HEADER.encode())
    if value:
        return value
    query = parse_qs((scope.get('query_string') or b'').decode('latin-1'))
    values = query.get(QUERY_PARAM)
    return values[0] if values else None


class ApiKeyMiddleware:
    def __init__(self, app, failed: FailedAuthLimiter | None = None, requests: RequestLimiter | None = None) -> None:
        self.app = app
        self.failed = failed or FailedAuthLimiter(
            max_failures=settings.vega_auth_max_failures,
            lockout_sec=settings.vega_auth_lockout_sec,
        )
        self.requests = requests or RequestLimiter(settings.vega_rate_limit_per_min)
        self._seen: dict[str, float] = {}
        self._noise: dict[str, float] = {}

    # -- helpers -----------------------------------------------------------------------------------------
    def _first_seen(self, identity: str, client: str) -> bool:
        now = monotonic()
        key = f'{identity}|{client}'
        last = self._seen.get(key)
        if last is not None and now - last < _SESSION_AUDIT_SEC:
            return False
        if len(self._seen) > 5000:
            self._seen.clear()
        self._seen[key] = now
        return True

    def _quiet(self, kind: str, client: str) -> bool:
        """True if an event of this kind from this client was audited within the last minute."""
        now = monotonic()
        key = f'{kind}|{client}'
        last = self._noise.get(key)
        if last is not None and now - last < _NOISE_AUDIT_SEC:
            return True
        if len(self._noise) > 5000:
            self._noise.clear()
        self._noise[key] = now
        return False

    @staticmethod
    async def _reject(send, scope, status: int, error: str, rid: str, retry_after: int = 0) -> None:
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        body = json.dumps({'success': False, 'error': error}).encode()
        headers = [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode()),
                   (b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                   (b'x-request-id', rid.encode())]
        if retry_after:
            headers.append((b'retry-after', str(retry_after).encode()))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': body})

    # -- ASGI --------------------------------------------------------------------------------------------
    async def __call__(self, scope, receive, send) -> None:
        if scope['type'] not in ('http', 'websocket'):
            await self.app(scope, receive, send)
            return

        rid = context.new_request_id(_header(scope, b'x-request-id'))
        context.request_id.set(rid)
        context.caller.set('')
        client = client_address(scope)
        method = scope.get('method', 'GET')
        path = scope.get('path', '')
        started = monotonic()
        status_seen: list[int] = []

        async def tracked_send(message) -> None:
            if message['type'] == 'http.response.start':
                status_seen.append(message['status'])
                existing = {k.lower() for k, _ in message.get('headers', [])}
                extra = [(b'x-request-id', rid.encode()), (b'x-content-type-options', b'nosniff'),
                         (b'referrer-policy', b'no-referrer')]
                if b'cache-control' not in existing:
                    extra.append((b'cache-control', b'no-store'))
                if scope.get('scheme') == 'https' and b'strict-transport-security' not in existing:
                    extra.append((b'strict-transport-security', b'max-age=31536000'))
                message = {**message, 'headers': [*message.get('headers', []), *extra]}
            await send(message)

        # 2. always-open
        if method == 'OPTIONS' or path in OPEN_PATHS:
            await self.app(scope, receive, tracked_send)
            return

        # 3. lockout
        locked = self.failed.retry_after(client)
        if locked:
            if not self._quiet('locked_out', client):
                audit.write('locked_out', rid=rid, client=client, method=method, path=path, retry_after=locked)
            await self._reject(send, scope, 429, 'too_many_attempts', rid, locked)
            return

        # 4. who is calling
        identity: str | None
        if settings.vega_auth_disabled:
            identity = 'anonymous'
        else:
            entries = current_entries()
            if not entries:
                if not self._quiet('no_key', 'server'):
                    logger.error('Request refused: no strong VEGA_API_KEY is configured (run: python3 '
                                 'backend/app/auth/keygen.py ensure)')
                    audit.write('refused_no_key', rid=rid, client=client, method=method, path=path)
                await self._reject(send, scope, 503, 'server_has_no_api_key_configured', rid)
                return
            provided = _provided_key(scope)
            identity = match_identity(provided, entries)
            if identity is None:
                lock_for = self.failed.record_failure(client)
                audit.write('auth_failed', rid=rid, client=client, method=method, path=path,
                            key_supplied=bool(provided), locked_out_for=lock_for or None)
                if lock_for:
                    logger.warning('Client %s locked out for %ds after repeated wrong API keys', client, lock_for)
                    await self._reject(send, scope, 429, 'too_many_attempts', rid, lock_for)
                else:
                    await self._reject(send, scope, 401, 'unauthorized', rid)
                return

        # 5. per-person rate limit
        wait = self.requests.allow(identity)
        if wait:
            if not self._quiet('rate_limited', f'{identity}|{client}'):
                audit.write('rate_limited', rid=rid, identity=identity, client=client, method=method, path=path)
            await self._reject(send, scope, 429, 'rate_limited', rid, wait)
            return

        # 6. serve, trace, audit
        context.caller.set(identity)
        scope['vega_identity'] = identity
        scope['vega_request_id'] = rid
        try:
            await self.app(scope, receive, tracked_send)
        finally:
            if (method in _AUDITED_METHODS or path.startswith('/api/logs')
                    or self._first_seen(identity, client)):
                audit.write('request', rid=rid, identity=identity, client=client, method=method, path=path,
                            status=status_seen[0] if status_seen else None,
                            ms=int((monotonic() - started) * 1000))


def log_status() -> None:
    """Startup report: say plainly whether the API is protected."""
    raw = configured_raw()
    weak = keys.weak_entries(raw)
    strong = keys.usable_entries(raw)
    if settings.vega_auth_disabled:
        logger.warning('VEGA_AUTH_DISABLED=true — the API is OPEN to anyone who can reach it (development only)')
        return
    for entry in weak:
        logger.error("API key '%s' is too weak (needs at least %d characters) and is IGNORED — generate one with: "
                     "python3 backend/app/auth/keygen.py new %s", entry.name, keys.MIN_KEY_LENGTH, entry.name)
    if not strong:
        logger.error('NO usable API key — every request will be refused. Run: python3 backend/app/auth/keygen.py ensure')
        return
    logger.info('API protection ON: %d key(s) [%s], rate limit %s/min per person, lockout after %d wrong keys',
                len(strong), ', '.join(e.name for e in strong),
                settings.vega_rate_limit_per_min or 'off', settings.vega_auth_max_failures)
