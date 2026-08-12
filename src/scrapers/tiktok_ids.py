"""Resolve TikTok numeric user id / secUid for stable yt-dlp profile scans."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Optional

from .tikwm import BROWSER_UA, TIKWM_API_MIRRORS


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_author_ids_from_tikwm(page_or_video_url: str) -> dict[str, str | None]:
    """
    Use TikWM video/profile helper to resolve author ids.
    Prefer numeric id for yt-dlp `tiktokuser:ID`.
    """
    params = urllib.parse.urlencode({"url": page_or_video_url, "hd": "1"})
    last_err: Exception | None = None
    payload = None
    for base in TIKWM_API_MIRRORS:
        req = urllib.request.Request(
            f"{base}?{params}",
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/json",
                "Referer": "https://www.tikwm.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.loads(resp.read().decode())
            break
        except Exception as e:
            last_err = e
            continue
    if not payload:
        raise ValueError(f"TikWM gagal resolve user id: {last_err}")

    if payload.get("code") != 0:
        raise ValueError(payload.get("msg") or "TikWM tidak mengembalikan author")

    data = payload.get("data") or {}
    author = data.get("author") or {}
    # Some payloads put author fields at top level
    numeric_id = (
        _safe_str(author.get("id"))
        or _safe_str(data.get("author_id"))
        or _safe_str(author.get("uid"))
    )
    sec_uid = (
        _safe_str(author.get("sec_uid"))
        or _safe_str(author.get("secUid"))
        or _safe_str(data.get("sec_uid"))
    )
    unique_id = (
        _safe_str(author.get("unique_id"))
        or _safe_str(author.get("uniqueId"))
        or _safe_str(data.get("unique_id"))
    )
    return {
        "id": numeric_id,
        "sec_uid": sec_uid,
        "unique_id": unique_id,
        "preferred": numeric_id or sec_uid,
    }


def extract_author_ids_from_ytdlp(
    video_url: str,
    cookies_file: str | None = None,
) -> dict[str, str | None]:
    import yt_dlp

    from ..ytdlp_util import apply_chrome_impersonate

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "http_headers": {
            "User-Agent": BROWSER_UA,
            "Referer": "https://www.tiktok.com/",
        },
    }
    apply_chrome_impersonate(opts)
    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False) or {}

    numeric_id = _safe_str(info.get("channel_id")) or _safe_str(info.get("uploader_id"))
    if numeric_id and not (numeric_id.isdigit() or numeric_id.startswith("MS4w")):
        # Sometimes uploader_id is the @handle — ignore
        if not numeric_id.isdigit():
            numeric_id = None

    sec_uid = None
    channel_url = _safe_str(info.get("channel_url") or info.get("uploader_url"))
    if channel_url and "secUid=" in channel_url:
        m = re.search(r"secUid=([^&]+)", channel_url)
        sec_uid = m.group(1) if m else None

    for key in ("uploader_id", "channel_id"):
        val = info.get(key)
        if isinstance(val, str) and val.isdigit() and not numeric_id:
            numeric_id = val

    preferred = numeric_id or sec_uid
    return {
        "id": numeric_id,
        "sec_uid": sec_uid,
        "unique_id": _safe_str(info.get("uploader") or info.get("channel") or info.get("creator")),
        "preferred": preferred,
    }


def resolve_tiktok_platform_user_id(
    *,
    username: str | None = None,
    sample_video_url: str | None = None,
    cookies_file: str | None = None,
) -> str | None:
    """
    Best-effort resolve stable TikTok user id for yt-dlp `tiktokuser:ID`.
    Prefer numeric id from a known video URL (TikWM first, then yt-dlp).
    """
    errors: list[str] = []

    if sample_video_url:
        try:
            ids = extract_author_ids_from_tikwm(sample_video_url)
            if ids.get("preferred"):
                return ids["preferred"]
        except Exception as e:
            errors.append(f"tikwm:{e}")
        try:
            ids = extract_author_ids_from_ytdlp(sample_video_url, cookies_file)
            if ids.get("preferred"):
                return ids["preferred"]
        except Exception as e:
            errors.append(f"ytdlp:{e}")

    if username:
        profile = f"https://www.tiktok.com/@{username.lstrip('@')}"
        try:
            # TikWM often accepts profile URL and returns latest video + author
            ids = extract_author_ids_from_tikwm(profile)
            if ids.get("preferred"):
                return ids["preferred"]
        except Exception as e:
            errors.append(f"tikwm_profile:{e}")

    return None


def tiktok_scan_input(username: str, platform_user_id: str | None) -> str:
    """URL/input string for yt-dlp profile scan."""
    uid = (platform_user_id or "").strip()
    if uid:
        # Numeric user id or secUid → tiktokuser:ID (yt-dlp recommended)
        if uid.isdigit() or uid.startswith("MS4w") or len(uid) >= 16:
            return f"tiktokuser:{uid}"
    return f"https://www.tiktok.com/@{username.lstrip('@')}"
