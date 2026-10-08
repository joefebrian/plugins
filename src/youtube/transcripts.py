"""Fetch YouTube video metadata and subtitles via yt-dlp."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp

_VIDEO_ID_PATTERNS = (
    re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)([A-Za-z0-9_-]{11})"),
    re.compile(r"^[A-Za-z0-9_-]{11}$"),
)


class YouTubeTranscriptError(ValueError):
    pass


def extract_youtube_video_id(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(raw)
        if match:
            return match.group(1)
    parsed = urlparse(raw)
    if parsed.netloc.endswith("youtube.com"):
        query = parse_qs(parsed.query)
        vid = (query.get("v") or [""])[0]
        if vid:
            return vid
    return ""


def build_youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _parse_vtt(text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if "-->" in line or re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return " ".join(lines)


def _pick_subtitle_lang(info: dict) -> tuple[str, dict] | tuple[None, None]:
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for lang in ("id", "en", "en-US", "en-GB"):
        if lang in manual:
            return lang, manual[lang]
    for lang in ("id", "en", "en-US", "en-GB"):
        if lang in auto:
            return lang, auto[lang]
    if manual:
        lang = next(iter(manual))
        return lang, manual[lang]
    if auto:
        lang = next(iter(auto))
        return lang, auto[lang]
    return None, None


def _subtitle_url(entries: list[dict]) -> str:
    preferred_ext = ("vtt", "srv3", "json3", "ttml")
    for ext in preferred_ext:
        for item in entries:
            if item.get("ext") == ext and item.get("url"):
                return str(item["url"])
    for item in entries:
        if item.get("url"):
            return str(item["url"])
    return ""


def fetch_youtube_video_content(url_or_id: str) -> dict[str, Any]:
    video_id = extract_youtube_video_id(url_or_id)
    if not video_id:
        raise YouTubeTranscriptError("URL atau ID video YouTube tidak valid")

    watch_url = build_youtube_watch_url(video_id)
    base_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(base_opts) as ydl:
        info = ydl.extract_info(watch_url, download=False)
    if not info:
        raise YouTubeTranscriptError("Video YouTube tidak ditemukan")

    title = str(info.get("title") or "")
    description = str(info.get("description") or "")
    channel = str(info.get("channel") or info.get("uploader") or "")
    thumbnail = ""
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        thumbnail = str(thumbs[-1].get("url") or "")

    transcript_text = ""
    transcript_lang = ""
    lang, entries = _pick_subtitle_lang(info)
    if lang and entries:
        sub_url = _subtitle_url(entries if isinstance(entries, list) else [])
        if sub_url:
            try:
                from curl_cffi import requests as curl_requests

                resp = curl_requests.get(sub_url, impersonate="chrome131", timeout=30)
                body = resp.text
            except Exception:
                import urllib.request

                with urllib.request.urlopen(sub_url, timeout=30) as raw:
                    body = raw.read().decode("utf-8", errors="replace")
            transcript_text = _parse_vtt(body)
            transcript_lang = lang

    if not transcript_text:
        with tempfile.TemporaryDirectory() as tmp:
            outtmpl = str(Path(tmp) / f"{video_id}.%(ext)s")
            sub_opts = {
                **base_opts,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["id", "en"],
                "subtitlesformat": "vtt",
                "outtmpl": outtmpl,
            }
            try:
                with yt_dlp.YoutubeDL(sub_opts) as ydl:
                    ydl.download([watch_url])
                for path in Path(tmp).glob("*.vtt"):
                    transcript_text = _parse_vtt(path.read_text(encoding="utf-8", errors="ignore"))
                    transcript_lang = path.stem.split(".")[-1] if "." in path.stem else "auto"
                    break
            except Exception:
                pass

    return {
        "video_id": video_id,
        "url": watch_url,
        "title": title,
        "description": description,
        "channel_title": channel,
        "thumbnail_url": thumbnail,
        "transcript": transcript_text,
        "transcript_lang": transcript_lang,
        "has_transcript": bool(transcript_text),
    }