"""YouTube title suggestions — search autocomplete + optional AI."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Optional

from sqlalchemy.orm import Session

YT_SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


class TitleGenerationError(Exception):
    pass


def _http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; AffiliateVideoTool/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_youtube_search_suggestions(query: str, *, limit: int = 12) -> list[str]:
    """Pull YouTube search autocomplete suggestions (no API key)."""
    q = (query or "").strip()
    if not q:
        return []

    params = urllib.parse.urlencode({
        "client": "firefox",
        "ds": "yt",
        "q": q,
    })
    raw = _http_get(f"{YT_SUGGEST_URL}?{params}")

    # Response: ["query", [["suggestion", ...], ...]]
    try:
        payload = json.loads(raw)
        suggestions = payload[1] if len(payload) > 1 else []
        results: list[str] = []
        for item in suggestions:
            if isinstance(item, list) and item:
                text = str(item[0]).strip()
            elif isinstance(item, str):
                text = item.strip()
            else:
                continue
            if text and text.lower() != q.lower():
                results.append(text)
        return results[:limit]
    except (json.JSONDecodeError, IndexError, TypeError):
        return []


def _clean_title(text: str, max_len: int = 100) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _heuristic_variants(
    base_title: str,
    suggestions: list[str],
    *,
    count: int = 5,
) -> list[dict[str, Any]]:
    base = _clean_title(base_title)
    variants: list[dict[str, Any]] = []
    seen = {base.lower()}

    patterns = [
        lambda s: _clean_title(s),
        lambda s: _clean_title(f"{base} — {s}"),
        lambda s: _clean_title(f"{s} | {base}"),
        lambda s: _clean_title(f"REVIEW: {s}"),
        lambda s: _clean_title(f"{base} ({s})"),
        lambda s: _clean_title(f"Wajib Tahu! {s}"),
    ]

    if base:
        variants.append({
            "title": base,
            "source": "original",
            "score": 100,
            "reason": "Judul asli dari video",
        })

    for suggestion in suggestions:
        for fn in patterns:
            title = fn(suggestion)
            key = title.lower()
            if not title or key in seen or len(title) < 8:
                continue
            seen.add(key)
            variants.append({
                "title": title,
                "source": "youtube_search",
                "score": max(50, 90 - len(variants) * 5),
                "reason": f"Berdasarkan pencarian: \"{suggestion}\"",
            })
            if len(variants) >= count:
                return variants[:count]

    return variants[:count]


def _ai_available(session: Session) -> bool:
    from ..ai.client import seed_from_env
    from ..ai.quota import pick_available_provider

    seed_from_env(session)
    return pick_available_provider(session) is not None


def _call_ai_titles(
    session: Session,
    *,
    base_title: str,
    keyword: str,
    suggestions: list[str],
    context: dict[str, Any],
    count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from ..ai.client import AIClientError, generate_json_array

    views = context.get("views", 0)
    gmv = context.get("gmv", 0)
    username = context.get("username", "")

    user_prompt = f"""Kamu ahli YouTube SEO untuk konten affiliate TikTok Indonesia.
Buat {count} judul video YouTube berbeda (maks 95 karakter) yang CTR tinggi.

Judul asli: {base_title}
Keyword utama: {keyword}
Saran pencarian YouTube: {", ".join(suggestions[:8]) or "-"}
Views TikTok: {views}
GMV: Rp {gmv}
Username: @{username}

Aturan:
- Bahasa Indonesia natural, click-worthy tapi tidak clickbait berlebihan
- Gabungkan trend pencarian YouTube di atas
- Variasi gaya: review, tips, viral, honest review
- Return JSON array saja: [{{"title":"...","reason":"..."}}]
"""

    try:
        items, result = generate_json_array(
            session,
            system="Return valid JSON array only.",
            user=user_prompt,
        )
    except AIClientError as e:
        raise TitleGenerationError(str(e)) from e

    results = []
    source = f"ai_{result.provider_type}"
    for i, item in enumerate(items[:count]):
        title = _clean_title(item.get("title", ""))
        if not title:
            continue
        results.append({
            "title": title,
            "source": source,
            "score": 95 - i,
            "reason": item.get("reason") or "AI rekomendasi berdasarkan YouTube search",
        })

    meta = {
        "provider_used": result.provider_label,
        "provider_type": result.provider_type,
        "model": result.model,
        "tokens_used": result.tokens_used,
    }
    return results, meta


def generate_title_variants(
    session: Session,
    *,
    base_title: str,
    keyword: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
    count: int = 5,
    use_ai: bool = True,
) -> dict[str, Any]:
    ctx = context or {}
    seed = (keyword or base_title or ctx.get("title") or "review produk").strip()
    suggestions = fetch_youtube_search_suggestions(seed, limit=15)

    words = [w for w in re.split(r"[^\w]+", seed) if len(w) > 3][:3]
    for word in words:
        extra = fetch_youtube_search_suggestions(word, limit=5)
        for s in extra:
            if s not in suggestions:
                suggestions.append(s)

    variants: list[dict[str, Any]] = []
    ai_used = False
    ai_error: Optional[str] = None
    ai_meta: dict[str, Any] = {}
    ai_available = _ai_available(session)

    if use_ai and ai_available:
        try:
            variants, ai_meta = _call_ai_titles(
                session,
                base_title=base_title,
                keyword=seed,
                suggestions=suggestions,
                context=ctx,
                count=count,
            )
            ai_used = bool(variants)
        except (TitleGenerationError, json.JSONDecodeError, KeyError, TypeError) as e:
            ai_error = str(e)

    if len(variants) < count:
        seen = {v["title"].lower() for v in variants}
        for item in _heuristic_variants(base_title, suggestions, count=count):
            if item["title"].lower() not in seen:
                variants.append(item)
                seen.add(item["title"].lower())
            if len(variants) >= count:
                break

    return {
        "keyword": seed,
        "search_suggestions": suggestions[:12],
        "variants": variants[:count],
        "ai_used": ai_used,
        "ai_available": ai_available,
        "ai_error": ai_error,
        "provider_used": ai_meta.get("provider_used"),
        "provider_type": ai_meta.get("provider_type"),
        "model": ai_meta.get("model"),
        "tokens_used": ai_meta.get("tokens_used"),
    }