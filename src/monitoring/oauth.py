"""OAuth state for Social Monitoring connect flows (separate from Multiupload)."""

from __future__ import annotations

import secrets
import time
from typing import Any, Optional

_pending_states: dict[str, dict[str, Any]] = {}
STATE_TTL_SECONDS = 600


def _cleanup() -> None:
    now = time.time()
    expired = [k for k, m in _pending_states.items() if now - m.get("created_at", 0) > STATE_TTL_SECONDS]
    for k in expired:
        _pending_states.pop(k, None)


def create_oauth_state(
    platform: str,
    *,
    user_id: int,
    label: str = "",
    oauth_app_id: Optional[int] = None,
    code_verifier: Optional[str] = None,
) -> str:
    _cleanup()
    state = secrets.token_urlsafe(32)
    _pending_states[state] = {
        "created_at": time.time(),
        "platform": platform,
        "user_id": user_id,
        "label": label.strip(),
        "oauth_app_id": oauth_app_id,
        "code_verifier": code_verifier,
    }
    return state


def pop_oauth_state_meta(state: str) -> dict[str, Any]:
    _cleanup()
    return _pending_states.pop(state, {})