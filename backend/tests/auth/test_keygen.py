import os
import stat
import subprocess
import sys

import pytest

from app.auth import keygen, keys

SCRIPT = os.path.join(os.path.dirname(keygen.__file__), 'keygen.py')


def test_ensure_creates_an_owner_key_when_none(tmp_path):
    env = tmp_path / '.env'
    env.write_text('A=1\nVEGA_API_KEY=weak\nB=2\n')
    created = keygen.ensure(str(env))
    assert created and created.startswith('owner:')
    text = env.read_text()
    assert 'A=1' in text and 'B=2' in text and 'VEGA_API_KEY=weak' not in text
    assert keys.usable_entries(keygen.read_value(str(env)))[0].name == 'owner'
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600            # secrets file is owner-only


def test_ensure_leaves_a_strong_key_alone(tmp_path):
    env = tmp_path / '.env'
    env.write_text('VEGA_API_KEY=alice:Xk3pQ9wLm2Zr7TvB5nYc8HdF1aJ4uSeG\n')
    before = env.read_text()
    assert keygen.ensure(str(env)) is None and env.read_text() == before


def test_add_appends_and_drops_weak_and_refuses_duplicates(tmp_path):
    env = tmp_path / '.env'
    env.write_text('VEGA_API_KEY=old:weak,alice:Xk3pQ9wLm2Zr7TvB5nYc8HdF1aJ4uSeG\n')
    entry = keygen.add(str(env), 'bob')
    names = [e.name for e in keys.usable_entries(keygen.read_value(str(env)))]
    assert names == ['alice', 'bob'] and entry.startswith('bob:')
    with pytest.raises(ValueError):
        keygen.add(str(env), 'alice')
    with pytest.raises(ValueError):
        keygen.new_entry('Bad Name')


def test_write_creates_missing_file_and_never_duplicates_the_line(tmp_path):
    env = tmp_path / 'new.env'
    keygen.write_value(str(env), 'a:1')
    keygen.write_value(str(env), 'b:2')
    assert env.read_text().count('VEGA_API_KEY=') == 1 and 'VEGA_API_KEY=b:2' in env.read_text()


def test_works_as_a_plain_script_without_the_app_installed(tmp_path):
    """start.sh runs it with the system python, before any virtualenv exists."""
    env = tmp_path / '.env'
    env.write_text('X=1\n')
    run = lambda *a: subprocess.run([sys.executable, '-S', SCRIPT, *a], capture_output=True, text=True,  # noqa: E731
                                    env={'VEGA_ENV_FILE': str(env), 'PATH': os.environ['PATH']})
    out = run('ensure')
    assert out.returncode == 0 and 'created one named' in out.stdout
    assert 'owner:' not in out.stdout                     # the secret is never printed by `ensure`
    secret = run('client-key').stdout
    assert keys.is_strong(secret) and ':' not in secret
    assert run('bogus').returncode == 2
