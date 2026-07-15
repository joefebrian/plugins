"""RedNote / Xiaohongshu web API client (profile feed + video download URLs)."""

from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

from .base import VideoInfo, _safe_int

USER_POSTED_URI = "/api/sns/web/v1/user_posted"
NOTE_FEED_URI = "/api/sns/web/v1/feed"

_REDNOTE_PROFILE_RE = re.compile(r"rednote\.com/user/profile/([^/?#]+)", re.I)
_XHS_PROFILE_RE = re.compile(r"xiaohongshu\.com/user/profile/([^/?#]+)", re.I)
_EXPLORE_RE = re.compile(r"(?:rednote|xiaohongshu)\.com/(?:explore|discovery/item)/([0-9a-f]+)", re.I)

_VIDEO_CDNS = (
    "https://sns-video-qc.xhscdn.com",
    "https://sns-video-hw.xhscdn.com",
    "https://sns-video-bd.xhscdn.com",
    "https://sns-video-qn.xhscdn.com",
)

_SIGNER: Any = None


def _get_signer():
    global _SIGNER
    if _SIGNER is None:
        from xhshow import Xhshow

        _SIGNER = Xhshow()
    return _SIGNER


def _api_bundle(*, international: bool) -> dict[str, str]:
    if international:
        return {
            "host": "https://webapi.rednote.com",
            "domain": "https://www.rednote.com",
            "origin": "https://www.rednote.com",
            "referer": "https://www.rednote.com/",
        }
    return {
        "host": "https://edith.xiaohongshu.com",
        "domain": "https://www.xiaohongshu.com",
        "origin": "https://www.xiaohongshu.com",
        "referer": "https://www.xiaohongshu.com/",
    }


def cookies_dict_from_file(
    path: str | None,
    *,
    domains: tuple[str, ...] = ("rednote.com", "xiaohongshu.com"),
) -> dict[str, str]:
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    pairs: dict[str, str] = {}
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
        if name and value and name not in pairs:
            pairs[name] = value
    return pairs


def cookies_header_from_file(path: str | None, *, domains: tuple[str, ...] = ("rednote.com", "xiaohongshu.com")) -> str:
    pairs = cookies_dict_from_file(path, domains=domains)
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def _ensure_cookie_dict(cookie_dict: dict[str, str]) -> dict[str, str]:
    cookies = dict(cookie_dict)
    if not cookies.get("a1") or not cookies.get("webId"):
        from xhshow import Xhshow

        a1 = cookies.get("a1") or Xhshow.generate_a1()
        cookies["a1"] = a1
        cookies.setdefault("webId", Xhshow.generate_web_id(a1))
    return cookies


def _base_headers(bundle: dict[str, str], cookie_header: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": bundle["origin"],
        "Referer": bundle["referer"],
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _build_query_string(params: dict[str, Any]) -> str:
    parts = []
    for key, value in params.items():
        value_str = "" if value is None else str(value)
        parts.append(f"{key}={quote(value_str, safe=',')}")
    return "&".join(parts)


def _http_get_json(
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str],
) -> dict:
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
        import urllib.request

        full = f"{url}?{_build_query_string(params)}"
        req = urllib.request.Request(full, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())


def _http_post_json(url: str, *, payload: dict, headers: dict[str, str]) -> dict:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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


def _signed_get(
    uri: str,
    params: dict[str, Any],
    *,
    cookies: dict[str, str],
    bundle: dict[str, str],
) -> dict:
    signer = _get_signer()
    sign_headers = signer.sign_headers_get(uri=uri, cookies=cookies, params=params)
    headers = _base_headers(bundle, "; ".join(f"{k}={v}" for k, v in cookies.items()))
    headers.update(sign_headers)
    full_url = f"{bundle['host']}{uri}"
    return _http_get_json(full_url, params=params, headers=headers)


def _signed_post(
    uri: str,
    payload: dict,
    *,
    cookies: dict[str, str],
    bundle: dict[str, str],
    user_id: str = "",
) -> dict:
    signer = _get_signer()
    sign_headers = signer.sign_headers_post(
        uri=uri,
        cookies=cookies,
        payload=payload,
        x_rap=True,
        user_id=user_id or None,
    )
    headers = _base_headers(bundle, "; ".join(f"{k}={v}" for k, v in cookies.items()))
    headers.update(sign_headers)
    return _http_post_json(f"{bundle['host']}{uri}", payload=payload, headers=headers)


