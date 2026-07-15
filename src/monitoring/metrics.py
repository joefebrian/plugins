"""Fetch and cache live metrics for monitoring accounts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..db.models import MonitoringAccount
from ..facebook.client import FacebookAPIError, fetch_page_metrics
from .scan import scan_username_metrics
from ..threads.client import ThreadsAPIError, fetch_threads_profile
from ..youtube.client import (
    YouTubeAPIError,
    YouTubeChannel,
    YouTubeClient,
    credentials_from_channel,
)
from ..youtube.quota import get_oauth_app, list_oauth_apps, pick_available_app
from .accounts import update_metrics
from .twitter_client import TwitterAPIError, fetch_user_metrics, get_twitter_config, refresh_access_token


def _youtube_client_for_account(session: Session, acc: MonitoringAccount) -> YouTubeClient:
    app_cfg = None
    if acc.oauth_app_id:
        app_cfg = get_oauth_app(session, acc.oauth_app_id)
    if not app_cfg:
        app_cfg = pick_available_app(session, for_grant=False) or (list_oauth_apps(session) or [None])[0]
    if not app_cfg or not app_cfg.client_id:
        raise YouTubeAPIError("Google OAuth App belum dikonfigurasi untuk monitoring YouTube.")

    temp = YouTubeChannel(
        refresh_token=acc.refresh_token,
        access_token=acc.access_token,
        token_expires_at=acc.token_expires_at,
    )

    def on_refresh():
        acc.access_token = temp.access_token
        acc.token_expires_at = temp.token_expires_at
        acc.updated_at = datetime.utcnow()
        session.commit()

    client = YouTubeClient(credentials_from_channel(app_cfg, temp), on_refresh=on_refresh)
    return client


def refresh_account_metrics(
    session: Session,
    acc: MonitoringAccount,
    *,
    cookies_file: str | None = None,
) -> MonitoringAccount:
    error = None
    try:
        if acc.platform == "youtube":
            client = _youtube_client_for_account(session, acc)
            stats = client.get_channel_statistics(acc.external_id)
            return update_metrics(
                session,
                acc,
                followers=stats.get("subscribers"),
                views=stats.get("views"),
                uploads_count=stats.get("video_count"),
            )

        if acc.platform == "facebook":
            if not acc.access_token:
                raise FacebookAPIError("Token Facebook kosong.")
            metrics = fetch_page_metrics(acc.external_id, acc.access_token)
            followers = metrics.get("followers_count") or metrics.get("fan_count")
            return update_metrics(session, acc, followers=int(followers) if followers is not None else None)

        if acc.platform == "threads":
            if not acc.access_token:
                raise ThreadsAPIError("Token Threads kosong.")
            profile = fetch_threads_profile(acc.access_token)
            acc.name = acc.name or profile.get("username")
            acc.handle = acc.handle or (f"@{profile['username']}" if profile.get("username") else None)
            acc.thumbnail = acc.thumbnail or profile.get("profile_picture")
            session.commit()
            return update_metrics(session, acc)

        if acc.platform == "twitter":
            token = acc.access_token
            cfg = get_twitter_config(session)
            if (
                cfg
                and acc.refresh_token
                and acc.token_expires_at
                and acc.token_expires_at < datetime.utcnow()
            ):
                refreshed = refresh_access_token(cfg.client_id, cfg.client_secret, acc.refresh_token)
                acc.access_token = refreshed["access_token"]
                acc.refresh_token = refreshed.get("refresh_token") or acc.refresh_token
                acc.token_expires_at = refreshed["token_expires_at"]
                session.commit()
                token = acc.access_token
            if not token:
                raise TwitterAPIError("Token X kosong.")
            data = fetch_user_metrics(token)
            return update_metrics(
                session,
                acc,
                followers=data.get("followers"),
                views=data.get("views"),
                uploads_count=data.get("uploads_count"),
            )

        if acc.platform in ("tiktok", "instagram", "kuaishou", "rednote"):
            data = scan_username_metrics(
                acc.platform,
                acc.handle or acc.external_id,
                cookies_file,
            )
            return update_metrics(
                session,
                acc,
                views=data.get("views"),
                uploads_count=data.get("uploads_count"),
                revenue=data.get("revenue"),
            )

    except (YouTubeAPIError, FacebookAPIError, ThreadsAPIError, TwitterAPIError, ValueError) as e:
        error = str(e)
    except Exception as e:
        error = str(e)

    return update_metrics(session, acc, error=error)