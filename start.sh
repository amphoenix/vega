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
#   PUBLIC_PORT  — what the browser hits (Hypercorn, HTTPS + HTTP/2 + HTTP/3)
PUBLIC_PORT="${BE_PORT:-47291}"
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

# Export all key=value pairs from .env so backend os.environ.get() sees them
set -a
# shellcheck source=/dev/null
source "$ROOT_DIR/.env"
set +a

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

# ── Self-signed TLS cert (Hypercorn HTTPS on localhost) ──────────────────────
# Hypercorn uses certs/{cert,key}.pem for TLS + HTTP/2 + HTTP/3 (QUIC).
# Generate a fresh self-signed cert if either is missing — this is purely
# for local-dev HTTPS (browser shows "Not Secure" on first visit; click
# through). For production, replace with a real cert (Let's Encrypt etc).
CERTS_DIR="$ROOT_DIR/certs"
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

# ── Prevent sleep / screen-off while running ─────────────────────────────────
if command -v caffeinate &>/dev/null; then
  caffeinate -dis &
  CAFFEINE_PID=$!
  info "Sleep inhibited (caffeinate pid $CAFFEINE_PID)"
fi

# ── Start ─────────────────────────────────────────────────────────────────────

cleanup() {
  echo ""
  info "Stopping..."
  [ -n "$BE_PID" ] && kill -TERM "$BE_PID" 2>/dev/null
  [ -n "$FE_PID" ] && kill -TERM "$FE_PID" 2>/dev/null
  sleep 3
  [ -n "$BE_PID" ] && kill -9 "$BE_PID" 2>/dev/null; pkill -9 -P "$BE_PID" 2>/dev/null
  [ -n "$FE_PID" ] && kill -9 "$FE_PID" 2>/dev/null; pkill -9 -P "$FE_PID" 2>/dev/null
  [ -n "$CAFFEINE_PID" ] && kill "$CAFFEINE_PID" 2>/dev/null
  lsof -ti :${PUBLIC_PORT} | xargs kill -9 2>/dev/null
  lsof -ti :${FE_PORT}     | xargs kill -9 2>/dev/null
  wait $BE_PID $FE_PID 2>/dev/null
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── Kill zombies from previous runs ────────────────────────────────────────────
_restarting=0
lsof -ti :${FE_PORT} &>/dev/null && _restarting=1

info "Cleaning up stale processes..."
lsof -ti :${PUBLIC_PORT} | xargs kill -9 2>/dev/null
lsof -ti :${FE_PORT}     | xargs kill -9 2>/dev/null
sleep 0.5

# Browser talks directly to Hypercorn over HTTPS + HTTP/2 + HTTP/3.
# No nginx proxy layer — Hypercorn handles TLS, H2 multiplexing, and QUIC.
if [[ "$MODE" != "backend" ]]; then
  echo "VITE_API_BASE_URL=https://localhost:${PUBLIC_PORT}" > "$ROOT_DIR/frontend/.env.local"
fi

if [[ "$MODE" != "frontend" ]]; then
  # Pass CORS origins to backend via env — no hardcoded ports in Python
  export CORS_ORIGINS="https://localhost:${PUBLIC_PORT},http://localhost:${FE_PORT},https://127.0.0.1:${PUBLIC_PORT},http://127.0.0.1:${FE_PORT}"

  info "Starting Backend  →  https://localhost:${PUBLIC_PORT}  (Hypercorn, HTTP/2 + HTTP/3)"
  cd "$ROOT_DIR/backend" && .venv/bin/hypercorn app.main:app \
    --config "$ROOT_DIR/backend/hypercorn.toml" \
    --bind "0.0.0.0:${PUBLIC_PORT}" \
    --quic-bind "0.0.0.0:${PUBLIC_PORT}" \
    --certfile "$CERTS_DIR/cert.pem" \
    --keyfile "$CERTS_DIR/key.pem" &
  BE_PID=$!
  cd "$ROOT_DIR"

  # Wait for Hypercorn to be ready
  info "Waiting for backend to be ready..."
  for i in {1..60}; do
    if curl -sk -o /dev/null --max-time 1 "https://127.0.0.1:${PUBLIC_PORT}/health" 2>/dev/null; then
      info "Backend ready (${i}×0.5s)"
      break
    fi
    sleep 0.5
  done
  if ! curl -sk -o /dev/null --max-time 1 "https://127.0.0.1:${PUBLIC_PORT}/health" 2>/dev/null; then
    warn "Backend not responding after 30s — continuing anyway"
  fi
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

# Auto-open browser to the UI once Vite is up. Vite serves the Vue app
# at FE_PORT (HTTP); Hypercorn serves API+SSE on PUBLIC_PORT (HTTPS).
# Skipped for --backend (no UI) and when NO_OPEN=1 is set.
if [[ "$MODE" != "backend" ]] && [[ "${NO_OPEN:-0}" != "1" ]] && [[ "$_restarting" == "0" ]]; then
  URL="http://localhost:${FE_PORT}"
  for i in {1..40}; do
    if curl -s -o /dev/null --max-time 1 "$URL" 2>/dev/null; then break; fi
    sleep 0.25
  done
  if   command -v open     &>/dev/null; then open "$URL"     >/dev/null 2>&1 &
  elif command -v xdg-open &>/dev/null; then xdg-open "$URL" >/dev/null 2>&1 &
  fi
fi

wait $BE_PID $FE_PID