def _ensure_success(data: dict, *, action: str) -> dict:
    if data.get("success"):
        payload = data.get("data")
        return payload if isinstance(payload, dict) else {}
    code = data.get("code")
    msg = data.get("msg") or data.get("message") or f"code={code}"
    if code in (-100, -101, 300012):
        raise ValueError(
            f"RedNote {action} butuh cookies login. "
            "Export cookies dari rednote.com atau xiaohongshu.com (login dulu) lalu upload di menu Cookies."
        )
    if code in (-104,):
        raise ValueError(
            f"RedNote {action} gagal: sesi habis. Login ulang di rednote.com lalu upload cookies baru."
        )
    raise ValueError(f"RedNote {action} gagal: {msg}")


def extract_note_id_from_url(url: str) -> str:
    match = _EXPLORE_RE.search(url or "")
    if match:
        return match.group(1)
    parts = (url or "").rstrip("/").split("/")
    for marker in ("explore", "discovery"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1].split("?")[0]
    return ""


def parse_note_url_tokens(url: str) -> tuple[str, str, str]:
    note_id = extract_note_id_from_url(url)
    parsed = urlparse(url or "")
    query = parse_qs(parsed.query)
    xsec_token = (query.get("xsec_token") or query.get("xsecToken") or [""])[0]
    xsec_source = (query.get("xsec_source") or query.get("xsecSource") or ["pc_user"])[0]
    return note_id, xsec_token, xsec_source


def build_note_url(
    note_id: str,
    *,
    xsec_token: str = "",
    xsec_source: str = "pc_user",
    international: bool = True,
) -> str:
    domain = "www.rednote.com" if international else "www.xiaohongshu.com"
    base = f"https://{domain}/explore/{note_id}"
    if xsec_token:
        return f"{base}?xsec_token={quote(xsec_token, safe='')}&xsec_source={xsec_source}"
    return base


def _video_url_from_note_card(note: dict) -> str:
    video = note.get("video") if isinstance(note.get("video"), dict) else {}
    consumer = video.get("consumer") if isinstance(video.get("consumer"), dict) else {}
    origin_key = consumer.get("origin_video_key") or consumer.get("originVideoKey")
    if isinstance(origin_key, str) and origin_key:
        return f"{random.choice(_VIDEO_CDNS)}/{origin_key}"

    media = video.get("media") if isinstance(video.get("media"), dict) else {}
    stream = media.get("stream") if isinstance(media.get("stream"), dict) else {}
    for key in ("h264", "h265", "av1"):
        variants = stream.get(key)
        if not isinstance(variants, list):
            continue
        for item in variants:
            if isinstance(item, dict):
                master = item.get("master_url") or item.get("masterUrl")
                if isinstance(master, str) and master.startswith("http"):
                    return master
    return ""


def fetch_user_posted(
    user_id: str,
    *,
    cursor: str = "",
    cookies_file: str | None = None,
    international: bool = True,
    xsec_token: str = "",
    xsec_source: str = "pc_feed",
) -> dict:
    cookies = _ensure_cookie_dict(cookies_dict_from_file(cookies_file))
    bundle = _api_bundle(international=international)
    params = {
        "num": "30",
        "cursor": cursor,
        "user_id": user_id,
        "image_formats": "jpg,webp,avif",
        "xsec_token": xsec_token,
        "xsec_source": xsec_source,
    }
    data = _signed_get(USER_POSTED_URI, params, cookies=cookies, bundle=bundle)
    return _ensure_success(data, action="scan profil")


def fetch_note_detail(
    note_id: str,
    *,
    xsec_token: str,
    xsec_source: str = "pc_user",
    cookies_file: str | None = None,
    international: bool = True,
    user_id: str = "",
) -> dict:
    cookies = _ensure_cookie_dict(cookies_dict_from_file(cookies_file))
    bundle = _api_bundle(international=international)
    payload = {
        "source_note_id": note_id,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": 1},
        "xsec_source": xsec_source,
        "xsec_token": xsec_token,
    }
    data = _signed_post(
        NOTE_FEED_URI,
        payload,
        cookies=cookies,
        bundle=bundle,
        user_id=user_id,
    )
    payload_data = _ensure_success(data, action="ambil detail video")
    items = payload_data.get("items") or []
    if items and isinstance(items[0], dict):
        card = items[0].get("note_card") or items[0].get("noteCard")
        if isinstance(card, dict):
            return card
    raise ValueError("RedNote tidak mengembalikan detail video — coba scan ulang profil.")


