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

        from ..ytdlp_util import apply_chrome_impersonate, format_ytdlp_error

        import yt_dlp

        incremental = bool(known_video_ids)

        def _make_opts(*, use_cookies: bool, ignore_errors: bool) -> dict:
            o = self._base_opts()
            # Stale TikTok cookies sometimes break profile extract ("secondary user ID").
            if not use_cookies:
                o.pop("cookiefile", None)
            apply_chrome_impersonate(o)
            o["http_headers"] = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
            }
            o["ignoreerrors"] = ignore_errors
            if incremental:
                o["lazy_playlist"] = True
                o["playlistend"] = self.INCREMENTAL_PLAYLIST_LIMIT
            return o

        last_err: Exception | None = None
        info = None
        # Prefer @username web URL first (more reliable with chrome impersonate).
        # Fall back to tiktokuser:ID when platform_user_id is known.
        attempt_urls: list[str] = []
        for u in (profile_url, scan_url):
            if u and u not in attempt_urls:
                attempt_urls.append(u)

        # Modes: cookies on/off × strict/soft errors.
        # Soft (ignoreerrors) often still returns playlist items when yt-dlp logs
        # "secondary user ID" once, which strict mode turns into hard failure.
        modes: list[tuple[bool, bool]] = []
        if self.cookies_file:
            modes.append((True, False))
            modes.append((True, True))
        modes.append((False, False))
        modes.append((False, True))

        for use_cookies, ignore_errors in modes:
            if info is not None:
                break
            opts = _make_opts(use_cookies=use_cookies, ignore_errors=ignore_errors)
            for attempt_url in attempt_urls:
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(attempt_url, download=False)
                    entries_probe = list((info or {}).get("entries") or [])
                    entries_probe = [e for e in entries_probe if e]
                    if entries_probe:
                        info = dict(info or {})
                        info["entries"] = entries_probe
                        break
                    if info and not incremental and not entries_probe:
                        last_err = ValueError("empty entries")
                        info = None
                    elif not info:
                        last_err = ValueError("empty extract result")
                except Exception as e:
                    last_err = e
                    info = None
                    continue

        entries = list((info or {}).get("entries") or [])
        if not entries and not incremental:
            detail = format_ytdlp_error(last_err, "empty entries")
            hint = ""
            if "secondary user ID" in detail or "secondary user id" in detail.lower():
                hint = (
                    " TikTok menolak extract by @username — coba Scan Ulang, "
                    "re-upload cookies TikTok yang baru, atau pastikan min. 1 video URL valid."
                )
            raise ValueError(
                f"Tidak bisa mengakses profil: {profile_url}.{hint} "
                f"Detail: {detail}. "
                "Upload cookies TikTok di Settings (file baru dari browser), "
                "atau pastikan ada min. 1 video URL valid."
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
            from ..ytdlp_util import format_ytdlp_error

            raise ValueError(
                f"Tidak bisa mengakses profil: {profile_url}. "
                f"Detail: {format_ytdlp_error(last_err, 'no videos')}"
            )

        return profile_url, videos
