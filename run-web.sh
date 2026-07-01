#!/bin/bash
set -e
cd "$(dirname "$0")"

PYTHON=""
for candidate in python3.12 python3.11 python3.10 /opt/homebrew/bin/python3.12; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver=$("$candidate" -c "import sys; print(sys.version_info[:2] >= (3, 10))")
    if [ "$ver" = "True" ]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "ERROR: Butuh Python 3.10+ (TikTok scraper tidak jalan di 3.9)"
  echo "Install: brew install python@3.12"
  exit 1
fi

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example — ganti AUTH_PASSWORD sebelum expose!"
fi

if [ ! -d .venv ]; then
  echo "Creating venv with $PYTHON ..."
  "$PYTHON" -m venv .venv
fi

.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

echo "Starting server → http://localhost:8080"
exec .venv/bin/python -m uvicorn src.web.app:app --host 0.0.0.0 --port 8080 --reload