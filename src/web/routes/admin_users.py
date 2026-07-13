"""Admin user approval API."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...users import approve_user, list_users, reject_user, user_to_dict
from ..auth_deps import load_session_user as _load_user
from ..auth_deps import require_admin
from ..deps import get_session

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class RejectUserRequest(BaseModel):
    reason: str = ""


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
    session: Session = Depends(get_session),
):
    admin_ctx = require_admin(request)
    admin = _load_user(session, request)
    if not admin:
        raise HTTPException(401, "Unauthorized")
    try:
        user = approve_user(session, user_id, admin)
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
    admin_ctx = require_admin(request)
    admin = _load_user(session, request)
    if not admin:
        raise HTTPException(401, "Unauthorized")
    try:
        user = reject_user(session, user_id, admin, req.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "user": user_to_dict(user)}