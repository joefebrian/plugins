"""Threads Uploader API — multi-account, AI topics, auto-post."""

from __future__ import annotations

import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db.models import Video, init_db
from ...facebook.client import (
    FacebookAPIError,
    app_config_to_dict,
    get_app_config,
)
from ...services import get_profile, parse_date_filter
from ...threads.autopost import run_autopost_for_account, run_due_autoposts, save_autopost_config
from ...threads.client import (
    ThreadsAPIError,
    account_to_dict,
    build_auth_url,
    create_oauth_state,
    create_or_update_account,
    delete_account,
    disconnect_account,
    exchange_code_for_token,
    exchange_long_lived_token,
    fetch_threads_profile,
    get_account,
    list_accounts,
    pop_oauth_state_meta,
    test_connection,
)
from ...threads.media import verify_public_media_token
from ...threads.topics import TopicGenerationError, generate_topics
from ...threads.uploader import bulk_post_videos, post_text
from ..auth_deps import get_current_user_id
from ..deps import DB_PATH, get_session, resolve_public_base_url
from ..jobs import job_manager

router = APIRouter(prefix="/api/threads", tags=["threads"])


class ThreadsConnectRequest(BaseModel):
    label: Optional[str] = None


class ThreadsAccountUpdate(BaseModel):
    label: Optional[str] = None
    voice_locale: Optional[str] = None
    voice_style: Optional[str] = None
    niche: Optional[str] = None


class ThreadsTopicRequest(BaseModel):
    niche: str = "lifestyle"
    locale: str = "id"
    style: str = "genz"
    count: int = 8
    account_id: Optional[int] = None


class ThreadsPostRequest(BaseModel):
    caption: str
    topic_tag: Optional[str] = None


class ThreadsAutoPostRequest(BaseModel):
    enabled: bool = False
    interval_hours: float = 4.0
    posts_per_day: int = 6
    post_video: bool = True
    profile_id: Optional[int] = None
    topic_seed: Optional[str] = None


class ThreadsBulkRequest(BaseModel):
    threads_account_id: int
    limit: Optional[int] = 5
    caption_template: str = "{title}\n\n{url}"
    skip_uploaded: bool = True
    use_ai_caption: bool = True
    status: Optional[str] = None
    sort_by: str = "gmv"
    min_views: Optional[int] = None
    max_views: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    apply_filters: bool = False


def _default_redirect(request: Request) -> str:
    env_redirect = os.getenv("THREADS_REDIRECT_URI", "").strip()
    if env_redirect:
        return env_redirect
    return str(request.base_url).rstrip("/") + "/api/threads/oauth/callback"


def _public_base(request: Request) -> str:
    return resolve_public_base_url(request)


@router.get("/app-config")
def api_threads_app_config(session: Session = Depends(get_session)):
    """Threads pakai Meta App yang sama dengan Facebook."""
    return app_config_to_dict(get_app_config(session))


