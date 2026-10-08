"""Visual brand scan — Phase 1 OCR + Phase 2 Vision AI on sampled frames."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .frame_sampler import VisualScanError, cleanup_frame_samples, sample_youtube_frames
from .mentions import ProductMention, dedupe_mentions
from .ocr_scan import scan_frames_ocr, tesseract_available
from .product_intent_filter import filter_affiliate_mentions
from .vision_scan import scan_frames_vision


def extract_brands_visual(
    session: Session,
    *,
    url: str,
    video_title: str = "",
    channel_title: str = "",
    user_id: int,
    use_ocr: bool = True,
    use_vision: bool = True,
) -> tuple[list[ProductMention], str]:
    """
    Sample frames, run OCR + vision AI.
    Returns (mentions, note) where note explains steps taken or errors.
    """
    notes: list[str] = []
    frames = []

    try:
        try:
            frames, duration, sample_method = sample_youtube_frames(url)
        except VisualScanError as exc:
            return [], str(exc)

        notes.append(f"{sample_method} · {int(duration)}s · {len(frames)} frame")

        if not frames:
            return [], "Tidak ada frame untuk dianalisis"

        mentions: list[ProductMention] = []

        if use_ocr:
            if tesseract_available():
                ocr_items = scan_frames_ocr(frames)
                mentions.extend(ocr_items)
                notes.append(f"OCR: {len(ocr_items)} kandidat")
            else:
                notes.append("OCR dilewati (brew install tesseract)")

        if use_vision:
            vision_items, vision_err = scan_frames_vision(
                session,
                frames=frames,
                video_title=video_title,
                channel_title=channel_title,
                user_id=user_id,
            )
            mentions.extend(vision_items)
            if vision_items:
                notes.append(f"Vision AI: {len(vision_items)} deteksi")
            if vision_err:
                notes.append(f"Vision: {vision_err}")

        filtered = dedupe_mentions(
            filter_affiliate_mentions(mentions, channel_title=channel_title)
        )[:40]
        return filtered, " · ".join(notes)

    finally:
        cleanup_frame_samples(frames)