"""AI token usage tracking and provider failover."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import AIProviderConfig

RATE_LIMIT_KEYWORDS = (
    "rate limit",
    "quota",
    "exceeded",
    "429",
    "resource_exhausted",
    "insufficient",
    "billing",
    "limit",
)


def _today_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def reset_daily_counters_if_needed(cfg: AIProviderConfig) -> None:
    today = _today_key()
    if cfg.usage_date != today:
        cfg.usage_date = today
        cfg.tokens_today = 0
        cfg.requests_today = 0
        if cfg.rate_limited_until and cfg.rate_limited_until <= datetime.utcnow():
            cfg.rate_limited_until = None
            cfg.last_error = None


def is_rate_limit_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(k in lower for k in RATE_LIMIT_KEYWORDS)


def is_provider_available(cfg: AIProviderConfig) -> bool:
    if not cfg.is_active or not cfg.api_key:
        return False
    reset_daily_counters_if_needed(cfg)
    if cfg.rate_limited_until and cfg.rate_limited_until > datetime.utcnow():
        return False
    if cfg.tokens_today >= cfg.daily_token_limit:
        return False
    if cfg.requests_today >= cfg.daily_request_limit:
        return False
    return True


def get_provider_status(cfg: AIProviderConfig) -> str:
    reset_daily_counters_if_needed(cfg)
    if not cfg.is_active:
        return "disabled"
    if cfg.rate_limited_until and cfg.rate_limited_until > datetime.utcnow():
        return "exhausted"
    token_pct = cfg.tokens_today / max(cfg.daily_token_limit, 1)
    req_pct = cfg.requests_today / max(cfg.daily_request_limit, 1)
    if token_pct >= 1 or req_pct >= 1:
        return "exhausted"
    if token_pct >= 0.8 or req_pct >= 0.8:
        return "warning"
    return "ok"


def list_providers(session: Session, user_id: int | None = None) -> list[AIProviderConfig]:
    q = session.query(AIProviderConfig)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.order_by(AIProviderConfig.priority.asc(), AIProviderConfig.id.asc()).all()


def get_provider(session: Session, provider_id: int) -> Optional[AIProviderConfig]:
    return session.query(AIProviderConfig).filter_by(id=provider_id).first()


def pick_available_provider(session: Session, user_id: int | None = None) -> Optional[AIProviderConfig]:
    for cfg in list_providers(session, user_id=user_id):
        if is_provider_available(cfg):
            return cfg
    return None


def record_usage(session: Session, cfg: AIProviderConfig, *, tokens: int) -> None:
    reset_daily_counters_if_needed(cfg)
    cfg.tokens_today += max(tokens, 0)
    cfg.requests_today += 1
    cfg.updated_at = datetime.utcnow()
    session.commit()


def mark_rate_limited(
    session: Session,
    cfg: AIProviderConfig,
    error: str,
    *,
    hours: int = 24,
) -> None:
    cfg.rate_limited_until = datetime.utcnow() + timedelta(hours=hours)
    cfg.last_error = error[:500]
    cfg.updated_at = datetime.utcnow()
    session.commit()


def clear_rate_limit(session: Session, cfg: AIProviderConfig) -> None:
    cfg.rate_limited_until = None
    cfg.last_error = None
    cfg.tokens_today = 0
    cfg.requests_today = 0
    cfg.updated_at = datetime.utcnow()
    session.commit()


def provider_monitoring_dict(cfg: AIProviderConfig) -> dict:
    reset_daily_counters_if_needed(cfg)
    key = cfg.api_key or ""
    masked = f"••{key[-4:]}" if len(key) >= 4 else ("••••" if key else "")
    status = get_provider_status(cfg)
    return {
        "id": cfg.id,
        "label": cfg.label,
        "provider": cfg.provider,
        "model": cfg.model,
        "api_key": masked,
        "priority": cfg.priority,
        "is_active": cfg.is_active,
        "configured": bool(cfg.api_key),
        "tokens_today": cfg.tokens_today,
        "tokens_limit": cfg.daily_token_limit,
        "tokens_pct": round(100 * cfg.tokens_today / max(cfg.daily_token_limit, 1), 1),
        "requests_today": cfg.requests_today,
        "requests_limit": cfg.daily_request_limit,
        "requests_pct": round(100 * cfg.requests_today / max(cfg.daily_request_limit, 1), 1),
        "status": status,
        "usage_date": cfg.usage_date,
        "rate_limited_until": cfg.rate_limited_until.isoformat() if cfg.rate_limited_until else None,
        "last_error": cfg.last_error,
        "available": is_provider_available(cfg),
    }


def monitoring_overview(session: Session, user_id: int | None = None) -> dict:
    items = [provider_monitoring_dict(c) for c in list_providers(session, user_id=user_id)]
    available = [i for i in items if i["available"]]
    recommended = pick_available_provider(session, user_id=user_id)
    return {
        "providers": items,
        "total_providers": len(items),
        "available_providers": len(available),
        "recommended_provider_id": recommended.id if recommended else None,
        "any_available": len(available) > 0,
    }