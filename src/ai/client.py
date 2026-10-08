"""Unified AI completion with multi-key failover (OpenAI / Gemini)."""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db.models import AIProviderConfig
from .quota import (
    estimate_tokens,
    is_auth_error,
    is_rate_limit_error,
    list_failover_chain,
    list_providers,
    mark_rate_limited,
    next_priority,
    pick_available_provider,
    record_usage,
)


class AIClientError(Exception):
    pass


DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    # gemini-2.0-flash free tier often returns limit:0 for new keys — use alias that works
    "gemini": "gemini-flash-latest",
}

# Tried in order when configured model hits 404/429 free-tier zero quota
GEMINI_MODEL_FALLBACKS = (
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-2.0-flash",
)


@dataclass
class AICompletionResult:
    text: str
    tokens_used: int
    provider_id: int
    provider_label: str
    provider_type: str
    model: str
    failover_from: str = ""


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

    # Clear exhausted flag when key is updated
    if data.get("api_key") and not str(data["api_key"]).startswith("••"):
        cfg.rate_limited_until = None
        cfg.last_error = None

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


def _existing_key_set(session: Session, user_id: int | None) -> set[str]:
    return {p.api_key.strip() for p in list_providers(session, user_id=user_id) if p.api_key}


def save_providers_bulk(
    session: Session,
    *,
    user_id: int,
    provider: str,
    api_keys: list[str],
    model: str | None = None,
    label_prefix: str = "",
    daily_token_limit: int = 100_000,
    daily_request_limit: int = 500,
) -> list[AIProviderConfig]:
    """Add many API keys (e.g. multi GSuite Gemini). Skips duplicates."""
    if provider not in ("openai", "gemini"):
        raise AIClientError("Provider harus openai atau gemini")

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in api_keys:
        key = (raw or "").strip()
        if not key or key.startswith("#"):
            continue
        # allow "label | key" lines
        if "|" in key and not key.startswith("sk-") and not key.startswith("AIza"):
            parts = [p.strip() for p in key.split("|", 1)]
            if len(parts) == 2 and parts[1]:
                key = parts[1]
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(key)

    if not cleaned:
        raise AIClientError("Tidak ada API key valid")

    existing = _existing_key_set(session, user_id)
    prefix = label_prefix or ("Gemini GSuite" if provider == "gemini" else "OpenAI")
    model_name = model or DEFAULT_MODELS.get(provider, "gpt-4o-mini")
    created: list[AIProviderConfig] = []
    skipped = 0

    for key in cleaned:
        if key in existing:
            skipped += 1
            continue
        prio = next_priority(session, user_id=user_id)
        same = sum(1 for p in list_providers(session, user_id=user_id) if p.provider == provider)
        label = f"{prefix} #{same + 1}"
        cfg = save_provider(
            session,
            {
                "user_id": user_id,
                "label": label,
                "provider": provider,
                "api_key": key,
                "model": model_name,
                "priority": prio,
                "daily_token_limit": daily_token_limit,
                "daily_request_limit": daily_request_limit,
                "is_active": True,
            },
        )
        created.append(cfg)
        existing.add(key)

    if not created and skipped:
        raise AIClientError(f"Semua {skipped} key sudah terdaftar (duplikat)")
    return created


