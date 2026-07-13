"""Settings API — AI provider configuration and monitoring."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...ai.client import AIClientError, delete_provider, save_provider, seed_from_env
from ...ai.quota import (
    clear_rate_limit,
    get_provider,
    list_providers,
    monitoring_overview,
    provider_monitoring_dict,
)
from ..auth_deps import get_current_user_id
from ..deps import get_session

router = APIRouter(prefix="/api/settings/ai", tags=["settings-ai"])

DEFAULT_MODELS = {"openai": "gpt-4o-mini", "gemini": "gemini-2.0-flash"}


class AIProviderRequest(BaseModel):
    label: str = "Primary AI"
    provider: str = Field(pattern="^(openai|gemini)$")
    api_key: str
    model: Optional[str] = None
    priority: int = 100
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
    return [provider_monitoring_dict(c) for c in list_providers(session, user_id=user_id)]


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
    try:
        cfg = save_provider(session, data)
    except AIClientError as e:
        raise HTTPException(400, str(e))
    return {
        "message": "AI Provider ditambahkan",
        "provider": provider_monitoring_dict(cfg),
    }


@router.patch("/providers/{provider_id}")
def api_update_ai_provider(
    provider_id: int,
    req: AIProviderUpdateRequest,
    session: Session = Depends(get_session),
):
    existing = get_provider(session, provider_id)
    if not existing:
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
def api_delete_ai_provider(provider_id: int, session: Session = Depends(get_session)):
    if not delete_provider(session, provider_id):
        raise HTTPException(404, "AI Provider tidak ditemukan")
    return {"ok": True, "message": "AI Provider dihapus"}


@router.post("/providers/{provider_id}/reset-limit")
def api_reset_ai_provider_limit(provider_id: int, session: Session = Depends(get_session)):
    cfg = get_provider(session, provider_id)
    if not cfg:
        raise HTTPException(404, "AI Provider tidak ditemukan")
    clear_rate_limit(session, cfg)
    return {"ok": True, "provider": provider_monitoring_dict(cfg)}