#!/bin/sh
set -e

PORT="${PORT:-8080}"
exec python -m uvicorn src.web.app:app --host 0.0.0.0 --port "$PORT"