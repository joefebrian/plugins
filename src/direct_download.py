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


def _normalize_tiktok_page_url(video: Video, principal_id: Optional[str] = None) -> str:
    """Ensure absolute https://www.tiktok.com/@user/video/ID URL for TikWM / yt-dlp."""
    url = (video.url or "").strip()
    vid = (video.platform_video_id or "").strip()
    handle = (principal_id or "").lstrip("@").strip()
    if url.startswith("http") and "/video/" in url:
        return url
    if vid and handle:
        return f"https://www.tiktok.com/@{handle}/video/{vid}"
    if vid and url.startswith("@"):
        return f"https://www.tiktok.com/{url}/video/{vid}" if "/video/" not in url else f"https://www.tiktok.com/{url}"
    if vid:
        # Bare id or relative path — username unknown; still better than bare id for TikWM
        if url.isdigit() or not url:
            return f"https://www.tiktok.com/video/{vid}"
        if url.startswith("/"):
            return f"https://www.tiktok.com{url}"
    if url.startswith("/"):
        return f"https://www.tiktok.com{url}"
    return url


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
    errors: list[str] = []

    if platform == "tiktok":
        page_url = _normalize_tiktok_page_url(video, principal_id)
        if page_url and page_url != (video.url or ""):
            video.url = page_url
        if not page_url:
            raise ValueError("URL video TikTok kosong — scan ulang profil")
        try:
            meta = get_tiktok_video_url(page_url, q)
            candidates = list(meta.get("candidates") or [])
            if meta.get("download_url") and meta["download_url"] not in candidates:
                candidates.insert(0, meta["download_url"])
            if candidates:
                return candidates
            errors.append("TikWM: no candidates")
        except Exception as e:
            from .ytdlp_util import format_ytdlp_error

            errors.append(f"TikWM: {format_ytdlp_error(e)}")
        # Fallback: yt-dlp direct URL
        try:
            return [_ytdlp_url(page_url, q, cookies_file)]
        except Exception as e:
            from .ytdlp_util import format_ytdlp_error

            errors.append(f"yt-dlp: {format_ytdlp_error(e)}")
            raise ValueError(
                "Gagal mengambil video: "
                + "; ".join(errors)
                + ". Upload cookies TikTok di Settings, atau coba lagi."
            ) from e

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
    is_tt = "tiktok.com" in (page_url or "")
    # Strict presets break TikTok ("Requested format is not available")
    fmt = (
        "best/mp4/bestvideo+bestaudio/bestvideo/bestaudio"
        if is_tt
        else FORMAT_PRESETS.get(quality, FORMAT_PRESETS["best"])
    )
    from .ytdlp_util import apply_chrome_impersonate

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": fmt,
        "skip_download": True,
        "http_headers": {"User-Agent": BROWSER_UA, "Referer": "https://www.tiktok.com/"},
    }
    apply_chrome_impersonate(opts)
    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=False)

    if not info:
        raise ValueError("Gagal mengambil URL video (yt-dlp)")

    url = info.get("url")
    if not url and info.get("formats"):
        for fmt_row in reversed(info["formats"]):
            if fmt_row.get("vcodec") and fmt_row.get("vcodec") != "none" and fmt_row.get("url"):
                url = fmt_row["url"]
                break
            if fmt_row.get("url") and not url:
                url = fmt_row["url"]
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

    from .ytdlp_util import format_ytdlp_error

    raise ValueError(
        f"Gagal mengambil video: {format_ytdlp_error(last_err, 'HTTP Error 403: Forbidden')}. "
        "Coba upload cookies TikTok di Settings, atau download ulang sebentar lagi."
    )
