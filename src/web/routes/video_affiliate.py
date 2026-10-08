"""Video Affiliate API — YouTube brand/product scan."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db.models import init_db
from ...video_affiliate.brand_scan import (
    delete_all_brand_scans,
    delete_brand_scan,
    get_brand_scan,
    list_brand_scans,
    run_brand_scan,
)
from ..auth_deps import get_current_user_id
from ..deps import DB_PATH, get_session
from ..jobs import job_manager

router = APIRouter(prefix="/api/video-affiliate", tags=["video-affiliate"])


class BrandScanRequest(BaseModel):
    url: str = Field(min_length=8, max_length=512)
    use_ai: bool = True
    use_visual: bool = True


def _run_brand_scan_job(user_id: int, url: str, use_ai: bool, use_visual: bool) -> dict:
    session = init_db(DB_PATH)
    try:
        return run_brand_scan(
            session,
            user_id=user_id,
            url=url,
            use_ai=use_ai,
            use_visual=use_visual,
        )
    finally:
        session.close()


@router.post("/brand-scan")
def api_brand_scan(
    req: BrandScanRequest,
    user_id: int = Depends(get_current_user_id),
):
    job = job_manager.create("brand-scan")
    job_manager.run(
        job,
        lambda: _run_brand_scan_job(user_id, req.url.strip(), req.use_ai, req.use_visual),
        "Mengunduh & menganalisis video (teks + frame)..." if req.use_visual else "Menganalisis video YouTube...",
    )
    return job_manager.to_dict(job)


@router.get("/brand-scans")
def api_list_brand_scans(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    return {"scans": list_brand_scans(session, user_id)}


@router.get("/brand-scans/{scan_id}")
def api_get_brand_scan(
    scan_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    data = get_brand_scan(session, scan_id, user_id)
    if not data:
        raise HTTPException(404, "Scan tidak ditemukan")
    return data


@router.delete("/brand-scans/{scan_id}")
def api_delete_brand_scan(
    scan_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    ok = delete_brand_scan(session, scan_id, user_id)
    if not ok:
        raise HTTPException(404, "Scan tidak ditemukan")
    return {"ok": True, "deleted_id": scan_id}


@router.delete("/brand-scans")
def api_delete_all_brand_scans(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    deleted = delete_all_brand_scans(session, user_id)
    return {"ok": True, "deleted": deleted}