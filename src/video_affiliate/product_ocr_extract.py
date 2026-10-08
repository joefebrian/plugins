"""Extract affiliate product candidates from OCR text + video title/hashtags.

Used as fallback when Vision AI is unavailable or returns empty.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Consumer product keywords (ID + EN) that signal affiliate-ready items
_PRODUCT_KEYWORDS = {
    "sabun": "sabun mandi",
    "sabunmandi": "sabun mandi",
    "sabunmandicair": "sabun mandi cair",
    "shampoo": "shampoo",
    "sampo": "shampoo",
    "conditioner": "conditioner",
    "bodymist": "body mist",
    "bodywash": "body wash",
    "bodylotion": "body lotion",
    "lotion": "body lotion",
    "serum": "serum",
    "moisturizer": "moisturizer",
    "sunscreen": "sunscreen",
    "skincare": "skincare",
    "makeup": "makeup",
    "lipstick": "lipstick",
    "parfum": "parfum",
    "perfume": "perfume",
    "deodorant": "deodorant",
    "masker": "masker wajah",
    "toner": "toner",
    "cleanser": "cleanser",
    "cushion": "cushion",
    "foundation": "foundation",
    "dress": "dress",
    "baju": "baju",
    "celana": "celana",
    "hoodie": "hoodie",
    "sepatu": "sepatu",
    "tas": "tas",
    "kalung": "kalung",
    "jam": "jam tangan",
    "hp": "handphone",
    "handphone": "handphone",
    "smartphone": "smartphone",
    "earphone": "earphone",
    "headphone": "headphone",
    "powerbank": "powerbank",
    "charger": "charger",
    "vitamin": "vitamin",
    "suplemen": "suplemen",
    "kopi": "kopi",
    "coffee": "coffee",
    "tumbler": "tumbler",
    "botol": "botol",
    "promo": None,  # skip alone
    "jumbo": None,
    "mandi": None,
    "cair": None,
}

_OCR_PRODUCT_PHRASE = re.compile(
    r"\b((?:PROMO\s+)?(?:SABUN(?:\s+MANDI)?(?:\s+CAIR)?|SHAMPOO|BODY\s*MIST|BODY\s*WASH|"
    r"SERUM|SKINCARE|PARFUM|PERFUME|LIPSTICK|FOUNDATION|CUSHION|MOISTURIZER|SUNSCREEN|"
    r"TONER|MASKER|DRESS|HOODIE|SNEAKERS|HANDPHONE|IPHONE|SAMSUNG)"
    r"(?:\s+[A-Z0-9][A-Za-z0-9%]{1,20}){0,4})\b",
    flags=re.I,
)

_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_\u00C0-\u024F]{3,40})")

_JUNK_OCR = frozenset({
    "promo", "jumbo", "ml", "gr", "rp", "the", "and", "for", "with",
})


def _clean_phrase(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip(" .,;:!?\"'|"))
    # drop isolated single chars / OCR garbage tokens
    parts = []
    for p in text.split():
        if len(p) <= 1 and not p.isdigit():
            continue
        if re.fullmatch(r"[a-z]{1,2}", p, flags=re.I) and p.lower() not in {"ml", "gr", "hp"}:
            continue
        parts.append(p)
    return " ".join(parts).strip()


def products_from_title(title: str, description: str = "") -> list[dict[str, Any]]:
    """Parse hashtags and product words from title/description."""
    combined = f"{title or ''} {description or ''}"
    found: dict[str, dict[str, Any]] = {}

    for tag in _HASHTAG_RE.findall(combined):
        raw = tag.replace("_", "")
        lower = raw.lower()
        # split camelCase-ish hashtags: promosabunmandi
        if lower in _PRODUCT_KEYWORDS and _PRODUCT_KEYWORDS[lower]:
            name = _PRODUCT_KEYWORDS[lower]
            found[name.lower()] = {
                "name": name.title() if name.islower() else name,
                "category": "hashtag",
                "function": "product",
                "function_label": "Disebut di judul/hashtag",
                "brand": None,
                "color": None,
                "context": f"#{tag}",
                "timestamp_sec": None,
                "confidence": 0.55,
                "source": "title-hashtag",
            }
            continue
        # compound: promosabunmandi, sabunmandicair
        for key, label in _PRODUCT_KEYWORDS.items():
            if not label:
                continue
            if key in lower and len(key) >= 5:
                found[label.lower()] = {
                    "name": label,
                    "category": "hashtag",
                    "function": "product",
                    "function_label": "Disebut di judul/hashtag",
                    "brand": None,
                    "color": None,
                    "context": f"#{tag}",
                    "timestamp_sec": None,
                    "confidence": 0.58,
                    "source": "title-hashtag",
                }

    # free text product words
    words = re.findall(r"[A-Za-z\u00C0-\u024F]{4,}", combined.lower())
    for w in words:
        if w in _PRODUCT_KEYWORDS and _PRODUCT_KEYWORDS[w]:
            label = _PRODUCT_KEYWORDS[w]
            found.setdefault(label.lower(), {
                "name": label,
                "category": "title",
                "function": "product",
                "function_label": "Disebut di judul",
                "brand": None,
                "color": None,
                "context": title[:80] if title else "",
                "timestamp_sec": None,
                "confidence": 0.5,
                "source": "title",
            })

    return list(found.values())


def products_from_ocr_texts(frame_texts: list[tuple[float, str]]) -> list[dict[str, Any]]:
    """frame_texts: list of (timestamp_sec, ocr_text)."""
    phrase_hits: Counter[str] = Counter()
    phrase_ts: dict[str, float] = {}
    phrase_sample: dict[str, str] = {}

    for ts, text in frame_texts:
        if not text:
            continue
        # Normalize OCR noise slightly
        cleaned = text.replace("|", " ").replace("\\", " ")
        cleaned = re.sub(r"[^\w\s\-%@.,]", " ", cleaned, flags=re.UNICODE)
        cleaned = re.sub(r"\s+", " ", cleaned)

        for match in _OCR_PRODUCT_PHRASE.finditer(cleaned):
            phrase = _clean_phrase(match.group(1))
            if len(phrase) < 4:
                continue
            key = phrase.lower()
            if key in _JUNK_OCR:
                continue
            phrase_hits[key] += 1
            phrase_ts.setdefault(key, ts)
            phrase_sample.setdefault(key, phrase)

        # keyword presence
        lower = cleaned.lower()
        for key, label in _PRODUCT_KEYWORDS.items():
            if not label:
                continue
            if re.search(rf"\b{re.escape(key)}\b", lower) or key in lower.replace(" ", ""):
                k = label.lower()
                phrase_hits[k] += 1
                phrase_ts.setdefault(k, ts)
                phrase_sample.setdefault(k, label)

    products: list[dict[str, Any]] = []
    for key, count in phrase_hits.most_common(20):
        name = phrase_sample.get(key, key)
        # Prefer multi-word product phrases
        conf = min(0.75, 0.48 + 0.05 * count)
        products.append({
            "name": name.title() if name.islower() else name,
            "category": "ocr-product",
            "function": "product",
            "function_label": "Teks produk di video (OCR)",
            "brand": None,
            "color": None,
            "context": f"OCR @ {int(phrase_ts.get(key, 0))}s · {count}x",
            "timestamp_sec": int(phrase_ts.get(key, 0)),
            "confidence": conf,
            "source": "ocr",
        })
    return products


def _normalize_merge_key(name: str) -> str:
    key = re.sub(r"\s+", " ", (name or "").lower()).strip()
    key = re.sub(r"\bpromo\b", " ", key)
    key = re.sub(r"\bjumbo\b", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    # collapse sabun mandi variants
    if "sabun" in key and "mandi" in key:
        if "cair" in key:
            return "sabun mandi cair"
        return "sabun mandi"
    return key


def merge_product_lists(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            key = _normalize_merge_key(name)
            if not key:
                continue
            existing = by_key.get(key)
            if not existing:
                by_key[key] = item
                continue
            conf = max(float(existing.get("confidence") or 0), float(item.get("confidence") or 0))
            # keep cleaner / longer product name
            prev = str(existing.get("name") or "")
            if len(name) >= len(prev) and re.search(r"[a-zA-Z]{3,}", name):
                existing = {**item}
            existing["confidence"] = min(0.92, conf + 0.08)
            sources = {existing.get("source"), item.get("source")}
            existing["source"] = "+".join(sorted(s for s in sources if s))
            by_key[key] = existing
    return sorted(by_key.values(), key=lambda x: -float(x.get("confidence") or 0))[:30]