def seed_from_env(session: Session, user_id: int | None = None) -> None:
    """
    Import keys from .env when empty / add multi keys if present.
    OPENAI_API_KEY, GEMINI_API_KEY
    OPENAI_API_KEYS / GEMINI_API_KEYS = comma or newline separated
    """
    providers = list_providers(session, user_id=user_id)
    existing = {p.api_key.strip() for p in providers if p.api_key}

    def _split_keys(raw: str) -> list[str]:
        parts: list[str] = []
        for chunk in re.split(r"[\n,;]+", raw or ""):
            k = chunk.strip()
            if k:
                parts.append(k)
        return parts

    openai_keys = _split_keys(os.getenv("OPENAI_API_KEYS", ""))
    single_oai = os.getenv("OPENAI_API_KEY", "").strip()
    if single_oai:
        openai_keys.insert(0, single_oai)

    gemini_keys = _split_keys(os.getenv("GEMINI_API_KEYS", ""))
    single_gem = os.getenv("GEMINI_API_KEY", "").strip()
    if single_gem:
        gemini_keys.insert(0, single_gem)

    # Only auto-seed when user has no providers yet, OR when multi-key env is set
    if not providers:
        for i, key in enumerate(dict.fromkeys(openai_keys)):
            if key in existing:
                continue
            save_provider(
                session,
                {
                    "user_id": user_id,
                    "label": f"OpenAI (env) #{i + 1}" if len(openai_keys) > 1 else "OpenAI (from .env)",
                    "provider": "openai",
                    "api_key": key,
                    "model": os.getenv("OPENAI_MODEL", DEFAULT_MODELS["openai"]).strip(),
                    "priority": 10 + i * 10,
                },
            )
            existing.add(key)
        for i, key in enumerate(dict.fromkeys(gemini_keys)):
            if key in existing:
                continue
            save_provider(
                session,
                {
                    "user_id": user_id,
                    "label": f"Gemini (env) #{i + 1}" if len(gemini_keys) > 1 else "Gemini (from .env)",
                    "provider": "gemini",
                    "api_key": key,
                    "model": os.getenv("GEMINI_MODEL", DEFAULT_MODELS["gemini"]).strip(),
                    "priority": 50 + i * 10,
                },
            )
            existing.add(key)
        return

    # If env multi-keys present, add missing keys only
    multi_env = os.getenv("OPENAI_API_KEYS", "") or os.getenv("GEMINI_API_KEYS", "")
    if not multi_env:
        return
    for key in dict.fromkeys(openai_keys):
        if key in existing:
            continue
        save_provider(
            session,
            {
                "user_id": user_id,
                "label": f"OpenAI env #{len(existing) + 1}",
                "provider": "openai",
                "api_key": key,
                "model": os.getenv("OPENAI_MODEL", DEFAULT_MODELS["openai"]).strip(),
                "priority": next_priority(session, user_id=user_id),
            },
        )
        existing.add(key)
    for key in dict.fromkeys(gemini_keys):
        if key in existing:
            continue
        save_provider(
            session,
            {
                "user_id": user_id,
                "label": f"Gemini env #{len(existing) + 1}",
                "provider": "gemini",
                "api_key": key,
                "model": os.getenv("GEMINI_MODEL", DEFAULT_MODELS["gemini"]).strip(),
                "priority": next_priority(session, user_id=user_id),
            },
        )
        existing.add(key)


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
    tokens = int(usage.get("total_tokens") or 0)
    if not tokens:
        tokens = int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
    content = payload["choices"][0]["message"]["content"]
    text = (content or "").strip()
    if tokens <= 0:
        tokens = estimate_tokens(system, user, text)
    return text, tokens


def _gemini_model_candidates(preferred: str | None) -> list[str]:
    models: list[str] = []
    if preferred:
        models.append(preferred.strip())
    for m in GEMINI_MODEL_FALLBACKS:
        if m not in models:
            models.append(m)
    return models


def _is_gemini_model_unavailable(error: str) -> bool:
    lower = (error or "").lower()
    if "404" in lower or "not found" in lower or "no longer available" in lower:
        return True
    # Free tier zero quota on a specific model — try another model before killing the key
    if "limit: 0" in lower and "free_tier" in lower:
        return True
    if "exceeded your current quota" in lower and "free_tier" in lower:
        return True
    return False


