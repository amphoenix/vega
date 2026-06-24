FROM python:3.12-slim

# Install Node.js 20, nginx, and openssl
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
     curl ca-certificates nginx openssl \
  && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

# Copy uv from official uv image
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# ── Backend deps (cached layer) ─────────────────────────────────────
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen

# ── Frontend deps (cached layer) ────────────────────────────────────
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci

# ── Copy all source ─────────────────────────────────────────────────
COPY . .

# ── Build frontend for production ───────────────────────────────────
RUN cd frontend && npm run build

EXPOSE 47291

# Start via start.sh which handles nginx + uvicorn + certs
CMD ["bash", "start.sh", "--backend"]