"""Base scraper using yt-dlp for profile video discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sys

import yt_dlp


@dataclass
class VideoInfo:
    platform_video_id: str
    url: str
    title: str | None = None
    description: str | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    posted_at: datetime | None = None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.utcfromtimestamp(int(value))
    except (TypeError, ValueError, OSError):
        return None


def _parse_upload_date(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d")
    except (TypeError, ValueError):
        return None
    return None


def _parse_posted_at(entry: dict) -> datetime | None:
    return (
        _parse_timestamp(entry.get("timestamp"))
        or _parse_timestamp(entry.get("create_time"))
        or _parse_upload_date(entry.get("upload_date"))
    )


class BaseScraper:
    platform: str = "unknown"

    def __init__(self, cookies_file: str | None = None):
        self.cookies_file = cookies_file

    def _base_opts(self) -> dict:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "retries": 3,
            "fragment_retries": 3,
            "sleep_interval": 1,
            "max_sleep_interval": 5,
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        return opts

    def normalize_username(self, username: str) -> str:
        return username.lstrip("@").strip()

    def build_profile_url(self, username: str) -> str:
        raise NotImplementedError

    def extract_video_id(self, entry: dict, fallback_url: str) -> str:
        vid = entry.get("id")
        if vid:
            return str(vid)
        match = re.search(r"/(\d+)", fallback_url)
        if match:
            return match.group(1)
        return fallback_url.rstrip("/").split("/")[-1]

    def _check_runtime(self):
        if sys.version_info < (3, 10):
            raise ValueError(
                "Python 3.9 tidak didukung untuk scan TikTok. "
                "Jalankan server dengan: ./run-web.sh (butuh Python 3.10+)"
            )
        version = tuple(int(x) for x in yt_dlp.version.__version__.split(".")[:3])
        if version < (2026, 3, 13):
            raise ValueError(
                f"yt-dlp {yt_dlp.version.__version__} terlalu lama untuk TikTok. "
                "Jalankan: ./run-web.sh untuk auto-update"
            )

    # On rescan: only walk newest entries until we hit videos already in DB.
    INCREMENTAL_PLAYLIST_LIMIT = 60
    STOP_AFTER_CONSECUTIVE_KNOWN = 5

    def scan_profile(
        self,
        username: str,
        known_video_ids: set[str] | None = None,
    ) -> tuple[str, list[VideoInfo]]:
        self._check_runtime()
        username = self.normalize_username(username)
        profile_url = self.build_profile_url(username)

        opts = self._base_opts()
        incremental = bool(known_video_ids)
        if incremental:
            opts["lazy_playlist"] = True
            opts["playlistend"] = self.INCREMENTAL_PLAYLIST_LIMIT

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)

        entries = (info or {}).get("entries") or []
        if not entries and not incremental:
            raise ValueError(
                f"Tidak bisa mengakses profil: {profile_url}. "
                "Coba lagi nanti (rate limit) atau gunakan --cookies cookies.txt"
            )

        videos: list[VideoInfo] = []
        consecutive_known = 0

        for entry in entries:
            if not entry:
                continue
            url = entry.get("url") or entry.get("webpage_url") or ""
            if not url:
                continue

            video_id = self.extract_video_id(entry, url)
            if incremental and video_id in known_video_ids:
                consecutive_known += 1
                if consecutive_known >= self.STOP_AFTER_CONSECUTIVE_KNOWN:
                    break
                continue

            consecutive_known = 0
            videos.append(
                VideoInfo(
                    platform_video_id=video_id,
                    url=url,
                    title=entry.get("title"),
                    description=entry.get("description"),
                    views=_safe_int(entry.get("view_count")),
                    likes=_safe_int(entry.get("like_count")),
                    comments=_safe_int(entry.get("comment_count")),
                    shares=_safe_int(entry.get("repost_count")),
                    posted_at=_parse_posted_at(entry),
                )
            )

        if not videos and not incremental:
            raise ValueError(
                f"Tidak bisa mengakses profil: {profile_url}. "
                "Coba lagi nanti (rate limit) atau gunakan --cookies cookies.txt"
            )

        return profile_url, videos

    def fetch_video_details(self, url: str) -> VideoInfo:
        opts = self._base_opts()
        opts["extract_flat"] = False

        with yt_dlp.YoutubeDL(opts) as ydl:
            entry = ydl.extract_info(url, download=False)

        if not entry:
            raise ValueError(f"Tidak bisa mengambil detail video: {url}")

        video_url = entry.get("webpage_url") or url
        return VideoInfo(
            platform_video_id=self.extract_video_id(entry, video_url),
            url=video_url,
            title=entry.get("title") or entry.get("description"),
            description=entry.get("description"),
            views=_safe_int(entry.get("view_count")),
            likes=_safe_int(entry.get("like_count")),
            comments=_safe_int(entry.get("comment_count")),
            shares=_safe_int(entry.get("repost_count")),
            posted_at=_parse_posted_at(entry),
        )