"""
Create or repair API keys.  Standard library only, so start.sh can run it before anything else:

    python3 backend/app/auth/keygen.py new <name>            # print a fresh `<name>:<key>` entry
    python3 backend/app/auth/keygen.py add <name>            # generate and append to .env
    python3 backend/app/auth/keygen.py ensure                # server start: make sure a strong key exists
    python3 backend/app/auth/keygen.py client-key            # print the secret this machine should send
"""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import sys

# keys.py is loaded by path so this file works both as `python3 backend/app/auth/keygen.py` (start.sh, no
# virtualenv, no package) and when imported by the application and its tests.
_spec = importlib.util.spec_from_file_location('vega_auth_keys', os.path.join(os.path.dirname(__file__), 'keys.py'))
keys = importlib.util.module_from_spec(_spec)           # type: ignore[arg-type]
_spec.loader.exec_module(keys)                          # type: ignore[union-attr]

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
DEFAULT_ENV = os.path.join(_PROJECT_ROOT, '.env')
_LINE = re.compile(r'^\s*VEGA_API_KEY\s*=')


def read_value(env_path: str) -> str:
    value = ''
    try:
        with open(env_path, encoding='utf-8') as fh:
            for line in fh:
                if _LINE.match(line):
                    value = line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return value


def write_value(env_path: str, value: str) -> None:
    """Set VEGA_API_KEY in .env (replace the line, or append), keeping every other line, mode 0600."""
    lines: list[str] = []
    try:
        with open(env_path, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
    except OSError:
        pass
    new = f'VEGA_API_KEY={value}'
    replaced = False
    out = []
    for line in lines:
        if _LINE.match(line):
            if not replaced:
                out.append(new)
                replaced = True
            continue                    # drop duplicates
        out.append(line)
    if not replaced:
        if out and out[-1] == '':
            out.insert(len(out) - 1, new)
        else:
            out.append(new)
    text = '\n'.join(out)
    if not text.endswith('\n'):
        text += '\n'
    tmp = env_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, env_path)


def new_entry(name: str) -> str:
    if not keys._NAME.match(name):
        raise ValueError('name must be lowercase letters, digits, - or _ (max 24 characters)')
    return f'{name}:{keys.generate_secret()}'


def add(env_path: str, name: str) -> str:
    """Append a new named key (keeps existing strong ones, drops weak ones). Returns the new entry."""
    current = keys.usable_entries(read_value(env_path))
    if any(e.name == name for e in current):
        raise ValueError(f'a key named {name!r} already exists')
    entry = new_entry(name)
    write_value(env_path, ','.join([f'{e.name}:{e.secret}' for e in current] + [entry]))
    return entry


def ensure(env_path: str) -> str | None:
    """If no strong key exists, create one named 'owner'. Returns the entry if one was created."""
    if keys.usable_entries(read_value(env_path)):
        return None
    entry = new_entry('owner')
    write_value(env_path, entry)
    return entry


def main(argv: list[str]) -> int:
    env_path = os.environ.get('VEGA_ENV_FILE', DEFAULT_ENV)
    cmd = argv[1] if len(argv) > 1 else ''
    try:
        if cmd == 'new' and len(argv) > 2:
            print(new_entry(argv[2]))
        elif cmd == 'add' and len(argv) > 2:
            entry = add(env_path, argv[2])
            print(f"Added key '{argv[2]}' to {env_path}. Give this person ONLY the part after the colon:")
            print(entry.split(':', 1)[1])
        elif cmd == 'ensure':
            created = ensure(env_path)
            if created:
                print(f"No strong API key was set — created one named 'owner' in {env_path} (mode 600).")
        elif cmd == 'client-key':
            print(keys.first_secret(read_value(env_path)), end='')
        else:
            print(__doc__)
            return 2
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