def _gemini_generate(
    api_key: str,
    model: str,
    parts: list[dict],
    *,
    temperature: float,
    max_output_tokens: int,
    timeout: int = 90,
) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    return _http_post_json(
        url,
        {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        },
        {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        timeout=timeout,
    )


def _parse_gemini_text_and_tokens(payload: dict, *estimate_parts: str, images: int = 0) -> tuple[str, int]:
    meta = payload.get("usageMetadata") or {}
    tokens = int(meta.get("totalTokenCount") or 0)
    if not tokens:
        tokens = int(meta.get("promptTokenCount") or 0) + int(meta.get("candidatesTokenCount") or 0)
    candidates = payload.get("candidates") or []
    if not candidates:
        raise AIClientError("Gemini tidak mengembalikan response")
    parts = candidates[0].get("content", {}).get("parts") or []
    # Skip thought-only parts; take first text part
    text = ""
    for part in parts:
        if part.get("text"):
            text = str(part["text"]).strip()
            break
    if tokens <= 0:
        tokens = estimate_tokens(*estimate_parts, text, images=images)
    return text, tokens


def _call_gemini_with_model_fallback(
    cfg: AIProviderConfig,
    parts: list[dict],
    *,
    temperature: float,
    max_output_tokens: int,
    timeout: int = 90,
    estimate_parts: tuple[str, ...] = (),
    images: int = 0,
) -> tuple[str, int]:
    """Try configured model then newer free-tier-friendly aliases."""
    last_error = "Gemini gagal"
    preferred = cfg.model or DEFAULT_MODELS["gemini"]
    for model in _gemini_model_candidates(preferred):
        try:
            payload = _gemini_generate(
                cfg.api_key,
                model,
                parts,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
            )
            text, tokens = _parse_gemini_text_and_tokens(
                payload, *estimate_parts, images=images
            )
            # Persist working model so next call skips dead free-tier models
            if cfg.model != model:
                cfg.model = model
                cfg.updated_at = __import__("datetime").datetime.utcnow()
            return text, tokens
        except AIClientError as e:
            last_error = str(e)
            if _is_gemini_model_unavailable(last_error):
                continue
            raise
    raise AIClientError(last_error)


def _call_gemini(cfg: AIProviderConfig, system: str, user: str) -> tuple[str, int]:
    prompt = f"{system}\n\n{user}" if system else user
    return _call_gemini_with_model_fallback(
        cfg,
        [{"text": prompt}],
        temperature=0.85,
        max_output_tokens=1200,
        estimate_parts=(system, user),
    )


def _encode_image_b64(image_path: Path) -> tuple[str, str]:
    suffix = image_path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return mime, data


def _call_openai_vision(
    cfg: AIProviderConfig,
    system: str,
    user: str,
    image_paths: list[Path],
) -> tuple[str, int]:
    content: list[dict[str, Any]] = [{"type": "text", "text": user}]
    for path in image_paths:
        mime, data = _encode_image_b64(path)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{data}"},
        })
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    payload = _http_post_json(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": cfg.model or DEFAULT_MODELS["openai"],
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1800,
        },
        {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        timeout=120,
    )
    usage = payload.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)
    if not tokens:
        tokens = int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
    content_out = payload["choices"][0]["message"]["content"]
    text = (content_out or "").strip()
    if tokens <= 0:
        tokens = estimate_tokens(system, user, text, images=len(image_paths))
    return text, tokens


def _call_gemini_vision(
    cfg: AIProviderConfig,
    system: str,
    user: str,
    image_paths: list[Path],
) -> tuple[str, int]:
    parts: list[dict[str, Any]] = []
    if system:
        parts.append({"text": system})
    parts.append({"text": user})
    for path in image_paths:
        mime, data = _encode_image_b64(path)
        parts.append({"inline_data": {"mime_type": mime, "data": data}})
    text, tokens = _call_gemini_with_model_fallback(
        cfg,
        parts,
        temperature=0.2,
        max_output_tokens=1800,
        timeout=120,
        estimate_parts=(system, user),
        images=len(image_paths),
    )
    if not text:
        raise AIClientError("Gemini vision tidak mengembalikan response")
    return text, tokens


def _dispatch_text(cfg: AIProviderConfig, system: str, user: str) -> tuple[str, int]:
    if cfg.provider == "gemini":
        return _call_gemini(cfg, system, user)
    return _call_openai(cfg, system, user)


