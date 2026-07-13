"""Admin user approval and subscription API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...users import (
    activate_paid_subscription,
    approve_user,
    list_users,
    reject_user,
    set_user_subscription,
    user_to_dict,
)
from ..auth_deps import load_session_user as _load_user
from ..auth_deps import require_admin
from ..deps import get_session

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class RejectUserRequest(BaseModel):
    reason: str = ""


class ApproveUserRequest(BaseModel):
    trial_days: Optional[int] = Field(default=None, ge=1, le=3650)
    plan: str = "trial"


class SetSubscriptionRequest(BaseModel):
    expires_at: Optional[str] = None  # ISO date or datetime
    extend_days: Optional[int] = Field(default=None, ge=1, le=3650)
    plan: Optional[str] = None
    lifetime: bool = False


class PaymentWebhookRequest(BaseModel):
    username: Optional[str] = None
    user_id: Optional[int] = None
    days: int = Field(default=30, ge=1, le=3650)
    plan: str = "monthly"
    payment_ref: str = ""
    webhook_secret: str = ""


def _parse_expires_at(value: str) -> datetime:
    raw = value.strip()
    if len(raw) <= 10:
        return datetime.strptime(raw[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError as e:
        raise ValueError("Format tanggal tidak valid (gunakan YYYY-MM-DD)") from e


@router.get("")
def api_list_users(
    request: Request,
    status: Optional[str] = None,
    session: Session = Depends(get_session),
):
    require_admin(request)
    users = list_users(session, status=status)
    return [user_to_dict(u) for u in users]


@router.get("/pending")
def api_list_pending_users(request: Request, session: Session = Depends(get_session)):
    require_admin(request)
    users = list_users(session, status="pending")
    return [user_to_dict(u) for u in users]


@router.post("/{user_id}/approve")
def api_approve_user(
    user_id: int,
    request: Request,
    req: ApproveUserRequest = ApproveUserRequest(),
    session: Session = Depends(get_session),
):
    require_admin(request)
    admin = _load_user(session, request)
    if not admin:
        raise HTTPException(401, "Unauthorized")
    try:
        user = approve_user(
            session,
            user_id,
            admin,
            trial_days=req.trial_days,
            plan=req.plan,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "user": user_to_dict(user)}


@router.post("/{user_id}/reject")
def api_reject_user(
    user_id: int,
    req: RejectUserRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    require_admin(request)
    admin = _load_user(session, request)
    if not admin:
        raise HTTPException(401, "Unauthorized")
    try:
        user = reject_user(session, user_id, admin, req.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "user": user_to_dict(user)}


@router.patch("/{user_id}/subscription")
def api_set_user_subscription(
    user_id: int,
    req: SetSubscriptionRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    require_admin(request)
    try:
        expires_at = _parse_expires_at(req.expires_at) if req.expires_at else None
        user = set_user_subscription(
            session,
            user_id,
            expires_at=expires_at,
            extend_days=req.extend_days,
            plan=req.plan,
            lifetime=req.lifetime,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "user": user_to_dict(user)}


@router.post("/webhooks/payment")
def api_payment_webhook(req: PaymentWebhookRequest, session: Session = Depends(get_session)):
    """Placeholder untuk payment gateway — set PAYMENT_WEBHOOK_SECRET di .env."""
    import os

    secret = os.getenv("PAYMENT_WEBHOOK_SECRET", "").strip()
    if not secret or req.webhook_secret != secret:
        raise HTTPException(403, "Webhook secret invalid")

    if not req.username and not req.user_id:
        raise HTTPException(400, "username atau user_id wajib")

    try:
        user = activate_paid_subscription(
            session,
            username=req.username,
            user_id=req.user_id,
            days=req.days,
            plan=req.plan,
            payment_ref=req.payment_ref,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, "user": user_to_dict(user)}