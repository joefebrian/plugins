"""Bulk upload downloaded videos to YouTube (multi-channel)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..db.models import Profile, Video
from ..services import list_videos
from .client import (
    YouTubeAPIError,
    _app_for_channel,
    client_for_channel,
    get_channel,
    get_channel_oauth_app,
    persist_channel_tokens,
    record_video_upload,
    video_uploaded_to_channel,
    render_upload_text,
)
from .quota import is_rate_limit_error, mark_minute_rate_limited, mark_rate_limited, record_upload
from .thumbnail import generate_video_thumbnail


def _maybe_thumbnail(
    file_path: Path,
    *,
    title: str,
    views: int | None = None,
    gmv: float | None = None,
    username: str = "",
    enabled: bool,
    work_dir: Path | None = None,
) -> Path | None:
    if not enabled:
        return None
    try:
        return generate_video_thumbnail(
            file_path,
            title=title,
            subtitle=f"@{username}" if username else "",
            views=views,
            gmv=gmv,
            work_dir=work_dir,
        )
    except Exception:
        return None


def bulk_upload_videos(
    session: Session,
    profile_id: int,
    youtube_channel_id: int,
    *,
    limit: int | None = 10,
    privacy: str = "private",
    category_id: str = "22",
    title_template: str = "{title}",
    description_template: str = "Source: {url}\nViews: {views}\nGMV: {gmv}",
    tags: list[str] | None = None,
    status: str | None = None,
    sort_by: str = "gmv",
    min_views: int | None = None,
    max_views: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    apply_filters: bool = False,
    skip_uploaded: bool = True,
    only_downloaded: bool = True,
    upload_delay_seconds: float = 2.0,
    auto_thumbnail: bool = False,
    thumbnail_dir: Path | None = None,
    schedule_start: datetime | None = None,
    schedule_interval_hours: float = 3.0,
) -> dict:
    channel = get_channel(session, youtube_channel_id)
    if not channel or not channel.is_active:
        raise YouTubeAPIError("Channel YouTube tidak ditemukan atau nonaktif.")
    if not channel.refresh_token:
        raise YouTubeAPIError(f"Channel '{channel.label or channel.channel_title}' belum terhubung.")

    profile = session.query(Profile).filter_by(id=profile_id).first()
    if not profile:
        raise ValueError("Profil tidak ditemukan")

    client = client_for_channel(session, youtube_channel_id)

    has_view_date_filters = any(v is not None for v in (min_views, max_views, date_from, date_to))
    has_status_filter = status in ("pending", "downloaded", "not_youtube")
    use_filters = apply_filters or has_view_date_filters or has_status_filter

    if use_filters:
        videos = list_videos(
            session,
            profile.platform,
            profile.username,
            status=None if status == "not_youtube" else status,
            sort_by=sort_by,
            min_views=min_views,
            max_views=max_views,
            date_from=date_from,
            date_to=date_to,
            youtube_channel_id=youtube_channel_id if status == "not_youtube" else None,
        )
        if status == "not_youtube":
            videos = [
                v
                for v in videos
                if not video_uploaded_to_channel(session, v.id, youtube_channel_id)
            ]
    else:
        videos = list_videos(session, profile.platform, profile.username, sort_by=sort_by)

    candidates: list[Video] = []
    for video in videos:
        if skip_uploaded and video_uploaded_to_channel(session, video.id, youtube_channel_id):
            continue
        if only_downloaded and not video.is_downloaded:
            continue
        if only_downloaded:
            if not video.file_path or not Path(video.file_path).exists():
                continue
        candidates.append(video)

    if limit:
        candidates = candidates[:limit]

    success, failed, skipped, errors = 0, 0, 0, []
    schedule_slot = 0
    interval = max(schedule_interval_hours, 0.5)

    for index, video in enumerate(candidates):
        file_path = Path(video.file_path) if video.file_path else None
        if not file_path or not file_path.exists():
            skipped += 1
            continue

        title = render_upload_text(title_template, video, profile.username) or video.platform_video_id
        description = render_upload_text(description_template, video, profile.username)

        thumb_path = _maybe_thumbnail(
            file_path,
            title=title,
            views=video.views,
            gmv=video.gmv,
            username=profile.username,
            enabled=auto_thumbnail,
            work_dir=thumbnail_dir,
        )
        video_publish_at = None
        if schedule_start:
            video_publish_at = schedule_start + timedelta(hours=interval * schedule_slot)
            schedule_slot += 1

        try:
            result = client.upload_video(
                file_path,
                title=title,
                description=description,
                tags=tags,
                privacy="private" if video_publish_at else privacy,
                category_id=category_id,
                thumbnail_path=thumb_path,
                publish_at=video_publish_at,
            )
            if thumb_path and thumb_path.exists() and not thumbnail_dir:
                thumb_path.unlink(missing_ok=True)
            record_video_upload(
                session,
                video,
                channel,
                result["youtube_video_id"],
                result["youtube_url"],
            )
            persist_channel_tokens(session, channel, client)
            record_upload(session, _app_for_channel(session, channel))
            success += 1
        except Exception as e:
            failed += 1
            session.rollback()
            if is_rate_limit_error(str(e)):
                try:
                    app = get_channel_oauth_app(session, channel)
                    if app:
                        if "429" in str(e) or "rate" in str(e).lower():
                            mark_minute_rate_limited(session, app)
                        else:
                            mark_rate_limited(session, app, str(e))
                except Exception:
                    pass
            if len(errors) < 5:
                errors.append(f"{video.platform_video_id}: {e}")

        if index < len(candidates) - 1 and upload_delay_seconds > 0:
            time.sleep(upload_delay_seconds)

    if not candidates:
        errors.append(
            "Tidak ada video siap upload ke channel ini "
            "(harus sudah di-download & belum di-upload ke channel tersebut)"
        )

    last_publish_at = None
    if schedule_start and schedule_slot > 0:
        last_publish_at = schedule_start + timedelta(hours=interval * (schedule_slot - 1))

    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(candidates),
        "errors": errors,
        "channel_id": channel.id,
        "channel_title": channel.channel_title or channel.label,
        "scheduled": bool(schedule_start),
        "schedule_start": schedule_start.isoformat() if schedule_start else None,
        "schedule_interval_hours": interval if schedule_start else None,
        "scheduled_slots": schedule_slot,
        "schedule_last": last_publish_at.isoformat() if last_publish_at else None,
    }


def _title_from_filename(path: Path) -> str:
    name = path.stem.replace("_", " ").replace("-", " ").strip()
    return name[:100] or path.name


def upload_manual_files(
    session: Session,
    youtube_channel_id: int,
    file_paths: list[Path],
    *,
    title: str = "",
    description: str = "",
    privacy: str = "private",
    category_id: str = "22",
    tags: list[str] | None = None,
    use_filename_as_title: bool = True,
    upload_delay_seconds: float = 2.0,
    auto_thumbnail: bool = False,
    publish_at: datetime | None = None,
) -> dict:
    channel = get_channel(session, youtube_channel_id)
    if not channel or not channel.is_active:
        raise YouTubeAPIError("Channel YouTube tidak ditemukan atau nonaktif.")
    if not channel.refresh_token:
        raise YouTubeAPIError(f"Channel '{channel.label or channel.channel_title}' belum terhubung.")

    client = client_for_channel(session, youtube_channel_id)
    allowed = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}

    candidates = [Path(p) for p in file_paths if Path(p).exists()]
    success, failed, skipped, errors, uploads = 0, 0, 0, [], []

    for index, file_path in enumerate(candidates):
        if file_path.suffix.lower() not in allowed:
            skipped += 1
            if len(errors) < 5:
                errors.append(f"{file_path.name}: format tidak didukung")
            continue
        if file_path.stat().st_size < 50_000:
            skipped += 1
            if len(errors) < 5:
                errors.append(f"{file_path.name}: file terlalu kecil")
            continue

        video_title = title.strip() if title.strip() else ""
        if not video_title and use_filename_as_title:
            video_title = _title_from_filename(file_path)
        if not video_title:
            video_title = file_path.name[:100]

        thumb_path = _maybe_thumbnail(
            file_path, title=video_title, enabled=auto_thumbnail
        )
        video_publish_at = publish_at if len(candidates) == 1 else None
        try:
            result = client.upload_video(
                file_path,
                title=video_title,
                description=description,
                tags=tags,
                privacy=privacy,
                category_id=category_id,
                thumbnail_path=thumb_path,
                publish_at=video_publish_at,
            )
            if thumb_path and thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            persist_channel_tokens(session, channel, client)
            record_upload(session, _app_for_channel(session, channel))
            success += 1
            uploads.append({
                "file": file_path.name,
                "title": video_title,
                "youtube_url": result["youtube_url"],
                "thumbnail_uploaded": result.get("thumbnail_uploaded", False),
            })
        except Exception as e:
            failed += 1
            session.rollback()
            if is_rate_limit_error(str(e)):
                try:
                    app = get_channel_oauth_app(session, channel)
                    if app:
                        if "429" in str(e) or "rate" in str(e).lower():
                            mark_minute_rate_limited(session, app)
                        else:
                            mark_rate_limited(session, app, str(e))
                except Exception:
                    pass
            if len(errors) < 5:
                errors.append(f"{file_path.name}: {e}")

        if index < len(candidates) - 1 and upload_delay_seconds > 0:
            time.sleep(upload_delay_seconds)

    if not candidates:
        errors.append("Tidak ada file video valid untuk di-upload")

    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(candidates),
        "errors": errors,
        "uploads": uploads,
        "channel_id": channel.id,
        "channel_title": channel.channel_title or channel.label,
    }


def run_ab_title_test(
    session: Session,
    youtube_channel_id: int,
    *,
    video_db_id: int | None = None,
    file_path: str | None = None,
    title_variants: list[str],
    description: str = "",
    tags: list[str] | None = None,
    auto_thumbnail: bool = False,
) -> dict:
    """Upload same video with different titles as unlisted for A/B comparison."""
    from ..db.models import Video

    channel = get_channel(session, youtube_channel_id)
    if not channel or not channel.refresh_token:
        raise YouTubeAPIError("Channel belum terhubung.")

    variants = [t.strip() for t in title_variants if t and t.strip()][:4]
    if len(variants) < 2:
        raise YouTubeAPIError("A/B test butuh minimal 2 judul.")

    path: Path | None = None
    video: Video | None = None
    if video_db_id:
        video = session.query(Video).filter_by(id=video_db_id).first()
        if not video or not video.file_path:
            raise YouTubeAPIError("Video tidak ditemukan atau belum di-download.")
        path = Path(video.file_path)
    elif file_path:
        path = Path(file_path)
    else:
        raise YouTubeAPIError("Pilih video dari profil atau file path.")

    if not path or not path.exists():
        raise YouTubeAPIError("File video tidak ditemukan.")

    client = client_for_channel(session, youtube_channel_id)
    uploads = []
    errors = []

    for index, title in enumerate(variants):
        thumb_path = None
        if auto_thumbnail:
            thumb_path = _maybe_thumbnail(
                path,
                title=title,
                views=video.views if video else None,
                gmv=video.gmv if video else None,
            )
        try:
            result = client.upload_video(
                path,
                title=title[:100],
                description=description,
                tags=tags,
                privacy="unlisted",
                thumbnail_path=thumb_path,
            )
            if thumb_path and thumb_path.exists():
                thumb_path.unlink(missing_ok=True)
            persist_channel_tokens(session, channel, client)
            record_upload(session, _app_for_channel(session, channel))
            uploads.append({
                "variant": chr(65 + index),
                "title": title,
                "youtube_video_id": result["youtube_video_id"],
                "youtube_url": result["youtube_url"],
                "thumbnail_uploaded": result.get("thumbnail_uploaded", False),
            })
            if index < len(variants) - 1:
                time.sleep(3.0)
        except Exception as e:
            session.rollback()
            errors.append(f"{title[:40]}: {e}")

    return {
        "uploads": uploads,
        "errors": errors,
        "channel_title": channel.channel_title or channel.label,
        "message": "Bandingkan performa di YouTube Studio → Content → video unlisted",
    }