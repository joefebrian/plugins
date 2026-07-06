"""Meta Graph API — OAuth, Page management, and video upload."""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db.models import FacebookAppConfig, FacebookPage, Video, VideoFacebookUpload

GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
GRAPH_VIDEO_BASE = f"https://graph-video.facebook.com/{GRAPH_VERSION}"
OAUTH_DIALOG_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"

FACEBOOK_SCOPES = [
    "pages_show_list",
    "pages_manage_posts",
    "pages_read_engagement",
    "publish_video",
]

_pending_oauth_states: dict[str, dict[str, Any]] = {}
STATE_TTL_SECONDS = 600


class FacebookAPIError(Exception):
    pass


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout: int = 120,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        raise FacebookAPIError(f"HTTP {e.code}: {body.decode(errors='replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise FacebookAPIError(str(e)) from e


def _graph_get(path: str, params: Optional[dict] = None) -> dict:
    query = dict(params or {})
    url = f"{GRAPH_BASE}{path}?{urllib.parse.urlencode(query)}"
    _, raw = _http_request(url)
    payload = json.loads(raw.decode())
    if "error" in payload:
        err = payload["error"]
        raise FacebookAPIError(err.get("message") or str(err))
    return payload


def _multipart_post(
    url: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
    timeout: int = 600,
) -> dict:
    boundary = f"----FbFormBoundary{secrets.token_hex(16)}"
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    for name, (filename, content, mime) in files.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode())

    _, raw = _http_request(
        url,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        data=bytes(body),
        timeout=timeout,
    )
    payload = json.loads(raw.decode())
    if "error" in payload:
        err = payload["error"]
        raise FacebookAPIError(err.get("message") or str(err))
    return payload


def _cleanup_oauth_states() -> None:
    now = time.time()
    expired = [
        key
        for key, meta in _pending_oauth_states.items()
        if now - meta.get("created_at", 0) > STATE_TTL_SECONDS
    ]
    for key in expired:
        _pending_oauth_states.pop(key, None)


def create_oauth_state(label: str = "") -> str:
    _cleanup_oauth_states()
    state = secrets.token_urlsafe(32)
    _pending_oauth_states[state] = {
        "created_at": time.time(),
        "label": label.strip(),
    }
    return state


def pop_oauth_state_meta(state: str) -> dict[str, Any]:
    _cleanup_oauth_states()
    return _pending_oauth_states.pop(state, {})


def build_auth_url(app_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": ",".join(FACEBOOK_SCOPES),
        "response_type": "code",
    }
    return f"{OAUTH_DIALOG_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    code: str,
) -> dict:
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    payload = _graph_get("/oauth/access_token", params)
    token = payload.get("access_token")
    if not token:
        raise FacebookAPIError("Facebook tidak mengembalikan access token.")
    expires_in = int(payload.get("expires_in", 3600))
    return {
        "access_token": token,
        "token_expires_at": datetime.utcnow() + timedelta(seconds=max(expires_in - 60, 300)),
    }


def exchange_long_lived_token(app_id: str, app_secret: str, short_token: str) -> dict:
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }
    payload = _graph_get("/oauth/access_token", params)
    token = payload.get("access_token")
    if not token:
        raise FacebookAPIError("Gagal exchange long-lived token.")
    expires_in = int(payload.get("expires_in", 60 * 60 * 24 * 60))
    return {
        "access_token": token,
        "token_expires_at": datetime.utcnow() + timedelta(seconds=max(expires_in - 3600, 86400)),
    }


def get_user_pages(user_access_token: str) -> list[dict]:
    payload = _graph_get(
        "/me/accounts",
        {
            "access_token": user_access_token,
            "fields": "id,name,access_token,picture{url}",
            "limit": 100,
        },
    )
    pages = []
    for item in payload.get("data") or []:
        picture = item.get("picture") or {}
        picture_data = picture.get("data") or {}
        pages.append({
            "page_id": item.get("id"),
            "page_name": item.get("name"),
            "page_access_token": item.get("access_token"),
            "page_thumbnail": picture_data.get("url"),
        })
    return pages


def upload_video_to_page(
    page: FacebookPage,
    file_path: Path,
    *,
    title: str,
    description: str = "",
    published: bool = True,
) -> dict:
    if not page.page_access_token:
        raise FacebookAPIError("Facebook Page belum terhubung (token kosong).")
    if not file_path.exists():
        raise FacebookAPIError(f"File tidak ditemukan: {file_path}")

    video_bytes = file_path.read_bytes()
    mime = "video/mp4"
    suffix = file_path.suffix.lower()
    if suffix in (".mov",):
        mime = "video/quicktime"
    elif suffix in (".webm",):
        mime = "video/webm"

    url = f"{GRAPH_VIDEO_BASE}/{page.page_id}/videos"
    payload = _multipart_post(
        url,
        fields={
            "access_token": page.page_access_token,
            "title": title[:200],
            "description": description[:5000],
            "published": "true" if published else "false",
        },
        files={"source": (file_path.name, video_bytes, mime)},
    )
    video_id = payload.get("id")
    if not video_id:
        raise FacebookAPIError("Upload selesai tapi Facebook tidak mengembalikan video ID.")

    post_url = f"https://www.facebook.com/{page.page_id}/videos/{video_id}"
    return {
        "platform_post_id": str(video_id),
        "post_url": post_url,
    }


def get_app_config(session: Session) -> Optional[FacebookAppConfig]:
    return (
        session.query(FacebookAppConfig)
        .filter_by(is_active=True)
        .order_by(FacebookAppConfig.id.asc())
        .first()
    )


