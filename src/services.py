"""Core business logic for profile scanning and video management."""

from __future__ import annotations

import csv
import io
from datetime import datetime, time
from pathlib import Path

from sqlalchemy.orm import Session

from .db.models import Profile, Video, VideoFacebookUpload, VideoThreadsPost, VideoYouTubeUpload
from .downloader import VideoDownloader
from .scrapers.instagram import InstagramScraper
from .scrapers.kuaishou import KuaishouScraper
from .scrapers.rednote import RedNoteScraper
from .scrapers.shopee import ShopeeScraper
from .scrapers.tiktok import TikTokScraper


def get_scraper(platform: str, cookies_file: str | None = None):
    scrapers = {
        "tiktok": TikTokScraper,
        "instagram": InstagramScraper,
        "kuaishou": KuaishouScraper,
        "rednote": RedNoteScraper,
        "shopee": ShopeeScraper,
    }
    if platform not in scrapers:
        raise ValueError(f"Platform tidak didukung: {platform}. Gunakan: tiktok, instagram, kuaishou, rednote, shopee")
    return scrapers[platform](cookies_file=cookies_file)


def get_or_create_profile(
    session: Session,
    platform: str,
    username: str,
    url: str,
    user_id: int,
) -> Profile:
    profile = (
        session.query(Profile)
        .filter_by(user_id=user_id, platform=platform, username=username)
        .first()
    )
    if profile:
        profile.url = url
        return profile

    profile = Profile(user_id=user_id, platform=platform, username=username, url=url)
    session.add(profile)
    session.commit()
    return profile


def sync_profile_videos(
    session: Session,
    platform: str,
    username: str,
    cookies_file: str | None = None,
    user_id: int | None = None,
) -> dict:
    if user_id is None:
        raise ValueError("user_id wajib untuk scan profil")
    scraper = get_scraper(platform, cookies_file)
    username = scraper.normalize_username(username)
    profile = (
        session.query(Profile)
        .filter_by(user_id=user_id, platform=platform, username=username)
        .first()
    )

    existing: dict[str, Video] = {}
    if profile:
        existing = {
            v.platform_video_id: v
            for v in session.query(Video).filter_by(profile_id=profile.id).all()
        }

    known_ids = set(existing.keys()) if existing else None
    profile_url, discovered = scraper.scan_profile(username, known_video_ids=known_ids)
    profile = get_or_create_profile(session, platform, username, profile_url, user_id)

    new_count = 0
    updated_count = 0
    incremental = bool(known_ids)

    for info in discovered:
        if info.platform_video_id in existing:
            # Rescan: video sudah di DB — jangan tarik/update lagi.
            continue

        video = Video(
            profile_id=profile.id,
            platform_video_id=info.platform_video_id,
            url=info.url,
            title=info.title,
            description=info.description,
            views=info.views,
            likes=info.likes,
            comments=info.comments,
            shares=info.shares,
            posted_at=info.posted_at,
        )
        session.add(video)
        existing[info.platform_video_id] = video
        new_count += 1

    profile.video_count = len(existing)
    profile.last_scanned_at = datetime.utcnow()
    session.commit()

    downloaded = session.query(Video).filter_by(profile_id=profile.id, is_downloaded=True).count()
    pending = profile.video_count - downloaded

    return {
        "profile": profile,
        "total": profile.video_count,
        "new": new_count,
        "updated": updated_count,
        "downloaded": downloaded,
        "pending": pending,
        "incremental": incremental,
    }


def parse_date_filter(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value[:10], "%Y-%m-%d")
        if end_of_day:
            return datetime.combine(parsed.date(), time(23, 59, 59))
        return parsed
    except ValueError:
        return None


