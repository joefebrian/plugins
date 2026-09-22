"""TikTok download fallback must not read a platform field Video does not have."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.db.models import Video
from src.downloader import VideoDownloader


class TikTokYtdlpFallbackTests(unittest.TestCase):
    def test_ytdlp_uses_tiktok_format_without_video_platform(self):
        video = Video(
            profile_id=1,
            platform_video_id="7600000000000000001",
            url="https://www.tiktok.com/@demo/video/7600000000000000001",
            title="demo",
        )
        with self.assertRaises(AttributeError):
            _ = video.platform

        captured: dict = {}

        class FakeYDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                out = Path(captured["opts"]["outtmpl"].replace("%(ext)s", "mp4"))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"\x00" * 60_000)
                return {"ext": "mp4", "vcodec": "h264"}

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            dl = VideoDownloader(target)
            with (
                patch("src.downloader.yt_dlp.YoutubeDL", FakeYDL),
                patch.object(VideoDownloader, "_is_video_file", return_value=True),
            ):
                path = dl._download_via_ytdlp(video, target)

        self.assertEqual(path.name, "7600000000000000001.mp4")
        self.assertIn("best/mp4", captured["opts"]["format"])
        self.assertNotIn("format_sort", captured["opts"])

    def test_non_tiktok_keeps_quality_preset(self):
        video = Video(
            profile_id=1,
            platform_video_id="ig1",
            url="https://www.instagram.com/reel/abc/",
            title="ig",
        )
        captured: dict = {}

        class FakeYDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                out = Path(captured["opts"]["outtmpl"].replace("%(ext)s", "mp4"))
                out.write_bytes(b"\x00" * 60_000)
                return {"ext": "mp4", "vcodec": "h264"}

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            dl = VideoDownloader(target, quality="720")
            with (
                patch("src.downloader.yt_dlp.YoutubeDL", FakeYDL),
                patch.object(VideoDownloader, "_is_video_file", return_value=True),
            ):
                dl._download_via_ytdlp(video, target)

        self.assertIn("height<=720", captured["opts"]["format"])
        self.assertIn("format_sort", captured["opts"])


if __name__ == "__main__":
    unittest.main()
