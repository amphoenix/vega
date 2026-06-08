"""Vega Backend Entry Point — plain Flask threaded dev server.

The 6-connection-per-origin browser cap is solved at the Vite layer:
Vite proxies /api/* through the frontend dev server, which uses HTTP/2
to the browser. Backend stays simple: single sync WSGI worker pool.
"""
import os
import sys

if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config


def main() -> None:
    errors = Config.validate()
    if errors:
        print("Configuration errors:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    app  = create_app()
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))

    print(f"[vega] Flask threaded dev server on http://{host}:{port}")
    app.run(host=host, port=port, debug=Config.DEBUG, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()