def _apply_video_filters(
    query,
    *,
    min_views: int | None = None,
    max_views: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    if min_views is not None:
        query = query.filter(Video.views >= min_views)
    if max_views is not None:
        query = query.filter(Video.views <= max_views)
    if date_from is not None:
        query = query.filter(Video.posted_at.isnot(None), Video.posted_at >= date_from)
    if date_to is not None:
        query = query.filter(Video.posted_at.isnot(None), Video.posted_at <= date_to)
    return query


def list_videos(
    session: Session,
    platform: str,
    username: str,
    status: str | None = None,
    sort_by: str = "gmv",
    min_views: int | None = None,
    max_views: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    youtube_channel_id: int | None = None,
    facebook_page_id: int | None = None,
    threads_account_id: int | None = None,
    user_id: int | None = None,
) -> list[Video]:
    q = session.query(Profile).filter_by(platform=platform, username=username)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    profile = q.first()
    if not profile:
        return []

    query = session.query(Video).filter_by(profile_id=profile.id)

    if status == "downloaded":
        query = query.filter_by(is_downloaded=True)
    elif status == "pending":
        query = query.filter_by(is_downloaded=False)
    elif status == "not_youtube":
        if youtube_channel_id:
            uploaded_video_ids = [
                row[0]
                for row in session.query(VideoYouTubeUpload.video_id)
                .filter_by(youtube_channel_id=youtube_channel_id)
                .all()
            ]
            if uploaded_video_ids:
                query = query.filter(~Video.id.in_(uploaded_video_ids))
        else:
            query = query.filter(Video.youtube_video_id.is_(None))
    elif status == "not_facebook":
        if facebook_page_id:
            uploaded_video_ids = [
                row[0]
                for row in session.query(VideoFacebookUpload.video_id)
                .filter_by(facebook_page_id=facebook_page_id)
                .all()
            ]
            if uploaded_video_ids:
                query = query.filter(~Video.id.in_(uploaded_video_ids))
    elif status == "not_threads":
        from .db.models import VideoThreadsPost

        if threads_account_id:
            uploaded_video_ids = [
                row[0]
                for row in session.query(VideoThreadsPost.video_id)
                .filter_by(threads_account_id=threads_account_id)
                .filter(VideoThreadsPost.video_id.isnot(None))
                .all()
            ]
            if uploaded_video_ids:
                query = query.filter(~Video.id.in_(uploaded_video_ids))

    query = _apply_video_filters(
        query,
        min_views=min_views,
        max_views=max_views,
        date_from=date_from,
        date_to=date_to,
    )

    videos = query.all()

    def sort_key(v: Video):
        if sort_by == "gmv":
            return (v.gmv or 0, v.views or 0, v.likes or 0)
        if sort_by == "views":
            return (v.views or 0, v.gmv or 0)
        if sort_by == "views_asc":
            return (v.views or 0, -(v.gmv or 0))
        if sort_by == "likes":
            return (v.likes or 0, v.views or 0)
        if sort_by in ("date", "date_desc"):
            ts = v.posted_at.timestamp() if v.posted_at else (v.first_seen_at.timestamp() if v.first_seen_at else 0)
            return (ts, v.views or 0)
        if sort_by == "date_asc":
            ts = v.posted_at.timestamp() if v.posted_at else (v.first_seen_at.timestamp() if v.first_seen_at else 0)
            return (ts, -(v.views or 0))
        return (v.first_seen_at.timestamp() if v.first_seen_at else 0,)

    reverse = sort_by not in ("date_asc", "views_asc")
    return sorted(videos, key=sort_key, reverse=reverse)


def videos_to_csv(videos: list[Video]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "video_id",
        "url",
        "title",
        "upload_date",
        "youtube_url",
        "views",
        "likes",
        "comments",
        "shares",
        "gmv",
        "commission",
        "orders",
        "status",
        "downloaded_at",
    ])
    for video in videos:
        writer.writerow([
            video.platform_video_id,
            video.url,
            video.title or "",
            video.posted_at.strftime("%Y-%m-%d %H:%M") if video.posted_at else "",
            video.youtube_url or "",
            video.views if video.views is not None else "",
            video.likes if video.likes is not None else "",
            video.comments if video.comments is not None else "",
            video.shares if video.shares is not None else "",
            video.gmv if video.gmv is not None else "",
            video.commission if video.commission is not None else "",
            video.orders if video.orders is not None else "",
            "downloaded" if video.is_downloaded else "pending",
            video.downloaded_at.strftime("%Y-%m-%d %H:%M") if video.downloaded_at else "",
        ])
    return "\ufeff" + output.getvalue()


