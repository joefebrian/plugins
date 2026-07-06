"""Auto-post runner — scheduled Threads posts per account."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Profile, ThreadsAccount, ThreadsAutoPostConfig
from .client import get_account, publish_post, record_post
from .topics import generate_topics, TopicGenerationError
from .uploader import post_video
from .media import build_public_video_url
from ..services import list_videos
from ..db.models import Video

logger = logging.getLogger(__name__)


def _today_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _reset_daily(cfg: ThreadsAutoPostConfig) -> None:
    today = _today_key()
    if cfg.usage_date != today:
        cfg.usage_date = today
        cfg.posts_today = 0


def save_autopost_config(session: Session, account_id: int, data: dict) -> ThreadsAutoPostConfig:
    acc = get_account(session, account_id)
    if not acc:
        raise ValueError("Akun tidak ditemukan")
    cfg = acc.autopost
    if not cfg:
        cfg = ThreadsAutoPostConfig(threads_account_id=account_id)
        session.add(cfg)

    for key in (
        "enabled", "interval_hours", "posts_per_day", "post_video",
        "profile_id", "topic_seed",
    ):
        if key in data and data[key] is not None:
            setattr(cfg, key, data[key])

    if cfg.enabled and not cfg.next_run_at:
        cfg.next_run_at = datetime.utcnow() + timedelta(minutes=5)

    cfg.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(cfg)
    return cfg


def run_autopost_for_account(
    session: Session,
    account_id: int,
    *,
    base_url: str,
) -> dict:
    acc = get_account(session, account_id)
    if not acc or not acc.access_token:
        return {"skipped": True, "reason": "not_connected"}

    cfg = acc.autopost
    if not cfg or not cfg.enabled:
        return {"skipped": True, "reason": "disabled"}

    _reset_daily(cfg)
    if cfg.posts_today >= cfg.posts_per_day:
        return {"skipped": True, "reason": "daily_limit"}

    now = datetime.utcnow()
    if cfg.next_run_at and cfg.next_run_at > now:
        return {"skipped": True, "reason": "not_due", "next_run_at": cfg.next_run_at.isoformat()}

    niche = cfg.topic_seed or acc.niche or "lifestyle"
    caption = ""
    topic_tag = None

    try:
        topics = generate_topics(
            session,
            niche=niche,
            locale=acc.voice_locale,
            style=acc.voice_style,
            count=3,
            access_token=acc.access_token,
            threads_user_id=acc.threads_user_id,
        )
        if topics.get("topics"):
            pick = topics["topics"][0]
            caption = pick.get("caption") or pick.get("hook") or ""
            topic_tag = pick.get("topic_tag")
    except TopicGenerationError as e:
        logger.warning("autopost AI topics failed: %s", e)
        caption = f"POV: {niche} hits different today 👀"

    video: Optional[Video] = None
    if cfg.post_video and cfg.profile_id:
        profile = session.query(Profile).filter_by(id=cfg.profile_id).first()
        if profile:
            videos = list_videos(
                session,
                profile.platform,
                profile.username,
                status="downloaded",
                sort_by="gmv",
                threads_account_id=acc.id,
            )
            for v in videos:
                if v.is_downloaded and v.file_path:
                    from .client import video_posted_to_account
                    if not video_posted_to_account(session, v.id, acc.id):
                        video = v
                        break

    try:
        if video and cfg.post_video:
            public_url = build_public_video_url(base_url, video.id)
            result = post_video(
                session,
                acc.id,
                caption=caption[:500],
                video_url=public_url,
                topic_tag=topic_tag,
                video=video,
            )
            media_type = "VIDEO"
        else:
            result = publish_post(
                acc,
                text=caption[:500],
                media_type="TEXT",
                topic_tag=topic_tag,
            )
            record_post(
                session,
                account=acc,
                platform_post_id=result["platform_post_id"],
                post_url=result["post_url"],
                caption=caption,
                media_type="TEXT",
                topic_tag=topic_tag,
            )
            media_type = "TEXT"

        cfg.posts_today += 1
        cfg.last_run_at = now
        cfg.next_run_at = now + timedelta(hours=max(cfg.interval_hours, 1))
        cfg.updated_at = now
        session.commit()

        return {
            "posted": True,
            "media_type": media_type,
            "post_url": result.get("post_url"),
            "next_run_at": cfg.next_run_at.isoformat(),
        }
    except Exception as e:
        session.rollback()
        cfg.next_run_at = now + timedelta(minutes=30)
        session.commit()
        return {"posted": False, "error": str(e)}


def run_due_autoposts(session: Session, *, base_url: str) -> list[dict]:
    accounts = session.query(ThreadsAccount).filter_by(is_active=True).all()
    results = []
    for acc in accounts:
        try:
            results.append({"account_id": acc.id, **run_autopost_for_account(session, acc.id, base_url=base_url)})
        except Exception as e:
            results.append({"account_id": acc.id, "error": str(e)})
    return results