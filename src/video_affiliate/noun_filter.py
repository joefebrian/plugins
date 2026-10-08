"""Noun-only filter — reject non-product parts of speech from brand scan results."""

from __future__ import annotations

import re

# Kata ganti (pronouns)
_PRONOUNS = frozenset({
    "saya", "aku", "gue", "gw", "kamu", "lu", "elo", "dia", "ia", "nya", "mu", "ku",
    "kita", "kami", "kalian", "anda", "mereka", "they", "them", "we", "us", "you", "i", "me",
    "he", "she", "it", "his", "her", "our", "your", "their", "my", "mine", "yours", "ours",
    "ini", "itu", "sini", "situ", "sana", "mana", "apa", "siapa", "what", "who", "which",
    "this", "that", "these", "those", "here", "there",
})

# Kata depan (prepositions)
_PREPOSITIONS = frozenset({
    "di", "ke", "dari", "pada", "dengan", "untuk", "oleh", "tentang", "atas", "bawah",
    "dalam", "luar", "antara", "tanpa", "sejak", "hingga", "sampai", "via", "per", "seperti",
    "in", "on", "at", "to", "for", "of", "by", "with", "from", "into", "onto", "upon",
    "about", "over", "under", "between", "through", "during", "before", "after", "off",
})

# Kata sambung (conjunctions)
_CONJUNCTIONS = frozenset({
    "dan", "atau", "tetapi", "tapi", "karena", "sebab", "maka", "jadi", "kalau", "jika",
    "bila", "supaya", "agar", "namun", "melainkan", "serta", "pun", "lalu", "maka",
    "and", "or", "but", "because", "so", "if", "when", "while", "although", "though",
    "nor", "yet", "unless", "until", "as", "than",
})

# Kata keterangan (adverbs)
_ADVERBS = frozenset({
    "sangat", "amat", "terlalu", "lebih", "kurang", "paling", "sekali", "banget", "bgt",
    "sudah", "belum", "akan", "pernah", "selalu", "kadang", "lagi", "masih", "baru", "saja",
    "hanya", "cuma", "juga", "pun", "memang", "benar", "betul", "really", "very", "too",
    "also", "just", "only", "even", "still", "already", "always", "never", "often", "sometimes",
    "now", "then", "today", "tomorrow", "yesterday", "here", "there", "quite", "rather",
    "super", "extremely", "totally", "absolutely", "literally", "basically", "actually",
})

# Kata sifat (adjectives) — bukan nama produk
_ADJECTIVES = frozenset({
    "bagus", "jelek", "cantik", "ganteng", "murah", "mahal", "besar", "kecil", "tinggi",
    "rendah", "panjang", "pendek", "putih", "hitam", "merah", "biru", "hijau", "kuning",
    "baru", "lama", "bagus", "mantap", "keren", "kece", "worth", "recommended", "favorit",
    "favorite", "best", "good", "nice", "great", "amazing", "awesome", "beautiful", "pretty",
    "cute", "soft", "hard", "fast", "slow", "easy", "difficult", "perfect", "special",
    "free", "gratis", "murmer", "original", "fake", "kw", "premium", "limited", "viral",
    "glowing", "halal", "aman", "lengkap", "lengkap", "utuh", "penuh", "kosong",
})

# Kata kerja (verbs) & predikat umum
_VERBS = frozenset({
    "adalah", "ialah", "ada", "pergi", "datang", "beli", "jual", "pakai", "pake", "gunakan",
    "buat", "bikin", "lihat", "liat", "coba", "kasih", "beri", "ambil", "taruh", "mau",
    "bisa", "harus", "perlu", "suka", "sayang", "ingin", "tahu", "tau", "pikir", "fikir",
    "bilang", "kata", "tanya", "jawab", "buka", "tutup", "mulai", "selesai", "jalan",
    "kerja", "kerjain", "main", "nonton", "dengar", "denger", "share", "subscribe",
    "like", "comment", "click", "tap", "swipe", "check", "cek", "download", "upload",
    "make", "made", "get", "got", "give", "gave", "take", "took", "see", "saw", "watch",
    "try", "tried", "use", "used", "buy", "bought", "love", "hate", "need", "want",
    "have", "has", "had", "been", "being", "was", "were", "is", "are", "am", "do", "does",
    "did", "can", "could", "will", "would", "should", "may", "might", "must", "shall",
    "going", "gonna", "wanna", "gotta",
})

