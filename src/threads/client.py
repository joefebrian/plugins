"""Meta Threads API — OAuth, multi-account, publish posts."""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db.models import ThreadsAccount, ThreadsAutoPostConfig, Video, VideoThreadsPost
from ..facebook.client import (
    FacebookAPIError,
    exchange_code_for_token,
    exchange_long_lived_token,
    get_app_config,
)

THREADS_VERSION = "v1.0"
THREADS_BASE = f"https://graph.threads.net/{THREADS_VERSION}"
GRAPH_VERSION = "v21.0"
OAUTH_DIALOG_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"

THREADS_SCOPES = [
    "threads_basic",
    "threads_content_publish",
]

_pending_oauth_states: dict[str, dict[str, Any]] = {}
STATE_TTL_SECONDS = 600


class ThreadsAPIError(Exception):
    pass


def _http_request(
    url: str,
    *,
    method: str = "GET",
    data: Optional[dict[str, str]] = None,
    timeout: int = 120,
) -> dict:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace") if e.fp else ""
        raise ThreadsAPIError(f"HTTP {e.code}: {err_body[:500]}") from e
    except urllib.error.URLError as e:
        raise ThreadsAPIError(str(e)) from e
    if "error" in payload:
        err = payload["error"]
        raise ThreadsAPIError(err.get("message") or str(err))
    return payload


def _threads_get(path: str, access_token: str, params: Optional[dict] = None) -> dict:
    query = dict(params or {})
    query["access_token"] = access_token
    url = f"{THREADS_BASE}{path}?{urllib.parse.urlencode(query)}"
    return _http_request(url)


def _threads_post(path: str, access_token: str, fields: dict[str, str]) -> dict:
    fields = dict(fields)
    fields["access_token"] = access_token
    url = f"{THREADS_BASE}{path}"
    return _http_request(url, method="POST", data=fields)


def _cleanup_oauth_states() -> None:
    now = time.time()
    expired = [
        k for k, m in _pending_oauth_states.items()
        if now - m.get("created_at", 0) > STATE_TTL_SECONDS
    ]
    for k in expired:
        _pending_oauth_states.pop(k, None)


def create_oauth_state(label: str = "", user_id: int | None = None) -> str:
    _cleanup_oauth_states()
    state = secrets.token_urlsafe(32)
    _pending_oauth_states[state] = {
        "created_at": time.time(),
        "label": label.strip(),
        "user_id": user_id,
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
        "scope": ",".join(THREADS_SCOPES),
        "response_type": "code",
    }
    return f"{OAUTH_DIALOG_URL}?{urllib.parse.urlencode(params)}"


def fetch_threads_profile(access_token: str) -> dict:
    payload = _threads_get(
        "/me",
        access_token,
        {"fields": "id,username,threads_profile_picture_url"},
    )
    user_id = payload.get("id")
    if not user_id:
        raise ThreadsAPIError("Threads profile tidak ditemukan. Pastikan akun IG Pro terhubung ke Threads.")
    return {
        "threads_user_id": str(user_id),
        "username": payload.get("username"),
        "profile_picture": payload.get("threads_profile_picture_url"),
    }


def create_or_update_account(
    session: Session,
    *,
    app_config_id: Optional[int],
    threads_user_id: str,
    username: Optional[str],
    access_token: str,
    token_expires_at: Optional[datetime],
    profile_picture: Optional[str] = None,
    label: Optional[str] = None,
    user_id: Optional[int] = None,
) -> ThreadsAccount:
    acc = session.query(ThreadsAccount).filter_by(threads_user_id=threads_user_id).first()
    if acc and user_id is not None and acc.user_id not in (None, user_id):
        acc = None
    if not acc:
        acc = ThreadsAccount(threads_user_id=threads_user_id, user_id=user_id)
        session.add(acc)

    acc.app_config_id = app_config_id
    acc.username = username
    acc.access_token = access_token
    acc.token_expires_at = token_expires_at
    acc.profile_picture = profile_picture
    if label:
        acc.label = label
    elif username and not acc.label:
        acc.label = f"@{username}"
    if user_id is not None:
        acc.user_id = user_id
    acc.is_active = True
    acc.updated_at = datetime.utcnow()
    session.flush()

    if not acc.autopost:
        cfg = ThreadsAutoPostConfig(threads_account_id=acc.id)
        session.add(cfg)

    session.commit()
    session.refresh(acc)
    return acc


def list_accounts(session: Session, user_id: int | None = None) -> list[ThreadsAccount]:
    q = session.query(ThreadsAccount).filter_by(is_active=True)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.order_by(ThreadsAccount.username.asc(), ThreadsAccount.id.asc()).all()


