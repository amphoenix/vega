"""Access control: identity, lockout, rate limit, fail-closed, tracing, audit."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.auth import api_key
from app.auth.api_key import ApiKeyMiddleware
from app.auth.ratelimit import FailedAuthLimiter, RequestLimiter
from tests.auth.conftest import KEY_ALICE, KEY_BOB


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def build(failed=None, requests=None):
    app = FastAPI()
    app.add_middleware(ApiKeyMiddleware, failed=failed, requests=requests)

    @app.get('/health')
    def health(): return {'status': 'ok'}

    @app.get('/api/thing')
    def thing(): return {'data': 1}

    @app.post('/api/trade/executor/auto-trading')
    def toggle(): return {'toggled': True}

    @app.get('/api/stream')
    async def stream():
        async def gen():
            for i in range(3):
                yield f'data: {i}\n\n'
                await asyncio.sleep(0.01)
        return StreamingResponse(gen(), media_type='text/event-stream')

    return TestClient(app)


@pytest.fixture
def client(env_file, audit_dir):
    return build()


def H(key): return {'X-Vega-Key': key}


def audit_events(audit_dir):
    out = []
    for f in audit_dir.glob('audit-*.log'):
        out += [json.loads(line) for line in f.read_text().splitlines() if line]
    return out


class TestProtection:
    def test_blocked_without_a_key(self, client):
        r = client.get('/api/thing')
        assert r.status_code == 401 and r.json() == {'success': False, 'error': 'unauthorized'}

    def test_each_person_can_use_their_own_key(self, client):
        assert client.get('/api/thing', headers=H(KEY_ALICE)).status_code == 200
        assert client.get('/api/thing', headers=H(KEY_BOB)).status_code == 200

    def test_wrong_empty_and_name_prefixed_keys_are_rejected(self, client):
        assert client.get('/api/thing', headers=H(KEY_ALICE + 'x')).status_code == 401
        assert client.get('/api/thing', headers=H('')).status_code == 401
        assert client.get('/api/thing', headers=H('alice:' + KEY_ALICE)).status_code == 401   # clients send the secret only

    def test_query_key_for_event_streams(self, client):
        assert client.get('/api/stream').status_code == 401
        r = client.get('/api/stream', params={'key': KEY_BOB})
        assert r.status_code == 200 and 'data: 2' in r.text

    def test_state_changing_routes_need_the_key(self, client):
        assert client.post('/api/trade/executor/auto-trading').status_code == 401
        assert client.post('/api/trade/executor/auto-trading', headers=H(KEY_ALICE)).status_code == 200

    def test_unknown_routes_are_not_revealed_without_a_key(self, client):
        assert client.get('/api/does-not-exist').status_code == 401

    def test_health_is_open_and_reveals_nothing(self, client):
        assert client.get('/health').status_code == 200

    def test_cors_preflight_is_never_blocked(self, client):
        r = client.options('/api/thing', headers={'Origin': 'http://x', 'Access-Control-Request-Method': 'GET'})
        assert r.status_code != 401


class TestFailClosed:
    def test_weak_keys_are_never_accepted(self, env_file, audit_dir):
        env_file.write_text('VEGA_API_KEY=old:weakkey123\n')
        c = build()
        r = c.get('/api/thing', headers=H('weakkey123'))
        assert r.status_code == 503 and 'no_api_key' in r.json()['error']

    def test_no_key_at_all_closes_the_api(self, env_file, audit_dir):
        env_file.write_text('OTHER=1\n')
        assert build().get('/api/thing').status_code == 503

    def test_weak_key_next_to_a_strong_one_is_ignored_but_the_strong_one_works(self, env_file, audit_dir):
        env_file.write_text(f'VEGA_API_KEY=old:weakkey123,alice:{KEY_ALICE}\n')
        c = build()
        assert c.get('/api/thing', headers=H('weakkey123')).status_code == 401
        assert c.get('/api/thing', headers=H(KEY_ALICE)).status_code == 200

    def test_explicit_opt_out_for_local_development(self, env_file, audit_dir, monkeypatch):
        env_file.write_text('OTHER=1\n')
        monkeypatch.setattr(api_key.settings, 'vega_auth_disabled', True)
        assert build().get('/api/thing').status_code == 200

    def test_health_still_answers_when_closed(self, env_file, audit_dir):
        env_file.write_text('OTHER=1\n')
        assert build().get('/health').status_code == 200


class TestLockout:
    def test_five_wrong_keys_lock_the_address_even_for_the_right_key(self, env_file, audit_dir):
        clock = Clock()
        c = build(failed=FailedAuthLimiter(5, 60, 900, clock=clock))
        for _ in range(4):
            assert c.get('/api/thing', headers=H('wrong')).status_code == 401
        r = c.get('/api/thing', headers=H('wrong'))                   # 5th strike
        assert r.status_code == 429 and int(r.headers['retry-after']) > 800
        # locked: nothing is evaluated — the correct key is refused too, so the key cannot be probed
        assert c.get('/api/thing', headers=H(KEY_ALICE)).status_code == 429
        clock.t += 901
        assert c.get('/api/thing', headers=H(KEY_ALICE)).status_code == 200

    def test_another_address_is_not_affected(self, env_file, audit_dir):
        clock = Clock()
        c = build(failed=FailedAuthLimiter(2, 60, 900, clock=clock))
        c.get('/api/thing', headers=H('wrong')); c.get('/api/thing', headers=H('wrong'))
        assert c.get('/api/thing', headers=H(KEY_ALICE)).status_code == 429
        # behind a local tunnel (peer is loopback) the forwarded address identifies the client
        tunnel = TestClient(c.app, client=('127.0.0.1', 4000))
        assert tunnel.get('/api/thing', headers={**H(KEY_ALICE), 'cf-connecting-ip': '203.0.113.9'}).status_code == 200
        tunnel.get('/api/thing', headers={**H('wrong'), 'cf-connecting-ip': '203.0.113.50'})
        tunnel.get('/api/thing', headers={**H('wrong'), 'cf-connecting-ip': '203.0.113.50'})
        assert tunnel.get('/api/thing', headers={**H(KEY_ALICE), 'cf-connecting-ip': '203.0.113.50'}).status_code == 429
        assert tunnel.get('/api/thing', headers={**H(KEY_ALICE), 'cf-connecting-ip': '203.0.113.9'}).status_code == 200

    def test_forwarded_address_is_ignored_unless_the_peer_is_loopback(self):
        scope = {'client': ('198.51.100.7', 5000), 'headers': [(b'cf-connecting-ip', b'203.0.113.9')]}
        assert api_key.client_address(scope) == '198.51.100.7'      # cannot be spoofed from the internet
        scope = {'client': ('127.0.0.1', 5000), 'headers': [(b'x-forwarded-for', b'203.0.113.9, 10.0.0.1')]}
        assert api_key.client_address(scope) == '203.0.113.9'
        scope = {'client': ('127.0.0.1', 5000), 'headers': [(b'x-forwarded-for', b'not-an-ip')]}
        assert api_key.client_address(scope) == '127.0.0.1'


class TestRateLimit:
    def test_per_person_limit_with_retry_after(self, env_file, audit_dir):
        clock = Clock()
        c = build(requests=RequestLimiter(3, clock=clock))
        assert [c.get('/api/thing', headers=H(KEY_ALICE)).status_code for _ in range(3)] == [200, 200, 200]
        r = c.get('/api/thing', headers=H(KEY_ALICE))
        assert r.status_code == 429 and r.json()['error'] == 'rate_limited' and int(r.headers['retry-after']) >= 1
        assert c.get('/api/thing', headers=H(KEY_BOB)).status_code == 200      # the other person is unaffected


class TestTracingAndHeaders:
    def test_every_response_carries_a_request_id_and_security_headers(self, client):
        r = client.get('/api/thing', headers=H(KEY_ALICE))
        assert len(r.headers['x-request-id']) >= 8
        assert r.headers['x-content-type-options'] == 'nosniff'
        assert r.headers['cache-control'] == 'no-store'
        assert r.headers['referrer-policy'] == 'no-referrer'

    def test_rejections_are_traceable_too(self, client):
        assert client.get('/api/thing').headers['x-request-id']

    def test_a_sane_caller_supplied_id_is_kept_and_a_hostile_one_is_replaced(self, client):
        ok = client.get('/api/thing', headers={**H(KEY_ALICE), 'X-Request-ID': 'trace-from-bob-0001'})
        assert ok.headers['x-request-id'] == 'trace-from-bob-0001'
        bad = client.get('/api/thing', headers={**H(KEY_ALICE), 'X-Request-ID': 'x\r\ninjected: 1'})
        assert 'injected' not in bad.headers['x-request-id']

    def test_streaming_is_not_buffered(self, client):
        with client.stream('GET', '/api/stream', params={'key': KEY_ALICE}) as r:
            assert r.status_code == 200
            assert next(r.iter_lines()).startswith('data: 0')

    def test_hsts_only_over_https(self, client):
        assert 'strict-transport-security' not in client.get('/api/thing', headers=H(KEY_ALICE)).headers
        secure = TestClient(client.app, base_url='https://testserver')
        assert 'max-age' in secure.get('/api/thing', headers=H(KEY_ALICE)).headers['strict-transport-security']


class TestAudit:
    def test_who_did_what_is_recorded_without_any_secret(self, client, audit_dir):
        client.get('/api/thing', headers=H(KEY_ALICE))                       # first request of a person: session start
        client.post('/api/trade/executor/auto-trading', headers=H(KEY_BOB))  # state change
        client.get('/api/thing', headers=H('definitely-wrong'))            # failure
        events = audit_events(audit_dir)
        kinds = [e['event'] for e in events]
        assert kinds.count('request') == 2 and kinds.count('auth_failed') == 1
        toggle = next(e for e in events if e['path'].endswith('auto-trading'))
        assert toggle['identity'] == 'bob' and toggle['status'] == 200 and toggle['method'] == 'POST'
        assert toggle['rid'] and toggle['client'] and 'ms' in toggle
        raw = ''.join(f.read_text() for f in audit_dir.glob('audit-*.log'))
        for secret in (KEY_ALICE, KEY_BOB, 'definitely-wrong'):
            assert secret not in raw                                         # never in the trail
        fail = next(e for e in events if e['event'] == 'auth_failed')
        assert fail['key_supplied'] is True and 'identity' not in fail

    def test_query_string_is_never_recorded(self, client, audit_dir):
        client.get('/api/stream', params={'key': KEY_ALICE, 'secret_param': 'abc'})
        assert 'secret_param' not in ''.join(f.read_text() for f in audit_dir.glob('audit-*.log'))

    def test_routine_reads_do_not_flood_the_trail(self, client, audit_dir):
        for _ in range(20):
            client.get('/api/thing', headers=H(KEY_ALICE))
        assert [e['event'] for e in audit_events(audit_dir)].count('request') == 1    # session start only

    def test_lockouts_and_limits_are_audited_once_a_minute_not_per_request(self, env_file, audit_dir):
        c = build(failed=FailedAuthLimiter(2, 60, 900), requests=RequestLimiter(1000))
        for _ in range(30):
            c.get('/api/thing', headers=H('wrong'))
        kinds = [e['event'] for e in audit_events(audit_dir)]
        assert kinds.count('locked_out') == 1 and kinds.count('auth_failed') == 2

    def test_audit_failure_never_breaks_the_api(self, client, monkeypatch, audit_dir):
        monkeypatch.setattr(api_key.audit, 'path_for', lambda d: '/proc/forbidden/x.log')
        assert client.get('/api/thing', headers=H(KEY_ALICE)).status_code == 200


class TestRotation:
    def test_changing_the_keys_needs_no_restart(self, client, env_file):
        import os
        assert client.get('/api/thing', headers=H(KEY_ALICE)).status_code == 200
        env_file.write_text(f'VEGA_API_KEY=bob:{KEY_BOB}\n')
        st = os.stat(env_file)
        os.utime(env_file, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
        assert client.get('/api/thing', headers=H(KEY_ALICE)).status_code == 401      # alice revoked
        assert client.get('/api/thing', headers=H(KEY_BOB)).status_code == 200

    def test_old_and_new_key_can_overlap(self, env_file, audit_dir):
        new = 'nEw-Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6'
        env_file.write_text(f'VEGA_API_KEY=alice:{KEY_ALICE},alice2:{new}\n')
        c = build()
        for k in (KEY_ALICE, new):
            assert c.get('/api/thing', headers=H(k)).status_code == 200

    def test_process_environment_is_the_fallback(self, env_file, audit_dir, monkeypatch):
        env_file.write_text('OTHER=1\n')
        env_file.touch()
        monkeypatch.setattr(api_key.settings, 'vega_api_key', f'viaenv:{KEY_ALICE}')
        api_key.configure(str(env_file))
        # .env has no VEGA_API_KEY line at all → falls back to the process environment value
        monkeypatch.setattr(api_key, '_raw_from_env_file', lambda: None)
        assert build().get('/api/thing', headers=H(KEY_ALICE)).status_code == 200


class TestRealApp:
    def test_every_real_route_is_locked_and_the_key_opens_them(self, env_file, audit_dir):
        from app.main import app
        def lone(n):                             # a different client address per probe, so the lockout never trips
            return TestClient(app, client=('127.0.0.1', 1)), {'cf-connecting-ip': f'198.51.100.{n}'}
        c = TestClient(app)                      # no lifespan: engines are not started
        for n, path in enumerate( ('/api/trade/executor/status', '/api/watchlist', '/api/signals/history',
                     '/api/trade/scalp-scanner/status', '/api/logs', '/api/logs/files', '/paper/status',
                     '/docs', '/openapi.json', '/redoc', '/'), start=1):
            probe, fwd = lone(n)
            assert probe.get(path, headers=fwd).status_code == 401, path
        assert c.get('/health').json() == {'status': 'ok'}
        assert c.get('/api/watchlist', headers=H(KEY_ALICE)).status_code == 200

    def test_cors_has_no_wildcard_and_401s_still_carry_cors_headers(self, env_file, audit_dir):
        from app.main import _cors_origins, app
        assert '*' not in _cors_origins
        c = TestClient(app, client=('127.0.0.1', 1))
        evil = c.get('/api/watchlist', headers={'Origin': 'https://evil.example', 'cf-connecting-ip': '198.51.100.201'})
        assert evil.status_code == 401 and 'access-control-allow-origin' not in evil.headers



class TestCorsOrder:
    def test_an_allowed_origin_gets_cors_headers_even_on_a_401(self, env_file, audit_dir):
        """Same stack order as main.py: key check inside, CORS outside — so the browser reports 401, not a CORS error."""
        from fastapi.middleware.cors import CORSMiddleware
        app = FastAPI()

        @app.get('/api/thing')
        def thing(): return {}
        app.add_middleware(ApiKeyMiddleware)
        app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:53847'], allow_credentials=False,
                           allow_methods=['GET'], allow_headers=['X-Vega-Key'], expose_headers=['X-Request-ID', 'Retry-After'])
        c = TestClient(app)
        r = c.get('/api/thing', headers={'Origin': 'http://localhost:53847'})
        assert r.status_code == 401 and r.headers['access-control-allow-origin'] == 'http://localhost:53847'
        assert 'x-request-id' in r.headers['access-control-expose-headers'].lower()
        other = c.get('/api/thing', headers={'Origin': 'https://evil.example'})
        assert 'access-control-allow-origin' not in other.headers
        pre = c.options('/api/thing', headers={'Origin': 'http://localhost:53847', 'Access-Control-Request-Method': 'GET',
                                               'Access-Control-Request-Headers': 'x-vega-key'})
        assert pre.status_code == 200 and 'x-vega-key' in pre.headers['access-control-allow-headers'].lower()