def download_videos(
    session: Session,
    platform: str,
    username: str,
    download_dir: Path,
    cookies_file: str | None = None,
    limit: int | None = None,
    only_pending: bool = True,
    video_ids: list[str] | None = None,
    quality: str = "best",
    status: str | None = None,
    sort_by: str = "gmv",
    min_views: int | None = None,
    max_views: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    apply_filters: bool = False,
    user_id: int | None = None,
) -> dict:
    q = session.query(Profile).filter_by(platform=platform, username=username)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    profile = q.first()
    if not profile:
        raise ValueError(f"Profil belum di-scan. Jalankan scan dulu: {platform}/{username}")

    has_view_date_filters = any(
        value is not None for value in (min_views, max_views, date_from, date_to)
    )
    has_status_filter = status in ("pending", "downloaded")
    use_filters = not video_ids and (
        apply_filters or has_view_date_filters or has_status_filter
    )

    if use_filters:
        videos = list_videos(
            session,
            platform,
            username,
            status=status,
            sort_by=sort_by,
            min_views=min_views,
            max_views=max_views,
            date_from=date_from,
            date_to=date_to,
            user_id=user_id,
        )
        if only_pending:
            videos = [v for v in videos if not v.is_downloaded]
    else:
        query = session.query(Video).filter_by(profile_id=profile.id)

        if video_ids:
            query = query.filter(Video.platform_video_id.in_(video_ids))
        elif only_pending:
            query = query.filter_by(is_downloaded=False)

        videos = sorted(
            query.all(),
            key=lambda v: (v.gmv or 0, v.views or 0),
            reverse=True,
        )

    if limit:
        videos = videos[:limit]

    downloader = VideoDownloader(download_dir, cookies_file=cookies_file, quality=quality)
    success, failed, skipped, errors = 0, 0, 0, []

    def _valid_existing(path: Path) -> bool:
        if not path.exists():
            return False
        if path.suffix.lower() in {".mp3", ".m4a", ".aac", ".opus", ".wav"}:
            return False
        return path.stat().st_size > 50_000

    for video in videos:
        # Already on server with a valid file → skip
        if video.is_downloaded and video.file_path and _valid_existing(Path(video.file_path)):
            skipped += 1
            continue
        # PC-only download (marked downloaded, no server file) or invalid file → save/re-save to server
        if video.is_downloaded and video.file_path:
            video.is_downloaded = False
            video.file_path = None
        try:
            downloader.download_video(
                session, video, platform, username, user_id=profile.user_id
            )
            success += 1
        except Exception as e:
            failed += 1
            if len(errors) < 3:
                errors.append(str(e))

    if not videos:
        errors.append("Tidak ada video pending untuk di-download")
    elif only_pending and success == 0 and failed == 0 and skipped > 0:
        errors.append("Semua video sudah di-download — tidak ada yang baru")

    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(videos),
        "errors": errors,
    }


def list_profiles(session: Session, user_id: int | None = None) -> list[Profile]:
    q = session.query(Profile)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.order_by(Profile.last_scanned_at.desc().nullslast()).all()


def get_profile(session: Session, profile_id: int, user_id: int | None = None) -> Profile | None:
    q = session.query(Profile).filter_by(id=profile_id)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.first()


