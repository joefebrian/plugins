"""Settings API — AI provider configuration and multi-key failover monitoring."""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...ai.client import (
    AIClientError,
    delete_provider,
    save_provider,
    save_providers_bulk,
    seed_from_env,
)
from ...ai.quota import (
    clear_all_rate_limits,
    clear_rate_limit,
    get_provider,
    list_providers,
    monitoring_overview,
    next_priority,
    provider_monitoring_dict,
)
from ..auth_deps import get_current_user_id
from ..deps import get_session

router = APIRouter(prefix="/api/settings/ai", tags=["settings-ai"])

DEFAULT_MODELS = {"openai": "gpt-4o-mini", "gemini": "gemini-flash-latest"}


class AIProviderRequest(BaseModel):
    label: str = "Primary AI"
    provider: str = Field(pattern="^(openai|gemini)$")
    api_key: str
    model: Optional[str] = None
    priority: Optional[int] = None
    daily_token_limit: int = 100_000
    daily_request_limit: int = 500
    is_active: bool = True


class AIProviderUpdateRequest(BaseModel):
    label: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    priority: Optional[int] = None
    daily_token_limit: Optional[int] = None
    daily_request_limit: Optional[int] = None
    is_active: Optional[bool] = None


class BulkAIProviderRequest(BaseModel):
    """Paste many Gemini/OpenAI keys (one per line) for GSuite multi-account failover."""

    provider: str = Field(default="gemini", pattern="^(openai|gemini)$")
    keys_text: str = Field(min_length=8, description="One API key per line")
    model: Optional[str] = None
    label_prefix: str = ""
    daily_token_limit: int = 100_000
    daily_request_limit: int = 500


def _parse_keys_text(raw: str) -> list[str]:
    keys: list[str] = []
    for line in re.split(r"[\n,;]+", raw or ""):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys.append(line)
    return keys


@router.get("/monitoring")
def api_ai_monitoring(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    seed_from_env(session, user_id=user_id)
    return monitoring_overview(session, user_id=user_id)


@router.get("/providers")
def api_list_ai_providers(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    seed_from_env(session, user_id=user_id)
    return [provider_monitoring_dict(c, session=session) for c in list_providers(session, user_id=user_id)]


@router.post("/providers")
def api_create_ai_provider(
    req: AIProviderRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    if not req.api_key:
        raise HTTPException(400, "API Key wajib diisi")
    data = req.model_dump()
    data["user_id"] = user_id
    if not data.get("model"):
        data["model"] = DEFAULT_MODELS.get(req.provider, "gpt-4o-mini")
    if data.get("priority") is None:
        data["priority"] = next_priority(session, user_id=user_id)
    try:
        cfg = save_provider(session, data)
    except AIClientError as e:
        raise HTTPException(400, str(e))
    return {
        "message": "AI Provider ditambahkan — siap di-failover chain",
        "provider": provider_monitoring_dict(cfg),
    }


@router.post("/providers/bulk")
def api_bulk_create_ai_providers(
    req: BulkAIProviderRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    keys = _parse_keys_text(req.keys_text)
    if not keys:
        raise HTTPException(400, "Tempel minimal 1 API key (satu per baris)")
    try:
        created = save_providers_bulk(
            session,
            user_id=user_id,
            provider=req.provider,
            api_keys=keys,
            model=req.model or DEFAULT_MODELS.get(req.provider),
            label_prefix=req.label_prefix
            or ("Gemini GSuite" if req.provider == "gemini" else "OpenAI"),
            daily_token_limit=req.daily_token_limit,
            daily_request_limit=req.daily_request_limit,
        )
    except AIClientError as e:
        raise HTTPException(400, str(e))
    return {
        "message": f"{len(created)} key ditambahkan — auto-switch saat quota habis",
        "added": len(created),
        "providers": [provider_monitoring_dict(c) for c in created],
    }


@router.patch("/providers/{provider_id}")
def api_update_ai_provider(
    provider_id: int,
    req: AIProviderUpdateRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    existing = get_provider(session, provider_id)
    if not existing or existing.user_id not in (None, user_id):
        raise HTTPException(404, "AI Provider tidak ditemukan")
    data = req.model_dump(exclude_unset=True)
    if not data.get("api_key") or str(data.get("api_key", "")).startswith("••"):
        data.pop("api_key", None)
    if data.get("provider") and data["provider"] not in ("openai", "gemini"):
        raise HTTPException(400, "Provider harus openai atau gemini")
    try:
        cfg = save_provider(session, data, provider_id=provider_id)
    except AIClientError as e:
        raise HTTPException(400, str(e))
    return {"message": "AI Provider diupdate", "provider": provider_monitoring_dict(cfg)}


@router.delete("/providers/{provider_id}")
def api_delete_ai_provider(
    provider_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    existing = get_provider(session, provider_id)
    if not existing or existing.user_id not in (None, user_id):
        raise HTTPException(404, "AI Provider tidak ditemukan")
    if not delete_provider(session, provider_id):
        raise HTTPException(404, "AI Provider tidak ditemukan")
    return {"ok": True, "message": "AI Provider dihapus"}


@router.post("/providers/{provider_id}/reset-limit")
def api_reset_ai_provider_limit(
    provider_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    cfg = get_provider(session, provider_id)
    if not cfg or cfg.user_id not in (None, user_id):
        raise HTTPException(404, "AI Provider tidak ditemukan")
    clear_rate_limit(session, cfg)
    return {"ok": True, "provider": provider_monitoring_dict(cfg)}


@router.post("/providers/reset-all-limits")
def api_reset_all_ai_limits(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    n = clear_all_rate_limits(session, user_id=user_id)
    return {
        "ok": True,
        "reset": n,
        "message": f"{n} provider di-reset (cooldown & counter)",
    }