def app_config_to_dict(cfg: Optional[FacebookAppConfig]) -> dict:
    if not cfg:
        return {
            "configured": False,
            "app_id": "",
            "app_secret": "",
            "redirect_uri": "",
            "label": "",
        }
    secret = cfg.app_secret or ""
    masked = f"••{secret[-4:]}" if len(secret) >= 4 else ("••••" if secret else "")
    return {
        "configured": bool(cfg.app_id and cfg.app_secret),
        "id": cfg.id,
        "label": cfg.label,
        "app_id": cfg.app_id,
        "app_secret": masked,
        "redirect_uri": cfg.redirect_uri or "",
    }


def save_app_config(session: Session, data: dict) -> FacebookAppConfig:
    cfg = get_app_config(session)
    if not cfg:
        cfg = FacebookAppConfig()
        session.add(cfg)

    for key in ("label", "app_id", "app_secret", "redirect_uri", "is_active"):
        if key in data and data[key] is not None:
            setattr(cfg, key, data[key])

    cfg.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(cfg)
    return cfg


def list_pages(session: Session) -> list[FacebookPage]:
    return (
        session.query(FacebookPage)
        .filter_by(is_active=True)
        .order_by(FacebookPage.page_name.asc(), FacebookPage.id.asc())
        .all()
    )


def get_page(session: Session, page_db_id: int) -> Optional[FacebookPage]:
    return session.query(FacebookPage).filter_by(id=page_db_id).first()


def get_page_by_fb_id(session: Session, page_id: str) -> Optional[FacebookPage]:
    return session.query(FacebookPage).filter_by(page_id=page_id).first()


def page_to_dict(page: FacebookPage) -> dict:
    return {
        "id": page.id,
        "label": page.label or page.page_name,
        "page_id": page.page_id,
        "page_name": page.page_name,
        "page_thumbnail": page.page_thumbnail,
        "connected": bool(page.page_access_token),
        "default_published": page.default_published,
        "is_active": page.is_active,
        "last_upload_at": page.last_upload_at.isoformat() if page.last_upload_at else None,
    }


def create_or_update_page(
    session: Session,
    *,
    app_config_id: Optional[int],
    page_id: str,
    page_name: str,
    page_access_token: str,
    user_access_token: str,
    page_thumbnail: Optional[str] = None,
    token_expires_at: Optional[datetime] = None,
    label: Optional[str] = None,
    default_published: bool = True,
) -> FacebookPage:
    page = get_page_by_fb_id(session, page_id)
    if page:
        page.page_name = page_name
        page.page_access_token = page_access_token
        page.user_access_token = user_access_token
        page.page_thumbnail = page_thumbnail
        page.token_expires_at = token_expires_at
        page.app_config_id = app_config_id
        if label:
            page.label = label
        page.is_active = True
    else:
        page = FacebookPage(
            app_config_id=app_config_id,
            label=label or page_name,
            page_id=page_id,
            page_name=page_name,
            page_access_token=page_access_token,
            user_access_token=user_access_token,
            page_thumbnail=page_thumbnail,
            token_expires_at=token_expires_at,
            default_published=default_published,
            is_active=True,
        )
        session.add(page)

    page.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(page)
    return page


def delete_page(session: Session, page_db_id: int) -> bool:
    page = get_page(session, page_db_id)
    if not page:
        return False
    session.query(VideoFacebookUpload).filter_by(facebook_page_id=page_db_id).delete()
    session.delete(page)
    session.commit()
    return True


def disconnect_page(session: Session, page_db_id: int) -> bool:
    page = get_page(session, page_db_id)
    if not page:
        return False
    page.page_access_token = None
    page.user_access_token = None
    page.token_expires_at = None
    page.updated_at = datetime.utcnow()
    session.commit()
    return True


def test_page_connection(session: Session, page_db_id: int) -> dict:
    page = get_page(session, page_db_id)
    if not page or not page.page_access_token:
        raise FacebookAPIError("Page belum terhubung.")

    payload = _graph_get(
        f"/{page.page_id}",
        {
            "access_token": page.page_access_token,
            "fields": "id,name,picture{url}",
        },
    )
    page.page_name = payload.get("name") or page.page_name
    picture = payload.get("picture") or {}
    picture_data = picture.get("data") or {}
    if picture_data.get("url"):
        page.page_thumbnail = picture_data["url"]
    page.updated_at = datetime.utcnow()
    session.commit()
    return {
        "page_id": page.page_id,
        "page_name": page.page_name,
        "page_thumbnail": page.page_thumbnail,
    }


def record_video_upload(
    session: Session,
    video: Video,
    page: FacebookPage,
    platform_post_id: str,
    post_url: str,
) -> VideoFacebookUpload:
    existing = (
        session.query(VideoFacebookUpload)
        .filter_by(video_id=video.id, facebook_page_id=page.id)
        .first()
    )
    now = datetime.utcnow()
    if existing:
        existing.platform_post_id = platform_post_id
        existing.post_url = post_url
        existing.uploaded_at = now
        record = existing
    else:
        record = VideoFacebookUpload(
            video_id=video.id,
            facebook_page_id=page.id,
            platform_post_id=platform_post_id,
            post_url=post_url,
            uploaded_at=now,
        )
        session.add(record)

    page.last_upload_at = now
    session.commit()
    session.refresh(record)
    return record


def video_uploaded_to_page(session: Session, video_id: int, page_db_id: int) -> bool:
    return (
        session.query(VideoFacebookUpload)
        .filter_by(video_id=video_id, facebook_page_id=page_db_id)
        .first()
        is not None
    )