# Affiliate Video Tool

Satu pipeline: scan profil sumber, simpan daftar videonya, download file, lalu tembak ke akun tujuan yang sudah terhubung.

Hari ini ini satu app FastAPI, bukan plugin terpisah. Dokumen alur untuk programmer ada di [docs/ALUR.md](docs/ALUR.md). Itu kontrak yang harus diikuti kalau platform baru ditambah.

Production: https://plugins-production-a347.up.railway.app/
Lokal: `./run-web.sh` lalu buka http://127.0.0.1:8080/login.html

## Peta singkat

```text
Sumber (scan)          Inti                         Tujuan (tembak)
TikTok                 Profile + Video              YouTube
Instagram Reels        download file                Facebook Page
Kuaishou               GMV / komisi                 Threads
RedNote                analisa produk di video
Shopee shop
```

Facebook, Instagram, dan X sebagai tujuan upload belum semua siap. Facebook sebagai sumber scan belum ada. Feed kreator Shopee Video bukan sumber yang didukung.

Jangan commit `data/`, cookie, atau `.env`.
