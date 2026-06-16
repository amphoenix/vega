#!/bin/bash

# Vega — start backend, frontend, or both
# Usage:
#   ./start.sh            — start both
#   ./start.sh --backend  — backend only
#   ./start.sh --frontend — frontend only

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()  { echo -e "${GREEN}[vega]${NC} $1"; }
warn()  { echo -e "${YELLOW}[vega]${NC} $1"; }
error() { echo -e "${RED}[vega]${NC} $1"; }

MODE="both"
case "$1" in
  --backend)  MODE="backend"  ;;
  --frontend) MODE="frontend" ;;
  "")         MODE="both"     ;;
  *)
    echo "Usage: ./start.sh [--backend | --frontend]"
    exit 1
    ;;
esac

# ── Hardcoded obscure ports ──────────────────────────────────────────────────
# Picked at random from 41000-59999 — outside all common dev ports
# (3000/5000/5173/8000/8080) and below macOS ephemeral range. Bookmark these.
#   PUBLIC_PORT  — what the browser hits (nginx, HTTPS + HTTP/2)
#   FLASK_PORT   — internal Flask, only nginx talks to it
PUBLIC_PORT="${BE_PORT:-47291}"
FLASK_INTERNAL_PORT="${FLASK_INTERNAL_PORT:-47292}"
FE_PORT="${FE_PORT:-53847}"
BE_PORT="$PUBLIC_PORT"   # back-compat: VITE_API_BASE_URL still uses BE_PORT

# ── Preflight checks ──────────────────────────────────────────────────────────

if [[ "$MODE" != "backend" ]] && ! command -v node &>/dev/null; then
  error "Node.js not found. Install from https://nodejs.org (v18+)"
  exit 1
fi

if [[ "$MODE" != "frontend" ]] && ! command -v uv &>/dev/null; then
  info "uv not found — installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv &>/dev/null; then
    error "uv install failed. Install manually: https://docs.astral.sh/uv/getting-started/installation"
    exit 1
  fi
  info "uv installed successfully."
fi

if [ ! -f "$ROOT_DIR/.env" ]; then
  warn ".env not found — copying from .env.example"
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi

# Warn if LLM_API_KEY is still a placeholder or empty
_api_key=$(grep -E '^LLM_API_KEY=' "$ROOT_DIR/.env" 2>/dev/null | cut -d= -f2-)
if [[ "$_api_key" == "your_api_key_here" || -z "$_api_key" ]]; then
  echo ""
  warn "ACTION REQUIRED: Open .env and set LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME"
  echo ""
fi
unset _api_key

# ── Auto-install deps ─────────────────────────────────────────────────────────