# Kata seru (interjections)
_INTERJECTIONS = frozenset({
    "oh", "ooh", "ah", "eh", "hmm", "hm", "wah", "wow", "yay", "yuk", "ayo", "hai", "hi",
    "hey", "please", "thanks", "makasih", "trimakasih", "terimakasih", "sorry", "oops",
    "yeah", "yes", "no", "nah", "yup", "nope", "okay", "ok", "oke", "well", "uh", "um",
})

# Kata bilangan / angka leksikal
_NUMERALS = frozenset({
    "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh",
    "sebelas", "dua", "belas", "puluh", "ratus", "ribu", "juta", "one", "two", "three",
    "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "first",
    "second", "third",
})

# Umum / meta video (bukan produk)
_META_STOP = frozenset({
    "video", "youtube", "channel", "creator", "content", "link", "bio", "description",
    "transcript", "subtitle", "official", "remaster", "lyrics", "website", "listen", "watch",
    "profil", "profile", "account", "akun", "orang", "hari", "tahun", "bulan", "waktu",
    "affiliate", "affiliatelink", "discount", "promo", "kode", "voucher", "review", "unboxing",
    "haul", "vlog", "tutorial", "tips", "trick", "cara", "begini", "beginian", "kayak",
    "kayaknya", "gimana", "bagaimana", "kenapa", "mengapa", "kapan", "dimana",
    "merek", "mereknya", "nama", "paten", "patenin",
})

NON_NOUN_WORDS = (
    _PRONOUNS
    | _PREPOSITIONS
    | _CONJUNCTIONS
    | _ADVERBS
    | _ADJECTIVES
    | _VERBS
    | _INTERJECTIONS
    | _NUMERALS
    | _META_STOP
)

# Kata benda produk — anchor yang valid dalam frasa
_PRODUCT_NOUN_ANCHORS = frozenset({
    "produk", "product", "brand", "merek", "item", "barang", "serum", "moisturizer",
    "sunscreen", "lipstick", "cushion", "foundation", "parfum", "perfume", "tas", "sepatu",
    "jam", "laptop", "handphone", "hp", "smartphone", "tablet", "kamera", "camera",
    "headphone", "earphone", "earbuds", "charger", "powerbank", "skincare", "makeup",
    "vitamin", "suplemen", "supplement", "dress", "baju", "celana", "hoodie", "sweater",
    "sandal", "sneakers", "topi", "ringlight", "toner", "essence", "cleanser", "sabun",
    "shampoo", "conditioner", "bodywash", "deodorant", "bedak", "masker", "mask", "cream",
    "lotion", "lip", "mascara", "eyeliner", "blush", "concealer", "primer", "setting",
    "spray", "wardrobe", "outfit", "accessories", "aksesoris", "gelang", "kalung", "cincin",
    "dompet", "wallet", "backpack", "koper", "botol", "tumbler", "blender", "mixer", "oven",
    "microwave", "vacuum", "kipas", "ac", "tv", "monitor", "keyboard", "mouse", "speaker",
})

_VERB_SUFFIXES = ("kan", "lah", "in", "ing", "ed", "es", "s")
_ADJ_PREFIXES = ("ter", "ber", "pe", "pen", "pem", "ke")


def _tokenize(text: str) -> list[str]:
    return [w for w in re.split(r"[\s\-_/]+", text.lower()) if w]


def _is_pure_number(token: str) -> bool:
    return bool(re.fullmatch(r"[\d.,]+", token))


