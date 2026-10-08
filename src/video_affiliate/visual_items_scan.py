"""Vision AI — extract visible items (outfit, accessories, held objects) from video frames.

Primary: descriptive items (blue dress, gold necklace) + usage/function.
Brand is optional when logo/tag is visible.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..ai.client import AIClientError, complete_vision_with_failover
from .frame_sampler import (
    DEFAULT_INTERVAL_SEC,
    VisualScanError,
    cleanup_frame_samples,
    extract_video_frames,
    sample_youtube_frames,
)
from .mentions import ProductMention, dedupe_mentions

ITEM_SCAN_MAX_DURATION = 72
ITEM_SCAN_MAX_FRAMES = 6
_ITEM_BATCH_SIZE = 6

_ITEM_VISION_SYSTEM = (
    "Kamu menganalisis frame video untuk menemukan PRODUK yang bisa di-tag affiliate.\n"
    "FOKUS UTAMA — deteksi SEMUA benda ini:\n"
    "1. Pakaian yang DIPAKAI (dress, baju, celana, jaket, hoodie, kaos, rok)\n"
    "2. Aksesoris di tubuh (kalung, anting, gelang, cincin, jam tangan, topi, kacamata)\n"
    "3. Tas / sepatu / sandal yang dipakai\n"
    "4. Benda yang DIGANTUNG (tas gantung, hanger, gantungan, aksesoris gantung)\n"
    "5. Benda dipegang tangan (HP, botol, makeup, skincare, makanan kemasan, mikrofon)\n"
    "6. Produk di latar dekat tubuh yang jelas sebagai merchandise\n"
    "FORMAT NAMA: [warna] + [jenis] (+ detail)\n"
    "Contoh: 'blue floral dress', 'gold chain necklace', 'black smartphone', 'white tote bag'\n"
    "FIELD function (wajib): worn_body | held_hand | hanging | footwear | bag | other\n"
    "FIELD brand (opsional): hanya jika logo/merek terbaca jelas\n"
    "EXCLUDE: dinding, pohon, jalan, menu warung, UI app, subtitle, wajah orang\n"
    "Output HANYA JSON array:\n"
    '[{"name":"blue dress","category":"clothing/dress","color":"blue","function":"worn_body",'
    '"brand":"","context":"dipakai presenter","timestamp_sec":0,"confidence":0.0-1.0}]'
)

_ITEM_NOISE = frozenset({
    "wall", "floor", "ceiling", "background", "tree", "sky", "road", "street",
    "table", "chair", "door", "window", "room", "kitchen", "coffee", "menu",
    "welcome", "subscribe", "instagram", "youtube", "video", "pov", "slow",
    "person", "face", "hair", "hand", "skin",
})

_COLOR_WORDS = frozenset({
    "red", "blue", "green", "yellow", "black", "white", "pink", "purple", "orange",
    "brown", "gray", "grey", "gold", "golden", "silver", "beige", "navy", "cream",
    "merah", "biru", "hijau", "kuning", "hitam", "putih", "ungu", "coklat", "emas",
})

_FUNCTION_LABELS = {
    "worn_body": "Dipakai di tubuh",
    "held_hand": "Dipegang tangan",
    "hanging": "Digantung",
    "footwear": "Alas kaki",
    "bag": "Tas",
    "other": "Terlihat di video",
}


def _parse_items_payload(text: str) -> list[dict]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    payload = json.loads(raw)
    return payload if isinstance(payload, list) else []


def _normalize_item_name(name: str, color: str = "", brand: str = "") -> str:
    text = re.sub(r"\s+", " ", (name or "").strip(" .,;:!?"))
    if not text:
        return ""
    lower = text.lower()
    if color and color.lower() not in lower:
        text = f"{color.strip()} {text}"
    if brand and brand.lower() not in lower:
        text = f"{text} ({brand.strip()})"
    return text.strip()


def _is_valid_visual_item(name: str, category: str = "") -> bool:
    if not name or len(name) < 4 or len(name) > 80:
        return False
    tokens = [t for t in re.split(r"[\s\-_/]+", name.lower()) if t]
    if not tokens:
        return False
    if all(t in _ITEM_NOISE for t in tokens):
        return False
    if tokens[0] in _ITEM_NOISE:
        return False
    clothing = {
        "dress", "dresses", "shirt", "blouse", "top", "skirt", "pants", "jeans",
        "jacket", "coat", "hoodie", "sweater", "cardigan", "shorts", "baju", "rok",
        "celana", "jaket", "kaos", "blazer", "gown", "jumpsuit", "hijab", "scarf",
    }
    accessories = {
        "necklace", "kalung", "earring", "earrings", "bracelet", "ring", "cincin",
        "watch", "jam", "bag", "handbag", "tas", "purse", "clutch", "backpack",
        "hat", "cap", "topi", "belt", "sunglasses", "glasses", "kacamata",
    }
    wearable = clothing | accessories | {
        "shoe", "shoes", "sneaker", "sneakers", "boot", "boots", "sandal", "sepatu",
        "phone", "handphone", "hp", "smartphone", "lipstick", "perfume", "bottle",
        "cup", "mug", "botol", "tumbler", "mic", "microphone", "makeup", "serum",
        "hanger", "gantungan", "sabun", "shampoo", "sampo", "lotion", "mist",
        "skincare", "parfum", "deodorant", "toner", "cleanser", "vitamin",
        "powerbank", "charger", "earphone", "headphone", "masker", "jumbo",
    }
    has_wearable = any(t in wearable for t in tokens)
    has_color = any(t in _COLOR_WORDS for t in tokens)
    cat = (category or "").lower()
    has_cat = any(
        x in cat
        for x in (
            "cloth", "dress", "accessor", "shoe", "bag", "watch", "jewel", "wear",
            "held", "hang", "product", "ocr", "hashtag", "beauty", "skincare", "soap",
        )
    )
    return has_wearable or (has_color and len(tokens) >= 2) or has_cat


def _normalize_function(raw: str) -> str:
    key = (raw or "other").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "worn": "worn_body",
        "wearing": "worn_body",
        "dipakai": "worn_body",
        "body": "worn_body",
        "held": "held_hand",
        "holding": "held_hand",
        "hand": "held_hand",
        "dipegang": "held_hand",
        "hang": "hanging",
        "digantung": "hanging",
        "shoes": "footwear",
        "sepatu": "footwear",
        "tas": "bag",
    }
    key = aliases.get(key, key)
    return key if key in _FUNCTION_LABELS else "other"


def _row_to_product_dict(row: dict) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    color = str(row.get("color") or "").strip()
    brand = str(row.get("brand") or "").strip()
    name = _normalize_item_name(str(row.get("name") or ""), color=color, brand=brand)
    category = str(row.get("category") or "item").strip()
    if not _is_valid_visual_item(name, category=category):
        return None
    try:
        confidence = float(row.get("confidence") or 0.68)
    except (TypeError, ValueError):
        confidence = 0.68
    fn = _normalize_function(str(row.get("function") or "other"))
    ts = row.get("timestamp_sec")
    return {
        "name": name,
        "category": category,
        "color": color or None,
        "brand": brand or None,
        "function": fn,
        "function_label": _FUNCTION_LABELS.get(fn, _FUNCTION_LABELS["other"]),
        "context": str(row.get("context") or "")[:200],
        "timestamp_sec": int(ts) if ts is not None else None,
        "confidence": max(0.0, min(confidence, 1.0)),
        "source": "vision-item",
    }


def _detect_items_batch(
    session: Session,
    *,
    frames: list,
    video_title: str,
    user_id: int,
) -> list[dict[str, Any]]:
    if not frames:
        return []

    timestamps = [int(f.timestamp_sec) for f in frames]
    user_prompt = (
        f"Video: {video_title}\n"
        f"Frame timestamps (detik): {timestamps}\n"
        "Deteksi SEMUA produk/benda yang dipakai di tubuh, digantung, "
        "dipegang tangan, atau alas kaki. Sertakan warna + jenis + function. "
        "Brand hanya jika logo terbaca."
    )
    result = complete_vision_with_failover(
        session,
        system=_ITEM_VISION_SYSTEM,
        user=user_prompt,
        image_paths=[f.path for f in frames],
        user_id=user_id,
    )
    rows = _parse_items_payload(result.text)
    items: list[dict[str, Any]] = []
    for row in rows:
        product = _row_to_product_dict(row)
        if product:
            items.append(product)
    return items


def _merge_similar_product_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    keys_by_stem: dict[str, str] = {}

    for item in items:
        name = str(item.get("name") or "")
        tokens = [t for t in re.split(r"[\s\-_/()]+", name.lower()) if t and t not in {"the", "a"}]
        stem_tokens = [t for t in tokens if t not in _COLOR_WORDS]
        stem = " ".join(stem_tokens[:3]) if stem_tokens else " ".join(tokens[:2])
        if not stem:
            continue

        matched_key = None
        for existing_stem, key in keys_by_stem.items():
            if stem in existing_stem or existing_stem in stem:
                matched_key = key
                break

        if matched_key and matched_key in groups:
            prev = groups[matched_key]
            if float(item.get("confidence") or 0) > float(prev.get("confidence") or 0) or len(name) > len(str(prev.get("name") or "")):
                groups[matched_key] = item
        else:
            key = name.lower()
            groups[key] = item
            keys_by_stem[stem] = key

    return sorted(groups.values(), key=lambda x: -float(x.get("confidence") or 0))[:25]


def _products_to_mentions(products: list[dict[str, Any]]) -> list[ProductMention]:
    out: list[ProductMention] = []
    for p in products:
        ts = p.get("timestamp_sec")
        ts_label = f"@{int(ts)}s" if ts is not None else ""
        fn_label = p.get("function_label") or ""
        ctx = f"{fn_label} {ts_label}: {p.get('context') or ''}".strip()
        out.append(
            ProductMention(
                name=str(p.get("name") or ""),
                mention_type="item",
                category=str(p.get("category") or "item"),
                context=ctx[:200],
                confidence=float(p.get("confidence") or 0.68),
                source="vision-item",
            )
        )
    return out


def _short_ai_error(exc: Exception | str) -> str:
    """Human-readable AI error without dumping full HTTP JSON."""
    text = str(exc or "")
    lower = text.lower()
    if "insufficient_quota" in lower or "exceeded your current quota" in lower or "limit: 0" in lower:
        return "semua key quota habis (free tier / billing). Tambah key GSuite lain atau upgrade plan"
    if "rate limit" in lower or "resource_exhausted" in lower:
        return "rate limit sementara — coba lagi sebentar atau ganti key"
    if "tidak ada ai provider" in lower:
        return "tidak ada AI provider aktif di Settings → AI"
    if "semua ai provider gagal" in lower:
        # Keep first line only
        head = text.split(".")[0].strip()
        n = 0
        if "dicoba" in lower:
            import re as _re
            m = _re.search(r"(\d+)\s*dicoba", lower)
            if m:
                n = int(m.group(1))
        if n:
            return f"{n} key gagal (quota/rate limit). Import key Gemini GSuite baru di Settings → AI"
        return "semua key gagal (quota/rate limit). Cek Settings → AI"
    # Truncate raw message
    clean = re.sub(r"\s+", " ", text)
    clean = re.sub(r"\{[^}]{20,}\}", "{…}", clean)
    return clean[:160]


def _run_vision_on_frames(
    session: Session,
    *,
    frames: list,
    video_title: str,
    user_id: int,
    notes: list[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Returns (products, vision_ok). vision_ok=False if AI unavailable/failed."""
    all_items: list[dict[str, Any]] = []
    vision_ok = False
    for start in range(0, len(frames), _ITEM_BATCH_SIZE):
        batch = frames[start : start + _ITEM_BATCH_SIZE]
        try:
            batch_items = _detect_items_batch(
                session,
                frames=batch,
                video_title=video_title,
                user_id=user_id,
            )
            all_items.extend(batch_items)
            vision_ok = True
        except (AIClientError, json.JSONDecodeError, ValueError) as exc:
            notes.append(f"Vision AI dilewati: {_short_ai_error(exc)}")
            break
    return _merge_similar_product_dicts(all_items), vision_ok