if [[ "$MODE" != "backend" ]] && [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
  info "Installing frontend dependencies..."
  cd "$ROOT_DIR/frontend" && npm install --silent
  cd "$ROOT_DIR"
fi

if [[ "$MODE" != "frontend" ]] && [ ! -d "$ROOT_DIR/backend/.venv" ]; then
  info "Installing backend dependencies (first run takes ~2 min)..."
  cd "$ROOT_DIR/backend" && uv sync
  cd "$ROOT_DIR"
fi

# ── Self-signed TLS cert (nginx HTTPS on localhost) ──────────────────────────
# nginx.conf points at backend/certs/{cert,key}.pem. Generate a fresh self-
# signed cert if either is missing — this is purely for local-dev HTTPS
# (browser will show a "Not Secure" warning on first visit; click through).
# For production, replace these with a real cert (Let's Encrypt etc).
CERTS_DIR="$ROOT_DIR/backend/certs"
if [ ! -f "$CERTS_DIR/cert.pem" ] || [ ! -f "$CERTS_DIR/key.pem" ]; then
  if ! command -v openssl &>/dev/null; then
    error "openssl not found — install it (macOS: 'brew install openssl', Linux: 'apt install openssl')"
    exit 1
  fi
  info "Generating self-signed TLS cert for localhost..."
  mkdir -p "$CERTS_DIR"
  openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
    -keyout "$CERTS_DIR/key.pem" \
    -out    "$CERTS_DIR/cert.pem" \
    -subj   "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1" \
    >/dev/null 2>&1
  chmod 600 "$CERTS_DIR/key.pem"
  info "Cert written to $CERTS_DIR/{cert,key}.pem (valid 825 days)"
fi

# ── Start ─────────────────────────────────────────────────────────────────────

cleanup() {
  echo ""
  info "Stopping..."
  # Graceful shutdown first, then SIGKILL after 3s if still running
  [ -n "$BE_PID"    ] && kill -TERM "$BE_PID"    2>/dev/null
  [ -n "$FE_PID"    ] && kill -TERM "$FE_PID"    2>/dev/null
  [ -n "$NGINX_PID" ] && kill -TERM "$NGINX_PID" 2>/dev/null
  sleep 3
  [ -n "$BE_PID"    ] && kill -9 "$BE_PID"    2>/dev/null; pkill -9 -P "$BE_PID"    2>/dev/null
  [ -n "$FE_PID"    ] && kill -9 "$FE_PID"    2>/dev/null; pkill -9 -P "$FE_PID"    2>/dev/null
  [ -n "$NGINX_PID" ] && kill -9 "$NGINX_PID"  2>/dev/null
  # Nuke anything still on our ports
  lsof -ti :${FLASK_INTERNAL_PORT} | xargs kill -9 2>/dev/null
  lsof -ti :${PUBLIC_PORT}         | xargs kill -9 2>/dev/null
  lsof -ti :${FE_PORT}             | xargs kill -9 2>/dev/null
  wait $BE_PID $FE_PID $NGINX_PID 2>/dev/null
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── Kill zombies from previous runs ────────────────────────────────────────────
info "Cleaning up stale processes..."
lsof -ti :${FLASK_INTERNAL_PORT} | xargs kill -9 2>/dev/null
lsof -ti :${PUBLIC_PORT}         | xargs kill -9 2>/dev/null
lsof -ti :${FE_PORT}             | xargs kill -9 2>/dev/null
sleep 0.5

# Frontend points at HTTPS nginx in front of Flask. Browser ↔ nginx is
# HTTP/2 (one TCP connection multiplexes all SSE streams + XHRs), nginx
# ↔ Flask is HTTP/1.1 on internal port. Eliminates the 6-per-origin cap.
if [[ "$MODE" != "backend" ]]; then
  echo "VITE_API_BASE_URL=https://localhost:${PUBLIC_PORT}" > "$ROOT_DIR/frontend/.env.local"
fi

if [[ "$MODE" != "frontend" ]]; then
  # Clear stale bytecode so Python always loads latest source
  find "$ROOT_DIR/backend" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
  find "$ROOT_DIR/backend" -name "*.pyc" -delete 2>/dev/null
  info "Starting Backend  →  http://127.0.0.1:${FLASK_INTERNAL_PORT}  (Flask, internal)"
  cd "$ROOT_DIR/backend" && FLASK_PORT="$FLASK_INTERNAL_PORT" FLASK_HOST="127.0.0.1" .venv/bin/python run.py &
  BE_PID=$!
  cd "$ROOT_DIR"

  # Wait briefly for Flask to bind, then start nginx in front of it.
  sleep 1
  if ! command -v nginx &>/dev/null; then
    error "nginx not found. Install: brew install nginx"
    exit 1
  fi
  mkdir -p .nginx_tmp
  info "Starting nginx    →  https://localhost:${PUBLIC_PORT}  (HTTP/2 → :${FLASK_INTERNAL_PORT})"
  nginx -p "$ROOT_DIR" -c "$ROOT_DIR/nginx.conf" &
  NGINX_PID=$!
fi

if [[ "$MODE" != "backend" ]]; then
  info "Starting Frontend →  http://localhost:${FE_PORT}  (API → :${BE_PORT})"
  cd "$ROOT_DIR/frontend" && npm run dev -- --port "$FE_PORT" --strictPort &
  FE_PID=$!
fi

echo ""
[[ "$MODE" == "both" ]]     && echo -e "${GREEN}Vega is running.${NC} Press Ctrl+C to stop."
[[ "$MODE" == "backend" ]]  && echo -e "${GREEN}Backend running.${NC} Press Ctrl+C to stop."
[[ "$MODE" == "frontend" ]] && echo -e "${GREEN}Frontend running.${NC} Press Ctrl+C to stop."
echo ""

# Auto-open browser to the UI once Vite is up. The UI is served by Vite
# at FE_PORT (HTTP); nginx :47291 is only a reverse proxy for API+SSE
# traffic and doesn't serve the Vue app — opening that port shows 404.
# Skipped for --backend (no UI) and when NO_OPEN=1 is set.
if [[ "$MODE" != "backend" ]] && [[ "${NO_OPEN:-0}" != "1" ]]; then
  URL="http://localhost:${FE_PORT}"
  # Wait until Vite is actually accepting connections (cold start can
  # take 1-2s on first run after `npm install`).
  for i in {1..40}; do
    if curl -s -o /dev/null --max-time 1 "$URL" 2>/dev/null; then break; fi
    sleep 0.25
  done
  if   command -v open     &>/dev/null; then open "$URL"     >/dev/null 2>&1 &
  elif command -v xdg-open &>/dev/null; then xdg-open "$URL" >/dev/null 2>&1 &
  fi
fi

wait $BE_PID $FE_PID $NGINX_PID
