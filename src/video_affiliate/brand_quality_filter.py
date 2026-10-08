"""Reject low-quality brand candidates — menu items, OCR junk, conversational fragments."""

from __future__ import annotations

import re

# Meta / percakapan — bukan nama produk
_META_PHRASE_WORDS = frozenset({
    "merek", "mereknya", "brand", "produk", "product", "nama", "aja", "doang", "saja",
    "paten", "patenin", "nunggu", "nunggunya", "sendiri", "gitu", "lumayan", "baru",
    "next", "time", "doain", "teh", "kak", "ya", "yuk", "nih", "deh", "banget",
})

# Menu minuman / makanan / rasa — bukan merek affiliate
_MENU_FOOD_WORDS = frozenset({
    "coffee", "kopi", "tea", "teh", "matcha", "macha", "maca", "lemon", "honey", "madu",
    "strawberry", "stroberi", "cranberry", "chocolate", "vanilla", "caramel", "mocha",
    "latte", "espresso", "cappuccino", "americano", "punch", "juice", "smoothie", "syrup",
    "sugar", "cream", "milk", "susu", "ice", "es", "hot", "cold", "fresh", "normal",
    "strong", "signature", "signatur", "bestseller", "menu", "drink", "minuman",
    "snack", "cake", "roti", "bread", "cookie", "pasta", "pizza", "burger", "sandwich",
    "salad", "soup", "noodle", "mie", "rice", "nasi", "ayam", "beef", "pork", "fish",
})

# Fragment OCR umum / kata partial
_OCR_JUNK_WORDS = frozenset({
    "alls", "all", "beth", "the", "and", "for", "with", "from", "this", "that",
    "welcome", "back", "please", "follow", "instagram", "tiktok", "pov", "slow",
    "living", "enjoy", "video", "chat", "happy", "great", "pleasure", "latest",
})

# Pola capitalization OCR rusak (COFFEE PuncH)
_BROKEN_CAPS_RE = re.compile(r"[a-z][A-Z]|[A-Z]{2,}[a-z]+[A-Z]")

# Nama orang / "nama X"
_PERSON_NAME_RE = re.compile(
    r"\b(?:nama|called|name\s+is)\s+[A-Z][a-z]+",
    flags=re.I,
)

# Hanya kata meta produk tanpa brand
_META_ONLY_RE = re.compile(
    r"^(?:aja\s+)?(?:merek|mereknya|brand|produk)(?:\s+aja)?$",
    flags=re.I,
)


def _tokenize(text: str) -> list[str]:
    return [w for w in re.split(r"[\s\-_/]+", text.lower()) if w]


def is_menu_or_food_label(name: str) -> bool:
    tokens = _tokenize(name)
    if not tokens:
        return False
    food_hits = sum(1 for t in tokens if t in _MENU_FOOD_WORDS)
    if food_hits >= 1 and len(tokens) <= 3:
        return True
    if food_hits >= 2:
        return True
    lower = name.lower()
    if re.search(r"\b(?:coffee|kopi)\s+\w+", lower) and len(tokens) <= 4:
        return True
    return False


def is_conversational_fragment(name: str, *, context: str = "") -> bool:
    combined = f"{name} {context}".lower()
    if _PERSON_NAME_RE.search(combined):
        return True
    if _META_ONLY_RE.match(name.strip()):
        return True

    tokens = _tokenize(name)
    if not tokens:
        return False

    meta_hits = sum(1 for t in tokens if t in _META_PHRASE_WORDS)
    if meta_hits >= 1 and len(tokens) <= 3:
        return True
    if meta_hits >= 2:
        return True

    # Frasa mengandung "nama" + kata lain (tas nama Akbar)
    if "nama" in tokens and len(tokens) >= 2:
        return True
    if tokens[0] in {"tas", "bag", "merek", "brand"} and len(tokens) >= 2:
        # tas nama X, merek doang — bukan produk spesifik
        if not re.search(r"[A-Z][a-z]+.*[A-Z]", name):
            return True

    return False


def is_ocr_junk(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return True
    tokens = _tokenize(text)
    lower = text.lower()

    if lower in _OCR_JUNK_WORDS:
        return True
    if all(t in _OCR_JUNK_WORDS for t in tokens):
        return True

    # Satu kata pendek dari OCR
    if len(tokens) == 1 and len(tokens[0]) < 5:
        return True

    # Capitalization rusak
    if _BROKEN_CAPS_RE.search(text):
        return True

    # ALL CAPS menu (CRANBERRY, COFFEE PUNCH)
    words = text.split()
    if words and all(w.isupper() for w in words if w.isalpha()):
        if any(w.lower() in _MENU_FOOD_WORDS for w in words):
            return True
        if len(words) <= 2 and all(len(w) >= 4 for w in words):
            # CRANBERRY, COFFEE PUNCH — likely menu board
            if any(w.lower() in _MENU_FOOD_WORDS | {"coffee", "punch", "fresh", "hot", "ice"} for w in words):
                return True

    return False


def is_own_business_name(name: str, channel_title: str) -> bool:
    if not channel_title:
        return False
    name_key = re.sub(r"[^a-z0-9]", "", name.lower())
    channel_key = re.sub(r"[^a-z0-9]", "", channel_title.lower())
    if not name_key or not channel_key:
        return False
    if name_key in channel_key or channel_key in name_key:
        return True
    # Hadowaku in Hadowaku Coffee
    for part in re.split(r"[\s\-_|]+", channel_title.lower()):
        if len(part) >= 5 and part in name.lower():
            return True
    return False


def is_quality_brand_candidate(
    name: str,
    *,
    context: str = "",
    channel_title: str = "",
    source: str = "",
) -> bool:
    """Final quality gate — consumer brand, not menu/conversation/OCR noise."""
    text = re.sub(r"\s+", " ", (name or "").strip())
    if not text or len(text) < 2:
        return False

    if is_own_business_name(text, channel_title):
        return False
    if is_menu_or_food_label(text):
        return False
    if is_conversational_fragment(text, context=context):
        return False

    if source == "ocr" or source == "vision":
        if is_ocr_junk(text):
            return False
        # Vision/OCR: tolak jika hanya kata umum
        if text.lower() in _MENU_FOOD_WORDS | _META_PHRASE_WORDS | _OCR_JUNK_WORDS:
            return False

    if source == "transcript":
        # Subtitle: butuh merek konsumen eksplisit, bukan percakapan
        has_proper = bool(re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z0-9][A-Za-z0-9]+)+\b", text))
        has_model = bool(re.search(r"\b[A-Z][a-z]{3,}\s*\d+", text))
        has_known = bool(re.search(
            r"\b(?:nike|adidas|apple|samsung|xiaomi|oppo|vivo|skintific|erha|wardah|pixy|"
            r"makeover|somethinc|glad2glow|hanasui|stanley|zara|uniqlo|hm|ikea|dyson|"
            r"philips|logitech|delonghi|breville|nespresso)\b",
            text,
            flags=re.I,
        ))
        if not (has_proper or has_model or has_known):
            return False

    return True