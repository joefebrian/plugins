"""Scan TikTok/Instagram/Kuaishou/RedNote profiles for monitoring without touching Dashboard profiles."""

from __future__ import annotations

from ..services import get_scraper


def scan_username_metrics(platform: str, username: str, cookies_file: str | None = None) -> dict:
    scraper = get_scraper(platform, cookies_file)
    username = scraper.normalize_username(username)
    if not username:
        raise ValueError("Username wajib diisi")

    profile_url, discovered = scraper.scan_profile(username, known_video_ids=None)
    total_views = sum(v.views or 0 for v in discovered)
    return {
        "external_id": username,
        "name": f"@{username}",
        "handle": username,
        "profile_url": profile_url,
        "uploads_count": len(discovered),
        "views": total_views if total_views else None,
        "revenue": None,
    }