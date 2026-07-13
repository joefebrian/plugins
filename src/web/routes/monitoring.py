"""Social account monitoring API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...monitoring.social import MONITORED_PLATFORMS, monitoring_overview, platform_metrics
from ..auth_deps import get_current_user_id
from ..deps import get_session

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


@router.get("/overview")
def api_monitoring_overview(
    live: bool = Query(True, description="Fetch live API stats for YouTube/Facebook"),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    return monitoring_overview(session, user_id, live=live)


@router.get("/{platform}")
def api_monitoring_platform(
    platform: str,
    live: bool = Query(True, description="Fetch live API stats where available"),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    platform = platform.lower().strip()
    if platform not in MONITORED_PLATFORMS:
        raise HTTPException(
            400,
            f"Platform tidak valid. Pilih: {', '.join(MONITORED_PLATFORMS)}",
        )
    try:
        return platform_metrics(session, user_id, platform, live=live)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e