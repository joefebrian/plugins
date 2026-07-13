"""Unified AI completion with OpenAI / Gemini and provider failover."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db.models import AIProviderConfig
from .quota import (
    is_rate_limit_error,
    list_providers,
    mark_rate_limited,
    pick_available_provider,
    record_usage,
)


class AIClientError(Exception):
    pass


DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}


@dataclass
class AICompletionResult:
    text: str
    tokens_used: int
    provider_id: int
    provider_label: str
    provider_type: str
    model: str


def save_provider(session: Session, data: dict, provider_id: Optional[int] = None) -> AIProviderConfig:
    if provider_id:
        cfg = session.query(AIProviderConfig).filter_by(id=provider_id).first()
        if not cfg:
            raise AIClientError("AI Provider tidak ditemukan")
    else:
        cfg = AIProviderConfig()
        session.add(cfg)

    for key in (
        "user_id",
        "label",
        "provider",
        "api_key",
        "model",
        "priority",
        "is_active",
        "daily_token_limit",
        "daily_request_limit",
    ):
        if key in data and data[key] is not None:
            setattr(cfg, key, data[key])

    if not cfg.model:
        cfg.model = DEFAULT_MODELS.get(cfg.provider, "gpt-4o-mini")

    cfg.updated_at = __import__("datetime").datetime.utcnow()
    session.commit()
    session.refresh(cfg)
    return cfg


def delete_provider(session: Session, provider_id: int) -> bool:
    cfg = session.query(AIProviderConfig).filter_by(id=provider_id).first()
    if not cfg:
        return False
    session.delete(cfg)
    session.commit()
    return True


def seed_from_env(session: Session, user_id: int | None = None) -> None:
    """Import OPENAI_API_KEY from .env if no providers configured."""
    import os

    if list_providers(session, user_id=user_id):
        return
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODELS["openai"]).strip()
    save_provider(
        session,
        {
            "user_id": user_id,
            "label": "OpenAI (from .env)",
            "provider": "openai",
            "api_key": key,
            "model": model,
            "priority": 100,
        },
    )


def _http_post_json(url: str, body: dict, headers: dict, timeout: int = 90) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace") if e.fp else ""
        raise AIClientError(f"HTTP {e.code}: {err_body[:500]}") from e


def _call_openai(cfg: AIProviderConfig, system: str, user: str) -> tuple[str, int]:
    payload = _http_post_json(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": cfg.model or DEFAULT_MODELS["openai"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.85,
            "max_tokens": 1200,
        },
        {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
    )
    usage = payload.get("usage") or {}
    tokens = int(usage.get("total_tokens") or usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
    content = payload["choices"][0]["message"]["content"]
    return content.strip(), tokens


def _call_gemini(cfg: AIProviderConfig, system: str, user: str) -> tuple[str, int]:
    model = cfg.model or DEFAULT_MODELS["gemini"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    prompt = f"{system}\n\n{user}" if system else user
    payload = _http_post_json(
        url,
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.85, "maxOutputTokens": 1200},
        },
        {
            "Content-Type": "application/json",
            "x-goog-api-key": cfg.api_key,
        },
    )
    meta = payload.get("usageMetadata") or {}
    tokens = int(
        meta.get("totalTokenCount")
        or meta.get("promptTokenCount", 0) + meta.get("candidatesTokenCount", 0)
    )
    candidates = payload.get("candidates") or []
    if not candidates:
        raise AIClientError("Gemini tidak mengembalikan response")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = parts[0].get("text", "") if parts else ""
    return text.strip(), tokens


def _strip_json_fences(text: str) -> str:
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content


def complete_with_failover(
    session: Session,
    *,
    system: str,
    user: str,
    preferred_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> AICompletionResult:
    """Try providers in priority order; rotate on rate limit."""
    from .quota import get_provider, is_provider_available

    seed_from_env(session, user_id=user_id)
    tried: set[int] = set()
    last_error = "Tidak ada AI provider tersedia"

    def _try(cfg: AIProviderConfig) -> AICompletionResult:
        if cfg.provider == "gemini":
            text, tokens = _call_gemini(cfg, system, user)
        else:
            text, tokens = _call_openai(cfg, system, user)
        record_usage(session, cfg, tokens=tokens)
        return AICompletionResult(
            text=text,
            tokens_used=tokens,
            provider_id=cfg.id,
            provider_label=cfg.label,
            provider_type=cfg.provider,
            model=cfg.model,
        )

    if preferred_id:
        cfg = get_provider(session, preferred_id)
        if cfg and user_id is not None and cfg.user_id not in (None, user_id):
            cfg = None
        if cfg and is_provider_available(cfg):
            try:
                return _try(cfg)
            except AIClientError as e:
                last_error = str(e)
                if is_rate_limit_error(str(e)):
                    mark_rate_limited(session, cfg, str(e))
                tried.add(cfg.id)

    while True:
        cfg = pick_available_provider(session, user_id=user_id)
        if not cfg or cfg.id in tried:
            break
        try:
            return _try(cfg)
        except AIClientError as e:
            last_error = str(e)
            if is_rate_limit_error(str(e)):
                mark_rate_limited(session, cfg, str(e))
            tried.add(cfg.id)

    raise AIClientError(last_error)


def generate_json_array(
    session: Session,
    *,
    system: str,
    user: str,
) -> tuple[list[Any], AICompletionResult]:
    result = complete_with_failover(session, system=system, user=user)
    text = _strip_json_fences(result.text)
    return json.loads(text), result