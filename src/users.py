"""Multi-user accounts with admin approval and subscription expiry."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from sqlalchemy.orm import Session

from .auth import AuthStore, _hash_password, _verify_password
from .db.models import User

BCRYPT_ROUNDS = 12
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
DEFAULT_TRIAL_DAYS = int(os.getenv("DEFAULT_TRIAL_DAYS", "30"))


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    name = _normalize_username(username)
    if not _USERNAME_RE.match(name):
        raise ValueError("Username 3–32 karakter: huruf, angka, titik, underscore, strip")
    return name


def is_subscription_active(user: User, *, at: Optional[datetime] = None) -> bool:
    """Admin never expires. Null expires_at = no limit (lifetime / belum di-set)."""
    if user.role == "admin":
        return True
    if user.expires_at is None:
        return True
    now = at or datetime.utcnow()
    return user.expires_at > now


def subscription_info(user: User) -> dict:
    now = datetime.utcnow()
    active = is_subscription_active(user, at=now)
    days_left: Optional[int] = None
    if user.role == "admin":
        days_left = None
    elif user.expires_at is None:
        days_left = None
    else:
        delta = user.expires_at - now
        days_left = max(0, delta.days) if active else 0

    return {
        "plan": user.plan,
        "expires_at": user.expires_at.isoformat() if user.expires_at else None,
        "is_active": active,
        "is_expired": not active and user.role != "admin",
        "days_left": days_left,
        "payment_ref": user.payment_ref,
    }


def access_block_reason(user: User) -> Optional[str]:
    if user.status == "pending":
        return "Akun menunggu persetujuan admin. Coba lagi setelah disetujui."
    if user.status == "rejected":
        reason = user.rejected_reason or "Pendaftaran ditolak"
        return f"Akun ditolak: {reason}"
    if not is_subscription_active(user):
        exp = user.expires_at.strftime("%d %b %Y") if user.expires_at else ""
        return f"Langganan kedaluwarsa{(' (' + exp + ')') if exp else ''}. Hubungi admin atau perpanjang pembayaran."
    return None


def user_to_dict(user: User, *, include_status: bool = True) -> dict:
    data = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "role": user.role,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "subscription": subscription_info(user),
    }
    if include_status:
        data.update(
            {
                "status": user.status,
                "approved_at": user.approved_at.isoformat() if user.approved_at else None,
                "rejected_reason": user.rejected_reason,
            }
        )
    return data


def get_user_by_username(session: Session, username: str) -> Optional[User]:
    return session.query(User).filter_by(username=_normalize_username(username)).first()


def get_user_by_id(session: Session, user_id: int) -> Optional[User]:
    return session.query(User).filter_by(id=user_id).first()


def ensure_admin_user(session: Session, auth_store: AuthStore) -> User:
    """Bootstrap admin from env / legacy auth.json."""
    admin = session.query(User).filter_by(role="admin").order_by(User.id.asc()).first()
    if admin:
        if admin.expires_at is not None:
            admin.expires_at = None
            admin.plan = "lifetime"
            session.commit()
        return admin

    username = os.getenv("AUTH_USERNAME", "admin").strip().lower() or "admin"
    password = os.getenv("AUTH_PASSWORD", "Affiliate@2026").strip()

    legacy = auth_store._load()
    if legacy.get("username"):
        username = str(legacy["username"]).strip().lower()
    if legacy.get("password_hash"):
        password_hash = legacy["password_hash"]
    else:
        password_hash = _hash_password(password)

    admin = User(
        username=username,
        password_hash=password_hash,
        role="admin",
        status="approved",
        display_name="Administrator",
        approved_at=datetime.utcnow(),
        plan="lifetime",
        expires_at=None,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def register_user(
    session: Session,
    *,
    username: str,
    password: str,
    display_name: str = "",
    email: str = "",
) -> User:
    name = validate_username(username)
    if len(password) < 8:
        raise ValueError("Password minimal 8 karakter")
    if get_user_by_username(session, name):
        raise ValueError("Username sudah dipakai")

    email_norm = email.strip().lower() or None
    if email_norm:
        existing = session.query(User).filter_by(email=email_norm).first()
        if existing:
            raise ValueError("Email sudah terdaftar")

    user = User(
        username=name,
        email=email_norm,
        password_hash=_hash_password(password),
        role="user",
        status="pending",
        display_name=(display_name.strip() or name),
        plan="trial",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate_user(session: Session, username: str, password: str) -> tuple[Optional[User], Optional[str]]:
    """Return (user, error_message). error_message set when login blocked."""
    user = get_user_by_username(session, username)
    if not user:
        bcrypt.checkpw(password.encode("utf-8"), bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)))
        return None, "Username atau password salah"

    if not _verify_password(password, user.password_hash):
        return None, "Username atau password salah"

    blocked = access_block_reason(user)
    if blocked:
        return None, blocked

    return user, None


def change_user_password(session: Session, user: User, current: str, new_password: str) -> None:
    if not _verify_password(current, user.password_hash):
        raise ValueError("Password saat ini salah")
    if len(new_password) < 8:
        raise ValueError("Password baru minimal 8 karakter")
    user.password_hash = _hash_password(new_password)
    session.commit()


def list_users(session: Session, *, status: Optional[str] = None) -> list[User]:
    q = session.query(User).order_by(User.created_at.desc())
    if status:
        q = q.filter_by(status=status)
    return q.all()


def _default_expiry(trial_days: Optional[int] = None) -> datetime:
    days = DEFAULT_TRIAL_DAYS if trial_days is None else max(1, trial_days)
    return datetime.utcnow() + timedelta(days=days)


def approve_user(
    session: Session,
    user_id: int,
    admin: User,
    *,
    trial_days: Optional[int] = None,
    plan: str = "trial",
) -> User:
    user = get_user_by_id(session, user_id)
    if not user:
        raise ValueError("User tidak ditemukan")
    if user.role == "admin":
        raise ValueError("Admin tidak perlu disetujui")
    user.status = "approved"
    user.approved_at = datetime.utcnow()
    user.approved_by_id = admin.id
    user.rejected_reason = None
    user.plan = plan or "trial"
    user.expires_at = _default_expiry(trial_days)
    session.commit()
    session.refresh(user)
    return user


def reject_user(session: Session, user_id: int, admin: User, reason: str = "") -> User:
    user = get_user_by_id(session, user_id)
    if not user:
        raise ValueError("User tidak ditemukan")
    if user.role == "admin":
        raise ValueError("Tidak bisa menolak akun admin")
    user.status = "rejected"
    user.approved_at = None
    user.approved_by_id = admin.id
    user.rejected_reason = reason.strip() or "Ditolak oleh admin"
    session.commit()
    session.refresh(user)
    return user


def set_user_subscription(
    session: Session,
    user_id: int,
    *,
    expires_at: Optional[datetime] = None,
    extend_days: Optional[int] = None,
    plan: Optional[str] = None,
    payment_ref: Optional[str] = None,
    lifetime: bool = False,
) -> User:
    user = get_user_by_id(session, user_id)
    if not user:
        raise ValueError("User tidak ditemukan")
    if user.role == "admin":
        raise ValueError("Langganan admin tidak perlu diatur")

    if lifetime:
        user.expires_at = None
        user.plan = "lifetime"
    elif extend_days is not None:
        base = user.expires_at if user.expires_at and user.expires_at > datetime.utcnow() else datetime.utcnow()
        user.expires_at = base + timedelta(days=max(1, extend_days))
        if plan:
            user.plan = plan
    elif expires_at is not None:
        user.expires_at = expires_at
        if plan:
            user.plan = plan

    if plan and not lifetime:
        user.plan = plan
    if payment_ref:
        user.payment_ref = payment_ref.strip()

    session.commit()
    session.refresh(user)
    return user


def activate_paid_subscription(
    session: Session,
    *,
    username: Optional[str] = None,
    user_id: Optional[int] = None,
    days: int = 30,
    plan: str = "monthly",
    payment_ref: str = "",
) -> User:
    """Hook for payment gateway — extend subscription after successful payment."""
    user = None
    if user_id:
        user = get_user_by_id(session, user_id)
    elif username:
        user = get_user_by_username(session, username)
    if not user:
        raise ValueError("User tidak ditemukan")

    if user.status != "approved":
        user.status = "approved"
        user.approved_at = user.approved_at or datetime.utcnow()

    return set_user_subscription(
        session,
        user.id,
        extend_days=days,
        plan=plan,
        payment_ref=payment_ref,
    )