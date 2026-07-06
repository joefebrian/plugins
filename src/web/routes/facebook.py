"""Facebook Uploader API routes."""

from __future__ import annotations

import os
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db.models import init_db
from ...facebook.client import (
    FacebookAPIError,
    app_config_to_dict,
    build_auth_url,
    create_oauth_state,
    create_or_update_page,
    delete_page,
    disconnect_page,
    exchange_code_for_token,
    exchange_long_lived_token,
    get_app_config,
    get_user_pages,
    list_pages,
    page_to_dict,
    pop_oauth_state_meta,
    save_app_config,
    test_page_connection,
)
from ...facebook.uploader import bulk_upload_videos, upload_manual_files
from ...services import get_profile, parse_date_filter
from ..deps import DB_PATH, DOWNLOAD_DIR, get_session
from ..jobs import job_manager

router = APIRouter(prefix="/api/facebook", tags=["facebook"])

MANUAL_FB_DIR = DOWNLOAD_DIR / "manual_facebook"
ALLOWED_MANUAL_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


class FacebookAppConfigRequest(BaseModel):
    label: str = "Facebook App"
    app_id: str
    app_secret: Optional[str] = None
    redirect_uri: Optional[str] = None


class FacebookConnectRequest(BaseModel):
    label: Optional[str] = None


class FacebookUploadRequest(BaseModel):
    facebook_page_id: int
    limit: Optional[int] = 10
    published: bool = True
    title_template: str = "{title}"
    description_template: str = "{url}\n\nViews: {views} | GMV: Rp {gmv} | @{username}"
    status: Optional[str] = None
    sort_by: str = "gmv"
    min_views: Optional[int] = None
    max_views: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    apply_filters: bool = False
    skip_uploaded: bool = True
    only_downloaded: bool = True


def _default_redirect(request: Request) -> str:
    env_redirect = os.getenv("FACEBOOK_REDIRECT_URI", "").strip()
    if env_redirect:
        return env_redirect
    return str(request.base_url).rstrip("/") + "/api/facebook/oauth/callback"


def _run_facebook_upload(profile_id: int, req: FacebookUploadRequest) -> dict:
    session = init_db(DB_PATH)
    try:
        filter_status = None if not req.status or req.status == "all" else req.status
        return bulk_upload_videos(
            session,
            profile_id,
            req.facebook_page_id,
            limit=req.limit,
            published=req.published,
            title_template=req.title_template,
            description_template=req.description_template,
            status=filter_status,
            sort_by=req.sort_by,
            min_views=req.min_views,
            max_views=req.max_views,
            date_from=parse_date_filter(req.date_from),
            date_to=parse_date_filter(req.date_to, end_of_day=True),
            apply_filters=req.apply_filters,
            skip_uploaded=req.skip_uploaded,
            only_downloaded=req.only_downloaded,
        )
    finally:
        session.close()


def _run_manual_facebook_upload(
    page_id: int,
    saved_paths: list[str],
    title: str,
    description: str,
    published: bool,
    use_filename_as_title: bool,
) -> dict:
    session = init_db(DB_PATH)
    try:
        return upload_manual_files(
            session,
            page_id,
            [Path(p) for p in saved_paths],
            title=title,
            description=description,
            published=published,
            use_filename_as_title=use_filename_as_title,
        )
    finally:
        for path_str in saved_paths:
            Path(path_str).unlink(missing_ok=True)
        session.close()


@router.get("/app-config")
def api_get_facebook_app_config(session: Session = Depends(get_session)):
    return app_config_to_dict(get_app_config(session))


@router.post("/app-config")
def api_save_facebook_app_config(
    req: FacebookAppConfigRequest, session: Session = Depends(get_session)
):
    data = req.model_dump()
    existing = get_app_config(session)
    if existing:
        if not data.get("app_secret") or str(data.get("app_secret", "")).startswith("••"):
            data.pop("app_secret", None)
    elif not data.get("app_secret"):
        raise HTTPException(400, "App Secret wajib diisi")

    try:
        cfg = save_app_config(session, data)
    except FacebookAPIError as e:
        raise HTTPException(400, str(e))

    return {"message": "Facebook App tersimpan", "config": app_config_to_dict(cfg)}


@router.get("/pages")
def api_list_facebook_pages(session: Session = Depends(get_session)):
    return [page_to_dict(p) for p in list_pages(session)]


@router.post("/oauth/start")
def api_facebook_oauth_start(
    request: Request,
    req: FacebookConnectRequest = FacebookConnectRequest(),
    session: Session = Depends(get_session),
):
    cfg = get_app_config(session)
    if not cfg or not cfg.app_id or not cfg.app_secret:
        raise HTTPException(400, "Facebook App belum dikonfigurasi. Isi App ID & Secret dulu.")

    redirect_uri = cfg.redirect_uri or _default_redirect(request)
    if not cfg.redirect_uri:
        cfg.redirect_uri = redirect_uri
        session.commit()

    state = create_oauth_state(req.label or "")
    auth_url = build_auth_url(cfg.app_id, redirect_uri, state)
    return {"auth_url": auth_url}


