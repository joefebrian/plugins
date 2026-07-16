"""Shopee shop profile scraper — product videos from seller usernames."""

from __future__ import annotations

from .base import VideoInfo
from .parse import parse_shopee_username
from .shopee_api import (
    fetch_item_detail,
    iter_profile_videos,
    item_to_video_info,
    resolve_shop,
)


class ShopeeScraper:
    platform = "shopee"
    INCREMENTAL_PLAYLIST_LIMIT = 60
    STOP_AFTER_CONSECUTIVE_KNOWN = 5

    def __init__(self, cookies_file: str | None = None):
        self.cookies_file = cookies_file

    def normalize_username(self, username: str) -> str:
        return parse_shopee_username(username)

    def build_profile_url(self, username: str) -> str:
        shop = resolve_shop(username, cookies_file=self.cookies_file)
        slug = shop.get("username") or username
        return f"https://shopee.co.id/{slug}"

    def scan_profile(
        self,
        username: str,
        known_video_ids: set[str] | None = None,
    ) -> tuple[str, list[VideoInfo]]:
        slug = self.normalize_username(username)
        if not slug:
            raise ValueError("Username toko Shopee wajib diisi")

        shop = resolve_shop(slug, cookies_file=self.cookies_file)
        profile_url = f"https://shopee.co.id/{shop.get('username') or slug}"
        incremental = bool(known_video_ids)
        videos: list[VideoInfo] = []
        consecutive_known = 0

        for info in iter_profile_videos(slug, cookies_file=self.cookies_file):
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
                f"Tidak ada video produk di toko Shopee: {profile_url}. "
                "Pastikan username benar, toko punya video produk, dan cookies shopee.co.id sudah login (SPC_EC)."
            )

        return profile_url, videos

    def fetch_video_details(self, url: str) -> VideoInfo:
        slug = self.normalize_username(url)
        shop = resolve_shop(slug, cookies_file=self.cookies_file)
        shopid = shop["shopid"]
        username = shop.get("username") or slug

        if "-i." in url:
            tail = url.split("-i.", 1)[1]
            parts = tail.split(".")
            if len(parts) >= 2:
                item_shopid = int(parts[0])
                itemid = int(parts[1].split("?")[0])
                item = fetch_item_detail(
                    item_shopid,
                    itemid,
                    cookies_file=self.cookies_file,
                    username=username,
                )
                info = item_to_video_info(
                    {"item_basic": item},
                    username=username,
                    shopid=item_shopid,
                )
                if info:
                    return info

        raise ValueError(f"Tidak bisa mengambil detail video Shopee: {url}")