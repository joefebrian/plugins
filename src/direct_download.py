"""Stream video directly to browser without saving on server."""

from __future__ import annotations

import re
import urllib.request
from typing import Generator, Optional
from urllib.parse import quote

import yt_dlp

from .db.models import Video
from .downloader import FORMAT_PRESETS, _sanitize_filename_stem
from .scrapers.kuaishou_api import resolve_kuaishou_download_url
from .scrapers.rednote_api import resolve_rednote_download_url
from .scrapers.shopee_api import resolve_shopee_download_url
from .scrapers.tikwm import BROWSER_UA, get_tiktok_video_url, open_cdn_stream


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
    principal_id: Optional[str] = None,
) -> str:
    """Primary URL only. Prefer resolve_direct_download_sources for TikTok fallbacks."""
    sources = resolve_direct_download_sources(
        video,
        platform,
        quality=quality,
        cookies_file=cookies_file,
        principal_id=principal_id,
    )
    return sources[0]


def resolve_direct_download_sources(
    video: Video,
    platform: str,
    *,
    quality: str = "best",
    cookies_file: Optional[str] = None,
    principal_id: Optional[str] = None,
) -> list[str]:
    """Ordered list of CDN/page URLs to try (first is preferred)."""
    q = quality if quality in FORMAT_PRESETS else "best"

    if platform == "tiktok":
        try:
            meta = get_tiktok_video_url(video.url, q)
            candidates = list(meta.get("candidates") or [])
            if meta.get("download_url") and meta["download_url"] not in candidates:
                candidates.insert(0, meta["download_url"])
            if candidates:
                return candidates
        except Exception:
            pass
        # Fallback: yt-dlp direct URL
        return [_ytdlp_url(video.url, q, cookies_file)]

    if platform == "kuaishou":
        if not principal_id:
            raise ValueError("Profil Kuaishou tidak ditemukan untuk download")
        return [
            resolve_kuaishou_download_url(
                video.url,
                principal_id,
                photo_id=video.platform_video_id,
                cookies_file=cookies_file,
            )
        ]

    if platform == "rednote":
        return [
            resolve_rednote_download_url(
                video.url,
                note_id=video.platform_video_id,
                cookies_file=cookies_file,
                user_id=principal_id or "",
            )
        ]

    if platform == "shopee":
        if not principal_id:
            raise ValueError("Profil Shopee tidak ditemukan untuk download")
        return [
            resolve_shopee_download_url(
                video.url,
                cookies_file=cookies_file,
                username=principal_id,
            )
        ]

    return [_ytdlp_url(video.url, q, cookies_file)]


def _ytdlp_url(page_url: str, quality: str, cookies_file: Optional[str]) -> str:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "format": FORMAT_PRESETS[quality],
        "skip_download": True,
        "http_headers": {"User-Agent": BROWSER_UA, "Referer": "https://www.tiktok.com/"},
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=False)

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
    fallback_urls: Optional[list[str]] = None,
) -> Generator[bytes, None, None]:
    urls = list(fallback_urls or [])
    if url and url not in urls:
        urls.insert(0, url)

    last_err: Exception | None = None
    for u in urls:
        try:
            resp = open_cdn_stream(u, referer=referer)
            try:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                resp.close()
            return
        except Exception as e:
            last_err = e
            continue

    # Last resort: simple request with browser UA
    try:
        req = urllib.request.Request(
            urls[0],
            headers={
                "User-Agent": BROWSER_UA,
                "Referer": referer,
                "Origin": "https://www.tiktok.com",
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
        return
    except Exception as e:
        last_err = e

    raise ValueError(
        f"Gagal mengambil video: {last_err or 'HTTP Error 403: Forbidden'}. "
        "Coba upload cookies TikTok di Settings, atau download ulang sebentar lagi."
    )