def extract_visual_items(
    session: Session,
    *,
    url: str,
    video_title: str = "",
    user_id: int,
    max_duration_sec: int = ITEM_SCAN_MAX_DURATION,
) -> tuple[list[ProductMention], str]:
    """YouTube URL path — returns ProductMention list for brand scan merge."""
    products, note = extract_visual_products_from_url(
        session,
        url=url,
        video_title=video_title,
        user_id=user_id,
        max_duration_sec=max_duration_sec,
    )
    return _products_to_mentions(products), note


def extract_visual_products_from_url(
    session: Session,
    *,
    url: str,
    video_title: str = "",
    user_id: int,
    max_duration_sec: int = ITEM_SCAN_MAX_DURATION,
) -> tuple[list[dict[str, Any]], str]:
    frames = []
    notes: list[str] = []
    try:
        try:
            frames, duration, method = sample_youtube_frames(
                url,
                interval_sec=DEFAULT_INTERVAL_SEC,
                max_frames=ITEM_SCAN_MAX_FRAMES,
                max_duration_sec=float(max_duration_sec),
            )
        except VisualScanError as exc:
            return [], str(exc)

        notes.append(f"{method} · {min(int(duration), max_duration_sec)}s · {len(frames)} frame")
        if not frames:
            return [], "Tidak ada frame untuk item scan"

        products, vision_ok = _run_vision_on_frames(
            session, frames=frames, video_title=video_title, user_id=user_id, notes=notes
        )
        if products:
            notes.append(f"{len(products)} item terdeteksi")
        elif not vision_ok:
            notes.append("Vision AI gagal / tidak tersedia")
        return products, " · ".join(notes)
    finally:
        cleanup_frame_samples(frames)


