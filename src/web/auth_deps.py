"""Request-scoped auth helpers."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..db.models import Profile, User
from ..users import get_user_by_id


def session_user_id(request: Request) -> Optional[int]:
    uid = request.session.get("user_id")
    return int(uid) if uid is not None else None


def session_is_admin(request: Request) -> bool:
    return request.session.get("role") == "admin"


def require_session_user(request: Request) -> dict:
    if not request.session.get("authenticated"):
        raise HTTPException(401, "Unauthorized")
    user_id = session_user_id(request)
    if not user_id:
        raise HTTPException(401, "Unauthorized")
    return {
        "user_id": user_id,
        "username": request.session.get("username"),
        "role": request.session.get("role", "user"),
        "is_admin": session_is_admin(request),
    }


def require_admin(request: Request) -> dict:
    ctx = require_session_user(request)
    if not ctx["is_admin"]:
        raise HTTPException(403, "Hanya admin yang bisa akses")
    return ctx


def get_owned_profile(
    session: Session,
    profile_id: int,
    user_id: int,
) -> Profile:
    profile = session.query(Profile).filter_by(id=profile_id, user_id=user_id).first()
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")
    return profile


def load_session_user(session: Session, request: Request) -> Optional[User]:
    user_id = session_user_id(request)
    if not user_id:
        return None
    return get_user_by_id(session, user_id)


def get_current_user_id(request: Request) -> int:
    return require_session_user(request)["user_id"]