def get_account(session: Session, account_id: int) -> Optional[ThreadsAccount]:
    return session.query(ThreadsAccount).filter_by(id=account_id).first()


def account_to_dict(acc: ThreadsAccount) -> dict:
    ap = acc.autopost
    return {
        "id": acc.id,
        "label": acc.label,
        "username": acc.username,
        "threads_user_id": acc.threads_user_id,
        "profile_picture": acc.profile_picture,
        "connected": bool(acc.access_token),
        "voice_locale": acc.voice_locale,
        "voice_style": acc.voice_style,
        "niche": acc.niche,
        "last_post_at": acc.last_post_at.isoformat() if acc.last_post_at else None,
        "autopost": {
            "enabled": ap.enabled if ap else False,
            "interval_hours": ap.interval_hours if ap else 4,
            "posts_per_day": ap.posts_per_day if ap else 6,
            "posts_today": ap.posts_today if ap else 0,
            "post_video": ap.post_video if ap else True,
            "profile_id": ap.profile_id if ap else None,
            "topic_seed": ap.topic_seed if ap else None,
            "next_run_at": ap.next_run_at.isoformat() if ap and ap.next_run_at else None,
        } if ap else None,
    }


def delete_account(session: Session, account_id: int) -> bool:
    acc = get_account(session, account_id)
    if not acc:
        return False
    session.delete(acc)
    session.commit()
    return True


def disconnect_account(session: Session, account_id: int) -> bool:
    acc = get_account(session, account_id)
    if not acc:
        return False
    acc.access_token = None
    acc.token_expires_at = None
    acc.updated_at = datetime.utcnow()
    session.commit()
    return True


def test_connection(session: Session, account_id: int) -> dict:
    acc = get_account(session, account_id)
    if not acc or not acc.access_token:
        raise ThreadsAPIError("Akun belum terhubung")
    profile = fetch_threads_profile(acc.access_token)
    return {
        "username": profile.get("username"),
        "threads_user_id": profile.get("threads_user_id"),
    }


def publish_post(
    acc: ThreadsAccount,
    *,
    text: str,
    media_type: str = "TEXT",
    video_url: Optional[str] = None,
    topic_tag: Optional[str] = None,
    wait_seconds: int = 35,
) -> dict:
    if not acc.access_token:
        raise ThreadsAPIError("Token Threads kosong")
    if not text.strip() and media_type == "TEXT":
        raise ThreadsAPIError("Caption tidak boleh kosong")

    fields: dict[str, str] = {
        "media_type": media_type,
        "text": (text or "")[:500],
    }
    if topic_tag:
        clean = topic_tag.strip().lstrip("#")[:50]
        if clean and "." not in clean and "&" not in clean:
            fields["topic_tag"] = clean
    if media_type == "VIDEO":
        if not video_url:
            raise ThreadsAPIError("Video URL wajib untuk post VIDEO")
        fields["video_url"] = video_url

    container = _threads_post(f"/{acc.threads_user_id}/threads", acc.access_token, fields)
    creation_id = container.get("id")
    if not creation_id:
        raise ThreadsAPIError("Gagal membuat media container Threads")

    time.sleep(max(wait_seconds, 30))

    published = _threads_post(
        f"/{acc.threads_user_id}/threads_publish",
        acc.access_token,
        {"creation_id": str(creation_id)},
    )
    post_id = published.get("id")
    if not post_id:
        raise ThreadsAPIError("Publish Threads gagal — coba lagi")

    username = acc.username or acc.threads_user_id
    post_url = f"https://www.threads.net/@{username}/post/{post_id}"
    return {
        "platform_post_id": str(post_id),
        "post_url": post_url,
        "creation_id": str(creation_id),
    }


def record_post(
    session: Session,
    *,
    account: ThreadsAccount,
    platform_post_id: str,
    post_url: str,
    caption: str,
    media_type: str = "TEXT",
    topic_tag: Optional[str] = None,
    video: Optional[Video] = None,
) -> VideoThreadsPost:
    row = VideoThreadsPost(
        video_id=video.id if video else None,
        threads_account_id=account.id,
        platform_post_id=platform_post_id,
        post_url=post_url,
        caption=caption[:2000] if caption else None,
        topic_tag=topic_tag,
        media_type=media_type,
    )
    session.add(row)
    account.last_post_at = datetime.utcnow()
    account.updated_at = datetime.utcnow()
    session.commit()
    return row


def video_posted_to_account(session: Session, video_id: int, account_id: int) -> bool:
    return (
        session.query(VideoThreadsPost)
        .filter_by(video_id=video_id, threads_account_id=account_id)
        .first()
        is not None
    )