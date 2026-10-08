"""Light product scan: small frames, 360p video-only, routes exist."""

from __future__ import annotations

import inspect
import unittest


class LightAffiliateScanTests(unittest.TestCase):
    def test_frame_cmd_is_small_jpeg(self):
        from src.video_affiliate.frame_sampler import frame_extract_cmd

        cmd = frame_extract_cmd(
            "in.mp4",
            "out_%03d.jpg",
            interval_sec=10,
            max_frames=6,
            max_duration_sec=72,
        )
        joined = " ".join(cmd)
        self.assertIn("scale=480:-2,fps=1/10", joined)
        self.assertIn("-an", cmd)
        self.assertEqual(cmd[cmd.index("-q:v") + 1], "8")
        self.assertEqual(cmd[cmd.index("-frames:v") + 1], "6")
        self.assertEqual(cmd[cmd.index("-t") + 1], "72")

    def test_360_preset_skips_audio(self):
        from src.downloader import FORMAT_PRESETS

        fmt = FORMAT_PRESETS["360"]
        self.assertIn("height<=360", fmt)
        self.assertNotIn("bestaudio", fmt)
        self.assertIn("360", FORMAT_PRESETS)

    def test_brand_scan_uses_one_visual_pass(self):
        from src.video_affiliate.brand_scan import run_brand_scan

        src = inspect.getsource(run_brand_scan)
        self.assertNotIn("extract_brands_visual", src)
        self.assertIn("extract_visual_items", src)

    def test_affiliate_product_routes_exist(self):
        from src.web.app import app

        paths = {getattr(route, "path", None) for route in app.routes}
        self.assertIn("/api/videos/{video_id}/affiliate-scan", paths)
        self.assertIn("/api/videos/{video_id}/affiliate-products", paths)


if __name__ == "__main__":
    unittest.main()
