"""Core business logic for profile scanning and video management."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .db.models import Profile, Video
from .downloader import VideoDownloader
from .scrapers.instagram import InstagramScraper
from .scrapers.tiktok import TikTokScraper


def get_scraper(platform: str, cookies_file: str | None = None):
    scrapers = {
        "tiktok": TikTokScraper,
        "instagram": InstagramScraper,
    }
    if platform not in scrapers:
        raise ValueError(f"Platform tidak didukung: {platform}. Gunakan: tiktok, instagram")
    return scrapers[platform](cookies_file=cookies_file)


def get_or_create_profile(
    session: Session, platform: str, username: str, url: str
) -> Profile:
    profile = (
        session.query(Profile)
        .filter_by(platform=platform, username=username)
        .first()
    )
    if profile:
        profile.url = url
        return profile

    profile = Profile(platform=platform, username=username, url=url)
    session.add(profile)
    session.commit()
    return profile


def sync_profile_videos(
    session: Session,
    platform: str,
    username: str,
    cookies_file: str | None = None,
) -> dict:
    scraper = get_scraper(platform, cookies_file)
    username = scraper.normalize_username(username)
    profile_url, discovered = scraper.scan_profile(username)
    profile = get_or_create_profile(session, platform, username, profile_url)

    existing = {
        v.platform_video_id: v
        for v in session.query(Video).filter_by(profile_id=profile.id).all()
    }

    new_count = 0
    updated_count = 0

    for info in discovered:
        video = existing.get(info.platform_video_id)
        if video:
            video.url = info.url
            video.title = info.title or video.title
            video.description = info.description or video.description
            video.views = info.views if info.views is not None else video.views
            video.likes = info.likes if info.likes is not None else video.likes
            video.comments = info.comments if info.comments is not None else video.comments
            video.shares = info.shares if info.shares is not None else video.shares
            video.posted_at = info.posted_at or video.posted_at
            video.last_updated_at = datetime.utcnow()
            updated_count += 1
        else:
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
            new_count += 1

    profile.video_count = len(discovered)
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
    }


def list_videos(
    session: Session,
    platform: str,
    username: str,
    status: str | None = None,
    sort_by: str = "gmv",
) -> list[Video]:
    profile = (
        session.query(Profile)
        .filter_by(platform=platform, username=username)
        .first()
    )
    if not profile:
        return []

    query = session.query(Video).filter_by(profile_id=profile.id)

    if status == "downloaded":
        query = query.filter_by(is_downloaded=True)
    elif status == "pending":
        query = query.filter_by(is_downloaded=False)

    videos = query.all()

    def sort_key(v: Video):
        if sort_by == "gmv":
            return (v.gmv or 0, v.views or 0, v.likes or 0)
        if sort_by == "views":
            return (v.views or 0, v.gmv or 0)
        if sort_by == "likes":
            return (v.likes or 0, v.views or 0)
        return (v.first_seen_at.timestamp(),)

    return sorted(videos, key=sort_key, reverse=True)


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
) -> dict:
    profile = (
        session.query(Profile)
        .filter_by(platform=platform, username=username)
        .first()
    )
    if not profile:
        raise ValueError(f"Profil belum di-scan. Jalankan scan dulu: {platform}/{username}")

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
        if video.is_downloaded and video.file_path and _valid_existing(Path(video.file_path)):
            skipped += 1
            continue
        if video.is_downloaded and video.file_path:
            video.is_downloaded = False
            video.file_path = None
        try:
            downloader.download_video(session, video, platform, username)
            success += 1
        except Exception as e:
            failed += 1
            if len(errors) < 3:
                errors.append(str(e))

    if not videos:
        errors.append("Tidak ada video pending untuk di-download")

    return {
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "total_attempted": len(videos),
        "errors": errors,
    }


def list_profiles(session: Session) -> list[Profile]:
    return session.query(Profile).order_by(Profile.last_scanned_at.desc().nullslast()).all()


def get_profile(session: Session, profile_id: int) -> Profile | None:
    return session.query(Profile).filter_by(id=profile_id).first()


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


def video_to_dict(video: Video) -> dict:
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
        "downloaded_at": video.downloaded_at.isoformat() if video.downloaded_at else None,
        "file_path": video.file_path,
        "posted_at": video.posted_at.isoformat() if video.posted_at else None,
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
) -> list[Video]:
    """Return top performing videos by GMV (hero candidates for cross-platform)."""
    videos = list_videos(session, platform, username, sort_by="gmv")
    with_gmv = [v for v in videos if v.gmv and v.gmv > 0]
    if with_gmv:
        return with_gmv[:top_n]
    # Fallback: rank by engagement if no GMV data yet
    return sorted(
        videos,
        key=lambda v: (v.views or 0) + (v.likes or 0) * 10,
        reverse=True,
    )[:top_n]