@router.get("/accounts")
def api_list_threads_accounts(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    return [account_to_dict(a) for a in list_accounts(session, user_id=user_id)]


@router.patch("/accounts/{account_id}")
def api_update_threads_account(
    account_id: int,
    req: ThreadsAccountUpdate,
    session: Session = Depends(get_session),
):
    acc = get_account(session, account_id)
    if not acc:
        raise HTTPException(404, "Akun tidak ditemukan")
    data = req.model_dump(exclude_unset=True)
    if data.get("voice_locale") and data["voice_locale"] not in ("id", "us"):
        raise HTTPException(400, "voice_locale harus id atau us")
    if data.get("voice_style") and data["voice_style"] not in ("genz", "millennial", "us_slang"):
        raise HTTPException(400, "voice_style tidak valid")
    for k, v in data.items():
        setattr(acc, k, v)
    session.commit()
    return {"message": "Akun diupdate", "account": account_to_dict(acc)}


@router.post("/oauth/start")
def api_threads_oauth_start(
    request: Request,
    req: ThreadsConnectRequest = ThreadsConnectRequest(),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    cfg = get_app_config(session)
    if not cfg or not cfg.app_id or not cfg.app_secret:
        raise HTTPException(400, "Meta App belum dikonfigurasi. Isi di Facebook Uploader dulu (App ID + Secret + Threads API product).")

    redirect_uri = cfg.redirect_uri or _default_redirect(request)
    if not cfg.redirect_uri:
        cfg.redirect_uri = redirect_uri.replace("/threads/", "/facebook/") if "/threads/" in redirect_uri else redirect_uri
        session.commit()

    threads_redirect = str(request.base_url).rstrip("/") + "/api/threads/oauth/callback"
    state = create_oauth_state(req.label or "", user_id=user_id)
    auth_url = build_auth_url(cfg.app_id, threads_redirect, state)
    return {"auth_url": auth_url}


@router.get("/oauth/callback")
def api_threads_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(f"/index.html?view=threads&threads=error&msg={error}")
    meta = pop_oauth_state_meta(state or "")
    if not code or not state or not meta:
        return RedirectResponse("/index.html?view=threads&threads=error&msg=invalid_oauth_state")
    if time.time() - meta.get("created_at", 0) > 600:
        return RedirectResponse("/index.html?view=threads&threads=error&msg=oauth_expired")

    session = init_db(DB_PATH)
    try:
        cfg = get_app_config(session)
        if not cfg:
            return RedirectResponse("/index.html?view=threads&threads=error&msg=config_missing")

        redirect_uri = str(request.base_url).rstrip("/") + "/api/threads/oauth/callback"
        short = exchange_code_for_token(cfg.app_id, cfg.app_secret, redirect_uri, code)
        long_lived = exchange_long_lived_token(cfg.app_id, cfg.app_secret, short["access_token"])
        token = long_lived["access_token"]
        expires = long_lived["token_expires_at"]

        profile = fetch_threads_profile(token)
        label = meta.get("label") or f"@{profile.get('username') or 'threads'}"
        create_or_update_account(
            session,
            app_config_id=cfg.id,
            threads_user_id=profile["threads_user_id"],
            username=profile.get("username"),
            access_token=token,
            token_expires_at=expires,
            profile_picture=profile.get("profile_picture"),
            label=label,
            user_id=meta.get("user_id"),
        )
        return RedirectResponse("/index.html?view=threads&threads=connected")
    except (ThreadsAPIError, FacebookAPIError) as e:
        return RedirectResponse(
            f"/index.html?view=threads&threads=error&msg={urllib.parse.quote(str(e))}"
        )
    finally:
        session.close()


@router.delete("/accounts/{account_id}")
def api_delete_threads_account(account_id: int, session: Session = Depends(get_session)):
    if not delete_account(session, account_id):
        raise HTTPException(404, "Akun tidak ditemukan")
    return {"ok": True}


@router.post("/accounts/{account_id}/disconnect")
def api_disconnect_threads_account(account_id: int, session: Session = Depends(get_session)):
    if not disconnect_account(session, account_id):
        raise HTTPException(404, "Akun tidak ditemukan")
    return {"ok": True, "message": "Disconnected"}


@router.post("/accounts/{account_id}/test")
def api_test_threads_account(account_id: int, session: Session = Depends(get_session)):
    try:
        return {"ok": True, **test_connection(session, account_id)}
    except ThreadsAPIError as e:
        raise HTTPException(400, str(e))


@router.post("/topics/generate")
def api_threads_generate_topics(req: ThreadsTopicRequest, session: Session = Depends(get_session)):
    token = None
    user_id = None
    if req.account_id:
        acc = get_account(session, req.account_id)
        if acc and acc.access_token:
            token = acc.access_token
            user_id = acc.threads_user_id
    try:
        return generate_topics(
            session,
            niche=req.niche,
            locale=req.locale,
            style=req.style,
            count=min(max(req.count, 3), 12),
            access_token=token,
            threads_user_id=user_id,
        )
    except TopicGenerationError as e:
        raise HTTPException(400, str(e))


@router.post("/accounts/{account_id}/post")
def api_threads_post_text(
    account_id: int,
    req: ThreadsPostRequest,
    session: Session = Depends(get_session),
):
    if not req.caption.strip():
        raise HTTPException(400, "Caption wajib diisi")
    try:
        return post_text(session, account_id, caption=req.caption, topic_tag=req.topic_tag)
    except ThreadsAPIError as e:
        raise HTTPException(400, str(e))


@router.post("/accounts/{account_id}/autopost")
def api_threads_autopost_config(
    account_id: int,
    req: ThreadsAutoPostRequest,
    session: Session = Depends(get_session),
):
    try:
        cfg = save_autopost_config(session, account_id, req.model_dump())
    except ValueError as e:
        raise HTTPException(404, str(e))
    acc = get_account(session, account_id)
    return {"message": "Auto-post disimpan", "account": account_to_dict(acc)}


@router.post("/accounts/{account_id}/autopost/run")
def api_threads_autopost_run(
    account_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    job = job_manager.create("threads-autopost")
    base = _public_base(request)
    def _run():
        session = init_db(DB_PATH)
        try:
            return run_autopost_for_account(session, account_id, base_url=base)
        finally:
            session.close()

    job_manager.run(job, _run, "Threads auto-post...")
    return job_manager.to_dict(job)


@router.post("/autopost/tick")
def api_threads_autopost_tick(request: Request, session: Session = Depends(get_session)):
    return {"results": run_due_autoposts(session, base_url=_public_base(request))}


@router.get("/public-media/{video_id}")
def api_threads_public_media(video_id: int, exp: int, sig: str, session: Session = Depends(get_session)):
    if not verify_public_media_token(video_id, exp, sig):
        raise HTTPException(403, "Link media expired atau invalid")

    video = session.query(Video).filter_by(id=video_id).first()
    if not video or not video.is_downloaded or not video.file_path:
        raise HTTPException(404, "Video tidak tersedia")

    path = Path(video.file_path)
    if not path.exists():
        raise HTTPException(404, "File tidak ditemukan")

    return FileResponse(path, media_type="video/mp4", filename=path.name)


def _run_threads_bulk(profile_id: int, req: ThreadsBulkRequest, base_url: str) -> dict:
    session = init_db(DB_PATH)
    try:
        filter_status = None if not req.status or req.status == "all" else req.status
        return bulk_post_videos(
            session,
            profile_id,
            req.threads_account_id,
            base_url=base_url,
            limit=req.limit,
            caption_template=req.caption_template,
            skip_uploaded=req.skip_uploaded,
            use_ai_caption=req.use_ai_caption,
            status=filter_status,
            sort_by=req.sort_by,
            min_views=req.min_views,
            max_views=req.max_views,
            date_from=parse_date_filter(req.date_from),
            date_to=parse_date_filter(req.date_to, end_of_day=True),
            apply_filters=req.apply_filters,
        )
    finally:
        session.close()


def register_threads_profile_routes(app):
    @app.post("/api/profiles/{profile_id}/threads-upload")
    def api_profile_threads_upload(
        profile_id: int,
        req: ThreadsBulkRequest,
        request: Request,
    ):
        session = init_db(DB_PATH)
        try:
            if not get_profile(session, profile_id):
                raise HTTPException(404, "Profil tidak ditemukan")
        finally:
            session.close()

        job = job_manager.create("threads-upload")
        base = _public_base(request)
        job_manager.run(
            job,
            lambda: _run_threads_bulk(profile_id, req, base),
            "Posting ke Threads...",
        )
        return job_manager.to_dict(job)