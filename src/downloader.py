"""Video downloader — TikTok via HD API, Instagram via yt-dlp."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yt_dlp
from sqlalchemy.orm import Session

from .db.models import Video
from .scrapers.kuaishou_api import resolve_kuaishou_download_url
from .scrapers.rednote_api import rednote_cdn_referer, resolve_rednote_download_url
from .scrapers.shopee_api import resolve_shopee_download_url
from .scrapers.tikwm import download_file, get_tiktok_video_url

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_STEM = 120


def _sanitize_filename_stem(title: str, fallback: str) -> str:
    """Turn video title into a safe filename stem (no extension)."""
    name = " ".join((title or "").split())
    name = _INVALID_FILENAME_CHARS.sub("", name).strip(" .")
    if not name:
        return fallback
    if len(name) > _MAX_FILENAME_STEM:
        name = name[:_MAX_FILENAME_STEM].rstrip(" .")
    return name or fallback


def _unique_file_path(target_dir: Path, stem: str, ext: str = ".mp4") -> Path:
    candidate = target_dir / f"{stem}{ext}"
    if not candidate.exists():
        return candidate
    for i in range(2, 100):
        candidate = target_dir / f"{stem} ({i}){ext}"
        if not candidate.exists():
            return candidate
    return target_dir / f"{stem} ({datetime.utcnow().strftime('%Y%m%d%H%M%S')}){ext}"

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

    def _tiktok_download_path(self, video: Video, target_dir: Path, meta_title: str | None = None) -> Path:
        if meta_title and not video.title:
            video.title = meta_title
        stem = _sanitize_filename_stem(video.title or meta_title or "", video.platform_video_id)
        return _unique_file_path(target_dir, stem)

    def _download_tiktok(self, video: Video, target_dir: Path) -> Path:
        file_path = self._tiktok_download_path(video, target_dir)
        last_err: Exception | None = None

        # 1) TikWM CDN URLs (HD → play → wm) with multi-referer on 403
        try:
            meta = get_tiktok_video_url(video.url, self.quality)
            file_path = self._tiktok_download_path(video, target_dir, meta.get("title"))
            download_file(
                meta["download_url"],
                str(file_path),
                candidates=meta.get("candidates"),
            )
            if self._is_video_file(file_path):
                if meta.get("title"):
                    video.title = video.title or meta["title"]
                return file_path
            file_path.unlink(missing_ok=True)
            last_err = ValueError("Download gagal — file bukan video valid")
        except Exception as e:
            last_err = e
            file_path.unlink(missing_ok=True)

        # 2) Fallback yt-dlp (cookies help when TikTok blocks CDN)
        try:
            ytdlp_path = self._download_via_ytdlp(video, target_dir)
            if self._is_video_file(ytdlp_path):
                return ytdlp_path
            ytdlp_path.unlink(missing_ok=True)
        except Exception as e:
            last_err = e

        from .ytdlp_util import format_ytdlp_error

        raise ValueError(
            f"Gagal download TikTok: {format_ytdlp_error(last_err)}. "
            "Upload cookies TikTok di Settings → Cookies jika 403 berulang."
        )

    def _yt_dlp_opts(self, output_template: str, *, for_tiktok: bool = False) -> dict:
        # TikTok rarely exposes separate video+audio streams matching our strict presets
        # → "Requested format is not available". Use flexible format for TikTok.
        if for_tiktok:
            fmt = "best/mp4/bestvideo+bestaudio/bestvideo/bestaudio"
        else:
            fmt = FORMAT_PRESETS[self.quality]

        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "outtmpl": output_template,
            "format": fmt,
            "merge_output_format": "mp4",
            "writethumbnail": False,
            "writeinfojson": False,
            "postprocessors": [],
            "retries": 5,
            "fragment_retries": 5,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
            },
        }
        if not for_tiktok:
            opts["format_sort"] = ["res", "fps", "codec:h264", "size", "br"]
        # curl_cffi chrome impersonation (requires yt-dlp[curl-cffi])
        from .ytdlp_util import apply_chrome_impersonate

        apply_chrome_impersonate(opts)
        if self.cookies_file and Path(self.cookies_file).exists():
            opts["cookiefile"] = self.cookies_file
        return opts

    def _download_shopee(self, video: Video, target_dir: Path, username: str) -> Path:
        stem = _sanitize_filename_stem(video.title or "", video.platform_video_id)
        file_path = _unique_file_path(target_dir, stem)
        source_url = resolve_shopee_download_url(
            video.url,
            cookies_file=self.cookies_file,
            username=username,
        )
        download_file(source_url, str(file_path), referer="https://shopee.co.id/")
        if not self._is_video_file(file_path):
            file_path.unlink(missing_ok=True)
            raise ValueError("Download Shopee gagal — file bukan video valid")
        return file_path

    def _download_rednote(self, video: Video, target_dir: Path, username: str) -> Path:
        stem = _sanitize_filename_stem(video.title or "", video.platform_video_id)
        file_path = _unique_file_path(target_dir, stem)
        source_url = resolve_rednote_download_url(
            video.url,
            note_id=video.platform_video_id,
            cookies_file=self.cookies_file,
            user_id=username,
        )
        download_file(source_url, str(file_path), referer=rednote_cdn_referer(source_url))
        if not self._is_video_file(file_path):
            file_path.unlink(missing_ok=True)
            raise ValueError("Download RedNote gagal — file bukan video valid")
        return file_path

    def _download_kuaishou(self, video: Video, target_dir: Path, username: str) -> Path:
        stem = _sanitize_filename_stem(video.title or "", video.platform_video_id)
        file_path = _unique_file_path(target_dir, stem)
        source_url = resolve_kuaishou_download_url(
            video.url,
            username,
            photo_id=video.platform_video_id,
            cookies_file=self.cookies_file,
        )
        download_file(source_url, str(file_path))
        if not self._is_video_file(file_path):
            file_path.unlink(missing_ok=True)
            raise ValueError("Download Kuaishou gagal — file bukan video valid")
        return file_path

    def _download_via_ytdlp(self, video: Video, target_dir: Path) -> Path:
        output_template = str(target_dir / f"{video.platform_video_id}.%(ext)s")
        for_tiktok = (video.platform or "").lower() == "tiktok" or "tiktok.com" in (
            video.url or ""
        )
        opts = self._yt_dlp_opts(output_template, for_tiktok=for_tiktok)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video.url, download=True)

        ext = (info or {}).get("ext", "mp4")
        vcodec = (info or {}).get("vcodec")
        file_path = target_dir / f"{video.platform_video_id}.{ext}"

        # yt-dlp may write merged name without expected ext — scan target_dir
        if not file_path.exists():
            matches = sorted(
                target_dir.glob(f"{video.platform_video_id}.*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if matches:
                file_path = matches[0]

        if vcodec == "none" or not self._is_video_file(file_path):
            file_path.unlink(missing_ok=True)
            raise ValueError(
                "Yang terdownload bukan video. Untuk TikTok, sistem pakai API HD otomatis."
            )
        return file_path

    def _download_into_dir(
        self,
        video: Video,
        platform: str,
        username: str,
        target_dir: Path,
    ) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        if platform == "tiktok":
            # Ensure absolute video URL (DB may store bare id from old scans)
            if video.url and "://" not in video.url:
                handle = username.lstrip("@")
                video.url = (
                    f"https://www.tiktok.com/@{handle}/video/{video.platform_video_id}"
                )
            elif video.platform_video_id and "/video/" not in (video.url or ""):
                handle = username.lstrip("@")
                video.url = (
                    f"https://www.tiktok.com/@{handle}/video/{video.platform_video_id}"
                )
            return self._download_tiktok(video, target_dir)
        if platform == "kuaishou":
            return self._download_kuaishou(video, target_dir, username)
        if platform == "rednote":
            return self._download_rednote(video, target_dir, username)
        if platform == "shopee":
            return self._download_shopee(video, target_dir, username)
        return self._download_via_ytdlp(video, target_dir)

    def download_video(
        self,
        session: Session,
        video: Video,
        platform: str,
        username: str,
        user_id: int | None = None,
    ) -> Path:
        if user_id:
            target_dir = self.download_dir / str(user_id) / platform / username
        else:
            target_dir = self.download_dir / platform / username
        target_dir.mkdir(parents=True, exist_ok=True)

        # Re-download if existing file is invalid (e.g. old MP3)
        if video.is_downloaded and video.file_path:
            existing = Path(video.file_path)
            if self._is_video_file(existing):
                return existing
            existing.unlink(missing_ok=True)
            video.is_downloaded = False
            video.file_path = None

        file_path = self._download_into_dir(video, platform, username, target_dir)

        video.is_downloaded = True
        video.downloaded_at = datetime.utcnow()
        video.file_path = str(file_path)
        session.commit()
        return file_path

    def download_ephemeral(
        self,
        video: Video,
        platform: str,
        username: str,
        target_dir: Path,
    ) -> Path:
        """Download video into target_dir without setting video.file_path (temp analyse)."""
        return self._download_into_dir(video, platform, username, target_dir)