def resolve_rednote_download_url(
    video_url: str,
    *,
    note_id: str | None = None,
    cookies_file: str | None = None,
    international: bool = True,
    user_id: str = "",
) -> str:
    nid, xsec_token, xsec_source = parse_note_url_tokens(video_url)
    nid = note_id or nid
    if not nid:
        raise ValueError("Note ID RedNote tidak ditemukan dari URL video")
    if not xsec_token:
        raise ValueError(
            "Token video RedNote tidak ada. Scan ulang profil agar xsec_token tersimpan di URL video."
        )

    note = fetch_note_detail(
        nid,
        xsec_token=xsec_token,
        xsec_source=xsec_source,
        cookies_file=cookies_file,
        international=international,
        user_id=user_id,
    )
    url = _video_url_from_note_card(note)
    if url:
        return url

    # Fallback: yt-dlp single-note extractor (xiaohongshu.com) with cookies
    import yt_dlp

    xhs_url = build_note_url(nid, xsec_token=xsec_token, xsec_source=xsec_source, international=False)
    opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(xhs_url, download=False)
    if info:
        direct = info.get("url")
        if isinstance(direct, str) and direct.startswith("http"):
            return direct
        for fmt in reversed(info.get("formats") or []):
            if fmt.get("vcodec") and fmt.get("vcodec") != "none" and fmt.get("url"):
                return fmt["url"]

    raise ValueError(
        "Gagal mengambil URL video RedNote. Upload cookies rednote.com / xiaohongshu.com atau scan ulang profil."
    )


def note_item_to_video_info(item: dict, *, international: bool = True) -> Optional[VideoInfo]:
    if not isinstance(item, dict):
        return None
    note_type = str(item.get("type") or "").lower()
    if note_type != "video":
        return None

    note_id = str(item.get("note_id") or item.get("noteId") or "")
    if not note_id:
        return None

    xsec_token = str(item.get("xsec_token") or item.get("xsecToken") or "")
    url = build_note_url(note_id, xsec_token=xsec_token, international=international)
    interact = item.get("interact_info") if isinstance(item.get("interact_info"), dict) else {}
    if not interact and isinstance(item.get("interactInfo"), dict):
        interact = item["interactInfo"]

    posted_at = None
    ts = item.get("time") or item.get("create_time") or item.get("createTime")
    if ts:
        try:
            value = int(ts)
            if value > 1_000_000_000_000:
                value //= 1000
            posted_at = datetime.utcfromtimestamp(value)
        except (TypeError, ValueError, OSError):
            posted_at = None

    title = item.get("display_title") or item.get("displayTitle") or item.get("title") or f"RedNote {note_id}"
    return VideoInfo(
        platform_video_id=note_id,
        url=url,
        title=title,
        description=title,
        views=None,
        likes=_safe_int(interact.get("liked_count") or interact.get("likedCount")),
        comments=_safe_int(interact.get("comment_count") or interact.get("commentCount")),
        shares=_safe_int(interact.get("share_count") or interact.get("shareCount")),
        posted_at=posted_at,
    )


def iter_profile_videos(
    user_id: str,
    *,
    cookies_file: str | None = None,
    international: bool = True,
    max_pages: int = 30,
):
    cursor = ""
    for _ in range(max_pages):
        payload = fetch_user_posted(
            user_id,
            cursor=cursor,
            cookies_file=cookies_file,
            international=international,
        )
        notes = payload.get("notes") or []
        for raw in notes:
            info = note_item_to_video_info(raw if isinstance(raw, dict) else {}, international=international)
            if info:
                yield info
        if not payload.get("has_more"):
            break
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break


def detect_international_from_input(value: str) -> bool:
    raw = (value or "").lower()
    if "xiaohongshu.com" in raw:
        return False
    return True