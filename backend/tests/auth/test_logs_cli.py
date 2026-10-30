"""The remote viewer against a real server over real HTTP — what a machine in another city would do."""

import asyncio
import importlib.util
import os
import socket
import threading

import pytest
from fastapi import FastAPI
from hypercorn.asyncio import serve
from hypercorn.config import Config

from app.api import logs as logs_api
from app.auth import audit
from app.auth.api_key import ApiKeyMiddleware
from app.shared.time import sleep, today_ist_str
from tests.auth.conftest import KEY_ALICE, KEY_BOB

_SCRIPT = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'scripts', 'vega_logs.py')
_spec = importlib.util.spec_from_file_location('vega_logs', _SCRIPT)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

TODAY = today_ist_str()


@pytest.fixture
def server(tmp_path, env_file, monkeypatch):
    logdir = tmp_path / 'logs'
    logdir.mkdir()
    monkeypatch.setattr(logs_api, 'LOG_DIR', str(logdir))
    audit.configure(str(logdir))
    (logdir / f'{TODAY}.log').write_text(
        f'[{TODAY} 10:00:00] INFO [vega.a.b:1] startup ok\n'
        f'[{TODAY} 10:00:01] ERROR [vega.a.b:2] scalp failed key=LEAKYVALUE123\n')
    app = FastAPI()
    app.add_middleware(ApiKeyMiddleware)
    app.include_router(logs_api.router)

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    cfg = Config()
    cfg.bind = [f'127.0.0.1:{port}']
    cfg.loglevel = 'ERROR'
    stop = threading.Event()
    loop_holder = {}

    def run():
        loop = asyncio.new_event_loop()
        loop_holder['loop'] = loop
        loop_holder['event'] = asyncio.Event()
        loop.run_until_complete(serve(app, cfg, shutdown_trigger=loop_holder['event'].wait))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.1).close()
            break
        except OSError:
            sleep(0.05)
    yield f'http://127.0.0.1:{port}', logdir
    loop_holder['loop'].call_soon_threadsafe(loop_holder['event'].set)
    stop.set()
    t.join(timeout=3)
    from app.shared.logger import LOG_DIR
    audit.configure(LOG_DIR)


def test_reads_redacted_logs_with_the_right_key(server, capsys):
    url, _ = server
    assert cli.main(['--url', url, '--key', KEY_ALICE]) == 0
    out = capsys.readouterr().out
    assert 'startup ok' in out and 'scalp failed' in out
    assert 'LEAKYVALUE123' not in out and 'key=<redacted>' in out


def test_each_person_can_read_and_is_named_in_the_audit_trail(server, capsys):
    url, logdir = server
    cli.main(['--url', url, '--key', KEY_BOB, '--level', 'ERROR'])
    assert 'startup ok' not in capsys.readouterr().out                       # level filter works remotely
    trail = (logdir / f'audit-{TODAY}.log').read_text()
    assert '"identity":"bob"' in trail and '"event":"logs_read"' in trail
    assert KEY_BOB not in trail


def test_wrong_key_is_refused_with_a_clear_message(server, capsys):
    url, _ = server
    assert cli.main(['--url', url, '--key', 'wrong-key']) == 1
    err = capsys.readouterr().err
    assert '401' in err and 'wrong or missing key' in err


def test_repeated_wrong_keys_get_locked_out(server, capsys):
    url, _ = server
    codes = [cli.main(['--url', url, '--key', 'wrong']) for _ in range(6)]
    assert codes == [1] * 6
    assert cli.main(['--url', url, '--key', KEY_ALICE]) == 1                  # even the right key: locked
    assert '429' in capsys.readouterr().err


def test_follow_shows_lines_appended_while_watching(server, capsys):
    url, logdir = server
    result = {}
    t = threading.Thread(target=lambda: result.setdefault('rc', cli.main(
        ['--url', url, '--key', KEY_ALICE, '--follow', '--interval', '0.1'])), daemon=True)
    t.start()
    sleep(0.6)
    with open(logdir / f'{TODAY}.log', 'a') as fh:
        fh.write(f'[{TODAY} 10:00:09] WARNING [vega.a.b:3] appeared later\n')
    sleep(0.8)
    out = capsys.readouterr().out
    assert out.count('startup ok') == 1 and 'appeared later' in out        # shown once, nothing repeated


def test_unreachable_server_is_reported_not_crashed(capsys):
    assert cli.main(['--url', 'http://127.0.0.1:9', '--key', 'k']) == 1
    assert 'Cannot reach' in capsys.readouterr().err


def test_no_key_given(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('VEGA_API_KEY', raising=False)
    assert cli.main(['--url', 'http://127.0.0.1:9']) == 2
