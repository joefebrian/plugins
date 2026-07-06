"""YouTube Data API v3 — OAuth, multi-channel, and resumable uploads."""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db.models import Video, YouTubeAppConfig, YouTubeChannel, VideoYouTubeUpload

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

_pending_oauth_states: dict[str, dict[str, Any]] = {}
STATE_TTL_SECONDS = 600


class YouTubeAPIError(Exception):
    pass


@dataclass
class YouTubeCredentials:
    client_id: str
    client_secret: str
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    redirect_uri: str = "http://localhost:8080/api/youtube/oauth/callback"


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout: int = 60,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        raise YouTubeAPIError(f"HTTP {e.code}: {body.decode(errors='replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise YouTubeAPIError(str(e)) from e


def _parse_token_response(payload: dict) -> dict:
    expires_in = int(payload.get("expires_in", 3600))
    return {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "token_expires_at": datetime.utcnow() + timedelta(seconds=max(expires_in - 60, 60)),
    }


def create_oauth_state(label: str = "", oauth_app_id: Optional[int] = None) -> str:
    _cleanup_oauth_states()
    state = secrets.token_urlsafe(32)
    _pending_oauth_states[state] = {
        "created_at": time.time(),
        "label": label.strip(),
        "oauth_app_id": oauth_app_id,
    }
    return state


def pop_oauth_state_meta(state: str) -> dict[str, Any]:
    _cleanup_oauth_states()
    return _pending_oauth_states.pop(state, {})


def validate_oauth_state(state: str) -> bool:
    meta = pop_oauth_state_meta(state)
    if not meta:
        return False
    return time.time() - meta.get("created_at", 0) <= STATE_TTL_SECONDS


def _cleanup_oauth_states():
    now = time.time()
    expired = [
        key
        for key, meta in _pending_oauth_states.items()
        if now - meta.get("created_at", 0) > STATE_TTL_SECONDS
    ]
    for key in expired:
        _pending_oauth_states.pop(key, None)


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> dict:
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    _, raw = _http_request(
        OAUTH_TOKEN_URL,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    payload = json.loads(raw.decode())
    if "error" in payload:
        raise YouTubeAPIError(payload.get("error_description") or payload["error"])
    return _parse_token_response(payload)


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    _, raw = _http_request(
        OAUTH_TOKEN_URL,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    payload = json.loads(raw.decode())
    if "error" in payload:
        raise YouTubeAPIError(payload.get("error_description") or payload["error"])
    return _parse_token_response(payload)


class YouTubeClient:
    def __init__(self, creds: YouTubeCredentials, *, on_refresh=None, on_rate_limit=None):
        self.creds = creds
        self._on_refresh = on_refresh
        self._on_rate_limit = on_rate_limit

    def _ensure_access_token(self) -> str:
        if (
            self.creds.access_token
            and self.creds.token_expires_at
            and self.creds.token_expires_at > datetime.utcnow()
        ):
            return self.creds.access_token

        if not self.creds.refresh_token:
            raise YouTubeAPIError("Channel belum terhubung. Connect akun YouTube dulu.")

        try:
            refreshed = refresh_access_token(
                self.creds.client_id,
                self.creds.client_secret,
                self.creds.refresh_token,
            )
        except YouTubeAPIError as e:
            if self._on_rate_limit:
                from .quota import is_rate_limit_error

                if is_rate_limit_error(str(e)):
                    self._on_rate_limit(str(e))
            raise
        self.creds.access_token = refreshed["access_token"]
        self.creds.token_expires_at = refreshed["token_expires_at"]
        if self._on_refresh:
            self._on_refresh()
        return self.creds.access_token

    def _api_get(self, path: str, params: Optional[dict] = None) -> dict:
        token = self._ensure_access_token()
        query = dict(params or {})
        url = f"{YOUTUBE_API_BASE}{path}?{urllib.parse.urlencode(query)}"
        _, raw = _http_request(url, headers={"Authorization": f"Bearer {token}"})
        return json.loads(raw.decode())

    def get_channel_info(self) -> dict:
        payload = self._api_get("/channels", {"part": "snippet", "mine": "true"})
        items = payload.get("items") or []
        if not items:
            raise YouTubeAPIError("Channel YouTube tidak ditemukan untuk akun ini.")
        channel = items[0]
        snippet = channel.get("snippet") or {}
        thumbs = snippet.get("thumbnails") or {}
        thumb = thumbs.get("default", {}).get("url") or thumbs.get("medium", {}).get("url")
        return {
            "channel_id": channel.get("id"),
            "channel_title": snippet.get("title"),
            "channel_thumbnail": thumb,
        }

    def upload_thumbnail(self, youtube_video_id: str, image_path: Path) -> None:
        if not image_path.exists():
            raise YouTubeAPIError(f"Thumbnail tidak ditemukan: {image_path}")

        token = self._ensure_access_token()
        image_data = image_path.read_bytes()
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        url = (
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?"
            + urllib.parse.urlencode({"videoId": youtube_video_id})
        )
        status, body = _http_request(
            url,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": mime,
                "Content-Length": str(len(image_data)),
            },
            data=image_data,
            timeout=120,
        )
        if status not in (200, 201):
            raise YouTubeAPIError(
                f"Gagal upload thumbnail (HTTP {status}): {body.decode(errors='replace')[:300]}"
            )

    def upload_video(
        self,
        file_path: Path,
        *,
        title: str,
        description: str = "",
        tags: Optional[list[str]] = None,
        privacy: str = "private",
        category_id: str = "22",
        thumbnail_path: Optional[Path] = None,
        publish_at: Optional[datetime] = None,
    ) -> dict:
        if not file_path.exists():
            raise YouTubeAPIError(f"File tidak ditemukan: {file_path}")

        token = self._ensure_access_token()
        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        if publish_at:
            pub_utc = publish_at
            if pub_utc.tzinfo is None:
                pub_utc = pub_utc.replace(tzinfo=timezone.utc)
            else:
                pub_utc = pub_utc.astimezone(timezone.utc)
            min_time = datetime.now(timezone.utc) + timedelta(minutes=15)
            if pub_utc < min_time:
                raise YouTubeAPIError(
                    "Jadwal tayang minimal 15 menit dari sekarang (aturan YouTube)."
                )
            metadata["status"]["privacyStatus"] = "private"
            metadata["status"]["publishAt"] = pub_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if tags:
            metadata["snippet"]["tags"] = [tag[:30] for tag in tags[:30]]

        params = {"uploadType": "resumable", "part": "snippet,status"}
        init_url = f"https://www.googleapis.com/upload/youtube/v3/videos?{urllib.parse.urlencode(params)}"
        init_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_path.stat().st_size),
        }
        init_req = urllib.request.Request(
            init_url,
            data=json.dumps(metadata).encode(),
            headers=init_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(init_req, timeout=60) as resp:
                upload_url = resp.headers.get("Location", "").strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace") if e.fp else ""
            raise YouTubeAPIError(f"Gagal init upload YouTube (HTTP {e.code}): {body[:300]}") from e
        except urllib.error.URLError as e:
            raise YouTubeAPIError(str(e)) from e

        if not upload_url.startswith("http"):
            raise YouTubeAPIError("Gagal memulai upload resumable ke YouTube (Location header kosong).")

        with file_path.open("rb") as video_file:
            video_data = video_file.read()

        status, body = _http_request(
            upload_url,
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(len(video_data)),
            },
            data=video_data,
            timeout=600,
        )
        if status not in (200, 201):
            raise YouTubeAPIError(f"Upload gagal (HTTP {status}): {body.decode(errors='replace')[:300]}")

        payload = json.loads(body.decode())
        video_id = payload.get("id")
        if not video_id:
            raise YouTubeAPIError("Upload selesai tapi YouTube tidak mengembalikan video ID.")

        thumb_ok = False
        if thumbnail_path and thumbnail_path.exists():
            try:
                self.upload_thumbnail(video_id, thumbnail_path)
                thumb_ok = True
            except YouTubeAPIError:
                thumb_ok = False

        return {
            "youtube_video_id": video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail_uploaded": thumb_ok,
        }


