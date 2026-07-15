"""Storage volume monitoring for downloaded videos on server."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from ..db.models import Profile, Video


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _bytes_to_gb(value: int) -> float:
    return round(value / (1024 ** 3), 3)


def storage_volume_overview(
    session: Session,
    user_id: int,
    download_dir: Path,
) -> dict:
    profiles = (
        session.query(Profile)
        .filter(Profile.user_id == user_id)
        .order_by(Profile.platform.asc(), Profile.username.asc())
        .all()
    )

    profile_rows: list[dict] = []
    tracked_bytes = 0
    tracked_files = 0

    for profile in profiles:
        videos = (
            session.query(Video)
            .filter(
                Video.profile_id == profile.id,
                Video.is_downloaded.is_(True),
                Video.file_path.isnot(None),
            )
            .all()
        )
        profile_bytes = 0
        file_count = 0
        for video in videos:
            path = Path(video.file_path) if video.file_path else None
            if not path or not path.exists():
                continue
            try:
                profile_bytes += path.stat().st_size
                file_count += 1
            except OSError:
                continue

        tracked_bytes += profile_bytes
        tracked_files += file_count
        profile_rows.append(
            {
                "profile_id": profile.id,
                "platform": profile.platform,
                "username": profile.username,
                "url": profile.url,
                "video_count": profile.video_count or 0,
                "downloaded_count": file_count,
                "bytes": profile_bytes,
                "gb": _bytes_to_gb(profile_bytes),
                "pct": 0.0,
            }
        )

    profile_rows.sort(key=lambda row: row["bytes"], reverse=True)
    if tracked_bytes:
        for row in profile_rows:
            row["pct"] = round(100 * row["bytes"] / tracked_bytes, 1)

    user_dir = download_dir / str(user_id)
    disk_bytes = _dir_size(user_dir)
    legacy_dir_bytes = 0
    if user_id:
        for profile in profiles:
            legacy = download_dir / profile.platform / profile.username
            if legacy.exists():
                legacy_dir_bytes += _dir_size(legacy)

    return {
        "total_bytes": tracked_bytes,
        "total_gb": _bytes_to_gb(tracked_bytes),
        "disk_bytes": disk_bytes,
        "disk_gb": _bytes_to_gb(disk_bytes),
        "legacy_bytes": legacy_dir_bytes,
        "legacy_gb": _bytes_to_gb(legacy_dir_bytes),
        "total_files": tracked_files,
        "profile_count": len(profiles),
        "profiles_with_storage": sum(1 for row in profile_rows if row["bytes"] > 0),
        "profiles": profile_rows,
        "download_dir": str(user_dir),
    }