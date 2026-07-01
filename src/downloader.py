"""Video downloader — TikTok via HD API, Instagram via yt-dlp."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import yt_dlp
from sqlalchemy.orm import Session

from .db.models import Video
from .scrapers.tikwm import download_file, get_tiktok_video_url

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".wav"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}

FORMAT_PRESETS = {
    "best": (
        "bestvideo[ext=mp4][vcodec!=none]+bestaudio[ext=m4a]/"
        "bestvideo[vcodec!=none]+bestaudio/"
        "best[vcodec!=none][ext=mp4]/best[vcodec!=none]"
    ),
    "1080": (
        "bestvideo[height<=1080][vcodec!=none][ext=mp4]+bestaudio/"
        "bestvideo[height<=1080][vcodec!=none]+bestaudio/"
        "best[height<=1080][vcodec!=none]"
    ),
    "720": (
        "bestvideo[height<=720][vcodec!=none][ext=mp4]+bestaudio/"
        "bestvideo[height<=720][vcodec!=none]+bestaudio/"
        "best[height<=720][vcodec!=none]"
    ),
}


class VideoDownloader:
    def __init__(
        self,
        download_dir: Path,
        cookies_file: Optional[str] = None,
        quality: str = "best",
    ):
        self.download_dir = download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_file = cookies_file
        self.quality = quality if quality in FORMAT_PRESETS else "best"

    def _is_video_file(self, file_path: Path) -> bool:
        if not file_path.exists():
            return False
        ext = file_path.suffix.lower()
        if ext in AUDIO_EXTS:
            return False
        if ext not in VIDEO_EXTS:
            return False
        if file_path.stat().st_size < 50_000:
            return False
        # Check MP4 magic bytes
        with file_path.open("rb") as f:
            magic = f.read(12)
        if magic[4:8] == b"ftyp" or magic[:4] == b"\x1aE\xdf\xa3":
            return True
        return file_path.stat().st_size > 200_000

    def _download_tiktok(self, video: Video, file_path: Path) -> Path:
        meta = get_tiktok_video_url(video.url, self.quality)
        download_file(meta["download_url"], str(file_path))
        if not self._is_video_file(file_path):
            file_path.unlink(missing_ok=True)
            raise ValueError("Download gagal — file bukan video valid")
        if meta.get("title") and not video.title:
            video.title = meta["title"]
        return file_path

    def _yt_dlp_opts(self, output_template: str) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": output_template,
            "format": FORMAT_PRESETS[self.quality],
            "format_sort": ["res", "fps", "codec:h264", "size", "br"],
            "merge_output_format": "mp4",
            "writethumbnail": False,
            "writeinfojson": False,
            "postprocessors": [],
            "retries": 3,
        }
        if self.cookies_file and Path(self.cookies_file).exists():
            opts["cookiefile"] = self.cookies_file
        return opts

    def _download_via_ytdlp(self, video: Video, target_dir: Path) -> Path:
        output_template = str(target_dir / f"{video.platform_video_id}.%(ext)s")
        with yt_dlp.YoutubeDL(self._yt_dlp_opts(output_template)) as ydl:
            info = ydl.extract_info(video.url, download=True)

        ext = (info or {}).get("ext", "mp4")
        vcodec = (info or {}).get("vcodec")
        file_path = target_dir / f"{video.platform_video_id}.{ext}"

        if vcodec == "none" or not self._is_video_file(file_path):
            file_path.unlink(missing_ok=True)
            raise ValueError(
                "Yang terdownload bukan video. Untuk TikTok, sistem pakai API HD otomatis."
            )
        return file_path

    def download_video(
        self,
        session: Session,
        video: Video,
        platform: str,
        username: str,
    ) -> Path:
        target_dir = self.download_dir / platform / username
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{video.platform_video_id}.mp4"

        # Re-download if existing file is invalid (e.g. old MP3)
        if video.is_downloaded and video.file_path:
            existing = Path(video.file_path)
            if self._is_video_file(existing):
                return existing
            existing.unlink(missing_ok=True)
            video.is_downloaded = False
            video.file_path = None

        if platform == "tiktok":
            file_path = self._download_tiktok(video, file_path)
        else:
            file_path = self._download_via_ytdlp(video, target_dir)

        video.is_downloaded = True
        video.downloaded_at = datetime.utcnow()
        video.file_path = str(file_path)
        session.commit()
        return file_path