@router.get("/oauth/callback")
def api_facebook_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    from fastapi.responses import RedirectResponse

    if error:
        return RedirectResponse(f"/index.html?view=facebook&facebook=error&msg={error}")
    meta = pop_oauth_state_meta(state or "")
    if not code or not state or not meta:
        return RedirectResponse("/index.html?view=facebook&facebook=error&msg=invalid_oauth_state")
    if time.time() - meta.get("created_at", 0) > 600:
        return RedirectResponse("/index.html?view=facebook&facebook=error&msg=oauth_expired")

    session = init_db(DB_PATH)
    try:
        cfg = get_app_config(session)
        if not cfg:
            return RedirectResponse("/index.html?view=facebook&facebook=error&msg=config_missing")

        redirect_uri = cfg.redirect_uri or _default_redirect(request)
        short = exchange_code_for_token(cfg.app_id, cfg.app_secret, redirect_uri, code)
        long_lived = exchange_long_lived_token(
            cfg.app_id, cfg.app_secret, short["access_token"]
        )
        user_token = long_lived["access_token"]
        token_expires = long_lived["token_expires_at"]

        pages = get_user_pages(user_token)
        if not pages:
            return RedirectResponse(
                "/index.html?view=facebook&facebook=error&msg=no_facebook_pages"
            )

        label_prefix = meta.get("label") or ""
        connected = 0
        for page_info in pages:
            create_or_update_page(
                session,
                app_config_id=cfg.id,
                page_id=page_info["page_id"],
                page_name=page_info["page_name"],
                page_access_token=page_info["page_access_token"],
                user_access_token=user_token,
                page_thumbnail=page_info.get("page_thumbnail"),
                token_expires_at=token_expires,
                label=f"{label_prefix} {page_info['page_name']}".strip()
                if label_prefix
                else page_info["page_name"],
            )
            connected += 1

        return RedirectResponse(
            f"/index.html?view=facebook&facebook=connected&count={connected}"
        )
    except FacebookAPIError as e:
        return RedirectResponse(
            f"/index.html?view=facebook&facebook=error&msg={urllib.parse.quote(str(e))}"
        )
    finally:
        session.close()


@router.delete("/pages/{page_id}")
def api_delete_facebook_page(page_id: int, session: Session = Depends(get_session)):
    if not delete_page(session, page_id):
        raise HTTPException(404, "Page tidak ditemukan")
    return {"ok": True, "message": "Page dihapus"}


@router.post("/pages/{page_id}/disconnect")
def api_disconnect_facebook_page(page_id: int, session: Session = Depends(get_session)):
    if not disconnect_page(session, page_id):
        raise HTTPException(404, "Page tidak ditemukan")
    return {"ok": True, "message": "Page disconnected"}


@router.post("/pages/{page_id}/test")
def api_test_facebook_page(page_id: int, session: Session = Depends(get_session)):
    try:
        info = test_page_connection(session, page_id)
    except FacebookAPIError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **info}


@router.post("/pages/{page_id}/upload-manual")
async def api_facebook_upload_manual(
    page_id: int,
    files: list[UploadFile] = File(...),
    title: str = Form(""),
    description: str = Form(""),
    published: bool = Form(True),
    use_filename_as_title: bool = Form(True),
    session: Session = Depends(get_session),
):
    from ...facebook.client import get_page

    page = get_page(session, page_id)
    if not page:
        raise HTTPException(404, "Page tidak ditemukan")
    if not page.page_access_token:
        raise HTTPException(400, "Page belum terhubung")

    if not files:
        raise HTTPException(400, "Pilih minimal 1 file video")

    MANUAL_FB_DIR.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []

    for index, upload in enumerate(files):
        if not upload.filename:
            continue
        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_MANUAL_VIDEO_EXT:
            raise HTTPException(400, f"Format tidak didukung: {upload.filename}")

        dest = MANUAL_FB_DIR / f"{page_id}_{int(time.time() * 1000)}_{index}_{upload.filename}"
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        saved_paths.append(str(dest))

    if not saved_paths:
        raise HTTPException(400, "Tidak ada file valid")

    job = job_manager.create("facebook-manual")
    job_manager.run(
        job,
        lambda: _run_manual_facebook_upload(
            page_id,
            saved_paths,
            title,
            description,
            published,
            use_filename_as_title,
        ),
        f"Upload {len(saved_paths)} file ke Facebook...",
    )
    return job_manager.to_dict(job)


def register_facebook_profile_routes(app):
    """Register profile-scoped Facebook upload route on main app."""

    @app.post("/api/profiles/{profile_id}/facebook-upload")
    def api_facebook_upload(profile_id: int, req: FacebookUploadRequest):
        session = init_db(DB_PATH)
        try:
            profile = get_profile(session, profile_id)
            if not profile:
                raise HTTPException(404, "Profil tidak ditemukan")
        finally:
            session.close()

        job = job_manager.create("facebook-upload")
        job_manager.run(
            job,
            lambda: _run_facebook_upload(profile_id, req),
            "Uploading videos ke Facebook...",
        )
        return job_manager.to_dict(job)