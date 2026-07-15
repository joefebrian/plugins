"""RedNote (rednote.com) profile scraper via signed web API."""

from __future__ import annotations

from .base import VideoInfo
from .parse import parse_rednote_username
from .rednote_api import (
    build_note_url,
    detect_international_from_input,
    extract_note_id_from_url,
    fetch_note_detail,
    iter_profile_videos,
    note_item_to_video_info,
    parse_note_url_tokens,
)


class RedNoteScraper:
    platform = "rednote"
    INCREMENTAL_PLAYLIST_LIMIT = 60
    STOP_AFTER_CONSECUTIVE_KNOWN = 5

    def __init__(self, cookies_file: str | None = None):
        self.cookies_file = cookies_file

    def normalize_username(self, username: str) -> str:
        return parse_rednote_username(username)

    def build_profile_url(self, username: str, *, international: bool = True) -> str:
        domain = "www.rednote.com" if international else "www.xiaohongshu.com"
        return f"https://{domain}/user/profile/{username}"

    def scan_profile(
        self,
        username: str,
        known_video_ids: set[str] | None = None,
    ) -> tuple[str, list[VideoInfo]]:
        user_id = self.normalize_username(username)
        if not user_id:
            raise ValueError("User ID RedNote wajib diisi")

        international = detect_international_from_input(username)
        profile_url = self.build_profile_url(user_id, international=international)
        incremental = bool(known_video_ids)
        videos: list[VideoInfo] = []
        consecutive_known = 0

        for info in iter_profile_videos(
            user_id,
            cookies_file=self.cookies_file,
            international=international,
        ):
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
                f"Tidak ada video di profil RedNote: {profile_url}. "
                "Pastikan User ID benar dan cookies rednote.com / xiaohongshu.com sudah di-upload."
            )

        return profile_url, videos

    def fetch_video_details(self, url: str) -> VideoInfo:
        note_id = extract_note_id_from_url(url)
        if not note_id:
            raise ValueError(f"URL video RedNote tidak valid: {url}")

        _, xsec_token, xsec_source = parse_note_url_tokens(url)
        international = detect_international_from_input(url)
        if xsec_token:
            note = fetch_note_detail(
                note_id,
                xsec_token=xsec_token,
                xsec_source=xsec_source,
                cookies_file=self.cookies_file,
                international=international,
            )
            wrapped = {
                "note_id": note_id,
                "type": note.get("type") or "video",
                "display_title": note.get("title") or note.get("desc"),
                "interact_info": note.get("interact_info") or {},
                "xsec_token": xsec_token,
            }
            info = note_item_to_video_info(wrapped, international=international)
            if info:
                return info

        return VideoInfo(
            platform_video_id=note_id,
            url=build_note_url(note_id, international=international),
            title=f"RedNote {note_id}",
        )