"""AI topic + caption generation for Threads — ID GenZ/Millennial & US slang."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Optional

from sqlalchemy.orm import Session

THREADS_VERSION = "v1.0"
THREADS_BASE = f"https://graph.threads.net/{THREADS_VERSION}"


class TopicGenerationError(Exception):
    pass


VOICE_PRESETS = {
    ("id", "genz"): (
        "Bahasa Indonesia Gen Z: santai, relatable, pakai 'ga/gak', 'btw', 'literally', "
        "emoji secukupnya, hook kuat di kalimat pertama, kayak ngobrol sama teman di Threads. "
        "Hindari bahasa terlalu formal."
    ),
    ("id", "millennial"): (
        "Bahasa Indonesia Milenial: storytelling ringan, tips praktis, sedikit nostalgia, "
        "tone hangat & trustworthy, tetap conversational untuk Threads Indonesia."
    ),
    ("us", "us_slang"): (
        "American English Gen Z/Millennial slang: ngl, lowkey, highkey, no cap, it's giving, "
        "ate, slay, vibe, rent-free, main character energy. Natural US Threads voice."
    ),
}


def voice_instruction(locale: str, style: str) -> str:
    key = (locale, style)
    if key in VOICE_PRESETS:
        return VOICE_PRESETS[key]
    if locale == "us":
        return VOICE_PRESETS[("us", "us_slang")]
    return VOICE_PRESETS[("id", "genz")]


def fetch_keyword_hints(access_token: str, threads_user_id: str, query: str, *, limit: int = 8) -> list[str]:
    """Threads keyword search — viral/trending topic hints (if API available)."""
    q = (query or "").strip()
    if not q or not access_token:
        return []
    params = urllib.parse.urlencode({
        "q": q,
        "access_token": access_token,
        "limit": limit,
    })
    url = f"{THREADS_BASE}/keyword_search?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AffiliateVideoTool/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        items = payload.get("data") or []
        return [str(i.get("text") or i.get("keyword") or "").strip() for i in items if i]
    except Exception:
        return []


def generate_topics(
    session: Session,
    *,
    niche: str,
    locale: str = "id",
    style: str = "genz",
    count: int = 8,
    access_token: Optional[str] = None,
    threads_user_id: Optional[str] = None,
) -> dict[str, Any]:
    from ..ai.client import AIClientError, generate_json_array

    seed = (niche or "lifestyle affiliate").strip()
    hints = []
    if access_token and threads_user_id:
        hints = fetch_keyword_hints(access_token, threads_user_id, seed, limit=10)

    voice = voice_instruction(locale, style)
    region = "Indonesia" if locale == "id" else "United States"
    hint_text = ", ".join(hints[:8]) if hints else "gunakan pola topik viral Threads umum (hot take, POV, tips cepat, relatable moment)"

    user_prompt = f"""Kamu ahli konten Threads dengan engagement tinggi di {region}.
Buat {count} ide topik/post yang viral-potential untuk niche: {seed}

Referensi trending di Threads: {hint_text}

Gaya bahasa WAJIB: {voice}

Return JSON array saja:
[
  {{
    "topic": "judul topik singkat",
    "hook": "kalimat pembuka hook (max 120 char)",
    "caption": "draft caption Threads lengkap max 450 char",
    "topic_tag": "tag tanpa hash max 30 char",
    "engagement_tip": "kenapa ini bakal dapet reply/repost"
  }}
]

Fokus retensi: provokatif positif, relatable, CTA halus (tanya jawab / pilih A-B), bukan clickbait toxic."""

    try:
        items, result = generate_json_array(
            session,
            system="Return valid JSON array only. Captions must match the voice style exactly.",
            user=user_prompt,
        )
    except AIClientError as e:
        raise TopicGenerationError(str(e)) from e

    topics = []
    for i, item in enumerate(items[:count]):
        caption = re.sub(r"\s+", " ", (item.get("caption") or "")).strip()[:500]
        tag = (item.get("topic_tag") or "").strip().lstrip("#")[:50]
        topics.append({
            "topic": item.get("topic") or f"Topik {i+1}",
            "hook": item.get("hook") or "",
            "caption": caption,
            "topic_tag": tag,
            "engagement_tip": item.get("engagement_tip") or "",
            "source": "ai",
        })

    return {
        "niche": seed,
        "locale": locale,
        "style": style,
        "keyword_hints": hints[:8],
        "topics": topics,
        "ai_used": True,
        "provider_used": result.provider_label,
        "tokens_used": result.tokens_used,
    }


def generate_caption(
    session: Session,
    *,
    topic: str,
    locale: str = "id",
    style: str = "genz",
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from ..ai.client import AIClientError, complete_with_failover

    ctx = context or {}
    voice = voice_instruction(locale, style)
    region = "Indonesia" if locale == "id" else "United States"

    user_prompt = f"""Tulis 1 caption Threads untuk {region}, max 480 karakter.
Topik: {topic}
Konteks video: {ctx.get('title', '')} | views {ctx.get('views', 0)} | @{ctx.get('username', '')}

Gaya: {voice}
Sertakan hook kuat + CTA engagement (pertanyaan/polling).
Return JSON: {{"caption":"...","topic_tag":"tanpa hash"}}"""

    try:
        result = complete_with_failover(
            session,
            system="Return valid JSON only.",
            user=user_prompt,
        )
        text = result.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        caption = re.sub(r"\s+", " ", data.get("caption", "")).strip()[:500]
        return {
            "caption": caption,
            "topic_tag": (data.get("topic_tag") or "").strip().lstrip("#")[:50],
            "provider_used": result.provider_label,
            "tokens_used": result.tokens_used,
        }
    except (AIClientError, json.JSONDecodeError, KeyError) as e:
        raise TopicGenerationError(str(e)) from e