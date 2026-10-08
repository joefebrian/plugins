"""Phase 2 — Vision AI brand/product detection from video frames."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from ..ai.client import AIClientError, complete_vision_with_failover
from .mentions import ProductMention

_VISION_SYSTEM = (
    "Kamu menganalisis frame video YouTube untuk YouTube Shopping affiliate tagging.\n"
    "Tugas: deteksi merek/produk konsumen yang TAMPIL VISUAL di frame "
    "(logo, kemasan, label, produk dipakai/dipegang, teks overlay).\n"
    "INCLUDE: Nike, Apple, Stanley, Skintific, Zara, Samsung, dll.\n"
    "EXCLUDE: nama lokasi, nama creator/channel, menu minuman (CRANBERRY, MATCHA, COFFEE).\n"
    "EXCLUDE: teks UI YouTube, subtitle, menu warung/kafe sendiri.\n"
    "HANYA merek konsumen pihak ketiga yang bisa dibeli online (Nike, Samsung, Stanley).\n"
    "Jika tidak ada produk/merek konsumen → []\n"
    "Output HANYA JSON array:\n"
    '[{"name":"...","type":"brand|product","category":"...","timestamp_sec":0,'
    '"confidence":0.0-1.0,"context":"apa yang terlihat"}]'
)

_BATCH_SIZE = 4


def _parse_vision_payload(text: str) -> list[dict]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    payload = json.loads(raw)
    return payload if isinstance(payload, list) else []


def _detect_batch(
    session: Session,
    *,
    frames: list,
    video_title: str,
    channel_title: str,
    user_id: int,
) -> list[ProductMention]:
    if not frames:
        return []

    image_paths = [f.path for f in frames]
    timestamps = [int(f.timestamp_sec) for f in frames]
    user_prompt = (
        f"Video: {video_title}\nChannel: {channel_title}\n"
        f"Frame timestamps (detik): {timestamps}\n"
        "Analisis setiap frame terlampir. Kembalikan merek/produk yang terlihat."
    )

    result = complete_vision_with_failover(
        session,
        system=_VISION_SYSTEM,
        user=user_prompt,
        image_paths=image_paths,
        user_id=user_id,
    )
    rows = _parse_vision_payload(result.text)

    items: list[ProductMention] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = re.sub(r"\s+", " ", str(row.get("name") or "").strip(" .,;:!?"))
        if len(name) < 2:
            continue
        mention_type = str(row.get("type") or "brand").lower()
        if mention_type not in ("brand", "product"):
            mention_type = "brand"
        try:
            confidence = float(row.get("confidence") or 0.65)
        except (TypeError, ValueError):
            confidence = 0.65
        ts = row.get("timestamp_sec")
        ts_label = f"@{int(ts)}s" if ts is not None else ""
        items.append(
            ProductMention(
                name=name,
                mention_type=mention_type,
                category=str(row.get("category") or "vision"),
                context=f"Vision {ts_label}: {str(row.get('context') or '')[:160]}",
                confidence=max(0.0, min(confidence, 1.0)),
                source="vision",
            )
        )
    return items


def scan_frames_vision(
    session: Session,
    *,
    frames: list,
    video_title: str = "",
    channel_title: str = "",
    user_id: int,
) -> tuple[list[ProductMention], str]:
    """Run vision AI on frame batches. Returns mentions and optional error note."""
    if not frames:
        return [], ""

    all_items: list[ProductMention] = []
    error_notes: list[str] = []

    for start in range(0, len(frames), _BATCH_SIZE):
        batch = frames[start : start + _BATCH_SIZE]
        try:
            all_items.extend(
                _detect_batch(
                    session,
                    frames=batch,
                    video_title=video_title,
                    channel_title=channel_title,
                    user_id=user_id,
                )
            )
        except (AIClientError, json.JSONDecodeError, ValueError) as exc:
            error_notes.append(str(exc))
            break

    return all_items, "; ".join(error_notes)