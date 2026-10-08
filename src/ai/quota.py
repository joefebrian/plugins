"""AI token usage tracking and multi-provider automatic failover."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

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

# Hard billing/quota — lock longer so we switch to other keys
HARD_QUOTA_KEYWORDS = (
    "insufficient_quota",
    "billing",
    "limit: 0",
    "free_tier",
    "plan and billing",
    "exceeded your current quota",
)

AUTH_KEYWORDS = (
    "invalid api key",
    "api key not valid",
    "permission denied",
    "unauthenticated",
    "401",
    "403",
    "api_key_invalid",
)


def _today_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def reset_daily_counters_if_needed(cfg: AIProviderConfig, *, persist: bool = False) -> bool:
    """
    Roll usage counters at UTC midnight.
    Returns True if counters were reset (caller may commit).
    """
    today = _today_key()
    changed = False
    if cfg.usage_date != today:
        cfg.usage_date = today
        cfg.tokens_today = 0
        cfg.requests_today = 0
        changed = True
    if cfg.rate_limited_until and cfg.rate_limited_until <= datetime.utcnow():
        cfg.rate_limited_until = None
        # keep last_error for history until next success
        changed = True
    return changed


def is_rate_limit_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(k in lower for k in RATE_LIMIT_KEYWORDS)


def is_hard_quota_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(k in lower for k in HARD_QUOTA_KEYWORDS)


def is_auth_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(k in lower for k in AUTH_KEYWORDS)


def cooldown_for_error(message: str) -> timedelta:
    """
    Soft 429 / RPM → short cooldown (auto-retry soon).
    Hard quota / billing → long cooldown (switch to other GSuite keys).
    Auth errors → long cooldown until key fixed.
    """
    if is_auth_error(message):
        return timedelta(hours=24)
    if is_hard_quota_error(message):
        return timedelta(hours=12)
    if is_rate_limit_error(message):
        return timedelta(minutes=3)
    return timedelta(minutes=15)


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


def next_priority(session: Session, user_id: int | None = None) -> int:
    """Auto priority for new keys: 10, 20, 30… after existing max."""
    providers = list_providers(session, user_id=user_id)
    if not providers:
        return 10
    return max(p.priority for p in providers) + 10


def pick_available_provider(
    session: Session,
    user_id: int | None = None,
    *,
    exclude_ids: Iterable[int] | None = None,
) -> Optional[AIProviderConfig]:
    """Next healthy provider by priority (skip exclude_ids — for failover chain)."""
    excluded = set(exclude_ids or ())
    for cfg in list_providers(session, user_id=user_id):
        if cfg.id in excluded:
            continue
        if is_provider_available(cfg):
            return cfg
    return None


def list_failover_chain(
    session: Session,
    user_id: int | None = None,
    *,
    exclude_ids: Iterable[int] | None = None,
) -> list[AIProviderConfig]:
    """All active providers in priority order, excluding already-tried ids."""
    excluded = set(exclude_ids or ())
    chain: list[AIProviderConfig] = []
    for cfg in list_providers(session, user_id=user_id):
        if cfg.id in excluded:
            continue
        if not cfg.is_active or not cfg.api_key:
            continue
        if is_provider_available(cfg):
            chain.append(cfg)
    return chain


def record_usage(session: Session, cfg: AIProviderConfig, *, tokens: int) -> None:
    """Record a successful API call. Always bumps request count; tokens may be estimated."""
    reset_daily_counters_if_needed(cfg)
    cfg.tokens_today = int(cfg.tokens_today or 0) + max(int(tokens or 0), 0)
    cfg.requests_today = int(cfg.requests_today or 0) + 1
    # Successful call → clear exhausted flag so bar stays green
    if cfg.rate_limited_until and cfg.rate_limited_until > datetime.utcnow():
        cfg.rate_limited_until = None
        cfg.last_error = None
    cfg.updated_at = datetime.utcnow()
    session.commit()


def estimate_tokens(*parts: str, images: int = 0) -> int:
    """Rough token estimate when provider omits usage metadata."""
    chars = sum(len(p or "") for p in parts)
    # ~4 chars/token + ~258 tokens per image (vision rough)
    return max(1, chars // 4 + images * 258)


def mark_rate_limited(
    session: Session,
    cfg: AIProviderConfig,
    error: str,
    *,
    hours: float | None = None,
    minutes: float | None = None,
) -> None:
    """Mark provider exhausted so failover switches to the next key."""
    if minutes is not None:
        delta = timedelta(minutes=minutes)
    elif hours is not None:
        delta = timedelta(hours=hours)
    else:
        delta = cooldown_for_error(error)
    cfg.rate_limited_until = datetime.utcnow() + delta
    cfg.last_error = (error or "")[:500]
    cfg.updated_at = datetime.utcnow()
    session.commit()


def clear_rate_limit(
    session: Session,
    cfg: AIProviderConfig,
    *,
    reset_counters: bool = False,
) -> None:
    """Clear cooldown / error. By default keep token usage stats visible."""
    cfg.rate_limited_until = None
    cfg.last_error = None
    if reset_counters:
        cfg.tokens_today = 0
        cfg.requests_today = 0
    cfg.updated_at = datetime.utcnow()
    session.commit()


def clear_all_rate_limits(
    session: Session,
    user_id: int | None = None,
    *,
    reset_counters: bool = False,
) -> int:
    n = 0
    for cfg in list_providers(session, user_id=user_id):
        needs = bool(cfg.rate_limited_until or cfg.last_error or reset_counters)
        if not needs:
            continue
        clear_rate_limit(session, cfg, reset_counters=reset_counters)
        n += 1
    return n


def provider_monitoring_dict(cfg: AIProviderConfig, session: Session | None = None) -> dict:
    if reset_daily_counters_if_needed(cfg) and session is not None:
        try:
            session.commit()
        except Exception:
            session.rollback()
    key = cfg.api_key or ""
    masked = f"••{key[-4:]}" if len(key) >= 4 else ("••••" if key else "")
    status = get_provider_status(cfg)
    tokens = int(cfg.tokens_today or 0)
    requests = int(cfg.requests_today or 0)
    token_limit = max(int(cfg.daily_token_limit or 1), 1)
    req_limit = max(int(cfg.daily_request_limit or 1), 1)
    cooldown_left = None
    if cfg.rate_limited_until and cfg.rate_limited_until > datetime.utcnow():
        secs = int((cfg.rate_limited_until - datetime.utcnow()).total_seconds())
        cooldown_left = max(0, secs)
    return {
        "id": cfg.id,
        "label": cfg.label,
        "provider": cfg.provider,
        "model": cfg.model,
        "api_key": masked,
        "priority": cfg.priority,
        "is_active": cfg.is_active,
        "configured": bool(cfg.api_key),
        "tokens_today": tokens,
        "tokens_limit": token_limit,
        "tokens_pct": round(100 * tokens / token_limit, 1),
        "requests_today": requests,
        "requests_limit": req_limit,
        "requests_pct": round(100 * requests / req_limit, 1),
        "status": status,
        "usage_date": cfg.usage_date,
        "rate_limited_until": cfg.rate_limited_until.isoformat() if cfg.rate_limited_until else None,
        "cooldown_seconds": cooldown_left,
        "last_error": cfg.last_error,
        "available": is_provider_available(cfg),
    }


def monitoring_overview(session: Session, user_id: int | None = None) -> dict:
    items = [provider_monitoring_dict(c, session=session) for c in list_providers(session, user_id=user_id)]
    available = [i for i in items if i["available"]]
    recommended = pick_available_provider(session, user_id=user_id)
    gemini_keys = sum(1 for i in items if i["provider"] == "gemini")
    openai_keys = sum(1 for i in items if i["provider"] == "openai")
    total_tokens = sum(i["tokens_today"] for i in items)
    total_requests = sum(i["requests_today"] for i in items)
    return {
        "providers": items,
        "total_providers": len(items),
        "available_providers": len(available),
        "gemini_keys": gemini_keys,
        "openai_keys": openai_keys,
        "tokens_today_total": total_tokens,
        "requests_today_total": total_requests,
        "recommended_provider_id": recommended.id if recommended else None,
        "any_available": len(available) > 0,
        "failover_enabled": len(items) > 1,
    }
