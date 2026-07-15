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

PORT="${PORT:-8080}"
if command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo ""
    echo "ERROR: Port $PORT sudah dipakai proses lain."
    echo "Cek: lsof -nP -iTCP:$PORT -sTCP:LISTEN"
    echo "Stop: kill \$(lsof -t -iTCP:$PORT -sTCP:LISTEN)"
    echo "Atau jalankan di port lain: PORT=8081 ./run-web.sh"
    exit 1
  fi
fi

if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
fi

echo ""
echo "Starting server → http://localhost:$PORT"
echo "Login     → http://localhost:$PORT/login.html"
if [ -n "${AUTH_USERNAME:-}" ]; then
  echo "Akun      → ${AUTH_USERNAME} (password dari file .env)"
else
  echo "Akun      → admin (password dari file .env)"
fi
echo ""
exec .venv/bin/python -m uvicorn src.web.app:app --host 0.0.0.0 --port "$PORT" --reload