def get_profile_stats(session: Session, profile_id: int) -> dict:
    profile = get_profile(session, profile_id)
    if not profile:
        return {}

    videos = session.query(Video).filter_by(profile_id=profile_id).all()
    downloaded = sum(1 for v in videos if v.is_downloaded)
    total_gmv = sum(v.gmv or 0 for v in videos)
    total_commission = sum(v.commission or 0 for v in videos)
    with_gmv = sum(1 for v in videos if v.gmv and v.gmv > 0)

    return {
        "total": len(videos),
        "downloaded": downloaded,
        "pending": len(videos) - downloaded,
        "total_gmv": total_gmv,
        "total_commission": total_commission,
        "with_gmv": with_gmv,
        "last_scanned_at": profile.last_scanned_at,
    }


def profile_to_dict(profile: Profile, stats: dict | None = None) -> dict:
    data = {
        "id": profile.id,
        "folder_id": profile.folder_id,
        "platform": profile.platform,
        "username": profile.username,
        "url": profile.url,
        "video_count": profile.video_count,
        "last_scanned_at": profile.last_scanned_at.isoformat() if profile.last_scanned_at else None,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }
    if stats:
        data.update(stats)
    return data


def mark_video_downloaded(session: Session, video: Video, *, file_path: str | None = None) -> Video:
    """Mark video as downloaded (PC direct and/or server save).

    - PC download: is_downloaded=True, file_path unchanged (usually None).
    - Server save: is_downloaded=True + file_path set by downloader.
    """
    video.is_downloaded = True
    if file_path is not None:
        video.file_path = file_path
    if not video.downloaded_at:
        video.downloaded_at = datetime.utcnow()
    session.commit()
    return video


def video_to_dict(video: Video) -> dict:
    has_server_file = bool(
        video.file_path and Path(video.file_path).exists() and Path(video.file_path).stat().st_size > 0
    )
    return {
        "id": video.id,
        "platform_video_id": video.platform_video_id,
        "url": video.url,
        "title": video.title,
        "description": video.description,
        "views": video.views,
        "likes": video.likes,
        "comments": video.comments,
        "shares": video.shares,
        "gmv": video.gmv,
        "commission": video.commission,
        "orders": video.orders,
        "is_downloaded": video.is_downloaded,
        "has_server_file": has_server_file,
        "download_location": "server" if has_server_file else ("pc" if video.is_downloaded else None),
        "downloaded_at": video.downloaded_at.isoformat() if video.downloaded_at else None,
        "file_path": video.file_path,
        "posted_at": video.posted_at.isoformat() if video.posted_at else None,
        "youtube_video_id": video.youtube_video_id,
        "youtube_url": video.youtube_url,
        "youtube_uploaded_at": video.youtube_uploaded_at.isoformat() if video.youtube_uploaded_at else None,
        "youtube_uploads": [
            {
                "channel_id": u.youtube_channel_id,
                "channel_title": u.youtube_channel.channel_title if u.youtube_channel else None,
                "youtube_video_id": u.youtube_video_id,
                "youtube_url": u.youtube_url,
                "uploaded_at": u.uploaded_at.isoformat() if u.uploaded_at else None,
            }
            for u in (video.youtube_uploads or [])
        ],
        "facebook_uploads": [
            {
                "page_id": u.facebook_page_id,
                "page_name": u.facebook_page.page_name if u.facebook_page else None,
                "platform_post_id": u.platform_post_id,
                "post_url": u.post_url,
                "uploaded_at": u.uploaded_at.isoformat() if u.uploaded_at else None,
            }
            for u in (video.facebook_uploads or [])
        ],
    }


def delete_profile(
    session: Session,
    profile_id: int,
    download_dir: Path,
    delete_files: bool = True,
) -> dict:
    profile = get_profile(session, profile_id)
    if not profile:
        raise ValueError("Profil tidak ditemukan")

    videos = session.query(Video).filter_by(profile_id=profile_id).all()
    file_count = 0

    if delete_files:
        for v in videos:
            if v.file_path:
                path = Path(v.file_path)
                if path.exists():
                    path.unlink()
                    file_count += 1
        # Remove empty profile download folder
        profile_dir = download_dir / profile.platform / profile.username
        if profile_dir.exists():
            try:
                profile_dir.rmdir()
            except OSError:
                pass

    video_count = len(videos)
    session.query(Video).filter_by(profile_id=profile_id).delete()
    session.delete(profile)
    session.commit()

    return {
        "deleted_profile": profile.username,
        "deleted_videos": video_count,
        "deleted_files": file_count,
    }


