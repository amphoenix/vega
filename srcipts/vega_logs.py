#!/usr/bin/env python3
"""
Read a Vega server's logs from another machine. Needs only Python 3 and a Vega checkout — no packages.

    python3 scripts/vega_logs.py --url https://my-server:53847 --key <your key> --lines 100
    python3 scripts/vega_logs.py --follow --level WARNING          # live tail
    python3 scripts/vega_logs.py --file audit --q auth_failed      # who tried what

URL and key default to the environment (VEGA_SERVER_URL, VEGA_API_KEY) and then to ./.env.
Use --insecure only while the server still has a self-signed certificate.
"""

from _future_ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

# Use the project's own time helpers (standard-library only), found relative to this script.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(_file_)), '..', 'backend'))
try:
    from app.shared.time import sleep
except ImportError:  # pragma: no cover - only when the script is copied out of a Vega checkout
    sys.exit('Run this script from a Vega checkout (it needs backend/app/shared/time.py).')


def env_value(name: str, env_path: str = '.env') -> str:
    if os.environ.get(name):
        return os.environ[name]
    try:
        with open(env_path, encoding='utf-8') as fh:
            for line in fh:
                if line.startswith(name + '='):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ''


def client_secret(raw: str) -> str:
    """<name>:secret,<name>:secret (a server's .env) → the first secret; a plain secret is returned as is."""
    first = raw.split(',')[0].strip()
    head, sep, tail = first.partition(':')
    return tail.strip() if sep and head.replace('-', '').replace('_', '').isalnum() and head.islower() else first


def fetch(url: str, key: str, params: dict, insecure: bool = False, timeout: float = 20.0) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})
    request = urllib.request.Request(f'{url.rstrip("/")}/api/logs?{query}', headers={'X-Vega-Key': key})
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:   # noqa: S310
        return json.load(response)['data']


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Read a Vega server\'s logs remotely')
    p.add_argument('--url', default=env_value('VEGA_SERVER_URL') or 'https://localhost:47291')
    p.add_argument('--key', default='')
    p.add_argument('--file', default='app', choices=['app', 'audit'])
    p.add_argument('--date', default='')
    p.add_argument('--lines', type=int, default=100)
    p.add_argument('--level', default='')
    p.add_argument('--q', default='')
    p.add_argument('--follow', '-f', action='store_true')
    p.add_argument('--interval', type=float, default=2.0)
    p.add_argument('--insecure', action='store_true')
    args = p.parse_args(argv)

    key = args.key or client_secret(env_value('VEGA_API_KEY'))
    if not key:
        print('No key: pass --key or set VEGA_API_KEY', file=sys.stderr)
        return 2
    params = {'file': args.file, 'date': args.date, 'lines': args.lines, 'level': args.level, 'q': args.q}
    offset = None
    try:
        while True:
            data = fetch(args.url, key, {**params, 'offset': offset}, args.insecure)
            if data.get('reset'):
                print('--- log file was rotated ---', file=sys.stderr)
            for line in data['lines']:
                print(line)
            sys.stdout.flush()
            if not args.follow:
                return 0
            offset = data['next_offset']
            sleep(args.interval)
    except urllib.error.HTTPError as exc:
        hint = {401: 'wrong or missing key', 403: 'forbidden', 429: 'too many attempts / rate limited — wait and retry',
                503: 'the server has no API key configured'}.get(exc.code, exc.reason)
        print(f'HTTP {exc.code}: {hint}', file=sys.stderr)
        return 1
    except (urllib.error.URLError, ssl.SSLError, TimeoutError) as exc:
        print(f'Cannot reach {args.url}: {getattr(exc, "reason", exc)}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if _name_ == '_main_':
    sys.exit(main())
    