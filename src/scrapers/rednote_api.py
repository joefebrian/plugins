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
# Origin uploads (no watermark) are reliably served from bd/qn only.
_ORIGIN_VIDEO_CDNS = (
    "https://sns-video-bd.xhscdn.com",
    "https://sns-video-qn.xhscdn.com",
)

_SIGNER: Any = None
_SESSION: Any = None

# edith works with RedNote export cookies; webapi often returns "login expired".
_PRIMARY_HOST = "https://edith.xiaohongshu.com"
_FALLBACK_HOST = "https://webapi.rednote.com"


def _get_signer():
    global _SIGNER
    if _SIGNER is None:
        from xhshow import Xhshow

        _SIGNER = Xhshow()
    return _SIGNER


def _get_session():
    global _SESSION
    if _SESSION is None:
        from xhshow import SessionManager

        _SESSION = SessionManager()
    return _SESSION


def _api_bundle(*, international: bool) -> dict[str, str]:
    domain = "https://www.rednote.com" if international else "https://www.xiaohongshu.com"
    return {
        "host": _PRIMARY_HOST,
        "fallback_host": _FALLBACK_HOST,
        "domain": domain,
        "origin": domain,
        "referer": f"{domain}/",
    }


def _stringify_params(params: dict[str, Any]) -> dict[str, str]:
    return {key: "" if value is None else str(value) for key, value in params.items()}


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


def _base_headers(
    bundle: dict[str, str],
    cookie_header: str,
    *,
    cookies: dict[str, str] | None = None,
    referer: str | None = None,
    with_json_content_type: bool = False,
) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Origin": bundle["origin"],
        "Referer": referer or bundle["referer"],
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }
    if cookies and cookies.get("xsecappid"):
        headers["xsecappid"] = cookies["xsecappid"]
    if with_json_content_type:
        headers["Content-Type"] = "application/json"
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _build_query_string(params: dict[str, Any]) -> str:
    parts = []
    for key, value in params.items():
        value_str = "" if value is None else str(value)
        parts.append(f"{key}={quote(value_str, safe=',')}")
    return "&".join(parts)


class RedNoteAPIError(ValueError):
    pass


def _parse_response_json(resp) -> dict:
    try:
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    snippet = (getattr(resp, "text", "") or "")[:200]
    raise RedNoteAPIError(f"RedNote API mengembalikan respons tidak valid (HTTP {resp.status_code}): {snippet}")


def _http_json(method: str, url: str, *, headers: dict[str, str], body: bytes | None = None) -> dict:
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.request(
            method,
            url,
            data=body,
            headers=headers,
            impersonate="chrome131",
            timeout=30,
        )
    except ImportError:
        import urllib.request

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as raw:
            class _Resp:
                status_code = raw.status
                text = raw.read().decode()

                def json(self_nonlocal):
                    return json.loads(self.text)

            resp = _Resp()

    if resp.status_code in (461, 471):
        verify_type = getattr(resp, "headers", {}).get("Verifytype", "")
        raise RedNoteAPIError(
            "RedNote meminta verifikasi captcha. Buka rednote.com di browser, buka satu profil, "
            "selesaikan captcha jika muncul, lalu export & upload cookies lagi."
            + (f" (Verifytype {verify_type})" if verify_type else "")
        )
    if resp.status_code == 406:
        raise RedNoteAPIError(
            "RedNote menolak request (HTTP 406). Coba upload ulang cookies dari rednote.com "
            "setelah login, lalu scan lagi."
        )

    data = _parse_response_json(resp)
    if resp.status_code >= 400 and not data.get("success"):
        msg = data.get("msg") or data.get("message") or f"HTTP {resp.status_code}"
        raise RedNoteAPIError(f"RedNote API error: {msg}")
    return data


