"""YouTube Video Brand Scan — extract product/brand nouns for affiliate tagging."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ..ai.client import AIClientError, complete_with_failover
from ..db.models import YouTubeBrandMention, YouTubeBrandScan
from ..youtube.transcripts import YouTubeTranscriptError, fetch_youtube_video_content
from .mentions import ProductMention, dedupe_mentions, merge_mention_lists
from .noun_filter import NON_NOUN_WORDS, is_noun_only_phrase
from .product_intent_filter import filter_affiliate_mentions, is_affiliate_product_mention

_PRODUCT_CUES = (
    "produk", "product", "brand", "merek", "serum", "moisturizer", "sunscreen", "lipstick",
    "cushion", "foundation", "parfum", "perfume", "tas", "sepatu", "jam", "watch", "laptop",
    "handphone", "hp", "smartphone", "tablet", "kamera", "camera", "headphone", "earphone",
    "charger", "powerbank", "skincare", "makeup", "vitamin", "suplemen", "supplement",
    "dress", "baju", "celana", "hoodie", "sweater", "sandal", "sneakers", "topi", "ringlight",
)


def _normalize_name(name: str) -> str:
    text = re.sub(r"\s+", " ", (name or "").strip(" .,;:!?\"'()[]"))
    return text


def _clean_matched_phrase(phrase: str) -> str:
    """Buang kata sambung/depan di awal frasa hasil regex."""
    tokens = phrase.split()
    while tokens and tokens[0].lower() in NON_NOUN_WORDS:
        tokens.pop(0)
    return _normalize_name(" ".join(tokens))


def _is_valid_product_noun(
    name: str,
    *,
    context: str = "",
    channel_title: str = "",
    source: str = "",
    category: str = "",
) -> bool:
    """Kata benda + intent produk affiliate (bukan lokasi/tag vlog/cerita)."""
    text = _normalize_name(name)
    if not is_noun_only_phrase(text):
        return False
    ctx = context
    if category == "hashtag":
        ctx = f"#{text} {context}"
    return is_affiliate_product_mention(
        text,
        context=ctx,
        channel_title=channel_title,
        source=source or ("description" if category == "hashtag" else ""),
    )


def _filter_product_mentions(
    items: list[ProductMention],
    *,
    channel_title: str = "",
) -> list[ProductMention]:
    return filter_affiliate_mentions(items, channel_title=channel_title)


def _mentions_from_hashtags(description: str, *, channel_title: str = "") -> list[ProductMention]:
    items: list[ProductMention] = []
    for tag in re.findall(r"#([A-Za-z0-9_\u00C0-\u024F]{3,40})", description or ""):
        name = tag.replace("_", " ")
        if _is_valid_product_noun(
            name,
            context=f"#{tag}",
            channel_title=channel_title,
            source="description",
            category="hashtag",
        ):
            items.append(
                ProductMention(
                    name=_normalize_name(name),
                    mention_type="product",
                    category="hashtag",
                    context=f"#{tag}",
                    confidence=0.55,
                    source="description",
                )
            )
    return items


def _mentions_from_title(title: str, *, channel_title: str = "") -> list[ProductMention]:
    items: list[ProductMention] = []
    full_title = _normalize_name(title)
    chunks = re.split(r"[|/•·\-–—:]+", title or "")
    for chunk in chunks:
        name = _normalize_name(chunk)
        if name.lower() == full_title.lower():
            continue
        if _is_valid_product_noun(
            name,
            context=name,
            channel_title=channel_title,
            source="title",
            category="judul",
        ) and len(name.split()) <= 8:
            items.append(
                ProductMention(
                    name=name,
                    mention_type="product",
                    category="judul",
                    context=name,
                    confidence=0.72,
                    source="title",
                )
            )
    return items


def _mentions_from_cue_patterns(text: str, *, source: str, channel_title: str = "") -> list[ProductMention]:
    items: list[ProductMention] = []
    patterns = [
        r"(?:brand|merek|produk|product)\s+([A-Z][\w\s\-&]{1,48})",
        r"(?:produk|product|brand|merek)\s*:\s*([^.\n|]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.I):
            raw = match.group(1)
            blob = _clean_matched_phrase(raw.split(".")[0].split("|")[0][:120])
            parts = re.split(r"\s*,\s*|\s+dan\s+|\s*&\s*", blob) if blob else []
            if not parts:
                parts = [blob]
            for part in parts:
                name = _clean_matched_phrase(part)
                if not name:
                    continue
                if _is_valid_product_noun(
                    name,
                    context=match.group(0)[:120],
                    channel_title=channel_title,
                    source=source,
                    category="konteks",
                ):
                    items.append(
                        ProductMention(
                            name=name,
                            mention_type="product",
                            category="konteks",
                            context=match.group(0)[:120],
                            confidence=0.68,
                            source=source,
                        )
                    )
    return items


def _looks_like_brand_token(name: str) -> bool:
    words = name.split()
    if len(words) >= 2:
        return True
    token = words[0] if words else name
    if len(token) < 3:
        return False
    if re.search(r"\d", token):
        return True
    if token.isupper() and len(token) >= 3:
        return True
    return False


def _mentions_from_capitalized(text: str, *, source: str, channel_title: str = "") -> list[ProductMention]:
    items: list[ProductMention] = []
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z0-9][A-Za-z0-9]+){0,4})\b", text or ""):
        name = _normalize_name(match.group(1))
        if not _looks_like_brand_token(name):
            continue
        if name.lower() in NON_NOUN_WORDS:
            continue
        if re.search(r"(ID|Music|Lyrics|Follow|Website|Video)$", name, flags=re.I):
            continue
        if re.search(r"\bID\b", name) or re.search(r"ID[A-Z]", name):
            continue
        if re.search(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
            name,
            flags=re.I,
        ):
            continue
        if _is_valid_product_noun(
            name,
            context=name,
            channel_title=channel_title,
            source=source,
            category="nama proper",
        ):
            items.append(
                ProductMention(
                    name=name,
                    mention_type="brand",
                    category="nama proper",
                    context=name,
                    confidence=0.6,
                    source=source,
                )
            )
    return items


def _mentions_from_product_keywords(text: str, *, source: str, channel_title: str = "") -> list[ProductMention]:
    items: list[ProductMention] = []
    lowered = (text or "").lower()
    for cue in _PRODUCT_CUES:
        if cue not in lowered:
            continue
        for match in re.finditer(
            rf"(\b[\w\-&]{{2,30}}\s+)?{re.escape(cue)}(?:\s+[\w\-&]{{2,30}}){{0,5}}",
            text or "",
            flags=re.I,
        ):
            phrase = _clean_matched_phrase(match.group(0))
            if _is_valid_product_noun(
                phrase,
                context=match.group(0)[:120],
                channel_title=channel_title,
                source=source,
                category="keyword",
            ) and len(phrase.split()) >= 1:
                items.append(
                    ProductMention(
                        name=phrase,
                        mention_type="product",
                        category=cue,
                        context=phrase,
                        confidence=0.58,
                        source=source,
                    )
                )
    return items


def extract_products_heuristic(
    *,
    title: str,
    description: str,
    transcript: str,
    channel_title: str = "",
) -> list[ProductMention]:
    items: list[ProductMention] = []
    items.extend(_mentions_from_title(title, channel_title=channel_title))
    items.extend(_mentions_from_hashtags(description, channel_title=channel_title))
    items.extend(_mentions_from_cue_patterns(description, source="description", channel_title=channel_title))
    items.extend(_mentions_from_capitalized(title, source="title", channel_title=channel_title))
    items.extend(_mentions_from_product_keywords(title, source="title", channel_title=channel_title))
    items.extend(_mentions_from_product_keywords(description, source="description", channel_title=channel_title))
    return dedupe_mentions(_filter_product_mentions(items, channel_title=channel_title))[:40]


def extract_products_ai(
    session: Session,
    *,
    title: str,
    description: str,
    transcript: str,
    channel_title: str = "",
    user_id: int,
) -> list[ProductMention]:
    transcript_sample = (transcript or "")[:12000]
    description_sample = (description or "")[:4000]
    user_prompt = json.dumps(
        {
            "title": title,
            "channel": channel_title,
            "description": description_sample,
            "transcript": transcript_sample,
        },
        ensure_ascii=False,
    )
    system = (
        "Kamu adalah asisten affiliate marketing. Ekstrak HANYA produk/merek yang BISA DIBELI "
        "dan di-tag sebagai link affiliate (skincare, gadget, fashion, makanan bermerek, dll).\n"
        "ATURAN KETAT:\n"
        "- INCLUDE: nama brand + produk spesifik "
        "(contoh: 'Serum Vitamin C Skintific', 'iPhone 15', 'Sepatu Nike Air Max', 'Parfum Zara')\n"
        "- EXCLUDE lokasi & destinasi wisata (Central Park, New York, Bali, Times Square)\n"
        "- EXCLUDE hashtag/tag sosial & branding channel "
        "(FamilyVlog, MomLife, LifestyleIndonesia, nama creator/channel)\n"
        "- EXCLUDE CTA deskripsi (FOLLOW, subscribe, Instagram, TikTok, perjalanan seru)\n"
        "- EXCLUDE kata 'jam' yang berarti WAKTU (jam segini, jam 8 malam), bukan jam tangan\n"
        "- EXCLUDE percakapan informal subtitle (guys, tuh, pokoknya, datangnya)\n"
        "- EXCLUDE menu minuman/makanan & rasa (matcha, cranberry, coffee punch, stroberi macha)\n"
        "- EXCLUDE kata meta (merek, mereknya, nama, brand) tanpa nama produk spesifik\n"
        "- EXCLUDE nama bisnis/channel sendiri & produk buatan sendiri\n"
        "- EXCLUDE kelas kata non-benda: kerja, sifat, keterangan, ganti, depan, sambung, seru\n"
        "- Jika video vlog/kuliner tanpa merek konsumen pihak ketiga → kembalikan array kosong []\n"
        "- Bahasa: pertahankan bahasa asli nama produk\n"
        "Output HANYA JSON array valid, tanpa markdown:\n"
        '[{"name":"...","type":"brand|product","category":"...","context":"kutipan singkat",'
        '"confidence":0.0-1.0,"source":"title|description|transcript"}]'
    )
    result = complete_with_failover(session, system=system, user=user_prompt, user_id=user_id)
    text = result.text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise AIClientError("AI tidak mengembalikan array produk")

    items: list[ProductMention] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        name = _normalize_name(str(row.get("name") or ""))
        row_source = str(row.get("source") or "transcript")
        if not _is_valid_product_noun(
            name,
            context=str(row.get("context") or "")[:200],
            channel_title=channel_title,
            source=row_source,
        ):
            continue
        mention_type = str(row.get("type") or "product").lower()
        if mention_type not in ("brand", "product"):
            mention_type = "product"
        try:
            confidence = float(row.get("confidence") or 0.7)
        except (TypeError, ValueError):
            confidence = 0.7
        items.append(
            ProductMention(
                name=name,
                mention_type=mention_type,
                category=str(row.get("category") or ""),
                context=str(row.get("context") or "")[:200],
                confidence=max(0.0, min(confidence, 1.0)),
                source=str(row.get("source") or "transcript"),
            )
        )
    return dedupe_mentions(_filter_product_mentions(items, channel_title=channel_title))[:40]


def run_brand_scan(
    session: Session,
    *,
    user_id: int,
    url: str,
    use_ai: bool = True,
    use_visual: bool = True,
) -> dict[str, Any]:
    try:
        content = fetch_youtube_video_content(url)
    except YouTubeTranscriptError as exc:
        raise ValueError(str(exc)) from exc

    text_mentions: list[ProductMention] = []
    visual_mentions: list[ProductMention] = []
    method = "heuristic"
    ai_error = ""
    visual_note = ""

    if use_ai:
        try:
            text_mentions = extract_products_ai(
                session,
                title=content["title"],
                description=content["description"],
                transcript=content["transcript"],
                channel_title=content.get("channel_title") or "",
                user_id=user_id,
            )
            method = "ai"
        except (AIClientError, json.JSONDecodeError, ValueError) as exc:
            ai_error = str(exc)

    if not text_mentions:
        text_mentions = extract_products_heuristic(
            title=content["title"],
            description=content["description"],
            transcript=content["transcript"],
            channel_title=content.get("channel_title") or "",
        )
        if ai_error and method != "ai":
            method = "heuristic"

    visual_item_mentions: list[ProductMention] = []

    if use_visual and use_ai:
        # One light pass: worn/held products. Skip the second brand OCR+vision sample.
        from .visual_items_scan import extract_visual_items

        try:
            visual_item_mentions, item_note = extract_visual_items(
                session,
                url=content["url"],
                video_title=content["title"],
                user_id=user_id,
            )
            if visual_item_mentions:
                method = f"{method}+items"
            visual_note = item_note or ""
        except Exception as exc:
            visual_note = f"Item scan: {exc}"
    elif use_visual:
        visual_note = "Analisis visual butuh AI. Hasil ini dari teks saja."

    mentions = merge_mention_lists(text_mentions, visual_mentions, visual_item_mentions)[:50]

    scan = YouTubeBrandScan(
        user_id=user_id,
        video_url=content["url"],
        youtube_video_id=content["video_id"],
        video_title=content["title"],
        channel_title=content["channel_title"],
        thumbnail_url=content["thumbnail_url"],
        transcript_lang=content.get("transcript_lang"),
        has_transcript=bool(content.get("has_transcript")),
        extraction_method=method,
        mention_count=len(mentions),
        status="done",
    )
    session.add(scan)
    session.flush()

    for idx, mention in enumerate(mentions):
        session.add(
            YouTubeBrandMention(
                scan_id=scan.id,
                name=mention.name,
                mention_type=mention.mention_type,
                category=mention.category,
                context_snippet=mention.context,
                confidence=mention.confidence,
                source=mention.source,
                sort_order=idx,
            )
        )

    session.commit()
    session.refresh(scan)

    return scan_to_dict(scan, mentions=mentions, ai_note=ai_error, visual_note=visual_note)


def list_brand_scans(session: Session, user_id: int, *, limit: int = 30) -> list[dict]:
    rows = (
        session.query(YouTubeBrandScan)
        .filter_by(user_id=user_id)
        .order_by(YouTubeBrandScan.created_at.desc())
        .limit(limit)
        .all()
    )
    return [scan_to_dict(row, include_mentions=False) for row in rows]


def get_brand_scan(session: Session, scan_id: int, user_id: int) -> dict | None:
    scan = (
        session.query(YouTubeBrandScan)
        .filter_by(id=scan_id, user_id=user_id)
        .first()
    )
    if not scan:
        return None
    mentions = (
        session.query(YouTubeBrandMention)
        .filter_by(scan_id=scan.id)
        .order_by(YouTubeBrandMention.sort_order.asc())
        .all()
    )
    return scan_to_dict(scan, mentions=[mention_to_dict(m) for m in mentions])


def delete_brand_scan(session: Session, scan_id: int, user_id: int) -> bool:
    """Delete one brand scan and its mentions (cascade)."""
    scan = (
        session.query(YouTubeBrandScan)
        .filter_by(id=scan_id, user_id=user_id)
        .first()
    )
    if not scan:
        return False
    session.delete(scan)
    session.commit()
    return True


def delete_all_brand_scans(session: Session, user_id: int) -> int:
    """Delete all brand scans for a user. Returns count deleted."""
    rows = session.query(YouTubeBrandScan).filter_by(user_id=user_id).all()
    count = len(rows)
    for scan in rows:
        session.delete(scan)
    session.commit()
    return count


def scan_to_dict(
    scan: YouTubeBrandScan,
    *,
    mentions: list | None = None,
    include_mentions: bool = True,
    ai_note: str = "",
    visual_note: str = "",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": scan.id,
        "video_url": scan.video_url,
        "youtube_video_id": scan.youtube_video_id,
        "video_title": scan.video_title,
        "channel_title": scan.channel_title,
        "thumbnail_url": scan.thumbnail_url,
        "transcript_lang": scan.transcript_lang,
        "has_transcript": scan.has_transcript,
        "extraction_method": scan.extraction_method,
        "mention_count": scan.mention_count,
        "status": scan.status,
        "created_at": scan.created_at.isoformat() if scan.created_at else None,
    }
    if ai_note:
        data["ai_note"] = ai_note
    if visual_note:
        data["visual_note"] = visual_note
    if include_mentions:
        if mentions is None:
            mentions = []
        if mentions and hasattr(mentions[0], "name"):
            all_rows = [m.to_dict() for m in mentions]
        else:
            all_rows = mentions
        data["mentions"] = all_rows
        data["items"] = [m for m in all_rows if m.get("type") == "item"]
        data["brands"] = [m for m in all_rows if m.get("type") != "item"]
    return data


def mention_to_dict(row: YouTubeBrandMention) -> dict[str, Any]:
    return {
        "name": row.name,
        "type": row.mention_type,
        "category": row.category,
        "context": row.context_snippet,
        "confidence": row.confidence,
        "source": row.source,
    }