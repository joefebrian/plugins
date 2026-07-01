#!/bin/bash
# Expose local server ke internet (gratis, tanpa buka port router)
# Butuh server jalan dulu: ./run-web.sh

set -e
PORT="${PORT:-8080}"

if ! curl -s "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "ERROR: Server belum jalan di port ${PORT}"
  echo "Jalankan dulu di terminal lain: ./run-web.sh"
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Install cloudflared dulu: brew install cloudflared"
  exit 1
fi

echo "Membuka tunnel ke port ${PORT}..."
echo "PENTING: Set COOKIE_SECURE=true di .env saat pakai HTTPS tunnel"
echo "Ganti AUTH_PASSWORD default sebelum expose ke internet!"
echo "Tunggu URL publik muncul di bawah (format: https://xxxx.trycloudflare.com)"
echo "Ctrl+C untuk stop tunnel"
echo ""

cloudflared tunnel --url "http://127.0.0.1:${PORT}"