"""Fetch TikTok video download URLs via tikwm.com API (HD, no watermark)."""

from __future__ import annotations

import urllib.parse
import urllib.request
import json
from typing import Optional


TIKWM_API = "https://www.tikwm.com/api/"


def get_tiktok_video_url(page_url: str, quality: str = "best") -> dict:
    """
    Return dict with download_url, title, size, is_hd.
    quality: best | 1080 | 720
    """
    params = urllib.parse.urlencode({"url": page_url, "hd": "1"})
    req = urllib.request.Request(
        f"{TIKWM_API}?{params}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode())

    if payload.get("code") != 0:
        raise ValueError(payload.get("msg") or "Gagal ambil URL video dari TikTok")

    data = payload.get("data") or {}

    # Priority: HD no watermark > standard no watermark > watermarked
    if quality == "720":
        url = data.get("play") or data.get("hdplay") or data.get("wmplay")
        size = data.get("size") or data.get("hd_size")
        is_hd = False
    else:
        url = data.get("hdplay") or data.get("play") or data.get("wmplay")
        size = data.get("hd_size") or data.get("size")
        is_hd = bool(data.get("hdplay"))

    if not url:
        raise ValueError("URL video tidak ditemukan. Coba lagi nanti.")

    return {
        "download_url": url,
        "title": data.get("title"),
        "size": size,
        "is_hd": is_hd,
        "duration": data.get("duration"),
    }


def download_file(url: str, dest: str, referer: str = "https://www.tiktok.com/") -> None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()

    if len(data) < 50_000:
        raise ValueError("File terlalu kecil — bukan video valid")

    # Reject audio-only files
    if data[:4] == b"ID3\x03" or data[:3] == b"ID3":
        raise ValueError("Yang terdownload audio MP3, bukan video")

    with open(dest, "wb") as f:
        f.write(data)