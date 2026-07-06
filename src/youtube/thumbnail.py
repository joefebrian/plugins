"""Auto-generate YouTube thumbnails from video frames."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .client import YouTubeAPIError

THUMB_DIR_NAME = "thumbnails"


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_video_frame(video_path: Path, *, at_seconds: float = 2.0) -> Path:
    """Extract a single JPEG frame from video."""
    if not video_path.exists():
        raise YouTubeAPIError(f"File tidak ditemukan: {video_path}")
    if not _has_ffmpeg():
        raise YouTubeAPIError(
            "ffmpeg tidak ditemukan. Install: brew install ffmpeg"
        )

    out = Path(tempfile.mkstemp(suffix=".jpg", prefix="yt_frame_")[1])
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(at_seconds),
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        out.unlink(missing_ok=True)
        raise YouTubeAPIError(f"Gagal extract frame video: {e}") from e

    if not out.exists() or out.stat().st_size < 1000:
        out.unlink(missing_ok=True)
        raise YouTubeAPIError("Frame video kosong atau corrupt")
    return out


def render_thumbnail_overlay(
    frame_path: Path,
    *,
    title: str = "",
    subtitle: str = "",
    views: Optional[int] = None,
    gmv: Optional[float] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """Add text overlay to frame — 1280x720 YouTube thumbnail."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise YouTubeAPIError("Pillow belum terinstall. Jalankan: pip install Pillow") from e

    img = Image.open(frame_path).convert("RGB")
    img = img.resize((1280, 720), Image.Resampling.LANCZOS)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Bottom gradient bar
    for y in range(400, 720):
        alpha = int(180 * ((y - 400) / 320))
        draw.line([(0, y), (1280, y)], fill=(0, 0, 0, alpha))

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font_lg = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 52)
        font_sm = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
    except OSError:
        font_lg = ImageFont.load_default()
        font_sm = ImageFont.load_default()

    title_text = (title or "Video")[:80]
    lines = _wrap_text(title_text, max_chars=28)

    y_pos = 520
    for line in lines[:2]:
        # Shadow
        draw.text((42, y_pos + 2), line, font=font_lg, fill=(0, 0, 0))
        draw.text((40, y_pos), line, font=font_lg, fill=(255, 255, 255))
        y_pos += 58

    badges = []
    if views is not None and views > 0:
        badges.append(f"{views:,} views".replace(",", "."))
    if gmv is not None and gmv > 0:
        badges.append(f"GMV Rp {int(gmv):,}".replace(",", "."))
    if subtitle:
        badges.append(subtitle[:40])

    if badges:
        badge_text = " · ".join(badges)
        draw.text((42, 660), badge_text, font=font_sm, fill=(255, 220, 100))

    out = output_path or Path(tempfile.mkstemp(suffix=".jpg", prefix="yt_thumb_")[1])
    img.save(out, "JPEG", quality=92, optimize=True)
    return out


def _wrap_text(text: str, max_chars: int = 28) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word])
        if len(trial) > max_chars and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [text[:max_chars]]


def generate_video_thumbnail(
    video_path: Path,
    *,
    title: str = "",
    subtitle: str = "",
    views: Optional[int] = None,
    gmv: Optional[float] = None,
    work_dir: Optional[Path] = None,
    keep_frame: bool = False,
) -> Path:
    """Full pipeline: extract frame → overlay → return thumbnail path."""
    frame = extract_video_frame(video_path)
    try:
        if work_dir:
            work_dir.mkdir(parents=True, exist_ok=True)
            out = work_dir / f"thumb_{video_path.stem}.jpg"
        else:
            out = None
        thumb = render_thumbnail_overlay(
            frame,
            title=title,
            subtitle=subtitle,
            views=views,
            gmv=gmv,
            output_path=out,
        )
        return thumb
    finally:
        if not keep_frame:
            frame.unlink(missing_ok=True)