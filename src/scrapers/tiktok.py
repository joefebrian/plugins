"""TikTok profile scraper."""

from __future__ import annotations

from .base import BaseScraper
from .parse import parse_tiktok_username


class TikTokScraper(BaseScraper):
    platform = "tiktok"

    def normalize_username(self, username: str) -> str:
        return parse_tiktok_username(username)

    def build_profile_url(self, username: str) -> str:
        return f"https://www.tiktok.com/@{username}"

    def extract_video_id(self, entry: dict, fallback_url: str) -> str:
        vid = entry.get("id")
        if vid:
            return str(vid)
        # TikTok URL: /@user/video/7123456789
        parts = fallback_url.rstrip("/").split("/")
        if "video" in parts:
            idx = parts.index("video")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return super().extract_video_id(entry, fallback_url)