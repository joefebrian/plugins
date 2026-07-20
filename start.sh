#!/bin/sh
# Low-resource uvicorn for Railway: 1 worker, capped concurrency, short keep-alive.
set -e

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

# Reduce glibc memory fragmentation on long-running small containers
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Concurrency cap keeps memory predictable under burst downloads
LIMIT_CONCURRENCY="${UVICORN_LIMIT_CONCURRENCY:-30}"
TIMEOUT_KEEP_ALIVE="${UVICORN_TIMEOUT_KEEP_ALIVE:-5}"
LOG_LEVEL="${UVICORN_LOG_LEVEL:-warning}"

# --workers 1: Railway cost ~ RAM; multi-worker multiplies memory
# --no-access-log: less disk/CPU on every request (use Railway metrics instead)
exec python -m uvicorn src.web.app:app \
  --host "$HOST" \
  --port "$PORT" \
  --workers 1 \
  --loop uvloop \
  --http httptools \
  --timeout-keep-alive "$TIMEOUT_KEEP_ALIVE" \
  --limit-concurrency "$LIMIT_CONCURRENCY" \
  --log-level "$LOG_LEVEL" \
  --no-access-log
