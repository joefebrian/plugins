"""Stream video directly to browser without saving on server."""

from __future__ import annotations

import re
import urllib.request
from typing import Generator, Optional
from urllib.parse import quote

import yt_dlp

from .db.models import Video
from .downloader import FORMAT_PRESETS, _sanitize_filename_stem
from .scrapers.tikwm import get_tiktok_video_url


def direct_download_filename(video: Video) -> str:
    stem = _sanitize_filename_stem(video.title or "", video.platform_video_id)
    return f"{stem}.mp4"


def _ascii_filename_fallback(filename: str) -> str:
    """ASCII-only filename for Content-Disposition (HTTP headers use latin-1)."""
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    safe = stem.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", safe)
    safe = " ".join(safe.split()).strip(" .")
    if not safe:
        safe = "video"
    return f"{safe}.{ext}" if ext else safe


def content_disposition_attachment(filename: str) -> str:
    """RFC 5987 attachment header safe for Starlette latin-1 encoding."""
    fallback = _ascii_filename_fallback(filename)
    encoded = quote(filename, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def resolve_direct_download_url(
    video: Video,
    platform: str,
    *,
    quality: str = "best",
    cookies_file: Optional[str] = None,
) -> str:
    q = quality if quality in FORMAT_PRESETS else "best"

    if platform == "tiktok":
        meta = get_tiktok_video_url(video.url, q)
        return meta["download_url"]

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "format": FORMAT_PRESETS[q],
        "skip_download": True,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video.url, download=False)

    if not info:
        raise ValueError("Gagal mengambil URL video")

    url = info.get("url")
    if not url and info.get("formats"):
        for fmt in reversed(info["formats"]):
            if fmt.get("vcodec") and fmt.get("vcodec") != "none" and fmt.get("url"):
                url = fmt["url"]
                break
    if not url:
        raise ValueError("URL video tidak tersedia untuk download langsung")
    return url


def stream_remote_video(
    url: str,
    *,
    referer: str = "https://www.tiktok.com/",
) -> Generator[bytes, None, None]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": referer,
        },
    )
    resp = urllib.request.urlopen(req, timeout=300)
    try:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            yield chunk
    finally:
        resp.close()