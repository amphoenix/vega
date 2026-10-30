"""The remote log viewer: protected, redacted, bounded, and safe to follow."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import logs as logs_api
from app.auth import audit
from app.auth.api_key import ApiKeyMiddleware
from app.shared.time import today_ist_str
from tests.auth.conftest import KEY_ALICE, KEY_BOB

H = {'X-Vega-Key': KEY_ALICE}
TODAY = today_ist_str()


@pytest.fixture
def logdir(tmp_path, env_file, monkeypatch):
    d = tmp_path / 'logs'
    d.mkdir()
    monkeypatch.setattr(logs_api, 'LOG_DIR', str(d))
    audit.configure(str(d))
    yield d
    from app.shared.logger import LOG_DIR
    audit.configure(LOG_DIR)


@pytest.fixture
def client(logdir):
    app = FastAPI()
    app.add_middleware(ApiKeyMiddleware)
    app.include_router(logs_api.router)
    return TestClient(app)


def write_log(logdir, lines, name=None, newline=True):
    p = logdir / (name or f'{TODAY}.log')
    p.write_text('\n'.join(lines) + ('\n' if newline else ''))
    return p


def L(level, msg, i=0):
    return f'[{TODAY} 10:00:{i % 60:02d}] {level} [vega.x.f:1] {msg}'


def get(client, **params):
    return client.get('/api/logs', params=params, headers=H)


class TestProtection:
    def test_key_required(self, client, logdir):
        write_log(logdir, [L('INFO', 'hello')])
        assert client.get('/api/logs').status_code == 401
        assert client.get('/api/logs/files').status_code == 401
        assert get(client).status_code == 200

    def test_route_refuses_when_the_middleware_did_not_identify_anyone(self, logdir):
        """Defence in depth: even if the middleware were left off, the logs are not served."""
        bare = FastAPI()
        bare.include_router(logs_api.router)
        write_log(logdir, [L('INFO', 'hello')])
        assert TestClient(bare).get('/api/logs').status_code == 403


class TestReading:
    def test_tail_returns_the_last_lines_in_order(self, client, logdir):
        write_log(logdir, [L('INFO', f'line {i}', i) for i in range(50)])
        d = get(client, lines=5).json()['data']
        assert d['lines'][0].endswith('line 45') and d['lines'][-1].endswith('line 49')
        assert len(d['lines']) == 5 and d['truncated'] is True and d['next_offset'] == d['size']

    def test_level_filter_keeps_stack_trace_lines_with_their_error(self, client, logdir):
        write_log(logdir, [L('INFO', 'ok 1'), L('ERROR', 'boom'), 'Traceback (most recent call last):',
                           '  File "x.py", line 1', 'ValueError: bad', L('INFO', 'ok 2'), L('WARNING', 'careful')])
        lines = get(client, level='ERROR').json()['data']['lines']
        assert [ln for ln in lines if 'ok' in ln] == []
        assert any('boom' in ln for ln in lines) and any('ValueError' in ln for ln in lines)
        warn = get(client, level='warning').json()['data']['lines']
        assert any('careful' in ln for ln in warn) and any('boom' in ln for ln in warn)

    def test_text_filter_is_case_insensitive(self, client, logdir):
        write_log(logdir, [L('INFO', 'Scalp SIGNAL buy'), L('INFO', 'forex tick')])
        assert len(get(client, q='signal').json()['data']['lines']) == 1

    def test_audit_file_is_readable_too(self, client, logdir):
        audit.write('request', identity='alice', path='/api/x')
        d = get(client, file='audit').json()['data']
        assert json.loads(d['lines'][0])['identity'] == 'alice'

    def test_list_files(self, client, logdir):
        write_log(logdir, [L('INFO', 'a')])
        audit.write('x')
        (logdir / 'notes.txt').write_text('ignored')
        files = client.get('/api/logs/files', headers=H).json()['data']
        assert {(f['file'], f['date']) for f in files} == {('app', TODAY), ('audit', TODAY)}


class TestRedaction:
    def test_credentials_never_leave_the_server(self, client, logdir):
        write_log(logdir, [
            L('INFO', 'GET /api/x?key=URLSECRET123 ok'),
            L('ERROR', "WS error {'set-cookie': '__cf_bm=COOKIESECRET; HttpOnly'}"),
            L('INFO', 'token eyJhbGciOiJIUzUx.eyJpc3MiOiJkaGFu.ZB6RgJIiMySA'),
            L('INFO', f'someone pasted {KEY_BOB} into a log'),         # a real configured key, anywhere
        ])
        text = '\n'.join(get(client).json()['data']['lines'])
        for secret in ('URLSECRET123', 'COOKIESECRET', 'eyJhbGciOiJIUzUx', KEY_BOB, KEY_ALICE):
            assert secret not in text
        assert 'GET /api/x?key=<redacted> ok' in text


class TestFollowMode:
    def test_only_new_complete_lines_are_returned_and_none_repeat(self, client, logdir):
        p = write_log(logdir, [L('INFO', 'one'), L('INFO', 'two')])
        first = get(client).json()['data']
        offset = first['next_offset']
        with open(p, 'a') as fh:
            fh.write(L('INFO', 'three') + '\n' + L('INFO', 'four-partial'))     # last line not finished yet
        second = get(client, offset=offset).json()['data']
        assert [ln.rsplit(' ', 1)[-1] for ln in second['lines']] == ['three']
        with open(p, 'a') as fh:
            fh.write('\n')
        third = get(client, offset=second['next_offset']).json()['data']
        assert [ln.rsplit(' ', 1)[-1] for ln in third['lines']] == ['four-partial']
        assert get(client, offset=third['next_offset']).json()['data']['lines'] == []

    def test_rotated_file_resets_instead_of_hanging(self, client, logdir):
        write_log(logdir, [L('INFO', f'l{i}') for i in range(20)])
        big = get(client).json()['data']['next_offset']
        write_log(logdir, [L('INFO', 'fresh')])                                # file replaced by a shorter one
        d = get(client, offset=big).json()['data']
        assert d['reset'] is True and any('fresh' in ln for ln in d['lines'])

    def test_a_flood_of_matches_is_paged_without_losing_lines(self, client, logdir):
        p = write_log(logdir, [L('INFO', 'start')])
        offset = get(client).json()['data']['next_offset']
        with open(p, 'a') as fh:
            fh.write(''.join(L('INFO', f'row{i:04d}') + '\n' for i in range(300)))
        seen, guard = [], 0
        while guard < 20:
            d = get(client, offset=offset, lines=100).json()['data']
            seen += [ln.rsplit(' ', 1)[-1] for ln in d['lines']]
            if not d['lines']:
                break
            offset, guard = d['next_offset'], guard + 1
        assert seen == [f'row{i:04d}' for i in range(300)]


class TestLimitsAndSafety:
    @pytest.mark.parametrize('params', [
        {'date': '../../etc/passwd'}, {'date': '2026-10-30/../../x'}, {'date': '20261030'},
        {'file': 'bogus'}, {'file': '../app'}, {'level': 'LOUD'},
    ])
    def test_bad_input_is_rejected(self, client, logdir, params):
        assert get(client, **params).status_code == 400

    @pytest.mark.parametrize('params', [{'lines': 0}, {'lines': 5000}, {'q': 'x' * 101}, {'offset': -1}])
    def test_out_of_range_values_are_rejected(self, client, logdir, params):
        assert get(client, **params).status_code == 422

    def test_missing_log_is_404(self, client, logdir):
        assert get(client, date='2001-01-01').status_code == 404

    def test_huge_file_is_read_from_the_end_only(self, client, logdir, monkeypatch):
        monkeypatch.setattr(logs_api, 'MAX_TAIL_BYTES', 2048)
        write_log(logdir, [L('INFO', f'row{i:05d}', i) for i in range(2000)])
        d = get(client, lines=2000).json()['data']
        assert 0 < len(d['lines']) < 60 and d['lines'][-1].endswith('row01999')
        assert all(ln.startswith('[') for ln in d['lines'])                   # no half-line from the cut

    def test_every_read_is_audited_with_who_and_what_but_not_the_search_text(self, client, logdir):
        write_log(logdir, [L('INFO', 'x')])
        get(client, q='private search', level='error')
        raw = (logdir / f'audit-{TODAY}.log').read_text()
        ev = [json.loads(x) for x in raw.splitlines() if 'logs_read' in x][0]
        assert ev['identity'] == 'alice' and ev['file'] == 'app' and ev['level'] == 'ERROR' and ev['q_len'] == 14
        assert 'private search' not in raw
