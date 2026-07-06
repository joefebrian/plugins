"""Bulk and manual video upload to Facebook Pages."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..db.models import Profile, Video
from ..services import list_videos
from ..youtube.client import render_upload_text
from .client import (
    FacebookAPIError,
    get_page,
    record_video_upload,
    upload_video_to_page,
    video_uploaded_to_page,
)


def bulk_upload_videos(
    session: Session,
    profile_id: int,
    facebook_page_id: int,
    *,
    limit: int | None = 10,
    published: bool = True,
    title_template: str = "{title}",
    description_template: str = "{url}\n\nViews: {views} | GMV: Rp {gmv} | @{username}",
    status: str | None = None,
    sort_by: str = "gmv",
    min_views: int | None = None,
    max_views: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    apply_filters: bool = False,
    skip_uploaded: bool = True,
    only_downloaded: bool = True,
    upload_delay_seconds: float = 3.0,
) -> dict:
    page = get_page(session, facebook_page_id)
    if not page or not page.is_active:
        raise FacebookAPIError("Facebook Page tidak ditemukan atau nonaktif.")
    if not page.page_access_token:
        raise FacebookAPIError(f"Page '{page.label or page.page_name}' belum terhubung.")

    profile = session.query(Profile).filter_by(id=profile_id).first()
    if not profile:
        raise ValueError("Profil tidak ditemukan")

    has_view_date_filters = any(v is not None for v in (min_views, max_views, date_from, date_to))
    has_status_filter = status in ("pending", "downloaded", "not_facebook")
    use_filters = apply_filters or has_view_date_filters or has_status_filter

    if use_filters:
        videos = list_videos(
            session,
            profile.platform,
            profile.username,
            status=None if status == "not_facebook" else status,
            sort_by=sort_by,
            min_views=min_views,
            max_views=max_views,
            date_from=date_from,
            date_to=date_to,
            facebook_page_id=facebook_page_id if status == "not_facebook" else None,
        )
        if status == "not_facebook":
            videos = [
                v
                for v in videos
                if not video_uploaded_to_page(session, v.id, facebook_page_id)
            ]
    else:
        videos = list_videos(session, profile.platform, profile.username, sort_by=sort_by)

    candidates: list[Video] = []
    for video in videos:
        if skip_uploaded and video_uploaded_to_page(session, video.id, facebook_page_id):
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
    publish = published if published is not None else page.default_published

    for index, video in enumerate(candidates):
        file_path = Path(video.file_path) if video.file_path else None
        if not file_path or not file_path.exists():
            skipped += 1
            continue

        title = render_upload_text(title_template, video, profile.username) or video.platform_video_id
        description = render_upload_text(description_template, video, profile.username)

        try:
            result = upload_video_to_page(
                page,
                file_path,
                title=title,
                description=description,
                published=publish,
            )
            record_video_upload(
                session,
                video,
                page,
                result["platform_post_id"],
                result["post_url"],
            )
            success += 1
        except Exception as e:
            failed += 1
            session.rollback()
            if len(errors) < 5:
                errors.append(f"{video.platform_video_id}: {e}")

        if index < len(candidates) - 1 and upload_delay_seconds > 0:
            time.sleep(upload_delay_seconds)

    if not candidates:
        errors.append(
            "Tidak ada video siap upload ke Page ini "
            "(harus sudah di-download & belum di-upload ke Page tersebut)"
        )

    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(candidates),
        "errors": errors,
        "page_id": page.id,
        "page_name": page.page_name or page.label,
    }


def _title_from_filename(path: Path) -> str:
    name = path.stem.replace("_", " ").replace("-", " ").strip()
    return name[:200] or path.name


def upload_manual_files(
    session: Session,
    facebook_page_id: int,
    file_paths: list[Path],
    *,
    title: str = "",
    description: str = "",
    published: bool = True,
    use_filename_as_title: bool = True,
    upload_delay_seconds: float = 3.0,
) -> dict:
    page = get_page(session, facebook_page_id)
    if not page or not page.is_active:
        raise FacebookAPIError("Facebook Page tidak ditemukan atau nonaktif.")
    if not page.page_access_token:
        raise FacebookAPIError(f"Page '{page.label or page.page_name}' belum terhubung.")

    allowed = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    candidates = [Path(p) for p in file_paths if Path(p).exists()]
    success, failed, skipped, errors, uploads = 0, 0, 0, [], []
    publish = published if published is not None else page.default_published

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
            video_title = file_path.name[:200]

        try:
            result = upload_video_to_page(
                page,
                file_path,
                title=video_title,
                description=description,
                published=publish,
            )
            success += 1
            uploads.append({
                "file": file_path.name,
                "title": video_title,
                "post_url": result["post_url"],
            })
        except Exception as e:
            failed += 1
            session.rollback()
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
        "page_id": page.id,
        "page_name": page.page_name or page.label,
    }