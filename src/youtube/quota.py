"""OAuth grant/refresh tracking and failover between multiple Google OAuth apps."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import YouTubeAppConfig, YouTubeChannel

TOKEN_WINDOW_SECONDS = 60
DEFAULT_MINUTE_GRANT_LIMIT = 18

RATE_LIMIT_KEYWORDS = (
    "rate limit",
    "rate_limit",
    "quota",
    "too many",
    "user rate limit",
    "daily limit",
    "exceeded",
    "429",
)


def _today_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _load_token_timestamps(app: YouTubeAppConfig) -> list[float]:
    raw = app.token_calls_window or "[]"
    try:
        return [float(ts) for ts in json.loads(raw)]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _prune_token_timestamps(timestamps: list[float]) -> list[float]:
    cutoff = time.time() - TOKEN_WINDOW_SECONDS
    return [ts for ts in timestamps if ts > cutoff]


def _save_token_timestamps(app: YouTubeAppConfig, timestamps: list[float]) -> None:
    app.token_calls_window = json.dumps(timestamps)


def tokens_last_minute(app: YouTubeAppConfig) -> int:
    return len(_prune_token_timestamps(_load_token_timestamps(app)))


def seconds_until_minute_slot(app: YouTubeAppConfig) -> int:
    timestamps = _prune_token_timestamps(_load_token_timestamps(app))
    limit = app.minute_grant_limit or DEFAULT_MINUTE_GRANT_LIMIT
    if len(timestamps) < limit:
        return 0
    oldest = min(timestamps)
    return max(1, int(TOKEN_WINDOW_SECONDS - (time.time() - oldest)) + 1)


def is_minute_limit_reached(app: YouTubeAppConfig) -> bool:
    limit = app.minute_grant_limit or DEFAULT_MINUTE_GRANT_LIMIT
    return tokens_last_minute(app) >= limit


def record_token_call(session: Session, app: YouTubeAppConfig) -> None:
    timestamps = _prune_token_timestamps(_load_token_timestamps(app))
    timestamps.append(time.time())
    _save_token_timestamps(app, timestamps)
    app.updated_at = datetime.utcnow()
    session.commit()


def reset_daily_counters_if_needed(app: YouTubeAppConfig) -> None:
    today = _today_key()
    if app.usage_date != today:
        app.usage_date = today
        app.grants_today = 0
        app.refreshes_today = 0
        app.uploads_today = 0
        if app.rate_limited_until and app.rate_limited_until <= datetime.utcnow():
            app.rate_limited_until = None
            app.last_error = None


def is_rate_limit_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(keyword in lower for keyword in RATE_LIMIT_KEYWORDS)


def is_app_available(app: YouTubeAppConfig, *, for_grant: bool = False) -> bool:
    if not app.is_active or not app.client_id or not app.client_secret:
        return False
    reset_daily_counters_if_needed(app)
    if app.rate_limited_until and app.rate_limited_until > datetime.utcnow():
        return False
    if is_minute_limit_reached(app):
        return False
    if for_grant and app.grants_today >= app.daily_grant_limit:
        return False
    if not for_grant and app.refreshes_today >= app.daily_refresh_limit:
        return False
    return True


def get_app_status(app: YouTubeAppConfig) -> str:
    reset_daily_counters_if_needed(app)
    if not app.is_active:
        return "disabled"
    if app.rate_limited_until and app.rate_limited_until > datetime.utcnow():
        return "exhausted"
    if is_minute_limit_reached(app):
        return "minute_limit"
    grant_pct = app.grants_today / max(app.daily_grant_limit, 1)
    refresh_pct = app.refreshes_today / max(app.daily_refresh_limit, 1)
    minute_pct = tokens_last_minute(app) / max(app.minute_grant_limit or DEFAULT_MINUTE_GRANT_LIMIT, 1)
    if grant_pct >= 1 or refresh_pct >= 1:
        return "exhausted"
    if grant_pct >= 0.8 or refresh_pct >= 0.8 or minute_pct >= 0.8:
        return "warning"
    return "ok"


def list_oauth_apps(session: Session) -> list[YouTubeAppConfig]:
    return (
        session.query(YouTubeAppConfig)
        .order_by(YouTubeAppConfig.priority.asc(), YouTubeAppConfig.id.asc())
        .all()
    )


def get_oauth_app(session: Session, app_id: int) -> Optional[YouTubeAppConfig]:
    return session.query(YouTubeAppConfig).filter_by(id=app_id).first()


def pick_available_app(session: Session, *, for_grant: bool = False) -> Optional[YouTubeAppConfig]:
    apps = list_oauth_apps(session)
    for app in apps:
        if is_app_available(app, for_grant=for_grant):
            return app
    return None


def pick_available_channel(
    session: Session,
    *,
    exclude_channel_id: Optional[int] = None,
) -> Optional[YouTubeChannel]:
    """Pick a connected channel whose OAuth app still has token/upload quota."""
    channels = (
        session.query(YouTubeChannel)
        .filter_by(is_active=True)
        .order_by(YouTubeChannel.id.asc())
        .all()
    )
    for channel in channels:
        if exclude_channel_id and channel.id == exclude_channel_id:
            continue
        if not channel.refresh_token:
            continue
        app = get_oauth_app(session, channel.oauth_app_id) if channel.oauth_app_id else None
        if not app:
            apps = list_oauth_apps(session)
            app = apps[0] if apps else None
        if app and is_app_available(app, for_grant=False):
            return channel
    return None


def record_grant(session: Session, app: YouTubeAppConfig) -> None:
    reset_daily_counters_if_needed(app)
    app.grants_today += 1
    record_token_call(session, app)


def record_refresh(session: Session, app: YouTubeAppConfig) -> None:
    reset_daily_counters_if_needed(app)
    app.refreshes_today += 1
    record_token_call(session, app)


def record_upload(session: Session, app: YouTubeAppConfig) -> None:
    reset_daily_counters_if_needed(app)
    app.uploads_today += 1
    app.updated_at = datetime.utcnow()
    session.commit()


def mark_rate_limited(
    session: Session,
    app: YouTubeAppConfig,
    error: str,
    *,
    hours: int = 24,
    minutes: Optional[int] = None,
) -> None:
    if minutes is not None:
        delta = timedelta(minutes=minutes)
    else:
        delta = timedelta(hours=hours)
    app.rate_limited_until = datetime.utcnow() + delta
    app.last_error = error[:500]
    app.updated_at = datetime.utcnow()
    session.commit()


def mark_minute_rate_limited(session: Session, app: YouTubeAppConfig) -> None:
    limit = app.minute_grant_limit or DEFAULT_MINUTE_GRANT_LIMIT
    wait = seconds_until_minute_slot(app)
    mark_rate_limited(
        session,
        app,
        f"Token grant rate {limit}/menit tercapai — tunggu ~{wait}s atau rotate ke backup app",
        minutes=1,
    )


def clear_rate_limit(session: Session, app: YouTubeAppConfig) -> None:
    app.rate_limited_until = None
    app.last_error = None
    _save_token_timestamps(app, [])
    app.updated_at = datetime.utcnow()
    session.commit()


def app_monitoring_dict(session: Session, app: YouTubeAppConfig) -> dict:
    reset_daily_counters_if_needed(app)
    channel_count = (
        session.query(YouTubeChannel).filter_by(oauth_app_id=app.id, is_active=True).count()
    )
    secret = app.client_secret or ""
    masked_secret = f"••{secret[-4:]}" if len(secret) >= 4 else ("••••" if secret else "")
    status = get_app_status(app)
    minute_limit = app.minute_grant_limit or DEFAULT_MINUTE_GRANT_LIMIT
    tokens_min = tokens_last_minute(app)
    minute_pct = round(100 * tokens_min / max(minute_limit, 1), 1)
    wait_seconds = seconds_until_minute_slot(app) if is_minute_limit_reached(app) else 0

    return {
        "id": app.id,
        "label": app.label or f"OAuth App #{app.id}",
        "client_id": app.client_id,
        "client_secret": masked_secret,
        "redirect_uri": app.redirect_uri or "http://localhost:8080/api/youtube/oauth/callback",
        "priority": app.priority,
        "is_active": app.is_active,
        "configured": bool(app.client_id and app.client_secret),
        "grants_today": app.grants_today,
        "grants_limit": app.daily_grant_limit,
        "grants_pct": round(100 * app.grants_today / max(app.daily_grant_limit, 1), 1),
        "refreshes_today": app.refreshes_today,
        "refreshes_limit": app.daily_refresh_limit,
        "refreshes_pct": round(100 * app.refreshes_today / max(app.daily_refresh_limit, 1), 1),
        "tokens_last_minute": tokens_min,
        "minute_grant_limit": minute_limit,
        "minute_pct": minute_pct,
        "minute_resets_in": wait_seconds,
        "uploads_today": app.uploads_today,
        "channels_count": channel_count,
        "status": status,
        "usage_date": app.usage_date,
        "rate_limited_until": app.rate_limited_until.isoformat() if app.rate_limited_until else None,
        "last_error": app.last_error,
        "available_for_grant": is_app_available(app, for_grant=True),
        "available_for_upload": is_app_available(app, for_grant=False),
    }


def monitoring_overview(session: Session) -> dict:
    apps = list_oauth_apps(session)
    items = [app_monitoring_dict(session, app) for app in apps]
    available = [a for a in items if a["available_for_upload"]]
    available_grant = [a for a in items if a["available_for_grant"]]
    recommended = pick_available_app(session, for_grant=True)
    return {
        "apps": items,
        "total_apps": len(items),
        "available_apps": len(available),
        "available_grant_apps": len(available_grant),
        "minute_grant_limit_default": DEFAULT_MINUTE_GRANT_LIMIT,
        "token_window_seconds": TOKEN_WINDOW_SECONDS,
        "recommended_app_id": recommended.id if recommended else None,
        "any_available": len(available) > 0,
        "any_grant_available": len(available_grant) > 0,
    }