def _remove_video_file(video: Video) -> bool:
    if not video.file_path:
        return False
    path = Path(video.file_path)
    if not path.exists():
        return False
    path.unlink()
    return True


def _purge_video_relations(session: Session, video_id: int) -> None:
    session.query(VideoYouTubeUpload).filter_by(video_id=video_id).delete(synchronize_session=False)
    session.query(VideoFacebookUpload).filter_by(video_id=video_id).delete(synchronize_session=False)
    session.query(VideoThreadsPost).filter_by(video_id=video_id).delete(synchronize_session=False)


def _refresh_profile_video_count(session: Session, profile: Profile) -> None:
    profile.video_count = session.query(Video).filter_by(profile_id=profile.id).count()


def delete_video(
    session: Session,
    video_id: int,
    *,
    user_id: int | None = None,
    delete_file: bool = True,
) -> dict:
    video = session.query(Video).filter_by(id=video_id).first()
    if not video:
        raise ValueError("Video tidak ditemukan")

    profile = get_profile(session, video.profile_id, user_id=user_id)
    if not profile:
        raise ValueError("Video tidak ditemukan")

    platform_video_id = video.platform_video_id
    file_deleted = _remove_video_file(video) if delete_file else False
    _purge_video_relations(session, video.id)
    session.delete(video)
    _refresh_profile_video_count(session, profile)
    session.commit()

    return {
        "deleted_video_id": video_id,
        "platform_video_id": platform_video_id,
        "file_deleted": file_deleted,
        "profile_id": profile.id,
    }


def delete_videos(
    session: Session,
    profile_id: int,
    video_ids: list[int],
    *,
    user_id: int | None = None,
    delete_files: bool = True,
) -> dict:
    profile = get_profile(session, profile_id, user_id=user_id)
    if not profile:
        raise ValueError("Profil tidak ditemukan")
    if not video_ids:
        raise ValueError("Pilih video dulu")

    deleted = 0
    files_removed = 0
    for vid in video_ids:
        video = session.query(Video).filter_by(id=vid, profile_id=profile_id).first()
        if not video:
            continue
        if delete_files and _remove_video_file(video):
            files_removed += 1
        _purge_video_relations(session, video.id)
        session.delete(video)
        deleted += 1

    _refresh_profile_video_count(session, profile)
    session.commit()

    return {
        "deleted": deleted,
        "files_removed": files_removed,
        "profile_id": profile_id,
    }


def update_video_metrics(
    session: Session,
    video_db_id: int,
    gmv: float | None = None,
    commission: float | None = None,
    orders: int | None = None,
) -> Video:
    video = session.query(Video).filter_by(id=video_db_id).first()
    if not video:
        raise ValueError("Video tidak ditemukan")

    if gmv is not None:
        video.gmv = gmv
    if commission is not None:
        video.commission = commission
    if orders is not None:
        video.orders = orders

    session.commit()
    return video


def get_hero_videos(
    session: Session,
    platform: str,
    username: str,
    top_n: int = 10,
    user_id: int | None = None,
) -> list[Video]:
    """Return top performing videos by GMV (hero candidates for cross-platform)."""
    videos = list_videos(session, platform, username, sort_by="gmv", user_id=user_id)
    with_gmv = [v for v in videos if v.gmv and v.gmv > 0]
    if with_gmv:
        return with_gmv[:top_n]
    # Fallback: rank by engagement if no GMV data yet
    return sorted(
        videos,
        key=lambda v: (v.views or 0) + (v.likes or 0) * 10,
        reverse=True,
    )[:top_n]