"""Fetch TikTok video download URLs via tikwm.com API (HD, no watermark) + resilient CDN fetch."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


TIKWM_API = "https://www.tikwm.com/api/"

# Full browser UA — bare "Mozilla/5.0" is often 403'd by TikTok CDN
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CDN_HEADERS_BASE = {
    "User-Agent": BROWSER_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}


def _cdn_headers(referer: str = "https://www.tiktok.com/") -> dict:
    h = dict(CDN_HEADERS_BASE)
    h["Referer"] = referer
    h["Origin"] = "https://www.tiktok.com"
    return h


def get_tiktok_video_url(page_url: str, quality: str = "best") -> dict:
    """
    Return dict with download_url, candidates[], title, size, is_hd.
    quality: best | 1080 | 720
    """
    params = urllib.parse.urlencode({"url": page_url, "hd": "1"})
    req = urllib.request.Request(
        f"{TIKWM_API}?{params}",
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Referer": "https://www.tikwm.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise ValueError(f"TikWM API error HTTP {e.code}") from e
    except Exception as e:
        raise ValueError(f"TikWM API gagal: {e}") from e

    if payload.get("code") != 0:
        raise ValueError(payload.get("msg") or "Gagal ambil URL video dari TikTok")

    data = payload.get("data") or {}

    # Collect all playable CDN URLs (order by preferred quality)
    candidates: list[str] = []
    if quality == "720":
        ordered_keys = ("play", "hdplay", "wmplay")
    else:
        ordered_keys = ("hdplay", "play", "wmplay")

    for key in ordered_keys:
        u = data.get(key)
        if u and u not in candidates:
            candidates.append(u)

    if not candidates:
        raise ValueError("URL video tidak ditemukan di TikWM. Coba lagi nanti.")

    is_hd = bool(data.get("hdplay")) and quality != "720"
    size = data.get("hd_size") if is_hd else data.get("size")

    return {
        "download_url": candidates[0],
        "candidates": candidates,
        "title": data.get("title"),
        "size": size,
        "is_hd": is_hd,
        "duration": data.get("duration"),
    }


def download_file(
    url: str,
    dest: str,
    referer: str = "https://www.tiktok.com/",
    *,
    candidates: Optional[list[str]] = None,
) -> None:
    """Download remote file. On 403, try alternate candidates / referers."""
    urls = list(candidates or [])
    if url and url not in urls:
        urls.insert(0, url)
    if not urls:
        raise ValueError("Tidak ada URL download")

    referers = [
        referer,
        "https://www.tiktok.com/",
        "https://www.tikwm.com/",
        "https://www.tiktok.com/foryou",
    ]

    last_err: Exception | None = None
    for u in urls:
        for ref in referers:
            try:
                _download_once(u, dest, ref)
                return
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                # only rotate referer/url on 403/forbidden/401
                if "403" not in msg and "forbidden" not in msg and "401" not in msg:
                    # for non-auth errors, still try next candidate once
                    if "404" in msg or "410" in msg:
                        break
                continue

    raise ValueError(
        f"Gagal mengambil video: {last_err or 'HTTP Error 403: Forbidden'}. "
        "TikTok CDN memblok request — coba upload cookies TikTok (Settings) atau coba lagi."
    )


def _download_once(url: str, dest: str, referer: str) -> None:
    req = urllib.request.Request(url, headers=_cdn_headers(referer))
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"HTTP Error {e.code}: {e.reason or 'Forbidden'}") from e

    if len(data) < 50_000:
        raise ValueError("File terlalu kecil — bukan video valid")

    if data[:4] == b"ID3\x03" or data[:3] == b"ID3":
        raise ValueError("Yang terdownload audio MP3, bukan video")

    with open(dest, "wb") as f:
        f.write(data)


def open_cdn_stream(url: str, referer: str = "https://www.tiktok.com/"):
    """Open CDN stream for StreamingResponse. Raises ValueError on failure."""
    last_err: Exception | None = None
    for ref in (referer, "https://www.tiktok.com/", "https://www.tikwm.com/"):
        try:
            req = urllib.request.Request(url, headers=_cdn_headers(ref))
            return urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (401, 403):
                break
            continue
        except Exception as e:
            last_err = e
            continue
    if isinstance(last_err, urllib.error.HTTPError):
        raise ValueError(f"HTTP Error {last_err.code}: {last_err.reason or 'Forbidden'}") from last_err
    raise ValueError(f"Gagal membuka stream video: {last_err}") from last_err
