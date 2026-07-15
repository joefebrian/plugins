"""Kuaishou web API client (profile feed + video download URLs)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .base import VideoInfo, _parse_posted_at, _safe_int

PROFILE_FEED_URL = "https://www.kuaishou.com/rest/v/profile/feed"
VIDEO_BY_ID_URL = "https://live.kuaishou.com/live_api/profile/feedbyid"

PC_DATA_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.kuaishou.com",
    "Referer": "https://www.kuaishou.com/new-reco?source=NewReco",
}

_SHORT_VIDEO_RE = re.compile(r"kuaishou\.com/short-video/([^/?#]+)", re.I)
_PROFILE_RE = re.compile(r"kuaishou\.com/profile/([^/?#]+)", re.I)


def extract_photo_id_from_url(url: str) -> str:
    match = _SHORT_VIDEO_RE.search(url or "")
    if match:
        return match.group(1)
    parts = (url or "").rstrip("/").split("/")
    if "short-video" in parts:
        idx = parts.index("short-video")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


def cookies_header_from_file(path: str | None, *, domains: tuple[str, ...] = ("kuaishou.com",)) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    pairs: list[str] = []
    for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain = parts[0].lstrip(".").lower()
        if not any(d in domain for d in domains):
            continue
        name, value = parts[5], parts[6]
        if name and value:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _merge_headers(cookie_header: str) -> dict[str, str]:
    headers = dict(PC_DATA_HEADERS)
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _http_post_json(url: str, payload: dict, *, cookie_header: str = "") -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = _merge_headers(cookie_header)
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.post(
            url,
            data=body,
            headers=headers,
            impersonate="chrome131",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        import urllib.request

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())


def _http_get_json(url: str, *, params: dict, cookie_header: str = "") -> dict:
    headers = _merge_headers(cookie_header)
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.get(
            url,
            params=params,
            headers=headers,
            impersonate="chrome131",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        import urllib.parse
        import urllib.request

        full = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(full, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())


def _photo_id_from_share(share_info: str) -> str:
    if not share_info:
        return ""
    parsed = urlparse(share_info)
    values = parse_qs(parsed.query).get("photoId") or []
    if values:
        return str(values[0])
    match = _SHORT_VIDEO_RE.search(share_info)
    return match.group(1) if match else ""


def _ensure_success(data: dict, *, action: str) -> None:
    result = data.get("result")
    if result == 1:
        return
    if result == 109:
        raise ValueError(
            f"Kuaishou {action} butuh cookies login. "
            "Export cookies dari kuaishou.com (login dulu) lalu upload di menu Cookies."
        )
    message = data.get("error_msg") or data.get("message") or data.get("error_id") or f"result={result}"
    raise ValueError(f"Kuaishou {action} gagal: {message}")


def fetch_profile_feed(
    user_id: str,
    *,
    pcursor: str = "",
    cookies_file: str | None = None,
) -> dict:
    cookie_header = cookies_header_from_file(cookies_file)
    data = _http_post_json(
        PROFILE_FEED_URL,
        {"user_id": user_id, "pcursor": pcursor, "page": "profile"},
        cookie_header=cookie_header,
    )
    _ensure_success(data, action="scan profil")
    return data


def fetch_video_detail(
    photo_id: str,
    principal_id: str,
    *,
    cookies_file: str | None = None,
) -> dict:
    cookie_header = cookies_header_from_file(cookies_file)
    data = _http_get_json(
        VIDEO_BY_ID_URL,
        params={"photoId": photo_id, "principalId": principal_id},
        cookie_header=cookie_header,
    )
    return data


def _extract_mp4_url(detail: dict) -> str:
    candidates: list[Any] = [
        detail.get("mp4Url"),
    ]
    current = detail.get("data", {}).get("currentWork") if isinstance(detail.get("data"), dict) else None
    if isinstance(current, dict):
        candidates.extend([current.get("mp4Url"), current.get("photoUrl"), current.get("url")])
    if isinstance(detail.get("currentWork"), dict):
        cw = detail["currentWork"]
        candidates.extend([cw.get("mp4Url"), cw.get("photoUrl")])
    for item in detail.get("feeds") or []:
        if isinstance(item, dict):
            candidates.append(item.get("mp4Url"))
    for url in candidates:
        if isinstance(url, str) and url.startswith("http"):
            return url
    return ""


def resolve_kuaishou_download_url(
    video_url: str,
    principal_id: str,
    *,
    photo_id: str | None = None,
    cookies_file: str | None = None,
) -> str:
    pid = photo_id or extract_photo_id_from_url(video_url)
    if not pid:
        raise ValueError("Photo ID Kuaishou tidak ditemukan dari URL video")
    detail = fetch_video_detail(pid, principal_id, cookies_file=cookies_file)
    url = _extract_mp4_url(detail)
    if url:
        return url
    raise ValueError(
        "Gagal mengambil URL video Kuaishou. Upload cookies kuaishou.com atau coba scan ulang profil."
    )


def feed_item_to_video_info(item: dict, user_id: str) -> Optional[VideoInfo]:
    photo = item.get("photo") if isinstance(item.get("photo"), dict) else item
    if not isinstance(photo, dict):
        return None

    photo_type = str(photo.get("photoType") or item.get("photoType") or "VIDEO").upper()
    if photo_type not in ("VIDEO", ""):
        return None

    share_info = str(photo.get("share_info") or photo.get("shareInfo") or "")
    photo_id = str(photo.get("id") or photo.get("photoId") or _photo_id_from_share(share_info) or "")
    if not photo_id:
        return None

    url = f"https://www.kuaishou.com/short-video/{photo_id}"
    posted_at = _parse_posted_at(photo)
    if not posted_at and photo.get("timestamp"):
        try:
            posted_at = datetime.utcfromtimestamp(int(photo["timestamp"]) / 1000)
        except (TypeError, ValueError, OSError):
            posted_at = None

    title = photo.get("caption") or photo.get("userName") or f"Kuaishou {photo_id}"
    return VideoInfo(
        platform_video_id=photo_id,
        url=url,
        title=title,
        description=photo.get("caption"),
        views=_safe_int(photo.get("viewCount")),
        likes=_safe_int(photo.get("likeCount")),
        comments=_safe_int(photo.get("commentCount")),
        shares=_safe_int(photo.get("shareCount")),
        posted_at=posted_at,
    )


def iter_profile_videos(
    user_id: str,
    *,
    cookies_file: str | None = None,
    max_pages: int = 30,
):
    pcursor = ""
    for _ in range(max_pages):
        payload = fetch_profile_feed(user_id, pcursor=pcursor, cookies_file=cookies_file)
        feeds = payload.get("feeds") or []
        for raw in feeds:
            info = feed_item_to_video_info(raw if isinstance(raw, dict) else {}, user_id)
            if info:
                yield info
        pcursor = str(payload.get("pcursor") or "")
        if not pcursor or pcursor in {"no_more", "0"}:
            break