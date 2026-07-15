"""Kuaishou profile scraper via official web API."""

from __future__ import annotations

from .base import VideoInfo
from .kuaishou_api import iter_profile_videos
from .parse import parse_kuaishou_username


class KuaishouScraper:
    platform = "kuaishou"
    INCREMENTAL_PLAYLIST_LIMIT = 60
    STOP_AFTER_CONSECUTIVE_KNOWN = 5

    def __init__(self, cookies_file: str | None = None):
        self.cookies_file = cookies_file

    def normalize_username(self, username: str) -> str:
        return parse_kuaishou_username(username)

    def build_profile_url(self, username: str) -> str:
        return f"https://www.kuaishou.com/profile/{username}"

    def scan_profile(
        self,
        username: str,
        known_video_ids: set[str] | None = None,
    ) -> tuple[str, list[VideoInfo]]:
        user_id = self.normalize_username(username)
        if not user_id:
            raise ValueError("User ID Kuaishou wajib diisi")

        profile_url = self.build_profile_url(user_id)
        incremental = bool(known_video_ids)
        videos: list[VideoInfo] = []
        consecutive_known = 0

        for info in iter_profile_videos(user_id, cookies_file=self.cookies_file):
            if incremental and info.platform_video_id in known_video_ids:
                consecutive_known += 1
                if consecutive_known >= self.STOP_AFTER_CONSECUTIVE_KNOWN:
                    break
                continue
            consecutive_known = 0
            videos.append(info)
            if incremental and len(videos) >= self.INCREMENTAL_PLAYLIST_LIMIT:
                break

        if not videos and not incremental:
            raise ValueError(
                f"Tidak ada video di profil Kuaishou: {profile_url}. "
                "Pastikan User ID benar dan cookies kuaishou.com sudah di-upload."
            )

        return profile_url, videos

    def fetch_video_details(self, url: str) -> VideoInfo:
        from .kuaishou_api import extract_photo_id_from_url, fetch_video_detail, feed_item_to_video_info

        photo_id = extract_photo_id_from_url(url)
        if not photo_id:
            raise ValueError(f"URL video Kuaishou tidak valid: {url}")
        detail = fetch_video_detail(photo_id, "", cookies_file=self.cookies_file)
        feeds = detail.get("feeds") or []
        if feeds and isinstance(feeds[0], dict):
            info = feed_item_to_video_info(feeds[0], "")
            if info:
                return info
        current = detail.get("data", {}).get("currentWork") if isinstance(detail.get("data"), dict) else None
        if isinstance(current, dict):
            wrapped = {"photo": current, "mp4Url": current.get("mp4Url")}
            info = feed_item_to_video_info(wrapped, "")
            if info:
                return info
        raise ValueError(f"Tidak bisa mengambil detail video: {url}")