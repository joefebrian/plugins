"""TikTok profile scraper."""

from __future__ import annotations

from .base import BaseScraper
from .parse import parse_tiktok_username
from .tiktok_ids import tiktok_scan_input


class TikTokScraper(BaseScraper):
    platform = "tiktok"

    def __init__(self, cookies_file: str | None = None, platform_user_id: str | None = None):
        super().__init__(cookies_file)
        self.platform_user_id = platform_user_id

    def normalize_username(self, username: str) -> str:
        return parse_tiktok_username(username)

    def build_profile_url(self, username: str) -> str:
        # Public web URL always uses @username (for DB storage / "Buka profil")
        return f"https://www.tiktok.com/@{username}"

    def build_scan_url(self, username: str) -> str:
        """Input for yt-dlp — prefers numeric/secUid when known."""
        return tiktok_scan_input(username, self.platform_user_id)

    def extract_video_id(self, entry: dict, fallback_url: str) -> str:
        vid = entry.get("id")
        if vid:
            return str(vid)
        parts = fallback_url.rstrip("/").split("/")
        if "video" in parts:
            idx = parts.index("video")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return super().extract_video_id(entry, fallback_url)

    def scan_profile(
        self,
        username: str,
        known_video_ids: set[str] | None = None,
    ) -> tuple[str, list]:
        """Override to scan via tiktokuser:ID when platform_user_id is set."""
        self._check_runtime()
        username = self.normalize_username(username)
        profile_url = self.build_profile_url(username)
        scan_url = self.build_scan_url(username)

        opts = self._base_opts()
        # TikTok scrape is flaky — cookies + chrome impersonation help
        try:
            import curl_cffi  # noqa: F401

            opts["impersonate"] = "chrome"
        except ImportError:
            pass
        opts["http_headers"] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        }

        incremental = bool(known_video_ids)
        if incremental:
            opts["lazy_playlist"] = True
            opts["playlistend"] = self.INCREMENTAL_PLAYLIST_LIMIT

        import yt_dlp

        last_err: Exception | None = None
        info = None
        # Try preferred scan URL, then fall back to @username web URL
        for attempt_url in (scan_url, profile_url):
            if info is not None:
                break
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(attempt_url, download=False)
                if info and (info.get("entries") or not incremental):
                    break
            except Exception as e:
                last_err = e
                info = None
                continue

        entries = (info or {}).get("entries") or []
        if not entries and not incremental:
            hint = ""
            if last_err and "secondary user ID" in str(last_err):
                hint = (
                    " TikTok menolak extract by @username — butuh channel_id numerik "
                    "(akan diisi otomatis dari video sample / Scan Ulang setelah cookies)."
                )
            raise ValueError(
                f"Tidak bisa mengakses profil: {profile_url}.{hint} "
                f"Detail: {last_err or 'empty entries'}. "
                "Upload cookies TikTok di Settings, atau pastikan ada min. 1 video URL valid."
            )

        videos = []
        consecutive_known = 0
        from .base import VideoInfo, _safe_int, _parse_posted_at

        for entry in entries:
            if not entry:
                continue
            url = entry.get("url") or entry.get("webpage_url") or ""
            if not url:
                continue
            # Normalize relative / bare ids to full TikTok URL
            if url.isdigit() or (url and "://" not in url and "/video/" not in url):
                url = f"https://www.tiktok.com/@{username}/video/{url.split('/')[-1]}"
            elif url.startswith("/@"):
                url = f"https://www.tiktok.com{url}"

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
                f"Detail: {last_err or 'no videos'}"
            )

        return profile_url, videos
