"""Phase 1 — OCR visible text from video frames (tesseract)."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .mentions import ProductMention

_OCR_BRAND_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&]{4,24}(?:\s+[A-Z0-9][A-Za-z0-9&]{2,24}){0,2})\b"
)
_OCR_NOISE = frozenset({
    "the", "and", "for", "with", "youtube", "subscribe", "follow", "like", "share",
    "video", "channel", "instagram", "tiktok", "official", "music", "lyrics",
    "welcome", "back", "please", "coffee", "slow", "living", "enjoy", "happy",
})


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(image_path: Path) -> str:
    if not tesseract_available():
        return ""
    if not image_path.exists():
        return ""
    try:
        proc = subprocess.run(
            ["tesseract", str(image_path), "stdout", "-l", "eng+ind", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return re.sub(r"\s+", " ", (proc.stdout or "").strip())


def _mentions_from_ocr_text(text: str, *, timestamp_sec: float) -> list[ProductMention]:
    items: list[ProductMention] = []
    if not text or len(text) < 3:
        return items

    seen: set[str] = set()
    for match in _OCR_BRAND_RE.finditer(text):
        name = re.sub(r"\s+", " ", match.group(1).strip(" .,;:!?"))
        if len(name) < 4:
            continue
        key = name.lower()
        if key in seen or key in _OCR_NOISE:
            continue
        seen.add(key)
        items.append(
            ProductMention(
                name=name,
                mention_type="brand",
                category="ocr",
                context=f"OCR @ {int(timestamp_sec)}s: {text[:120]}",
                confidence=0.52,
                source="ocr",
            )
        )
    return items


def scan_frames_ocr(frames: list) -> list[ProductMention]:
    """Run OCR on frames; keep brands seen in 2+ frames or strong multi-word names."""
    if not tesseract_available():
        return []

    raw_items: list[ProductMention] = []
    name_counts: Counter[str] = Counter()

    for frame in frames:
        text = ocr_image(frame.path)
        frame_items = _mentions_from_ocr_text(text, timestamp_sec=frame.timestamp_sec)
        raw_items.extend(frame_items)
        for item in frame_items:
            name_counts[item.name.lower()] += 1

    # Cross-frame validation: single-word OCR must appear 2+ times
    items: list[ProductMention] = []
    seen: set[str] = set()
    for item in raw_items:
        key = item.name.lower()
        if key in seen:
            continue
        words = item.name.split()
        if len(words) == 1 and name_counts[key] < 2:
            continue
        if len(words) >= 2 and name_counts[key] < 1:
            continue
        seen.add(key)
        boosted = min(0.72, item.confidence + 0.05 * name_counts[key])
        items.append(
            ProductMention(
                name=item.name,
                mention_type=item.mention_type,
                category=item.category,
                context=item.context,
                confidence=boosted,
                source=item.source,
            )
        )
    return items