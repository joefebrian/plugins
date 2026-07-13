"""X / Twitter OAuth 2.0 PKCE and user metrics."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import MonitoringPlatformConfig

TWITTER_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TWITTER_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
TWITTER_API_BASE = "https://api.twitter.com/2"
TWITTER_SCOPES = ["tweet.read", "users.read", "offline.access"]


class TwitterAPIError(Exception):
    pass


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
        raise TwitterAPIError(f"HTTP {e.code}: {body.decode(errors='replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise TwitterAPIError(str(e)) from e


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def get_twitter_config(session: Session) -> MonitoringPlatformConfig | None:
    cfg = session.query(MonitoringPlatformConfig).filter_by(platform="twitter", is_active=True).first()
    if cfg and cfg.client_id and cfg.client_secret:
        return cfg
    env_id = os.getenv("TWITTER_CLIENT_ID", "").strip()
    env_secret = os.getenv("TWITTER_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        if not cfg:
            cfg = MonitoringPlatformConfig(platform="twitter", client_id=env_id, client_secret=env_secret)
            session.add(cfg)
            session.commit()
            session.refresh(cfg)
        else:
            cfg.client_id = env_id
            cfg.client_secret = env_secret
            session.commit()
        return cfg
    return cfg if cfg and cfg.client_id else None


def config_to_dict(cfg: MonitoringPlatformConfig | None) -> dict:
    if not cfg:
        return {"configured": False, "client_id": "", "client_secret": "", "redirect_uri": ""}
    secret = cfg.client_secret or ""
    masked = ("••" + secret[-4:]) if len(secret) > 4 else ("••" if secret else "")
    return {
        "configured": bool(cfg.client_id and cfg.client_secret),
        "client_id": cfg.client_id,
        "client_secret": masked,
        "redirect_uri": cfg.redirect_uri or "",
    }


def save_twitter_config(session: Session, data: dict) -> MonitoringPlatformConfig:
    cfg = session.query(MonitoringPlatformConfig).filter_by(platform="twitter").first()
    if not cfg:
        cfg = MonitoringPlatformConfig(platform="twitter")
        session.add(cfg)
    if data.get("client_id"):
        cfg.client_id = data["client_id"]
    secret = data.get("client_secret")
    if secret and not str(secret).startswith("••"):
        cfg.client_secret = secret
    if "redirect_uri" in data:
        cfg.redirect_uri = data.get("redirect_uri")
    if "is_active" in data:
        cfg.is_active = bool(data["is_active"])
    cfg.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(cfg)
    return cfg


def build_auth_url(client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(TWITTER_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{TWITTER_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": client_id,
    }).encode()
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    _, raw = _http_request(
        TWITTER_TOKEN_URL,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
        data=body,
    )
    payload = json.loads(raw.decode())
    if "error" in payload:
        raise TwitterAPIError(payload.get("error_description") or payload["error"])
    expires_in = int(payload.get("expires_in", 7200))
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "token_expires_at": datetime.utcnow() + timedelta(seconds=max(expires_in - 120, 300)),
    }


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode()
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    _, raw = _http_request(
        TWITTER_TOKEN_URL,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
        data=body,
    )
    payload = json.loads(raw.decode())
    if "error" in payload:
        raise TwitterAPIError(payload.get("error_description") or payload["error"])
    expires_in = int(payload.get("expires_in", 7200))
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or refresh_token,
        "token_expires_at": datetime.utcnow() + timedelta(seconds=max(expires_in - 120, 300)),
    }


def fetch_user_metrics(access_token: str) -> dict:
    params = urllib.parse.urlencode({
        "user.fields": "public_metrics,profile_image_url,username,name",
    })
    url = f"{TWITTER_API_BASE}/users/me?{params}"
    _, raw = _http_request(url, headers={"Authorization": f"Bearer {access_token}"})
    payload = json.loads(raw.decode())
    data = payload.get("data") or {}
    metrics = data.get("public_metrics") or {}
    user_id = data.get("id")
    username = data.get("username")
    return {
        "external_id": str(user_id) if user_id else "",
        "name": data.get("name") or username,
        "handle": f"@{username}" if username else None,
        "thumbnail": data.get("profile_image_url"),
        "profile_url": f"https://x.com/{username}" if username else None,
        "followers": metrics.get("followers_count"),
        "views": None,
        "uploads_count": metrics.get("tweet_count"),
    }