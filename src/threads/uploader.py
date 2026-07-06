"""Post to Threads — text, video, bulk from profile."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Profile, Video
from ..services import list_videos
from ..youtube.client import render_upload_text
from .client import (
    ThreadsAPIError,
    get_account,
    publish_post,
    record_post,
    video_posted_to_account,
)
from .media import build_public_video_url
from .topics import generate_caption, TopicGenerationError


def post_text(
    session: Session,
    account_id: int,
    *,
    caption: str,
    topic_tag: Optional[str] = None,
) -> dict:
    acc = get_account(session, account_id)
    if not acc:
        raise ThreadsAPIError("Akun Threads tidak ditemukan")
    result = publish_post(acc, text=caption, media_type="TEXT", topic_tag=topic_tag)
    record_post(
        session,
        account=acc,
        platform_post_id=result["platform_post_id"],
        post_url=result["post_url"],
        caption=caption,
        media_type="TEXT",
        topic_tag=topic_tag,
    )
    return {"message": "Post Threads berhasil", **result}


def post_video(
    session: Session,
    account_id: int,
    *,
    caption: str,
    video_url: str,
    topic_tag: Optional[str] = None,
    video: Optional[Video] = None,
) -> dict:
    acc = get_account(session, account_id)
    if not acc:
        raise ThreadsAPIError("Akun Threads tidak ditemukan")
    result = publish_post(
        acc,
        text=caption,
        media_type="VIDEO",
        video_url=video_url,
        topic_tag=topic_tag,
    )
    record_post(
        session,
        account=acc,
        platform_post_id=result["platform_post_id"],
        post_url=result["post_url"],
        caption=caption,
        media_type="VIDEO",
        topic_tag=topic_tag,
        video=video,
    )
    return {"message": "Video Threads berhasil", **result}


def bulk_post_videos(
    session: Session,
    profile_id: int,
    threads_account_id: int,
    *,
    base_url: str,
    limit: int | None = 5,
    caption_template: str = "{title}\n\n{url}",
    skip_uploaded: bool = True,
    only_downloaded: bool = True,
    use_ai_caption: bool = True,
    post_delay_seconds: float = 5.0,
    status: str | None = None,
    sort_by: str = "gmv",
    apply_filters: bool = False,
    min_views: int | None = None,
    max_views: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    acc = get_account(session, threads_account_id)
    if not acc or not acc.access_token:
        raise ThreadsAPIError("Akun Threads tidak terhubung")

    profile = session.query(Profile).filter_by(id=profile_id).first()
    if not profile:
        raise ValueError("Profil tidak ditemukan")

    filter_status = None if not status or status == "all" else status
    has_filters = apply_filters or any(v is not None for v in (min_views, max_views, date_from, date_to))
    if has_filters or filter_status == "not_threads":
        videos = list_videos(
            session,
            profile.platform,
            profile.username,
            status=None if filter_status == "not_threads" else filter_status,
            sort_by=sort_by,
            min_views=min_views,
            max_views=max_views,
            date_from=date_from,
            date_to=date_to,
            threads_account_id=threads_account_id if filter_status == "not_threads" else None,
        )
        if filter_status == "not_threads":
            videos = [v for v in videos if not video_posted_to_account(session, v.id, threads_account_id)]
    else:
        videos = list_videos(session, profile.platform, profile.username, sort_by=sort_by)

    candidates: list[Video] = []
    for video in videos:
        if skip_uploaded and video_posted_to_account(session, video.id, threads_account_id):
            continue
        if only_downloaded and not video.is_downloaded:
            continue
        if not video.file_path or not Path(video.file_path).exists():
            continue
        candidates.append(video)
    if limit:
        candidates = candidates[:limit]

    success, failed, skipped, errors = 0, 0, 0, []

    for index, video in enumerate(candidates):
        caption = render_upload_text(caption_template, video, profile.username) or video.title or ""
        topic_tag = None
        if use_ai_caption:
            try:
                ai = generate_caption(
                    session,
                    topic=acc.niche or video.title or "viral tips",
                    locale=acc.voice_locale,
                    style=acc.voice_style,
                    context={
                        "title": video.title,
                        "views": video.views,
                        "username": profile.username,
                        "url": video.url,
                    },
                )
                caption = ai.get("caption") or caption
                topic_tag = ai.get("topic_tag")
            except TopicGenerationError:
                pass

        public_url = build_public_video_url(base_url, video.id)
        try:
            post_video(
                session,
                threads_account_id,
                caption=caption[:500],
                video_url=public_url,
                topic_tag=topic_tag,
                video=video,
            )
            success += 1
        except Exception as e:
            failed += 1
            session.rollback()
            if len(errors) < 5:
                errors.append(f"{video.platform_video_id}: {e}")

        if index < len(candidates) - 1 and post_delay_seconds > 0:
            time.sleep(post_delay_seconds)

    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(candidates),
        "errors": errors,
        "account": acc.username or acc.label,
    }