def _dispatch_vision(
    cfg: AIProviderConfig,
    system: str,
    user: str,
    paths: list[Path],
) -> tuple[str, int]:
    if cfg.provider == "gemini":
        return _call_gemini_vision(cfg, system, user, paths)
    return _call_openai_vision(cfg, system, user, paths)


def _handle_provider_failure(session: Session, cfg: AIProviderConfig, error: str) -> None:
    if is_rate_limit_error(error) or is_auth_error(error):
        mark_rate_limited(session, cfg, error)


def _failover_loop(
    session: Session,
    *,
    user_id: Optional[int],
    preferred_id: Optional[int],
    try_fn,
    empty_message: str,
) -> AICompletionResult:
    """Try providers in priority order; auto-switch when one is exhausted."""
    from .quota import get_provider, is_provider_available

    seed_from_env(session, user_id=user_id)
    tried: set[int] = set()
    errors: list[str] = []
    switched_from: list[str] = []

    if preferred_id:
        cfg = get_provider(session, preferred_id)
        if cfg and user_id is not None and cfg.user_id not in (None, user_id):
            cfg = None
        if cfg and is_provider_available(cfg):
            try:
                text, tokens = try_fn(cfg)
                record_usage(session, cfg, tokens=tokens)
                return AICompletionResult(
                    text=text,
                    tokens_used=tokens,
                    provider_id=cfg.id,
                    provider_label=cfg.label,
                    provider_type=cfg.provider,
                    model=cfg.model,
                )
            except AIClientError as e:
                errors.append(f"{cfg.label}: {e}")
                _handle_provider_failure(session, cfg, str(e))
                tried.add(cfg.id)
                switched_from.append(cfg.label)

    # Walk full chain — exclude tried so we never infinite-loop on non-429 errors
    while True:
        cfg = pick_available_provider(session, user_id=user_id, exclude_ids=tried)
        if not cfg:
            break
        try:
            text, tokens = try_fn(cfg)
            record_usage(session, cfg, tokens=tokens)
            return AICompletionResult(
                text=text,
                tokens_used=tokens,
                provider_id=cfg.id,
                provider_label=cfg.label,
                provider_type=cfg.provider,
                model=cfg.model,
                failover_from=" → ".join(switched_from) if switched_from else "",
            )
        except AIClientError as e:
            errors.append(f"{cfg.label}: {e}")
            _handle_provider_failure(session, cfg, str(e))
            tried.add(cfg.id)
            switched_from.append(cfg.label)

    chain_count = len(list_failover_chain(session, user_id=user_id)) + len(tried)
    detail = " | ".join(errors[-6:]) if errors else empty_message
    raise AIClientError(
        f"Semua AI provider gagal ({len(tried)} dicoba, {chain_count} terdaftar). {detail}"
    )


def complete_vision_with_failover(
    session: Session,
    *,
    system: str,
    user: str,
    image_paths: list[Path | str],
    preferred_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> AICompletionResult:
    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        raise AIClientError("Tidak ada gambar frame untuk vision AI")

    return _failover_loop(
        session,
        user_id=user_id,
        preferred_id=preferred_id,
        try_fn=lambda cfg: _dispatch_vision(cfg, system, user, paths),
        empty_message="Tidak ada AI provider tersedia untuk vision",
    )


def complete_with_failover(
    session: Session,
    *,
    system: str,
    user: str,
    preferred_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> AICompletionResult:
    """Try providers by priority; on quota/rate-limit auto-switch to next key."""
    return _failover_loop(
        session,
        user_id=user_id,
        preferred_id=preferred_id,
        try_fn=lambda cfg: _dispatch_text(cfg, system, user),
        empty_message="Tidak ada AI provider tersedia",
    )


def _strip_json_fences(text: str) -> str:
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content


def generate_json_array(
    session: Session,
    *,
    system: str,
    user: str,
) -> tuple[list[Any], AICompletionResult]:
    result = complete_with_failover(session, system=system, user=user)
    text = _strip_json_fences(result.text)
    return json.loads(text), result