def _looks_like_verb_token(token: str) -> bool:
    if token in _VERBS:
        return True
    if len(token) > 4 and token.endswith("ing") and token not in _PRODUCT_NOUN_ANCHORS:
        return True
    if len(token) > 3 and token.endswith("ed") and token not in _PRODUCT_NOUN_ANCHORS:
        return True
    if len(token) > 4 and token.endswith("kan") and token not in _PRODUCT_NOUN_ANCHORS:
        return True
    return False


def _looks_like_adjective_token(token: str) -> bool:
    if token in _ADJECTIVES:
        return True
    for prefix in _ADJ_PREFIXES:
        if token.startswith(prefix) and len(token) > len(prefix) + 2:
            if token[len(prefix):] in _ADJECTIVES or token[len(prefix):] in _VERBS:
                return True
    return False


def is_noun_token(token: str) -> bool:
    t = token.strip().lower().strip(".,;:!?\"'()[]")
    if not t or len(t) < 2:
        return False
    if _is_pure_number(t):
        return False
    if t in NON_NOUN_WORDS:
        return False
    if _looks_like_verb_token(t):
        return False
    if _looks_like_adjective_token(t):
        return False
    return True


def is_noun_only_phrase(name: str) -> bool:
    """Return True if phrase is composed only of noun-like tokens (product/brand)."""
    text = re.sub(r"\s+", " ", (name or "").strip(" .,;:!?\"'()[]"))
    if len(text) < 2 or len(text) > 80:
        return False
    if re.fullmatch(r"[\W\d_]+", text):
        return False

    lower = text.lower()
    if lower in NON_NOUN_WORDS:
        return False

    # Tanggal, tahun, metadata platform
    if re.search(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
        text,
        flags=re.I,
    ):
        return False
    if re.search(r"\bID\b", text) or re.search(r"ID[A-Z]", text):
        return False
    if re.search(r"(Music|Lyrics|Follow|Website|Video|Channel|Official)$", text, flags=re.I):
        return False

    tokens = _tokenize(text)
    if not tokens:
        return False

    # Frasa tidak boleh diawali kata non-benda
    if tokens[0] in _PRONOUNS | _PREPOSITIONS | _CONJUNCTIONS | _ADVERBS | _INTERJECTIONS | _VERBS:
        return False

    noun_count = 0
    for i, token in enumerate(tokens):
        if _is_pure_number(token):
            continue
        if is_noun_token(token):
            noun_count += 1
            continue
        # Izinkan token campuran brand (angka di tengah: iphone15, spf50)
        if re.search(r"[a-z]", token) and re.search(r"\d", token):
            noun_count += 1
            continue
        # Izinkan singkatan satu huruf dalam frasa produk (Vitamin C, SPF A)
        if len(token) == 1 and token.isalpha():
            prev = tokens[i - 1] if i > 0 else ""
            if prev in _PRODUCT_NOUN_ANCHORS or prev in {"vitamin", "spf", "uv", "pro", "max", "plus"}:
                noun_count += 1
                continue
        return False

    if noun_count == 0:
        return False

    # Minimal satu anchor produk ATAU frasa nama proper multi-kata ATAU brand dengan angka
    has_anchor = any(t in _PRODUCT_NOUN_ANCHORS for t in tokens)
    has_proper = bool(re.search(r"[A-Z][a-z]+(?:\s+[A-Z0-9][A-Za-z0-9]+)+", text))
    has_brand_digit = bool(re.search(r"\b[A-Za-z]+\s*\d+[A-Za-z0-9]*\b", text))
    has_hashtag_style = "_" not in text and len(tokens) >= 2 and all(len(t) >= 2 for t in tokens)

    if has_anchor or has_proper or has_brand_digit:
        return True

    # Satu kata: hanya jika anchor produk atau brand dengan digit/min 5 huruf
    if len(tokens) == 1:
        t = tokens[0]
        return t in _PRODUCT_NOUN_ANCHORS or bool(re.search(r"\d", t)) or len(t) >= 5

    return has_hashtag_style and noun_count >= 2


def filter_noun_mentions(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if not is_noun_only_phrase(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out