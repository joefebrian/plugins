"""Aggregate social account metrics per user (followers, views, uploads, revenue)."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import (
    FacebookPage,
    Profile,
    ThreadsAccount,
    Video,
    VideoFacebookUpload,
    VideoThreadsPost,
    VideoYouTubeUpload,
    YouTubeChannel,
)
from ..facebook.client import FacebookAPIError, fetch_page_metrics
from ..threads.client import ThreadsAPIError, fetch_threads_profile
from ..youtube.client import YouTubeAPIError, client_for_channel

MONITORED_PLATFORMS = ("youtube", "instagram", "tiktok", "facebook", "threads", "twitter")


def _account_row(
    *,
    account_id: int | str,
    platform: str,
    name: str,
    handle: str | None = None,
    thumbnail: str | None = None,
    followers: int | None = None,
    views: int | None = None,
    uploads: int = 0,
    revenue: float | None = None,
    connected: bool = True,
    source: str = "db",
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "id": account_id,
        "platform": platform,
        "name": name,
        "handle": handle,
        "thumbnail": thumbnail,
        "followers": followers,
        "views": views,
        "uploads": uploads,
        "revenue": revenue,
        "connected": connected,
        "source": source,
        "error": error,
    }


def _totals(accounts: list[dict]) -> dict[str, Any]:
    followers = sum(a["followers"] for a in accounts if a.get("followers") is not None)
    views = sum(a["views"] for a in accounts if a.get("views") is not None)
    uploads = sum(a.get("uploads") or 0 for a in accounts)
    revenue = sum(a["revenue"] for a in accounts if a.get("revenue") is not None)
    has_followers = any(a.get("followers") is not None for a in accounts)
    has_views = any(a.get("views") is not None for a in accounts)
    has_revenue = any(a.get("revenue") is not None for a in accounts)
    return {
        "accounts": len(accounts),
        "followers": followers if has_followers else None,
        "views": views if has_views else None,
        "uploads": uploads,
        "revenue": revenue if has_revenue else None,
    }


def _profile_metrics(session: Session, user_id: int, platform: str) -> list[dict]:
    profiles = (
        session.query(Profile)
        .filter_by(user_id=user_id, platform=platform)
        .order_by(Profile.username.asc())
        .all()
    )
    rows: list[dict] = []
    for profile in profiles:
        agg = (
            session.query(
                func.count(Video.id),
                func.coalesce(func.sum(Video.views), 0),
                func.coalesce(func.sum(Video.gmv), 0.0),
                func.coalesce(func.sum(Video.commission), 0.0),
            )
            .filter_by(profile_id=profile.id)
            .one()
        )
        video_count, total_views, total_gmv, total_commission = agg
        revenue = (total_gmv or 0) + (total_commission or 0)
        rows.append(
            _account_row(
                account_id=profile.id,
                platform=platform,
                name=f"@{profile.username}",
                handle=profile.username,
                thumbnail=None,
                followers=None,
                views=int(total_views) if total_views else None,
                uploads=int(video_count or profile.video_count or 0),
                revenue=revenue if revenue > 0 else None,
                connected=True,
                source="db",
            )
        )
    return rows


def _youtube_metrics(session: Session, user_id: int, *, live: bool = True) -> list[dict]:
    channels = (
        session.query(YouTubeChannel)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(YouTubeChannel.channel_title.asc(), YouTubeChannel.id.asc())
        .all()
    )
    rows: list[dict] = []
    for ch in channels:
        uploads = (
            session.query(func.count(VideoYouTubeUpload.id))
            .filter_by(youtube_channel_id=ch.id)
            .scalar()
            or 0
        )
        followers = views = video_count = None
        source = "db"
        error = None
        connected = bool(ch.refresh_token)

        if live and connected:
            try:
                client = client_for_channel(session, ch.id)
                stats = client.get_channel_statistics(ch.channel_id)
                followers = stats.get("subscribers")
                views = stats.get("views")
                video_count = stats.get("video_count")
                source = "api"
            except YouTubeAPIError as e:
                error = str(e)

        rows.append(
            _account_row(
                account_id=ch.id,
                platform="youtube",
                name=ch.label or ch.channel_title or f"Channel #{ch.id}",
                handle=ch.channel_id,
                thumbnail=ch.channel_thumbnail,
                followers=followers,
                views=views,
                uploads=int(uploads or video_count or 0),
                revenue=None,
                connected=connected,
                source=source,
                error=error,
            )
        )
    return rows


def _facebook_metrics(session: Session, user_id: int, *, live: bool = True) -> list[dict]:
    pages = (
        session.query(FacebookPage)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(FacebookPage.page_name.asc(), FacebookPage.id.asc())
        .all()
    )
    rows: list[dict] = []
    for page in pages:
        uploads = (
            session.query(func.count(VideoFacebookUpload.id))
            .filter_by(facebook_page_id=page.id)
            .scalar()
            or 0
        )
        followers = None
        source = "db"
        error = None
        connected = bool(page.page_access_token)

        if live and connected and page.page_access_token:
            try:
                metrics = fetch_page_metrics(page.page_id, page.page_access_token)
                followers = metrics.get("followers_count") or metrics.get("fan_count")
                source = "api"
            except FacebookAPIError as e:
                error = str(e)

        rows.append(
            _account_row(
                account_id=page.id,
                platform="facebook",
                name=page.label or page.page_name or f"Page #{page.id}",
                handle=page.page_id,
                thumbnail=page.page_thumbnail,
                followers=int(followers) if followers is not None else None,
                views=None,
                uploads=int(uploads),
                revenue=None,
                connected=connected,
                source=source,
                error=error,
            )
        )
    return rows


def _threads_metrics(session: Session, user_id: int, *, live: bool = True) -> list[dict]:
    accounts = (
        session.query(ThreadsAccount)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(ThreadsAccount.username.asc(), ThreadsAccount.id.asc())
        .all()
    )
    rows: list[dict] = []
    for acc in accounts:
        uploads = (
            session.query(func.count(VideoThreadsPost.id))
            .filter_by(threads_account_id=acc.id)
            .scalar()
            or 0
        )
        source = "db"
        error = None
        connected = bool(acc.access_token)
        handle = f"@{acc.username}" if acc.username else acc.threads_user_id

        if live and connected and acc.access_token:
            try:
                fetch_threads_profile(acc.access_token)
                source = "api"
            except ThreadsAPIError as e:
                error = str(e)

        rows.append(
            _account_row(
                account_id=acc.id,
                platform="threads",
                name=acc.label or handle,
                handle=handle,
                thumbnail=acc.profile_picture,
                followers=None,
                views=None,
                uploads=int(uploads),
                revenue=None,
                connected=connected,
                source=source,
                error=error,
            )
        )
    return rows


def _twitter_metrics() -> list[dict]:
    return []


def platform_metrics(
    session: Session,
    user_id: int,
    platform: str,
    *,
    live: bool = True,
) -> dict[str, Any]:
    platform = platform.lower().strip()
    if platform not in MONITORED_PLATFORMS:
        raise ValueError(f"Platform tidak didukung: {platform}")

    if platform == "twitter":
        accounts = _twitter_metrics()
        return {
            "platform": platform,
            "coming_soon": True,
            "accounts": accounts,
            "totals": _totals(accounts),
        }

    fetchers = {
        "youtube": lambda: _youtube_metrics(session, user_id, live=live),
        "instagram": lambda: _profile_metrics(session, user_id, "instagram"),
        "tiktok": lambda: _profile_metrics(session, user_id, "tiktok"),
        "facebook": lambda: _facebook_metrics(session, user_id, live=live),
        "threads": lambda: _threads_metrics(session, user_id, live=live),
    }
    accounts = fetchers[platform]()
    return {
        "platform": platform,
        "coming_soon": False,
        "accounts": accounts,
        "totals": _totals(accounts),
    }


def monitoring_overview(session: Session, user_id: int, *, live: bool = True) -> dict[str, Any]:
    platforms: dict[str, dict] = {}
    all_accounts: list[dict] = []

    for platform in MONITORED_PLATFORMS:
        data = platform_metrics(session, user_id, platform, live=live)
        platforms[platform] = {
            "coming_soon": data.get("coming_soon", False),
            "totals": data["totals"],
            "account_count": len(data["accounts"]),
        }
        all_accounts.extend(data["accounts"])

    return {
        "platforms": platforms,
        "totals": _totals(all_accounts),
        "accounts": all_accounts,
    }