"""Analyze profile videos for affiliate products — temp file on server, then delete.

Storage-friendly flow:
  1. Prefer existing permanent server file if present
  2. Else download to temp dir, analyse, auto-delete file
  3. Only persist affiliate_products_json (+ status note) on the Video row
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Profile, Video
from ..downloader import VideoDownloader
from .visual_items_scan import extract_visual_products_from_file


def _parse_products_json(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def get_video_affiliate_products(video: Video) -> list[dict[str, Any]]:
    return _parse_products_json(video.affiliate_products_json)


def _valid_server_file(path: Path | None) -> bool:
    if not path:
        return False
    try:
        return path.exists() and path.stat().st_size > 50_000
    except OSError:
        return False


def _cleanup_path(path: Path | None, temp_root: Path | None) -> None:
    if path:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if temp_root:
        shutil.rmtree(temp_root, ignore_errors=True)


def analyze_downloaded_video(
    session: Session,
    *,
    video: Video,
    user_id: int,
    download_dir: Path,
    cookies_file: str | None = None,
    quality: str = "360",
) -> dict[str, Any]:
    """
    Vision/OCR product scan without permanent storage.

    - If video already has a valid permanent file_path → use it (do not delete).
    - Otherwise download a short 360p file under download_dir/_tmp_affiliate_scan/, analyse, delete.
    - Does not mark the video as downloaded. Results stay in affiliate_products_json.
    """
    profile = session.query(Profile).filter_by(id=video.profile_id).first()
    if not profile:
        raise ValueError("Profil video tidak ditemukan")
    if profile.user_id not in (None, user_id):
        raise ValueError("Video tidak ditemukan")

    video.affiliate_scan_status = "running"
    video.affiliate_scan_note = "Menyiapkan file (temp) + analisa frame..."
    session.commit()

    permanent = Path(video.file_path) if video.file_path else None
    use_permanent = _valid_server_file(permanent)

    temp_root: Path | None = None
    work_path: Path | None = None
    is_temp = False
    storage_note = ""

    try:
        if use_permanent:
            work_path = permanent
            storage_note = "file permanen di server"
        else:
            # Ephemeral download — never leave file on disk after scan
            is_temp = True
            temp_root = (
                Path(download_dir)
                / "_tmp_affiliate_scan"
                / f"u{user_id}"
                / f"v{video.id}_{uuid.uuid4().hex[:10]}"
            )
            temp_root.mkdir(parents=True, exist_ok=True)
            video.affiliate_scan_note = "Download sementara ke server untuk analisa..."
            session.commit()

            downloader = VideoDownloader(
                download_dir=Path(download_dir),
                cookies_file=cookies_file,
                quality=quality if quality in ("best", "1080", "720", "360") else "360",
            )
            work_path = downloader.download_ephemeral(
                video,
                profile.platform,
                profile.username,
                temp_root,
            )
            if not use_permanent:
                video.file_path = None
            session.commit()
            storage_note = "temp 360p (auto-hapus setelah analisa)"

        if not work_path or not _valid_server_file(work_path):
            raise ValueError("Gagal menyiapkan file video untuk analisa")

        video.affiliate_scan_note = f"Menganalisis frame ({storage_note})..."
        session.commit()

        products, note = extract_visual_products_from_file(
            session,
            video_path=work_path,
            video_title=video.title or video.platform_video_id,
            description=video.description or "",
            user_id=user_id,
        )

        full_note = f"{note} · Storage: {storage_note}"
        video.affiliate_products_json = json.dumps(products, ensure_ascii=False)
        video.affiliate_scan_status = "done"
        video.affiliate_scan_at = datetime.utcnow()
        video.affiliate_scan_note = full_note
        # Ensure PC-only stays without permanent file after temp scan
        if is_temp:
            video.file_path = None
        session.commit()
        session.refresh(video)

        return {
            "video_id": video.id,
            "status": "done",
            "products": products,
            "product_count": len(products),
            "note": full_note,
            "storage": "temp" if is_temp else "server",
            "file_kept": not is_temp,
            "scanned_at": video.affiliate_scan_at.isoformat() if video.affiliate_scan_at else None,
        }
    except Exception as exc:
        video.affiliate_scan_status = "error"
        video.affiliate_scan_note = str(exc)
        video.affiliate_scan_at = datetime.utcnow()
        if is_temp:
            video.file_path = None
        session.commit()
        raise
    finally:
        if is_temp:
            _cleanup_path(work_path, temp_root)
            # Remove empty parent dirs under _tmp_affiliate_scan/uX
            try:
                parent = (Path(download_dir) / "_tmp_affiliate_scan" / f"u{user_id}")
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass


def get_owned_downloaded_video(
    session: Session,
    video_id: int,
    user_id: int,
) -> Video:
    video = (
        session.query(Video)
        .join(Profile)
        .filter(Video.id == video_id, Profile.user_id == user_id)
        .first()
    )
    if not video:
        raise ValueError("Video tidak ditemukan")
    return video