def save_oauth_app(session: Session, data: dict, app_id: Optional[int] = None) -> YouTubeAppConfig:
    if app_id:
        cfg = session.query(YouTubeAppConfig).filter_by(id=app_id).first()
        if not cfg:
            raise YouTubeAPIError("OAuth App tidak ditemukan")
    else:
        cfg = YouTubeAppConfig()
        session.add(cfg)

    for key in (
        "label",
        "client_id",
        "client_secret",
        "redirect_uri",
        "priority",
        "is_active",
        "daily_grant_limit",
        "daily_refresh_limit",
        "minute_grant_limit",
    ):
        if key in data and data[key] is not None:
            setattr(cfg, key, data[key])

    cfg.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(cfg)
    return cfg


def delete_oauth_app(session: Session, app_id: int) -> bool:
    cfg = session.query(YouTubeAppConfig).filter_by(id=app_id).first()
    if not cfg:
        return False
    linked = session.query(YouTubeChannel).filter_by(oauth_app_id=app_id).count()
    if linked:
        raise YouTubeAPIError(
            f"OAuth App masih dipakai {linked} channel. Hapus/pindahkan channel dulu."
        )
    session.delete(cfg)
    session.commit()
    return True


def list_channels(session: Session, active_only: bool = False) -> list[YouTubeChannel]:
    query = session.query(YouTubeChannel).order_by(YouTubeChannel.created_at.asc())
    if active_only:
        query = query.filter_by(is_active=True)
    return query.all()