def _signed_get(
    uri: str,
    params: dict[str, Any],
    *,
    cookies: dict[str, str],
    bundle: dict[str, str],
    referer: str | None = None,
    host: str | None = None,
) -> dict:
    signer = _get_signer()
    str_params = _stringify_params(params)
    sign_headers = signer.sign_headers_get(
        uri=uri,
        cookies=cookies,
        params=str_params,
        session=_get_session(),
    )
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    headers = _base_headers(bundle, cookie_header, cookies=cookies, referer=referer)
    headers.update(sign_headers)
    api_host = host or bundle["host"]
    full_url = signer.build_url(base_url=f"{api_host}{uri}", params=str_params)
    return _http_json("GET", full_url, headers=headers)


def _signed_post(
    uri: str,
    payload: dict,
    *,
    cookies: dict[str, str],
    bundle: dict[str, str],
    user_id: str = "",
    referer: str | None = None,
    host: str | None = None,
) -> dict:
    signer = _get_signer()
    sign_headers = signer.sign_headers_post(
        uri=uri,
        cookies=cookies,
        payload=payload,
        x_rap=True,
        user_id=user_id or None,
        session=_get_session(),
    )
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    headers = _base_headers(
        bundle,
        cookie_header,
        cookies=cookies,
        referer=referer,
        with_json_content_type=True,
    )
    headers.update(sign_headers)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    api_host = host or bundle["host"]
    return _http_json("POST", f"{api_host}{uri}", headers=headers, body=body)


def check_rednote_login(cookies_file: str | None) -> dict:
    """Return login state using /v2/user/me (guest=False means logged in)."""
    cookies = _ensure_cookie_dict(cookies_dict_from_file(cookies_file))
    if not cookies.get("web_session") and not cookies.get("a1"):
        return {"ok": False, "guest": True, "message": "Cookies RedNote tidak lengkap (tanpa web_session/a1)"}

    bundle = _api_bundle(international=True)
    try:
        data = _signed_get("/api/sns/web/v2/user/me", {}, cookies=cookies, bundle=bundle)
    except RedNoteAPIError as exc:
        return {"ok": False, "guest": True, "message": str(exc)}

    payload = data.get("data") if data.get("success") else {}
    if not isinstance(payload, dict):
        payload = {}
    guest = bool(payload.get("guest"))
    if guest:
        return {
            "ok": False,
            "guest": True,
            "message": (
                "Cookies RedNote terdeteksi sebagai tamu (belum login). "
                "Login di rednote.com, buka beranda/profil, lalu export cookies lagi."
            ),
        }
    return {
        "ok": True,
        "guest": False,
        "user_id": payload.get("user_id"),
        "message": "Sesi RedNote aktif",
    }


def _ensure_success(data: dict, *, action: str) -> dict:
    if data.get("success"):
        payload = data.get("data")
        return payload if isinstance(payload, dict) else {}
    code = data.get("code")
    msg = data.get("msg") or data.get("message") or f"code={code}"
    if code in (-100, -101, 300012):
        raise RedNoteAPIError(
            f"RedNote {action}: sesi login habis. "
            "Login ulang di rednote.com, export cookies baru, upload di menu Cookies."
        )
    if code in (-104,):
        raise RedNoteAPIError(
            f"RedNote {action}: akun tidak punya akses API. "
            "Coba login ulang di rednote.com lalu upload cookies baru."
        )
    raise RedNoteAPIError(f"RedNote {action} gagal: {msg}")


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


