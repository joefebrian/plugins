"""Filter mentions that are nouns but not purchasable affiliate products."""

from __future__ import annotations

import re

from .brand_quality_filter import is_quality_brand_candidate
from .noun_filter import NON_NOUN_WORDS, _PRODUCT_NOUN_ANCHORS, is_noun_only_phrase

# Lokasi / landmark — bukan produk affiliate
_LOCATION_MARKERS = frozenset({
    "park", "square", "street", "avenue", "boulevard", "plaza", "tower", "bridge",
    "museum", "temple", "beach", "island", "mountain", "lake", "river", "city",
    "country", "district", "station", "airport", "terminal", "mall", "market",
    "jalan", "kota", "negara", "pulau", "pantai", "gunung", "danau", "sungai",
    "taman", "stasiun", "bandara",
})

_KNOWN_LOCATIONS = frozenset({
    "central park", "new york", "times square", "bali", "jakarta", "singapore",
    "tokyo", "paris", "london", "dubai", "los angeles", "san francisco",
})

# Hashtag / tag sosial — bukan produk
_VLOG_HASHTAG_PARTS = frozenset({
    "vlog", "family", "mom", "dad", "life", "lifestyle", "daily", "journey",
    "perjalanan", "travel", "trip", "holiday", "vacation", "routine", "day",
    "indonesia", "online", "channel", "creator", "content", "subscribe",
    "follow", "support", "update", "keluarga", "familyvlog", "momlife",
    "lifestyleindonesia", "glowing",
})

# Kata percakapan informal di subtitle — bukan nama produk
_CONVERSATIONAL = frozenset({
    "guys", "tuh", "pokoknya", "segini", "gimana", "kayak", "banget", "nih", "deh",
    "dong", "sih", "ya", "yuk", "nih", "loh", "lah", "kan", "gitu", "gini",
    "datangnya", "nyampai", "jalan", "hilang", "panas", "terang", "pintar",
    "follow", "perjalanan", "seru", "inspiratif", "keseharian", "cerita", "moment",
    "support", "subscribe", "comment", "instagram", "tiktok", "terima", "kasih",
})

# "jam" = waktu, bukan arloji
_TIME_JAM_CONTEXT = re.compile(
    r"\b(?:jam\s+\d|jam\s+segini|jam\s+pagi|jam\s+sore|jam\s+malam|jam\s+siang|"
    r"pokoknya\s+jam|datangnya\s+jam|tuh\s+jam|malamannya|mataharinya)\b",
    flags=re.I,
)

# CTA / deskripsi channel
_CTA_PATTERN = re.compile(
    r"\b(?:follow|subscribe|like\s+comment|instagram|tiktok|support|jangan\s+lupa|"
    r"perjalanan|keseharian|keluarga\s+online|channel|bio|link\s+di)\b",
    flags=re.I,
)

# Frasa dengan morfologi percakapan Indonesia (datangnya, pokoknya, …)
_INFORMAL_SUFFIX = re.compile(
    r"\b\w+(?:nya|kan|lah|dong|deh|sih)\b",
    flags=re.I,
)

# Kata kategori generik — bukan produk tanpa nama brand
_GENERIC_PRODUCT_WORDS = frozenset({
    "produk", "product", "brand", "merek", "item", "barang", "moisturizer", "serum",
    "sunscreen", "lipstick", "foundation", "parfum", "perfume", "skincare", "makeup",
})

# Anchor produk yang ambigu tanpa konteks brand (jam=waktu vs jam tangan)
_AMBIGUOUS_ANCHORS = frozenset({"jam", "watch"})

# Kategori hashtag yang jelas bukan produk
_NON_PRODUCT_HASHTAG_RE = re.compile(
    r"(?i)(vlog|family|mom|dad|life|style|travel|trip|daily|journey|indonesia|"
    r"subscribe|follow|glowing|scarlett|keluarga|online|creator|content)",
)


def _tokenize(text: str) -> list[str]:
    return [w for w in re.split(r"[\s\-_/]+", text.lower()) if w]


def _normalize_channel_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _looks_like_location(name: str) -> bool:
    lower = name.lower()
    if lower in _KNOWN_LOCATIONS:
        return True
    tokens = _tokenize(lower)
    if any(t in _LOCATION_MARKERS for t in tokens):
        # Central Park, Times Square, 103 Street
        if len(tokens) >= 2 or any(t in {"park", "square", "taman", "kota"} for t in tokens):
            return True
    if re.search(r"\b(?:street|avenue|st\.?)\b", lower):
        return True
    return False