def get_channel(session: Session, channel_db_id: int) -> Optional[YouTubeChannel]:
    return session.query(YouTubeChannel).filter_by(id=channel_db_id).first()


def get_channel_by_yt_id(session: Session, yt_channel_id: str) -> Optional[YouTubeChannel]:
    return session.query(YouTubeChannel).filter_by(channel_id=yt_channel_id).first()


def channel_to_dict(channel: YouTubeChannel) -> dict:
    oauth_label = None
    if channel.oauth_app:
        oauth_label = channel.oauth_app.label
    return {
        "id": channel.id,
        "oauth_app_id": channel.oauth_app_id,
        "oauth_app_label": oauth_label,
        "label": channel.label or channel.channel_title or f"Channel #{channel.id}",
        "channel_id": channel.channel_id,
        "channel_title": channel.channel_title,
        "channel_thumbnail": channel.channel_thumbnail,
        "connected": bool(channel.refresh_token),
        "default_privacy": channel.default_privacy,
        "default_category": channel.default_category,
        "is_active": channel.is_active,
        "last_upload_at": channel.last_upload_at.isoformat() if channel.last_upload_at else None,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
    }


def credentials_from_channel(
    app_cfg: YouTubeAppConfig,
    channel: YouTubeChannel,
) -> YouTubeCredentials:
    redirect = app_cfg.redirect_uri or "http://localhost:8080/api/youtube/oauth/callback"
    return YouTubeCredentials(
        client_id=app_cfg.client_id,
        client_secret=app_cfg.client_secret,
        refresh_token=channel.refresh_token,
        access_token=channel.access_token,
        token_expires_at=channel.token_expires_at,
        redirect_uri=redirect,
    )


def get_channel_oauth_app(session: Session, channel: YouTubeChannel) -> Optional[YouTubeAppConfig]:
    app_id = channel.oauth_app_id
    app_cfg = session.query(YouTubeAppConfig).filter_by(id=app_id).first() if app_id else None
    if not app_cfg:
        from .quota import list_oauth_apps

        apps = list_oauth_apps(session)
        app_cfg = apps[0] if apps else None
    return app_cfg


def _app_for_channel(session: Session, channel: YouTubeChannel) -> YouTubeAppConfig:
    from .quota import (
        is_app_available,
        is_minute_limit_reached,
        pick_available_channel,
        seconds_until_minute_slot,
    )

    app_cfg = get_channel_oauth_app(session, channel)
    if not app_cfg or not app_cfg.client_id or not app_cfg.client_secret:
        raise YouTubeAPIError("Google OAuth App belum dikonfigurasi.")
    if is_minute_limit_reached(app_cfg):
        wait = seconds_until_minute_slot(app_cfg)
        fallback = pick_available_channel(session, exclude_channel_id=channel.id)
        hint = f" Tunggu ~{wait}s atau pakai channel backup"
        if fallback:
            hint += f" (mis: {fallback.label or fallback.channel_title})."
        else:
            hint += " — tambah OAuth App backup di Monitoring."
        raise YouTubeAPIError(
            f"OAuth App '{app_cfg.label}' token grant rate "
            f"{app_cfg.minute_grant_limit}/menit habis.{hint}"
        )
    if not is_app_available(app_cfg, for_grant=False):
        backup_hint = " Gunakan channel yang terhubung ke OAuth App backup, atau tambah OAuth App baru."
        raise YouTubeAPIError(
            f"OAuth App '{app_cfg.label}' limit harian habis.{backup_hint}"
        )
    return app_cfg


