"""CRUD helpers for monitoring accounts."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import MonitoringAccount


def list_accounts(
    session: Session,
    user_id: int,
    platform: str | None = None,
) -> list[MonitoringAccount]:
    q = session.query(MonitoringAccount).filter_by(user_id=user_id, is_active=True)
    if platform:
        q = q.filter_by(platform=platform)
    return q.order_by(MonitoringAccount.name.asc(), MonitoringAccount.id.asc()).all()


def get_account(session: Session, account_id: int, user_id: int) -> MonitoringAccount | None:
    return (
        session.query(MonitoringAccount)
        .filter_by(id=account_id, user_id=user_id, is_active=True)
        .first()
    )


def get_by_external(
    session: Session,
    user_id: int,
    platform: str,
    external_id: str,
) -> MonitoringAccount | None:
    return (
        session.query(MonitoringAccount)
        .filter_by(user_id=user_id, platform=platform, external_id=external_id)
        .first()
    )


def upsert_account(
    session: Session,
    *,
    user_id: int,
    platform: str,
    external_id: str,
    label: str | None = None,
    name: str | None = None,
    handle: str | None = None,
    thumbnail: str | None = None,
    profile_url: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_expires_at: datetime | None = None,
    oauth_app_id: int | None = None,
) -> MonitoringAccount:
    acc = get_by_external(session, user_id, platform, external_id)
    if not acc:
        acc = MonitoringAccount(user_id=user_id, platform=platform, external_id=external_id)
        session.add(acc)

    if label:
        acc.label = label
    if name:
        acc.name = name
    if handle:
        acc.handle = handle
    if thumbnail:
        acc.thumbnail = thumbnail
    if profile_url:
        acc.profile_url = profile_url
    if access_token is not None:
        acc.access_token = access_token
    if refresh_token is not None:
        acc.refresh_token = refresh_token
    if token_expires_at is not None:
        acc.token_expires_at = token_expires_at
    if oauth_app_id is not None:
        acc.oauth_app_id = oauth_app_id

    acc.is_active = True
    acc.last_error = None
    acc.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(acc)
    return acc


def update_metrics(
    session: Session,
    acc: MonitoringAccount,
    *,
    followers: int | None = None,
    views: int | None = None,
    uploads_count: int | None = None,
    revenue: float | None = None,
    error: str | None = None,
) -> MonitoringAccount:
    if followers is not None:
        acc.followers = followers
    if views is not None:
        acc.views = views
    if uploads_count is not None:
        acc.uploads_count = uploads_count
    if revenue is not None:
        acc.revenue = revenue
    acc.metrics_updated_at = datetime.utcnow()
    acc.last_error = error
    acc.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(acc)
    return acc


def delete_account(session: Session, account_id: int, user_id: int) -> bool:
    acc = get_account(session, account_id, user_id)
    if not acc:
        return False
    acc.is_active = False
    acc.access_token = None
    acc.refresh_token = None
    acc.updated_at = datetime.utcnow()
    session.commit()
    return True


def account_to_dict(acc: MonitoringAccount) -> dict:
    return {
        "id": acc.id,
        "platform": acc.platform,
        "external_id": acc.external_id,
        "label": acc.label or acc.name,
        "name": acc.name,
        "handle": acc.handle,
        "thumbnail": acc.thumbnail,
        "profile_url": acc.profile_url,
        "connected": bool(acc.access_token or acc.platform in ("tiktok", "instagram", "kuaishou")),
        "followers": acc.followers,
        "views": acc.views,
        "uploads": acc.uploads_count,
        "revenue": acc.revenue,
        "metrics_updated_at": acc.metrics_updated_at.isoformat() if acc.metrics_updated_at else None,
        "last_error": acc.last_error,
        "source": "cache" if acc.metrics_updated_at else "db",
    }