def _looks_like_vlog_hashtag(name: str) -> bool:
    lower = name.lower().replace(" ", "")
    if _NON_PRODUCT_HASHTAG_RE.search(lower):
        return True
    tokens = _tokenize(name)
    if tokens and all(t in _VLOG_HASHTAG_PARTS or len(t) <= 3 for t in tokens):
        return True
    # CamelCase tanpa spasi: FamilyVlog, MomLife
    if " " not in name.strip() and re.search(r"[a-z][A-Z]|[A-Z][a-z]+[A-Z]", name):
        compact = re.sub(r"[^a-zA-Z]", "", name).lower()
        for part in _VLOG_HASHTAG_PARTS:
            if part in compact and part not in _PRODUCT_NOUN_ANCHORS:
                return True
    return False


def _matches_channel_branding(name: str, channel_title: str) -> bool:
    if not channel_title:
        return False
    name_key = _normalize_channel_key(name)
    channel_key = _normalize_channel_key(channel_title)
    if not name_key or not channel_key:
        return False
    if name_key in channel_key or channel_key in name_key:
        return True
    # LouiseScarlett in Louisse Scarlett Family
    if len(name_key) >= 5 and name_key in channel_key:
        return True
    return False


def _has_conversational_noise(name: str) -> bool:
    lower = name.lower()
    tokens = _tokenize(lower)
    conv_count = sum(1 for t in tokens if t in _CONVERSATIONAL or t in NON_NOUN_WORDS)
    if conv_count >= 2:
        return True
    if conv_count >= 1 and len(tokens) <= 3:
        return True
    if _INFORMAL_SUFFIX.search(lower):
        return True
    if _CTA_PATTERN.search(lower):
        return True
    return False


def _is_time_jam_phrase(name: str, context: str = "") -> bool:
    combined = f"{name} {context}".lower()
    if "jam" not in combined:
        return False
    if _TIME_JAM_CONTEXT.search(combined):
        return True
    tokens = _tokenize(name)
    if tokens == ["jam"] or (len(tokens) <= 3 and "jam" in tokens):
        time_neighbors = {"segini", "pagi", "sore", "malam", "siang", "pokoknya", "datangnya", "tuh"}
        if any(t in time_neighbors for t in tokens):
            return True
    return False


def _has_strong_product_signal(name: str) -> bool:
    """Minimal satu sinyal bahwa ini benar-benar produk/merek, bukan lokasi/tag."""
    text = name.strip()
    tokens = _tokenize(text)
    lower = text.lower()

    # Satu kata kategori generik saja (Moisturizer, Produk) — bukan affiliate tag
    if len(tokens) == 1:
        t = tokens[0]
        if t in _GENERIC_PRODUCT_WORDS:
            return False
        if t in _PRODUCT_NOUN_ANCHORS and t not in _AMBIGUOUS_ANCHORS and len(t) < 6:
            return False

    if any(t in _PRODUCT_NOUN_ANCHORS and t not in _AMBIGUOUS_ANCHORS for t in tokens):
        # Butuh minimal satu token non-generik (nama brand) jika hanya kategori
        non_generic = [t for t in tokens if t not in _GENERIC_PRODUCT_WORDS]
        if len(non_generic) >= 2 or (len(non_generic) == 1 and len(non_generic[0]) >= 5):
            return True
        if re.search(r"\d", text):
            return True

    # Brand + model: iPhone 15, Nike Air Max, Serum Skintific
    if re.search(r"\b[A-Za-z]+\s*\d+[A-Za-z0-9]*\b", text):
        return True

    # Multi-word proper brand (bukan lokasi)
    if re.search(r"[A-Z][a-z]+(?:\s+[A-Z][a-z0-9]+)+", text) and not _looks_like_location(text):
        # But require not pure location words
        non_loc = [t for t in tokens if t not in _LOCATION_MARKERS]
        if len(non_loc) >= 2:
            return True

    # Kategori produk spesifik (bukan kata meta merek/brand/produk saja)
    if re.search(r"\b(?:serum|moisturizer|lipstick|parfum|sunscreen|cushion|foundation)\b", lower):
        return True

    return False


_VISUAL_SOURCES = frozenset({"ocr", "vision"})