def _parse_initial_state(html: str) -> dict:
    marker = "window.__INITIAL_STATE__="
    idx = html.find(marker)
    if idx < 0:
        raise RedNoteAPIError("Halaman video RedNote tidak berisi data — coba scan ulang profil.")
    start = idx + len(marker)
    end = html.find("</script>", start)
    raw = html[start:end].strip().rstrip(";")
    raw = re.sub(r"\bundefined\b", "null", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RedNoteAPIError("Gagal membaca data video RedNote dari halaman.") from exc


def _note_from_initial_state(state: dict, note_id: str) -> dict | None:
    note_map = (state.get("note") or {}).get("noteDetailMap") or {}
    if not isinstance(note_map, dict):
        return None

    entry = note_map.get(note_id)
    if isinstance(entry, dict):
        note = entry.get("note") if isinstance(entry.get("note"), dict) else entry
        if isinstance(note, dict):
            return note

    for entry in note_map.values():
        if not isinstance(entry, dict):
            continue
        note = entry.get("note") if isinstance(entry.get("note"), dict) else entry
        if not isinstance(note, dict):
            continue
        nid = str(note.get("note_id") or note.get("noteId") or "")
        if nid == note_id:
            return note
    return None


def _fetch_explore_html(
    note_id: str,
    *,
    xsec_token: str,
    xsec_source: str,
    cookies_file: str | None,
    international: bool,
) -> str:
    cookie_header = cookies_header_from_file(cookies_file)
    if not cookie_header:
        raise RedNoteAPIError("Cookies RedNote belum di-upload.")

    page_url = build_note_url(
        note_id,
        xsec_token=xsec_token,
        xsec_source=xsec_source,
        international=international,
    )
    bundle = _api_bundle(international=international)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Cookie": cookie_header,
        "Referer": bundle["referer"],
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.get(
            page_url,
            headers=headers,
            impersonate="chrome131",
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RedNoteAPIError(f"Halaman video RedNote tidak bisa dibuka (HTTP {resp.status_code}).")
        return resp.text
    except ImportError:
        import urllib.request

        req = urllib.request.Request(page_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as raw:
            return raw.read().decode()


def fetch_note_detail_from_html(
    note_id: str,
    *,
    xsec_token: str,
    xsec_source: str = "pc_feed",
    cookies_file: str | None = None,
    international: bool = True,
) -> dict:
    """Load note detail by parsing explore page HTML (works when feed API returns -100)."""
    if not xsec_token:
        raise RedNoteAPIError(
            "Token video RedNote tidak ada. Scan ulang profil agar xsec_token tersimpan di URL video."
        )

    sources = []
    for candidate in (xsec_source, "pc_feed", "pc_user"):
        if candidate and candidate not in sources:
            sources.append(candidate)

    last_error: Exception | None = None
    for source in sources:
        try:
            html = _fetch_explore_html(
                note_id,
                xsec_token=xsec_token,
                xsec_source=source,
                cookies_file=cookies_file,
                international=international,
            )
            note = _note_from_initial_state(_parse_initial_state(html), note_id)
            if note and str(note.get("type") or "").lower() == "video":
                return note
            last_error = RedNoteAPIError("Video tidak ditemukan di halaman RedNote.")
        except RedNoteAPIError as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise RedNoteAPIError("Gagal mengambil detail video RedNote dari halaman.")


def rednote_cdn_referer(url: str) -> str:
    """xhscdn origin files reject rednote.com Referer; use xiaohongshu.com instead."""
    host = urlparse(url or "").netloc.lower()
    if "xhscdn.com" in host:
        return "https://www.xiaohongshu.com/"
    return "https://www.rednote.com/"


def _is_watermarked_stream(item: dict) -> bool:
    desc = str(item.get("stream_desc") or item.get("streamDesc") or "").upper()
    return desc.startswith("WM_")


def _probe_origin_video_url(url: str) -> bool:
    headers = {
        "Referer": rednote_cdn_referer(url),
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.head(
            url,
            headers=headers,
            impersonate="chrome131",
            timeout=15,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return False
        size = int(resp.headers.get("content-length") or 0)
        return size == 0 or size > 100_000
    except Exception:
        try:
            import urllib.request

            req = urllib.request.Request(url, headers=headers, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as raw:
                if raw.status != 200:
                    return False
                size = int(raw.headers.get("Content-Length") or 0)
                return size == 0 or size > 100_000
        except Exception:
            return False


def _origin_video_url_from_consumer(consumer: dict) -> str:
    origin_key = consumer.get("origin_video_key") or consumer.get("originVideoKey")
    if not isinstance(origin_key, str) or not origin_key:
        return ""
    for cdn in _ORIGIN_VIDEO_CDNS:
        url = f"{cdn}/{origin_key}"
        if _probe_origin_video_url(url):
            return url
    return ""


def _stream_urls_from_item(item: dict) -> list[str]:
    urls: list[str] = []
    master = item.get("master_url") or item.get("masterUrl")
    if isinstance(master, str) and master.startswith("http"):
        urls.append(master)
    backups = item.get("backupUrls") or item.get("backup_urls") or []
    if isinstance(backups, list):
        for backup in backups:
            if isinstance(backup, str) and backup.startswith("http"):
                urls.append(backup)
    return urls


def _video_url_from_note_card(note: dict, *, prefer_no_watermark: bool = True) -> str:
    video = note.get("video") if isinstance(note.get("video"), dict) else {}
    if not video and isinstance(note.get("media"), dict):
        video = note

    consumer = video.get("consumer") if isinstance(video.get("consumer"), dict) else {}
    if prefer_no_watermark:
        origin_url = _origin_video_url_from_consumer(consumer)
        if origin_url:
            return origin_url

    media = video.get("media") if isinstance(video.get("media"), dict) else {}
    stream = media.get("stream") if isinstance(media.get("stream"), dict) else {}

    clean_urls: list[str] = []
    watermarked_urls: list[str] = []
    for codec in ("h264", "h265", "av1"):
        variants = stream.get(codec)
        if not isinstance(variants, list):
            continue
        for item in variants:
            if not isinstance(item, dict):
                continue
            bucket = watermarked_urls if _is_watermarked_stream(item) else clean_urls
            bucket.extend(_stream_urls_from_item(item))

    if prefer_no_watermark:
        if clean_urls:
            return clean_urls[0]
        if watermarked_urls:
            return watermarked_urls[0]
    else:
        if watermarked_urls:
            return watermarked_urls[0]
        if clean_urls:
            return clean_urls[0]

    origin_key = consumer.get("origin_video_key") or consumer.get("originVideoKey")
    if isinstance(origin_key, str) and origin_key:
        return f"{random.choice(_VIDEO_CDNS)}/{origin_key}"
    return ""


def fetch_user_posted(
    user_id: str,
    *,
    cursor: str = "",
    cookies_file: str | None = None,
    international: bool = True,
    xsec_token: str = "",
    xsec_source: str = "pc_user",
) -> dict:
    login = check_rednote_login(cookies_file)
    if not login.get("ok"):
        raise RedNoteAPIError(login.get("message") or "Cookies RedNote belum login")

    cookies = _ensure_cookie_dict(cookies_dict_from_file(cookies_file))
    bundle = _api_bundle(international=international)
    referer = f"{bundle['domain']}/user/profile/{user_id}"
    params = {
        "num": "30",
        "cursor": cursor,
        "user_id": user_id,
        "image_formats": "jpg,webp,avif",
        "xsec_token": xsec_token,
        "xsec_source": xsec_source,
    }

    last_error: Exception | None = None
    for host in (bundle["host"], bundle.get("fallback_host")):
        if not host:
            continue
        try:
            data = _signed_get(
                USER_POSTED_URI,
                params,
                cookies=cookies,
                bundle=bundle,
                referer=referer,
                host=host,
            )
            return _ensure_success(data, action="scan profil")
        except RedNoteAPIError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise RedNoteAPIError("RedNote scan profil gagal")


def fetch_note_detail(
    note_id: str,
    *,
    xsec_token: str,
    xsec_source: str = "pc_feed",
    cookies_file: str | None = None,
    international: bool = True,
    user_id: str = "",
) -> dict:
    return fetch_note_detail_from_html(
        note_id,
        xsec_token=xsec_token,
        xsec_source=xsec_source,
        cookies_file=cookies_file,
        international=international,
    )


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

    note = fetch_note_detail_from_html(
        nid,
        xsec_token=xsec_token,
        xsec_source=xsec_source,
        cookies_file=cookies_file,
        international=international,
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

    raise RedNoteAPIError(
        "Gagal mengambil URL video RedNote. Pastikan cookies masih aktif (hijau di menu Cookies) "
        "dan scan ulang profil jika video lama tidak bisa di-download."
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
    url = build_note_url(
        note_id,
        xsec_token=xsec_token,
        xsec_source="pc_feed",
        international=international,
    )
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