def extract_visual_products_from_file(
    session: Session,
    *,
    video_path: Path | str,
    video_title: str = "",
    description: str = "",
    user_id: int,
    max_duration_sec: int = ITEM_SCAN_MAX_DURATION,
) -> tuple[list[dict[str, Any]], str]:
    """Analyze local video: Vision AI + OCR + title/hashtag fallback."""
    from .ocr_scan import ocr_image, tesseract_available
    from .product_ocr_extract import (
        merge_product_lists,
        products_from_ocr_texts,
        products_from_title,
    )

    path = Path(video_path)
    if not path.exists() or path.stat().st_size < 1000:
        return [], "File video tidak ditemukan di server"

    frames = []
    notes: list[str] = []
    try:
        try:
            frames = extract_video_frames(
                path,
                interval_sec=DEFAULT_INTERVAL_SEC,
                max_frames=ITEM_SCAN_MAX_FRAMES,
                max_duration_sec=float(max_duration_sec),
            )
        except VisualScanError as exc:
            return [], str(exc)

        notes.append(f"local-file · {len(frames)} frame · max {max_duration_sec}s")
        if not frames:
            return [], "Tidak ada frame dari file video"

        title = video_title or path.stem
        title_products = products_from_title(title, description)
        if title_products:
            notes.append(f"Judul/hashtag: {len(title_products)}")

        vision_products, vision_ok = _run_vision_on_frames(
            session,
            frames=frames,
            video_title=title,
            user_id=user_id,
            notes=notes,
        )
        if vision_products:
            notes.append(f"Vision: {len(vision_products)}")
        elif vision_ok:
            notes.append("Vision: 0")

        ocr_products: list[dict[str, Any]] = []
        if vision_products:
            notes.append("OCR dilewati")
        elif tesseract_available():
            frame_texts: list[tuple[float, str]] = []
            for frame in frames:
                text = ocr_image(frame.path)
                if text:
                    frame_texts.append((frame.timestamp_sec, text))
            ocr_products = products_from_ocr_texts(frame_texts)
            notes.append(f"OCR: {len(ocr_products)}")
        else:
            notes.append("OCR dilewati (install tesseract)")

        products = merge_product_lists(vision_products, ocr_products, title_products)
        if products:
            sources = []
            if vision_products:
                sources.append("Vision")
            if ocr_products:
                sources.append("OCR")
            if title_products:
                sources.append("hashtag")
            notes.append(f"✓ {len(products)} produk ({'+'.join(sources) or 'mixed'})")
            # User-friendly summary first when vision failed but OCR worked
            summary = f"{len(products)} produk dari {'+'.join(sources)}"
            if not vision_ok:
                summary += " · Vision AI tidak dipakai (quota key habis) — hasil dari OCR/hashtag"
            detail = " · ".join(notes)
            return products, f"{summary} · {detail}"

        # Empty: surface actionable reason (don't pretend "no products" if AI dead)
        if not vision_ok and not ocr_products and not title_products:
            raise AIClientError(
                "Tidak ada produk terdeteksi. Vision AI: "
                + _short_ai_error(" ".join(notes))
                + " — cek Settings → AI (tambah key Gemini GSuite / reset cooldown)."
            )
        return products, " · ".join(notes)
    finally:
        cleanup_frame_samples(frames)