def is_visual_brand_mention(
    name: str,
    *,
    context: str = "",
    channel_title: str = "",
) -> bool:
    """Relaxed rules for OCR / vision-detected brands (single-word logos allowed)."""
    text = re.sub(r"\s+", " ", (name or "").strip(" .,;:!?\"'()[]"))
    if len(text) < 2 or len(text) > 80:
        return False
    if _looks_like_location(text):
        return False
    if _looks_like_vlog_hashtag(text):
        return False
    if _matches_channel_branding(text, channel_title):
        return False
    if _CTA_PATTERN.search(text) or _CTA_PATTERN.search(context):
        return False

    tokens = _tokenize(text)
    if not tokens:
        return False
    if tokens and all(t in _AMBIGUOUS_ANCHORS for t in tokens):
        return False
    if any(t in _GENERIC_PRODUCT_WORDS for t in tokens) and len(tokens) == 1:
        return False

    if not is_quality_brand_candidate(
        text, context=context, channel_title=channel_title, source="vision"
    ):
        return False

    # Brand proper noun / logo — minimal 5 huruf untuk satu kata
    if re.search(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z0-9][A-Za-z0-9]+)+\b", text):
        return True
    if re.search(r"\b[A-Za-z]+\s*\d+[A-Za-z0-9]*\b", text):
        return True
    if len(tokens) == 1 and len(tokens[0]) >= 5 and re.match(r"^[A-Z][a-z]+$", text):
        return True
    if any(t in _PRODUCT_NOUN_ANCHORS and t not in _AMBIGUOUS_ANCHORS for t in tokens):
        return len(tokens) >= 2 and _has_strong_product_signal(text)
    return _has_strong_product_signal(text)


def is_affiliate_product_mention(
    name: str,
    *,
    context: str = "",
    channel_title: str = "",
    source: str = "",
) -> bool:
    """
    Stricter than is_noun_only_phrase: must look like a purchasable product/brand,
    not a location, vlog tag, CTA, or conversational subtitle fragment.
    """
    text = re.sub(r"\s+", " ", (name or "").strip())
    if not text:
        return False

    if source in _VISUAL_SOURCES:
        return is_visual_brand_mention(
            text,
            context=context,
            channel_title=channel_title,
        )

    if not is_noun_only_phrase(text):
        return False

    if _looks_like_location(text):
        return False
    if _looks_like_vlog_hashtag(text):
        return False
    if _matches_channel_branding(text, channel_title):
        return False
    if _has_conversational_noise(text):
        return False
    if _is_time_jam_phrase(text, context):
        return False

    tokens = _tokenize(text)
    if tokens and all(t in _AMBIGUOUS_ANCHORS for t in tokens):
        return False

    # Hashtag dari deskripsi: hampir selalu tag sosial, bukan produk
    if source == "description" and "hashtag" in (context or "").lower() or source == "description":
        # category passed separately — check via hashtag pattern in name
        if "#" in (context or "") or _looks_like_vlog_hashtag(text):
            return False
        if not _has_strong_product_signal(text) and re.match(r"^[A-Za-z]+$", text.replace(" ", "")):
            return False

    # Transcript sangat noisy — wajib sinyal produk kuat
    if source == "transcript":
        if not _has_strong_product_signal(text):
            return False
        if _has_conversational_noise(context) or len(text.split()) >= 4:
            # Long conversational fragments
            if not re.search(r"[A-Z][a-z]+", text):
                return False

    # Title/description capitalized tanpa sinyal produk
    if source in ("title", "description") and not _has_strong_product_signal(text):
        if _looks_like_location(text) or _CTA_PATTERN.search(text):
            return False
        # Proper noun lokasi di judul travel vlog
        if any(t in _LOCATION_MARKERS for t in tokens):
            return False

    if not is_quality_brand_candidate(
        text, context=context, channel_title=channel_title, source=source
    ):
        return False

    return _has_strong_product_signal(text)


def filter_affiliate_mentions(
    items: list,
    *,
    channel_title: str = "",
) -> list:
    """Filter list of ProductMention-like objects."""
    out = []
    for item in items:
        name = getattr(item, "name", "") or ""
        context = getattr(item, "context", "") or ""
        source = getattr(item, "source", "") or ""
        category = getattr(item, "category", "") or ""
        ctx = context
        if category == "hashtag":
            ctx = f"#{name} {context}"
        if is_affiliate_product_mention(
            name,
            context=ctx,
            channel_title=channel_title,
            source=source if source else ("description" if category == "hashtag" else ""),
        ):
            out.append(item)
    return out