def client_for_channel(session: Session, channel_db_id: int) -> YouTubeClient:
    from .quota import is_rate_limit_error, mark_rate_limited, record_refresh

    channel = get_channel(session, channel_db_id)
    if not channel or not channel.refresh_token:
        raise YouTubeAPIError("Channel YouTube tidak ditemukan atau belum terhubung.")

    app_cfg = _app_for_channel(session, channel)

    def on_refresh():
        record_refresh(session, app_cfg)

    def on_rate_limit(msg: str):
        from .quota import is_minute_limit_reached, mark_minute_rate_limited

        if is_minute_limit_reached(app_cfg) or "429" in msg:
            mark_minute_rate_limited(session, app_cfg)
        else:
            mark_rate_limited(session, app_cfg, msg)

    return YouTubeClient(
        credentials_from_channel(app_cfg, channel),
        on_refresh=on_refresh,
        on_rate_limit=on_rate_limit,
    )


def persist_channel_tokens(
    session: Session,
    channel: YouTubeChannel,
    client: YouTubeClient,
    extra: Optional[dict] = None,
) -> YouTubeChannel:
    channel.access_token = client.creds.access_token
    channel.token_expires_at = client.creds.token_expires_at
    if extra:
        if extra.get("refresh_token"):
            channel.refresh_token = extra["refresh_token"]
        for key in ("channel_id", "channel_title", "channel_thumbnail", "label"):
            if extra.get(key):
                setattr(channel, key, extra[key])
    channel.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(channel)
    return channel


def create_or_update_channel(
    session: Session,
    *,
    refresh_token: str,
    access_token: Optional[str],
    token_expires_at: Optional[datetime],
    channel_id: str,
    channel_title: str,
    channel_thumbnail: Optional[str],
    label: str = "",
    oauth_app_id: Optional[int] = None,
    default_privacy: str = "private",
    default_category: str = "22",
) -> YouTubeChannel:
    channel = get_channel_by_yt_id(session, channel_id)
    if channel:
        channel.refresh_token = refresh_token
        channel.access_token = access_token
        channel.token_expires_at = token_expires_at
        channel.channel_title = channel_title
        channel.channel_thumbnail = channel_thumbnail
        if label:
            channel.label = label
        if oauth_app_id:
            channel.oauth_app_id = oauth_app_id
        channel.is_active = True
    else:
        channel = YouTubeChannel(
            oauth_app_id=oauth_app_id,
            label=label or channel_title,
            refresh_token=refresh_token,
            access_token=access_token,
            token_expires_at=token_expires_at,
            channel_id=channel_id,
            channel_title=channel_title,
            channel_thumbnail=channel_thumbnail,
            default_privacy=default_privacy,
            default_category=default_category,
            is_active=True,
        )
        session.add(channel)

    channel.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(channel)
    return channel


def delete_channel(session: Session, channel_db_id: int) -> bool:
    channel = get_channel(session, channel_db_id)
    if not channel:
        return False
    session.query(VideoYouTubeUpload).filter_by(youtube_channel_id=channel_db_id).delete()
    session.delete(channel)
    session.commit()
    return True


def record_video_upload(
    session: Session,
    video: Video,
    channel: YouTubeChannel,
    youtube_video_id: str,
    youtube_url: str,
) -> VideoYouTubeUpload:
    existing = (
        session.query(VideoYouTubeUpload)
        .filter_by(video_id=video.id, youtube_channel_id=channel.id)
        .first()
    )
    now = datetime.utcnow()
    if existing:
        existing.youtube_video_id = youtube_video_id
        existing.youtube_url = youtube_url
        existing.uploaded_at = now
        record = existing
    else:
        record = VideoYouTubeUpload(
            video_id=video.id,
            youtube_channel_id=channel.id,
            youtube_video_id=youtube_video_id,
            youtube_url=youtube_url,
            uploaded_at=now,
        )
        session.add(record)

    video.youtube_video_id = youtube_video_id
    video.youtube_url = youtube_url
    video.youtube_uploaded_at = now
    channel.last_upload_at = now
    session.commit()
    session.refresh(record)
    return record


def video_uploaded_to_channel(session: Session, video_id: int, channel_db_id: int) -> bool:
    return (
        session.query(VideoYouTubeUpload)
        .filter_by(video_id=video_id, youtube_channel_id=channel_db_id)
        .first()
        is not None
    )


def render_upload_text(template: str, video: Video, profile_username: str) -> str:
    posted = video.posted_at.strftime("%Y-%m-%d") if video.posted_at else ""
    mapping = {
        "title": video.title or video.platform_video_id,
        "video_id": video.platform_video_id,
        "username": profile_username,
        "views": str(video.views or 0),
        "likes": str(video.likes or 0),
        "gmv": str(int(video.gmv or 0)),
        "commission": str(int(video.commission or 0)),
        "posted_at": posted,
        "url": video.url,
    }
    text = template
    for key, value in mapping.items():
        text = text.replace("{" + key + "}", value)
    return text.strip()