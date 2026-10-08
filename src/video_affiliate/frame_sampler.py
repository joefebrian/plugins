"""Download or stream YouTube video and extract frames for visual brand scan."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from ..youtube.transcripts import build_youtube_watch_url, extract_youtube_video_id

DEFAULT_INTERVAL_SEC = 10.0
DEFAULT_MAX_FRAMES = 8
DEFAULT_MAX_DURATION_SEC = 90
_FRAME_WIDTH = 480
_JPEG_Q = 8


class VisualScanError(ValueError):
    pass


@dataclass
class FrameSample:
    path: Path
    timestamp_sec: float


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _ydl_info(url: str) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": (
            "best[height<=480][ext=mp4][vcodec!=none]/"
            "best[height<=480][vcodec!=none]/"
            "best[height<=720][vcodec!=none]/best[vcodec!=none]"
        ),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise VisualScanError("Video YouTube tidak ditemukan")
    return info


def frame_extract_cmd(
    source: str,
    pattern: str,
    *,
    interval_sec: float,
    max_frames: int,
    max_duration_sec: float,
) -> list[str]:
    """Small JPEGs only: 480px, no audio, stop after max_frames."""
    interval = max(3.0, float(interval_sec))
    fps = int(interval) if interval == int(interval) else interval
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source,
        "-t", str(max_duration_sec),
        "-an",
        "-vf", f"scale={_FRAME_WIDTH}:-2,fps=1/{fps}",
        "-frames:v", str(max(1, int(max_frames))),
        "-q:v", str(_JPEG_Q),
        pattern,
    ]


def _pick_stream_url(info: dict) -> str:
    url = info.get("url") or ""
    if url:
        return str(url)
    formats = info.get("formats") or []
    for fmt in reversed(formats):
        if fmt.get("vcodec") and fmt.get("vcodec") != "none" and fmt.get("url"):
            return str(fmt["url"])
    raise VisualScanError("Tidak ada stream video yang bisa diakses")


def extract_frames_from_stream(
    stream_url: str,
    *,
    interval_sec: float = DEFAULT_INTERVAL_SEC,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_duration_sec: float = DEFAULT_MAX_DURATION_SEC,
) -> list[FrameSample]:
    """Extract frames directly from stream URL — no full video download."""
    if not _has_ffmpeg():
        raise VisualScanError("ffmpeg tidak ditemukan. Install: brew install ffmpeg")

    interval = max(3.0, float(interval_sec))
    max_frames = max(4, min(int(max_frames), 36))

    with tempfile.TemporaryDirectory(prefix="brand_frames_") as tmp:
        tmp_path = Path(tmp)
        pattern = str(tmp_path / "frame_%04d.jpg")
        cmd = frame_extract_cmd(
            stream_url,
            pattern,
            interval_sec=interval,
            max_frames=max_frames,
            max_duration_sec=max_duration_sec,
        )
        try:
            subprocess.run(cmd, check=True, timeout=240)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise VisualScanError(f"Gagal extract frame dari stream: {exc}") from exc

        raw_frames = sorted(tmp_path.glob("frame_*.jpg"))
        if not raw_frames:
            raise VisualScanError("Tidak ada frame yang berhasil diekstrak")

        persist_dir = Path(tempfile.mkdtemp(prefix="brand_scan_frames_"))
        samples: list[FrameSample] = []
        for idx, src in enumerate(raw_frames[:max_frames]):
            ts = idx * interval
            dest = persist_dir / f"frame_{idx:04d}_{int(ts)}s.jpg"
            shutil.copy2(src, dest)
            samples.append(FrameSample(path=dest, timestamp_sec=ts))
        return samples


def download_youtube_video_for_scan(
    url_or_id: str,
    work_dir: Path,
    *,
    max_height: int = 360,
) -> tuple[Path, float]:
    """Fallback: download compact MP4 when stream extract fails."""
    video_id = extract_youtube_video_id(url_or_id)
    if not video_id:
        raise VisualScanError("URL atau ID video YouTube tidak valid")

    work_dir.mkdir(parents=True, exist_ok=True)
    watch_url = build_youtube_watch_url(video_id)
    outtmpl = str(work_dir / f"{video_id}.%(ext)s")

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": outtmpl,
        "format": (
            f"best[height<={max_height}][ext=mp4][vcodec!=none]/"
            f"best[height<={max_height}][vcodec!=none]/"
            "best[vcodec!=none][ext=mp4]/best[vcodec!=none]"
        ),
        "merge_output_format": "mp4",
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(watch_url, download=True)
    if not info:
        raise VisualScanError("Gagal mengunduh video untuk analisis visual")

    duration = float(info.get("duration") or 0)
    candidates = sorted(work_dir.glob(f"{video_id}.*"))
    video_path = next((p for p in candidates if p.suffix.lower() in {".mp4", ".webm", ".mkv"}), None)
    if not video_path or not video_path.exists():
        raise VisualScanError("File video tidak ditemukan setelah unduhan")
    return video_path, duration


def extract_video_frames(
    video_path: Path,
    *,
    interval_sec: float = DEFAULT_INTERVAL_SEC,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_duration_sec: float = DEFAULT_MAX_DURATION_SEC,
) -> list[FrameSample]:
    """Extract JPEG frames from local video file (ffmpeg)."""
    if not video_path.exists():
        raise VisualScanError(f"Video tidak ditemukan: {video_path}")
    if not _has_ffmpeg():
        raise VisualScanError("ffmpeg tidak ditemukan. Install: brew install ffmpeg")

    interval = max(3.0, float(interval_sec))
    max_frames = max(4, min(int(max_frames), 36))

    with tempfile.TemporaryDirectory(prefix="brand_frames_") as tmp:
        tmp_path = Path(tmp)
        pattern = str(tmp_path / "frame_%04d.jpg")
        cmd = frame_extract_cmd(
            str(video_path),
            pattern,
            interval_sec=interval,
            max_frames=max_frames,
            max_duration_sec=max_duration_sec,
        )
        try:
            subprocess.run(cmd, check=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise VisualScanError(f"Gagal extract frame: {exc}") from exc

        raw_frames = sorted(tmp_path.glob("frame_*.jpg"))
        if not raw_frames:
            raise VisualScanError("Tidak ada frame yang berhasil diekstrak")

        persist_dir = Path(tempfile.mkdtemp(prefix="brand_scan_frames_"))
        samples: list[FrameSample] = []
        for idx, src in enumerate(raw_frames[:max_frames]):
            ts = idx * interval
            dest = persist_dir / f"frame_{idx:04d}_{int(ts)}s.jpg"
            shutil.copy2(src, dest)
            samples.append(FrameSample(path=dest, timestamp_sec=ts))
        return samples


def sample_youtube_frames(
    url_or_id: str,
    *,
    interval_sec: float = DEFAULT_INTERVAL_SEC,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_duration_sec: float = DEFAULT_MAX_DURATION_SEC,
) -> tuple[list[FrameSample], float, str]:
    """
    Sample frames from YouTube video.
    Returns (frames, duration_sec, method_note).
    Prefers stream extraction (no full download); falls back to compact download.
    """
    video_id = extract_youtube_video_id(url_or_id)
    if not video_id:
        raise VisualScanError("URL atau ID video YouTube tidak valid")
    watch_url = build_youtube_watch_url(video_id)

    info = _ydl_info(watch_url)
    duration = float(info.get("duration") or 0)

    if _has_ffmpeg():
        try:
            stream_url = _pick_stream_url(info)
            frames = extract_frames_from_stream(
                stream_url,
                interval_sec=interval_sec,
                max_frames=max_frames,
                max_duration_sec=max_duration_sec,
            )
            if frames:
                return frames, duration, "stream+ffmpeg"
        except VisualScanError:
            pass

    with tempfile.TemporaryDirectory(prefix="brand_visual_dl_") as tmp:
        work_dir = Path(tmp)
        video_path, duration = download_youtube_video_for_scan(watch_url, work_dir)
        try:
            frames = extract_video_frames(
                video_path,
                interval_sec=interval_sec,
                max_frames=max_frames,
                max_duration_sec=max_duration_sec,
            )
            return frames, duration, "download+ffmpeg"
        finally:
            cleanup_video_file(video_path)


def cleanup_frame_samples(samples: list[FrameSample]) -> None:
    seen_dirs: set[Path] = set()
    for sample in samples:
        sample.path.unlink(missing_ok=True)
        seen_dirs.add(sample.path.parent)
    for directory in seen_dirs:
        try:
            directory.rmdir()
        except OSError:
            pass


def cleanup_video_file(video_path: Path) -> None:
    video_path.unlink(missing_ok=True)
    try:
        video_path.parent.rmdir()
    except OSError:
        pass