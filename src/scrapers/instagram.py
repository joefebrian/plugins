"""Instagram profile scraper (reels/posts)."""

from __future__ import annotations

from .base import BaseScraper
from .parse import parse_instagram_username


class InstagramScraper(BaseScraper):
    platform = "instagram"

    def normalize_username(self, username: str) -> str:
        return parse_instagram_username(username)

    def build_profile_url(self, username: str) -> str:
        return f"https://www.instagram.com/{username}/reels/"

    def extract_video_id(self, entry: dict, fallback_url: str) -> str:
        vid = entry.get("id") or entry.get("shortcode")
        if vid:
            return str(vid)
        # Instagram URL: /reel/ABC123/ or /p/ABC123/
        parts = fallback_url.rstrip("/").split("/")
        for marker in ("reel", "p", "tv"):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    return parts[idx + 1]
        return super().extract_video_id(entry, fallback_url)