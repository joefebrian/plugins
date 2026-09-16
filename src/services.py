"""Core business logic for profile scanning and video management."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, time
from pathlib import Path

from sqlalchemy import case, func
from sqlalchemy.orm import Session, noload, selectinload

from .db.models import Profile, Video, VideoFacebookUpload, VideoThreadsPost, VideoYouTubeUpload
from .downloader import VideoDownloader
from .scrapers.instagram import InstagramScraper
from .scrapers.kuaishou import KuaishouScraper
from .scrapers.rednote import RedNoteScraper
from .scrapers.shopee import ShopeeScraper
from .scrapers.tiktok import TikTokScraper


def get_scraper(
    platform: str,
    cookies_file: str | None = None,
    *,
    platform_user_id: str | None = None,
):
    if platform == "tiktok":
        return TikTokScraper(cookies_file=cookies_file, platform_user_id=platform_user_id)
    scrapers = {
        "instagram": InstagramScraper,
        "kuaishou": KuaishouScraper,
        "rednote": RedNoteScraper,
        "shopee": ShopeeScraper,
    }
    if platform not in scrapers:
        raise ValueError(
            f"Platform tidak didukung: {platform}. "
            "Gunakan: tiktok, instagram, kuaishou, rednote, shopee"
        )
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
    username_norm = username
    profile = None
    # Preload profile early for TikTok platform_user_id
    if platform == "tiktok":
        from .scrapers.parse import parse_tiktok_username
        from .scrapers.tiktok_ids import resolve_tiktok_platform_user_id

        username_norm = parse_tiktok_username(username)
        profile = (
            session.query(Profile)
            .filter_by(user_id=user_id, platform=platform, username=username_norm)
            .first()
        )
        sample_url = None
        if profile:
            sample = (
                session.query(Video)
                .filter_by(profile_id=profile.id)
                .order_by(Video.id.desc())
                .first()
            )
            if sample and sample.url:
                sample_url = sample.url
        # Resolve / refresh numeric channel id when missing
        if not (profile and profile.platform_user_id):
            resolved = resolve_tiktok_platform_user_id(
                username=username_norm,
                sample_video_url=sample_url,
                cookies_file=cookies_file,
            )
            if resolved and profile:
                profile.platform_user_id = resolved
                session.commit()
            platform_user_id = resolved or (profile.platform_user_id if profile else None)
        else:
            platform_user_id = profile.platform_user_id
        scraper = get_scraper(
            platform, cookies_file, platform_user_id=platform_user_id
        )
    else:
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

    # After first videos exist, backfill platform_user_id from a video URL
    if platform == "tiktok" and not profile.platform_user_id and discovered:
        from .scrapers.tiktok_ids import resolve_tiktok_platform_user_id

        sample = discovered[0].url
        resolved = resolve_tiktok_platform_user_id(
            username=username,
            sample_video_url=sample,
            cookies_file=cookies_file,
        )
        if resolved:
            profile.platform_user_id = resolved

    new_count = 0
    updated_count = 0
    incremental = bool(known_ids)

    for info in discovered:
        if info.platform_video_id in existing:
            # Rescan: video sudah di DB — jangan tarik/update lagi.
            # But fix broken relative URLs on existing rows once
            old = existing[info.platform_video_id]
            if old.url and "://" not in old.url and info.url and "://" in info.url:
                old.url = info.url
                updated_count += 1
            continue

        # Ensure absolute TikTok video URL for downloads
        url = info.url
        if platform == "tiktok" and url and "://" not in url:
            url = f"https://www.tiktok.com/@{username}/video/{info.platform_video_id}"

        video = Video(
            profile_id=profile.id,
            platform_video_id=info.platform_video_id,
            url=url,
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
        "platform_user_id": getattr(profile, "platform_user_id", None),
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

    query = (
        session.query(Video)
        .options(
            selectinload(Video.youtube_uploads).selectinload(VideoYouTubeUpload.youtube_channel),
            selectinload(Video.facebook_uploads).selectinload(VideoFacebookUpload.facebook_page),
            noload(Video.threads_uploads),
        )
        .filter_by(profile_id=profile.id)
    )

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


def get_profiles_stats(session: Session, profiles: list[Profile]) -> dict[int, dict]:
    """One GROUP BY query for sidebar stats instead of loading every video row."""
    if not profiles:
        return {}

    ids = [p.id for p in profiles]
    downloaded_col = func.coalesce(func.sum(case((Video.is_downloaded.is_(True), 1), else_=0)), 0)
    with_gmv_col = func.coalesce(func.sum(case((Video.gmv > 0, 1), else_=0)), 0)
    rows = (
        session.query(
            Video.profile_id,
            func.count(Video.id).label("total"),
            downloaded_col.label("downloaded"),
            func.coalesce(func.sum(Video.gmv), 0).label("total_gmv"),
            func.coalesce(func.sum(Video.commission), 0).label("total_commission"),
            with_gmv_col.label("with_gmv"),
        )
        .filter(Video.profile_id.in_(ids))
        .group_by(Video.profile_id)
        .all()
    )
    by_id: dict[int, dict] = {}
    for row in rows:
        total = int(row.total or 0)
        downloaded = int(row.downloaded or 0)
        by_id[row.profile_id] = {
            "total": total,
            "downloaded": downloaded,
            "pending": total - downloaded,
            "total_gmv": float(row.total_gmv or 0),
            "total_commission": float(row.total_commission or 0),
            "with_gmv": int(row.with_gmv or 0),
        }

    out: dict[int, dict] = {}
    for profile in profiles:
        stats = by_id.get(
            profile.id,
            {
                "total": 0,
                "downloaded": 0,
                "pending": 0,
                "total_gmv": 0.0,
                "total_commission": 0.0,
                "with_gmv": 0,
            },
        ).copy()
        stats["last_scanned_at"] = profile.last_scanned_at
        out[profile.id] = stats
    return out


def get_profile_stats(session: Session, profile_id: int) -> dict:
    profile = get_profile(session, profile_id)
    if not profile:
        return {}
    return get_profiles_stats(session, [profile])[profile.id]


def profile_to_dict(profile: Profile, stats: dict | None = None) -> dict:
    data = {
        "id": profile.id,
        "folder_id": profile.folder_id,
        "platform": profile.platform,
        "username": profile.username,
        "url": profile.url,
        "platform_user_id": getattr(profile, "platform_user_id", None),
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
    has_server_file = bool(video.file_path)
    affiliate_products: list = []
    raw_products = getattr(video, "affiliate_products_json", None)
    if raw_products:
        try:
            parsed = json.loads(raw_products)
            if isinstance(parsed, list):
                affiliate_products = parsed
        except (json.JSONDecodeError, TypeError):
            affiliate_products = []

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
        "affiliate_products": affiliate_products,
        "affiliate_product_count": len(affiliate_products),
        "affiliate_scan_status": getattr(video, "affiliate_scan_status", None),
        "affiliate_scan_at": (
            video.affiliate_scan_at.isoformat()
            if getattr(video, "affiliate_scan_at", None)
            else None
        ),
        "affiliate_scan_note": getattr(video, "affiliate_scan_note", None),
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