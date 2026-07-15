"""FastAPI web application for Affiliate Video Tool."""

from __future__ import annotations

import os
import shutil
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from ..auth import (
    check_rate_limit,
    clear_rate_limit as clear_auth_rate_limit,
    record_failed_login,
    setup_auth,
)
from ..cookies_util import filter_tiktok_cookies, validate_tiktok_cookies
from ..db.models import init_db, run_migrations
from ..gmv.importer import import_gmv_csv, import_gmv_text
from ..gmv.tiktok_shop import (
    TikTokShopAPIError,
    TikTokShopClient,
    config_from_model,
    config_to_dict,
    get_shop_config,
    save_shop_config,
    sync_gmv_from_api,
)
from ..direct_download import (
    content_disposition_attachment,
    direct_download_filename,
    resolve_direct_download_url,
    stream_remote_video,
)
from ..profile_folders import (
    create_folder,
    delete_folder,
    list_folders_with_counts,
    move_profile_to_folder,
    rename_folder,
)
from ..services import (
    delete_profile,
    delete_video,
    delete_videos,
    download_videos,
    get_hero_videos,
    get_profile,
    get_profile_stats,
    get_scraper,
    list_profiles,
    list_videos,
    parse_date_filter,
    profile_to_dict,
    sync_profile_videos,
    update_video_metrics,
    video_to_dict,
    videos_to_csv,
)
from ..youtube.client import (
    YouTubeAPIError,
    YouTubeClient,
    build_auth_url,
    channel_to_dict,
    client_for_channel,
    create_oauth_state,
    create_or_update_channel,
    credentials_from_channel,
    delete_channel,
    delete_oauth_app,
    exchange_code_for_tokens,
    get_channel,
    pop_oauth_state_meta,
    list_channels,
    persist_channel_tokens,
    save_oauth_app,
)
from ..youtube.quota import (
    app_monitoring_dict,
    clear_rate_limit as clear_oauth_rate_limit,
    get_oauth_app,
    is_app_available,
    list_oauth_apps,
    monitoring_overview,
    pick_available_app,
    record_grant,
)
from ..youtube.titles import generate_title_variants
from ..youtube.thumbnail import generate_video_thumbnail
from ..youtube.uploader import bulk_upload_videos, run_ab_title_test, upload_manual_files
from ..users import (
    access_block_reason,
    authenticate_user,
    change_user_password,
    get_user_by_id,
    register_user,
    subscription_info,
    user_to_dict,
)
from .auth_deps import get_current_user_id, get_owned_profile, load_session_user, require_admin
from .deps import (
    BASE_DIR,
    COOKIES_DIR,
    DB_PATH,
    DOWNLOAD_DIR,
    STATIC_DIR,
    get_session,
    resolve_public_base_url,
)
from .jobs import job_manager
from .routes.admin_users import router as admin_users_router
from .routes.ai_settings import router as ai_settings_router
from .routes.facebook import register_facebook_profile_routes, router as facebook_router
from .routes.monitoring import router as monitoring_router
from .routes.threads import register_threads_profile_routes, router as threads_router

app = FastAPI(title="Affiliate Video Tool", version="0.2.0")

auth_store, _session_secret = setup_auth(BASE_DIR)
_on_railway = os.getenv("RAILWAY_ENVIRONMENT") is not None
COOKIE_SECURE = os.getenv(
    "COOKIE_SECURE",
    "true" if _on_railway else "false",
).lower() in ("true", "1", "yes")

PUBLIC_PATHS = frozenset({
    "/login.html",
    "/signup.html",
    "/style.css",
    "/app.js",
    "/favicon.ico",
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/me",
    "/api/admin/users/webhooks/payment",
    "/api/health",
    "/api/youtube/oauth/callback",
    "/api/facebook/oauth/callback",
    "/api/threads/oauth/callback",
    "/api/monitoring/youtube/oauth/callback",
    "/api/monitoring/facebook/oauth/callback",
    "/api/monitoring/threads/oauth/callback",
    "/api/monitoring/twitter/oauth/callback",
})

app.include_router(facebook_router)
app.include_router(threads_router)
app.include_router(ai_settings_router)
app.include_router(admin_users_router)
app.include_router(monitoring_router)
register_facebook_profile_routes(app)
register_threads_profile_routes(app)


@app.on_event("startup")
def _startup():
    run_migrations(DB_PATH)
    from ..threads.scheduler import start_autopost_scheduler

    start_autopost_scheduler(resolve_public_base_url())


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=255)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/api/threads/public-media/"):
        return await call_next(request)

    if not _is_authenticated(request):
        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return RedirectResponse("/login.html", status_code=302)

    user_id = request.session.get("user_id")
    if user_id:
        session = init_db(DB_PATH)
        try:
            user = get_user_by_id(session, int(user_id))
            blocked = access_block_reason(user) if user else "Sesi tidak valid"
            if user and blocked:
                request.session.clear()
                if path.startswith("/api/"):
                    return JSONResponse(status_code=402, content={"detail": blocked})
                return RedirectResponse("/login.html?expired=1", status_code=302)
        finally:
            session.close()

    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".js", ".css", ".html")) or path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    session_cookie="av_session",
    max_age=60 * 60 * 24 * 7,
    same_site="strict",
    https_only=COOKIE_SECURE,
)


class ProfileFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ProfileFolderRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ProfileMoveFolderRequest(BaseModel):
    folder_id: Optional[int] = None


class ScanRequest(BaseModel):
    platform: str
    username: str


class DownloadRequest(BaseModel):
    limit: Optional[int] = 10
    video_ids: Optional[list[str]] = None
    only_pending: bool = True
    quality: str = "best"
    status: Optional[str] = None
    sort_by: str = "gmv"
    min_views: Optional[int] = None
    max_views: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    apply_filters: bool = False


class DeleteVideosRequest(BaseModel):
    video_ids: list[int] = Field(min_length=1)
    delete_files: bool = True


class GmvTextRequest(BaseModel):
    text: str


class VideoMetricsRequest(BaseModel):
    gmv: Optional[float] = None
    commission: Optional[float] = None
    orders: Optional[int] = None


class TikTokShopConfigRequest(BaseModel):
    app_key: str
    app_secret: Optional[str] = None  # None = keep existing
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    shop_cipher: Optional[str] = None
    shop_id: Optional[str] = None
    region: str = "ID"
    base_url: Optional[str] = None
    is_active: bool = True


class TikTokShopSyncRequest(BaseModel):
    days: int = 30


class YouTubeOAuthAppRequest(BaseModel):
    label: str = "Backup OAuth App"
    client_id: str
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    priority: int = 200
    daily_grant_limit: int = 100
    daily_refresh_limit: int = 5000
    minute_grant_limit: int = 18
    is_active: bool = True


class YouTubeOAuthAppUpdateRequest(BaseModel):
    label: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    priority: Optional[int] = None
    daily_grant_limit: Optional[int] = None
    daily_refresh_limit: Optional[int] = None
    minute_grant_limit: Optional[int] = None
    is_active: Optional[bool] = None


class YouTubeConnectRequest(BaseModel):
    label: Optional[str] = None
    oauth_app_id: Optional[int] = None


class YouTubeUploadRequest(BaseModel):
    youtube_channel_id: int
    limit: Optional[int] = 10
    privacy: str = "private"
    category_id: str = "22"
    title_template: str = "{title}"
    description_template: str = "Source: {url}\nViews: {views}\nGMV: Rp {gmv}"
    tags: Optional[list[str]] = None
    status: Optional[str] = None
    sort_by: str = "gmv"
    min_views: Optional[int] = None
    max_views: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    apply_filters: bool = False
    skip_uploaded: bool = True
    only_downloaded: bool = True
    auto_thumbnail: bool = False
    schedule_enabled: bool = False
    schedule_start: Optional[str] = None
    schedule_interval_hours: float = 3.0


class YouTubeTitleGenerateRequest(BaseModel):
    base_title: str = ""
    keyword: Optional[str] = None
    video_id: Optional[int] = None
    profile_id: Optional[int] = None
    count: int = 5
    use_ai: bool = True


class YouTubeABTestRequest(BaseModel):
    youtube_channel_id: int
    video_id: Optional[int] = None
    title_variants: list[str]
    description: str = ""
    tags: Optional[list[str]] = None
    auto_thumbnail: bool = False


def _cookies_path() -> Optional[str]:
    tiktok_only = COOKIES_DIR / "tiktok_only.txt"
    if tiktok_only.exists():
        return str(tiktok_only)
    path = COOKIES_DIR / "cookies.txt"
    return str(path) if path.exists() else None


def _run_scan(
    platform: str,
    username: str,
    user_id: int,
    cookies_file: Optional[str] = None,
) -> dict:
    cookies_file = cookies_file or _cookies_path()
    session = init_db(DB_PATH)
    try:
        result = sync_profile_videos(session, platform, username, cookies_file, user_id=user_id)
        profile = result["profile"]
        stats = get_profile_stats(session, profile.id)
        return {
            "profile": profile_to_dict(profile, stats),
            "scan": {
                "total": result["total"],
                "new": result["new"],
                "updated": result["updated"],
                "downloaded": result["downloaded"],
                "pending": result["pending"],
                "incremental": result.get("incremental", False),
            },
        }
    finally:
        session.close()


def _run_download(
    profile_id: int,
    user_id: int,
    limit: Optional[int],
    video_ids: Optional[list[str]],
    only_pending: bool,
    cookies_file: Optional[str],
    quality: str = "best",
    status: Optional[str] = None,
    sort_by: str = "gmv",
    min_views: Optional[int] = None,
    max_views: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    apply_filters: bool = False,
) -> dict:
    cookies_file = cookies_file or _cookies_path()
    session = init_db(DB_PATH)
    try:
        profile = get_profile(session, profile_id, user_id=user_id)
        if not profile:
            raise ValueError("Profil tidak ditemukan")
        filter_status = None if not status or status == "all" else status
        return download_videos(
            session,
            profile.platform,
            profile.username,
            DOWNLOAD_DIR,
            cookies_file=cookies_file,
            limit=limit,
            only_pending=only_pending,
            video_ids=video_ids,
            quality=quality,
            status=filter_status,
            sort_by=sort_by,
            min_views=min_views,
            max_views=max_views,
            date_from=parse_date_filter(date_from),
            date_to=parse_date_filter(date_to, end_of_day=True),
            apply_filters=apply_filters,
            user_id=user_id,
        )
    finally:
        session.close()


def _set_user_session(request: Request, user) -> dict:
    request.session.clear()
    request.session["authenticated"] = True
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role
    return {
        "ok": True,
        "username": user.username,
        "role": user.role,
        "user_id": user.id,
        "is_admin": user.role == "admin",
    }


@app.post("/api/auth/signup")
async def auth_signup(req: SignupRequest):
    session = init_db(DB_PATH)
    try:
        user = register_user(
            session,
            username=req.username,
            password=req.password,
            display_name=req.display_name,
            email=req.email,
        )
        return {
            "ok": True,
            "message": "Pendaftaran berhasil. Tunggu persetujuan admin untuk login.",
            "username": user.username,
            "status": user.status,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        session.close()


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest, request: Request):
    ip = _client_ip(request)
    rate_msg = check_rate_limit(ip)
    if rate_msg:
        raise HTTPException(429, rate_msg)

    username = req.username.strip()
    session = init_db(DB_PATH)
    try:
        user, err = authenticate_user(session, username, req.password)
        if user:
            clear_auth_rate_limit(ip)
            return _set_user_session(request, user)
        if err:
            record_failed_login(ip)
            raise HTTPException(401, err)
    finally:
        session.close()

    record_failed_login(ip)
    raise HTTPException(401, "Username atau password salah")


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(request: Request, session: Session = Depends(get_session)):
    if not _is_authenticated(request):
        return {"authenticated": False}
    user = load_session_user(session, request)
    if not user:
        return {"authenticated": False}
    blocked = access_block_reason(user)
    if blocked:
        request.session.clear()
        return {"authenticated": False, "expired": True, "message": blocked}
    return {
        "authenticated": True,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "role": user.role,
        "user_id": user.id,
        "is_admin": user.role == "admin",
        "status": user.status,
        "subscription": subscription_info(user),
    }


@app.post("/api/auth/change-password")
async def auth_change_password(
    req: ChangePasswordRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    if not _is_authenticated(request):
        raise HTTPException(401, "Unauthorized")
    user = load_session_user(session, request)
    if not user:
        raise HTTPException(401, "Unauthorized")
    try:
        change_user_password(session, user, req.current_password, req.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    request.session.clear()
    return {"ok": True, "message": "Password berhasil diubah. Silakan login ulang."}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/profiles")
def api_list_profiles(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    profiles = list_profiles(session, user_id=user_id)
    result = []
    for p in profiles:
        stats = get_profile_stats(session, p.id)
        result.append(profile_to_dict(p, stats))
    return result


@app.get("/api/profile-folders")
def api_list_profile_folders(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    return list_folders_with_counts(session, user_id)


@app.post("/api/profile-folders")
def api_create_profile_folder(
    req: ProfileFolderRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        folder = create_folder(session, user_id, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "folder": {"id": folder.id, "name": folder.name}}


@app.patch("/api/profile-folders/{folder_id}")
def api_rename_profile_folder(
    folder_id: int,
    req: ProfileFolderRenameRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        folder = rename_folder(session, user_id, folder_id, req.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "folder": {"id": folder.id, "name": folder.name}}


@app.delete("/api/profile-folders/{folder_id}")
def api_delete_profile_folder(
    folder_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        return delete_folder(session, user_id, folder_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/profiles/{profile_id}/folder")
def api_move_profile_folder(
    profile_id: int,
    req: ProfileMoveFolderRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        profile = move_profile_to_folder(session, user_id, profile_id, req.folder_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    stats = get_profile_stats(session, profile.id)
    return {"ok": True, "profile": profile_to_dict(profile, stats)}


@app.get("/api/profiles/{profile_id}")
def api_get_profile(
    profile_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    profile = get_profile(session, profile_id, user_id=user_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")
    stats = get_profile_stats(session, profile_id)
    return profile_to_dict(profile, stats)


@app.get("/api/profiles/{profile_id}/videos")
def api_list_videos(
    profile_id: int,
    status: str = "all",
    sort_by: str = "gmv",
    min_views: Optional[int] = None,
    max_views: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    profile = get_profile(session, profile_id, user_id=user_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")

    filter_status = None if status == "all" else status
    videos = list_videos(
        session,
        profile.platform,
        profile.username,
        filter_status,
        sort_by,
        min_views=min_views,
        max_views=max_views,
        date_from=parse_date_filter(date_from),
        date_to=parse_date_filter(date_to, end_of_day=True),
        user_id=user_id,
    )
    return [video_to_dict(v) for v in videos]


@app.get("/api/profiles/{profile_id}/videos/export.csv")
def api_export_videos_csv(
    profile_id: int,
    status: str = "all",
    sort_by: str = "gmv",
    min_views: Optional[int] = None,
    max_views: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    profile = get_profile(session, profile_id, user_id=user_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")

    filter_status = None if status == "all" else status
    videos = list_videos(
        session,
        profile.platform,
        profile.username,
        filter_status,
        sort_by,
        min_views=min_views,
        max_views=max_views,
        date_from=parse_date_filter(date_from),
        date_to=parse_date_filter(date_to, end_of_day=True),
        user_id=user_id,
    )
    filename = f"{profile.platform}_{profile.username}_videos.csv"
    return Response(
        content=videos_to_csv(videos),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/profiles/{profile_id}/heroes")
def api_heroes(
    profile_id: int,
    top: int = 10,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    profile = get_profile(session, profile_id, user_id=user_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")

    videos = get_hero_videos(session, profile.platform, profile.username, top, user_id=user_id)
    has_gmv = any(v.gmv for v in videos)
    return {
        "ranked_by": "gmv" if has_gmv else "engagement",
        "videos": [video_to_dict(v) for v in videos],
    }


@app.post("/api/scan")
def api_scan(req: ScanRequest, user_id: int = Depends(get_current_user_id)):
    if req.platform not in ("tiktok", "instagram"):
        raise HTTPException(400, "Platform harus tiktok atau instagram")

    username = get_scraper(req.platform).normalize_username(req.username)
    if not username:
        raise HTTPException(400, "Username wajib diisi")

    job = job_manager.create("scan")
    job_manager.run(
        job,
        lambda: _run_scan(req.platform, username, user_id),
        f"Scanning @{username}...",
    )
    return job_manager.to_dict(job)


@app.post("/api/profiles/{profile_id}/download")
def api_download(profile_id: int, req: DownloadRequest, user_id: int = Depends(get_current_user_id)):
    job = job_manager.create("download")
    job_manager.run(
        job,
        lambda: _run_download(
            profile_id,
            user_id,
            req.limit,
            req.video_ids,
            req.only_pending,
            None,
            req.quality,
            req.status,
            req.sort_by,
            req.min_views,
            req.max_views,
            req.date_from,
            req.date_to,
            req.apply_filters,
        ),
        "Downloading videos...",
    )
    return job_manager.to_dict(job)


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(
    profile_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    if not get_profile(session, profile_id, user_id=user_id):
        raise HTTPException(404, "Profil tidak ditemukan")
    try:
        result = delete_profile(session, profile_id, DOWNLOAD_DIR, delete_files=True)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return result


@app.delete("/api/videos/{video_id}")
def api_delete_video(
    video_id: int,
    delete_file: bool = True,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        return delete_video(session, video_id, user_id=user_id, delete_file=delete_file)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/profiles/{profile_id}/videos/delete")
def api_delete_videos(
    profile_id: int,
    req: DeleteVideosRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        return delete_videos(
            session,
            profile_id,
            req.video_ids,
            user_id=user_id,
            delete_files=req.delete_files,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/videos/{video_id}/metrics")
def api_update_video_metrics(
    video_id: int,
    req: VideoMetricsRequest,
    session: Session = Depends(get_session),
):
    try:
        video = update_video_metrics(
            session, video_id, gmv=req.gmv, commission=req.commission, orders=req.orders
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return video_to_dict(video)


@app.post("/api/profiles/{profile_id}/import-gmv-text")
def api_import_gmv_text(
    profile_id: int,
    req: GmvTextRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    profile = get_profile(session, profile_id, user_id=user_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")
    try:
        return import_gmv_text(session, req.text, profile_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/profiles/{profile_id}/import-gmv")
async def api_import_gmv(
    profile_id: int,
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    profile = get_profile(session, profile_id, user_id=user_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "File harus berformat CSV")

    tmp_path = DOWNLOAD_DIR / f"_upload_{profile_id}_{file.filename}"
    with tmp_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = import_gmv_csv(session, tmp_path, profile_id)
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return result


@app.get("/api/tiktok-shop/config")
def api_get_tiktok_shop_config(session: Session = Depends(get_session)):
    cfg = get_shop_config(session)
    return config_to_dict(cfg)


@app.post("/api/tiktok-shop/config")
def api_save_tiktok_shop_config(req: TikTokShopConfigRequest, session: Session = Depends(get_session)):
    existing = get_shop_config(session)
    data = req.model_dump()

    # Jangan timpa secret/token kalau user kirim placeholder atau kosong
    if existing:
        if not data.get("app_secret") or data["app_secret"].startswith("••"):
            data["app_secret"] = existing.app_secret
        if not data.get("access_token") or data["access_token"].startswith("••"):
            data["access_token"] = existing.access_token
        if not data.get("refresh_token"):
            data["refresh_token"] = existing.refresh_token

    if not data.get("app_secret"):
        raise HTTPException(400, "App Secret wajib diisi")

    cfg = save_shop_config(session, data)
    return {"message": "API credentials tersimpan", "config": config_to_dict(cfg)}


def _default_youtube_redirect(request: Request) -> str:
    env_redirect = os.getenv("YOUTUBE_REDIRECT_URI", "").strip()
    if env_redirect:
        return env_redirect
    return str(request.base_url).rstrip("/") + "/api/youtube/oauth/callback"


def _run_youtube_upload(profile_id: int, req: YouTubeUploadRequest) -> dict:
    session = init_db(DB_PATH)
    try:
        filter_status = None if not req.status or req.status == "all" else req.status
        schedule_start = _parse_publish_at(req.schedule_start) if req.schedule_enabled else None
        return bulk_upload_videos(
            session,
            profile_id,
            req.youtube_channel_id,
            limit=req.limit,
            privacy=req.privacy,
            category_id=req.category_id,
            title_template=req.title_template,
            description_template=req.description_template,
            tags=req.tags,
            status=filter_status,
            sort_by=req.sort_by,
            min_views=req.min_views,
            max_views=req.max_views,
            date_from=parse_date_filter(req.date_from),
            date_to=parse_date_filter(req.date_to, end_of_day=True),
            apply_filters=req.apply_filters,
            skip_uploaded=req.skip_uploaded,
            only_downloaded=req.only_downloaded,
            auto_thumbnail=req.auto_thumbnail,
            thumbnail_dir=DOWNLOAD_DIR / "thumbnails",
            schedule_start=schedule_start,
            schedule_interval_hours=req.schedule_interval_hours,
        )
    finally:
        session.close()


@app.post("/api/youtube/titles/generate")
def api_youtube_titles_generate(
    req: YouTubeTitleGenerateRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    from ..db.models import Profile, Video

    context: dict = {}
    base_title = req.base_title.strip()
    keyword = req.keyword

    if req.video_id:
        video = session.query(Video).filter_by(id=req.video_id).first()
        if not video:
            raise HTTPException(404, "Video tidak ditemukan")
        profile = session.query(Profile).filter_by(id=video.profile_id, user_id=user_id).first()
        if not profile:
            raise HTTPException(404, "Video tidak ditemukan")
        base_title = base_title or video.title or video.platform_video_id
        keyword = keyword or base_title
        context = {
            "title": video.title,
            "views": video.views or 0,
            "gmv": video.gmv or 0,
            "username": profile.username if profile else "",
            "url": video.url,
        }
    elif req.profile_id:
        profile = get_profile(session, req.profile_id, user_id=user_id)
        if not profile:
            raise HTTPException(404, "Profil tidak ditemukan")
        keyword = keyword or f"@{profile.username} tiktok"
        context = {"username": profile.username}

    if not base_title and not keyword:
        raise HTTPException(400, "Isi base_title atau keyword")

    result = generate_title_variants(
        session,
        base_title=base_title or keyword or "review produk",
        keyword=keyword,
        context=context,
        count=min(max(req.count, 2), 8),
        use_ai=req.use_ai,
    )
    return result


@app.post("/api/youtube/titles/ab-test")
def api_youtube_ab_title_test(req: YouTubeABTestRequest):
    job = job_manager.create("youtube-ab-test")
    job_manager.run(
        job,
        lambda: _run_ab_title_test(req),
        "A/B title test upload ke YouTube...",
    )
    return job_manager.to_dict(job)


def _run_ab_title_test(req: YouTubeABTestRequest) -> dict:
    session = init_db(DB_PATH)
    try:
        tags = [t.strip() for t in (req.tags or []) if t.strip()] if req.tags else None
        return run_ab_title_test(
            session,
            req.youtube_channel_id,
            video_db_id=req.video_id,
            title_variants=req.title_variants,
            description=req.description,
            tags=tags,
            auto_thumbnail=req.auto_thumbnail,
        )
    finally:
        session.close()


@app.get("/api/youtube/thumbnails/preview/{video_id}")
def api_youtube_thumbnail_preview(video_id: int, session: Session = Depends(get_session)):
    from ..db.models import Profile, Video

    video = session.query(Video).filter_by(id=video_id).first()
    if not video or not video.is_downloaded or not video.file_path:
        raise HTTPException(404, "Video belum di-download")
    path = Path(video.file_path)
    if not path.exists():
        raise HTTPException(404, "File video tidak ditemukan")

    profile = session.query(Profile).filter_by(id=video.profile_id).first()
    title = video.title or video.platform_video_id
    try:
        thumb = generate_video_thumbnail(
            path,
            title=title,
            subtitle=f"@{profile.username}" if profile else "",
            views=video.views,
            gmv=video.gmv,
        )
    except Exception as e:
        raise HTTPException(400, str(e))

    return FileResponse(thumb, media_type="image/jpeg", filename=f"thumb_{video_id}.jpg")


@app.get("/api/youtube/oauth-apps/monitoring")
def api_youtube_oauth_monitoring(session: Session = Depends(get_session)):
    return monitoring_overview(session)


@app.get("/api/youtube/oauth-apps")
def api_list_youtube_oauth_apps(session: Session = Depends(get_session)):
    apps = list_oauth_apps(session)
    return [app_monitoring_dict(session, app) for app in apps]


@app.post("/api/youtube/oauth-apps")
def api_create_youtube_oauth_app(req: YouTubeOAuthAppRequest, session: Session = Depends(get_session)):
    if not req.client_secret:
        raise HTTPException(400, "Client Secret wajib diisi")
    try:
        cfg = save_oauth_app(session, req.model_dump())
    except YouTubeAPIError as e:
        raise HTTPException(400, str(e))
    return {
        "message": "OAuth App backup ditambahkan",
        "app": app_monitoring_dict(session, cfg),
    }


@app.patch("/api/youtube/oauth-apps/{app_id}")
def api_update_youtube_oauth_app(
    app_id: int, req: YouTubeOAuthAppUpdateRequest, session: Session = Depends(get_session)
):
    existing = get_oauth_app(session, app_id)
    if not existing:
        raise HTTPException(404, "OAuth App tidak ditemukan")
    data = req.model_dump(exclude_unset=True)
    if not data.get("client_secret") or str(data.get("client_secret", "")).startswith("••"):
        data.pop("client_secret", None)
    try:
        cfg = save_oauth_app(session, data, app_id=app_id)
    except YouTubeAPIError as e:
        raise HTTPException(400, str(e))
    return {"message": "OAuth App diupdate", "app": app_monitoring_dict(session, cfg)}


@app.delete("/api/youtube/oauth-apps/{app_id}")
def api_delete_youtube_oauth_app(app_id: int, session: Session = Depends(get_session)):
    try:
        if not delete_oauth_app(session, app_id):
            raise HTTPException(404, "OAuth App tidak ditemukan")
    except YouTubeAPIError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "message": "OAuth App dihapus"}


@app.post("/api/youtube/oauth-apps/{app_id}/reset-limit")
def api_reset_oauth_app_limit(app_id: int, session: Session = Depends(get_session)):
    app_cfg = get_oauth_app(session, app_id)
    if not app_cfg:
        raise HTTPException(404, "OAuth App tidak ditemukan")
    clear_oauth_rate_limit(session, app_cfg)
    return {"ok": True, "app": app_monitoring_dict(session, app_cfg)}


# Backward-compatible alias
@app.get("/api/youtube/app-config")
def api_get_youtube_app_config_legacy(session: Session = Depends(get_session)):
    apps = list_oauth_apps(session)
    if not apps:
        return {"configured": False, "client_id": "", "client_secret": "", "redirect_uri": ""}
    primary = apps[0]
    return app_monitoring_dict(session, primary)


@app.post("/api/youtube/app-config")
def api_save_youtube_app_config_legacy(
    req: YouTubeOAuthAppRequest, session: Session = Depends(get_session)
):
    apps = list_oauth_apps(session)
    data = req.model_dump()
    if apps:
        if not data.get("client_secret") or str(data["client_secret"]).startswith("••"):
            data.pop("client_secret", None)
        cfg = save_oauth_app(session, data, app_id=apps[0].id)
    else:
        if not data.get("client_secret"):
            raise HTTPException(400, "Client Secret wajib diisi")
        data.setdefault("label", "Primary OAuth App")
        data.setdefault("priority", 100)
        cfg = save_oauth_app(session, data)
    return {"message": "OAuth App tersimpan", "config": app_monitoring_dict(session, cfg)}


@app.get("/api/youtube/channels")
def api_list_youtube_channels(
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    channels = list_channels(session, user_id=user_id)
    return [channel_to_dict(ch) for ch in channels]


@app.post("/api/youtube/oauth/start")
def api_youtube_oauth_start(
    request: Request,
    req: YouTubeConnectRequest = YouTubeConnectRequest(),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    rotated_from = None
    if req.oauth_app_id:
        app_cfg = get_oauth_app(session, req.oauth_app_id)
        if not app_cfg:
            raise HTTPException(404, "OAuth App tidak ditemukan")
        if not is_app_available(app_cfg, for_grant=True):
            fallback = pick_available_app(session, for_grant=True)
            if fallback and fallback.id != app_cfg.id:
                rotated_from = app_cfg.label
                app_cfg = fallback
            elif not is_app_available(app_cfg, for_grant=True):
                raise HTTPException(
                    400,
                    f"OAuth App '{app_cfg.label}' limit habis (harian atau {app_cfg.minute_grant_limit}/menit). "
                    "Tambah backup app atau tunggu reset.",
                )
    else:
        app_cfg = pick_available_app(session, for_grant=True)

    if not app_cfg or not app_cfg.client_id or not app_cfg.client_secret:
        raise HTTPException(
            400,
            "Tidak ada OAuth App tersedia. Tambah OAuth App backup di menu Monitoring.",
        )
    if not is_app_available(app_cfg, for_grant=True):
        raise HTTPException(
            400,
            f"OAuth App '{app_cfg.label}' grant limit habis. Pilih app backup atau tunggu reset.",
        )

    redirect_uri = app_cfg.redirect_uri or _default_youtube_redirect(request)
    if not app_cfg.redirect_uri:
        app_cfg.redirect_uri = redirect_uri
        session.commit()

    state = create_oauth_state(req.label or "", oauth_app_id=app_cfg.id, user_id=user_id)
    auth_url = build_auth_url(app_cfg.client_id, redirect_uri, state)
    result = {
        "auth_url": auth_url,
        "oauth_app_id": app_cfg.id,
        "oauth_app_label": app_cfg.label,
    }
    if rotated_from:
        result["rotated_from"] = rotated_from
        result["message"] = f"Auto-rotate: {rotated_from} → {app_cfg.label}"
    return result


@app.get("/api/youtube/oauth/callback")
def api_youtube_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(f"/index.html?view=youtube&youtube=error&msg={error}")
    meta = pop_oauth_state_meta(state or "")
    if not code or not state or not meta:
        return RedirectResponse("/index.html?view=youtube&youtube=error&msg=invalid_oauth_state")
    if time.time() - meta.get("created_at", 0) > 600:
        return RedirectResponse("/index.html?view=youtube&youtube=error&msg=oauth_expired")
    session = init_db(DB_PATH)
    try:
        oauth_app_id = meta.get("oauth_app_id")
        app_cfg = get_oauth_app(session, oauth_app_id) if oauth_app_id else pick_available_app(session)
        if not app_cfg:
            return RedirectResponse("/index.html?view=youtube&youtube=error&msg=config_missing")

        redirect_uri = app_cfg.redirect_uri or _default_youtube_redirect(request)
        tokens = exchange_code_for_tokens(
            app_cfg.client_id, app_cfg.client_secret, redirect_uri, code
        )
        record_grant(session, app_cfg)

        from ..db.models import YouTubeChannel as YTChannelModel

        temp_channel = YTChannelModel(
            refresh_token=tokens.get("refresh_token"),
            access_token=tokens["access_token"],
            token_expires_at=tokens["token_expires_at"],
        )
        client = YouTubeClient(credentials_from_channel(app_cfg, temp_channel))
        channel_info = client.get_channel_info()

        channel = create_or_update_channel(
            session,
            refresh_token=tokens.get("refresh_token") or "",
            access_token=tokens["access_token"],
            token_expires_at=tokens["token_expires_at"],
            channel_id=channel_info["channel_id"],
            channel_title=channel_info["channel_title"],
            channel_thumbnail=channel_info.get("channel_thumbnail"),
            label=meta.get("label") or channel_info["channel_title"],
            oauth_app_id=app_cfg.id,
            user_id=meta.get("user_id"),
        )
        persist_channel_tokens(session, channel, client, channel_info)
    except YouTubeAPIError as e:
        return RedirectResponse(
            f"/index.html?view=youtube&youtube=error&msg={urllib.parse.quote(str(e))}"
        )
    finally:
        session.close()

    return RedirectResponse("/index.html?view=youtube&youtube=connected")


@app.delete("/api/youtube/channels/{channel_id}")
def api_delete_youtube_channel(channel_id: int, session: Session = Depends(get_session)):
    if not delete_channel(session, channel_id):
        raise HTTPException(404, "Channel tidak ditemukan")
    return {"ok": True, "message": "Channel dihapus"}


@app.post("/api/youtube/channels/{channel_id}/disconnect")
def api_disconnect_youtube_channel(channel_id: int, session: Session = Depends(get_session)):
    channel = get_channel(session, channel_id)
    if not channel:
        raise HTTPException(404, "Channel tidak ditemukan")
    channel.refresh_token = None
    channel.access_token = None
    channel.token_expires_at = None
    session.commit()
    return {"ok": True, "message": "Channel disconnected"}


@app.post("/api/youtube/channels/{channel_id}/test")
def api_test_youtube_channel(channel_id: int, session: Session = Depends(get_session)):
    channel = get_channel(session, channel_id)
    if not channel or not channel.refresh_token:
        raise HTTPException(400, "Channel belum terhubung.")

    try:
        client = client_for_channel(session, channel_id)
        info = client.get_channel_info()
        persist_channel_tokens(session, channel, client, info)
    except YouTubeAPIError as e:
        raise HTTPException(400, str(e))

    return {"ok": True, **info}


MANUAL_YT_DIR = DOWNLOAD_DIR / "manual_youtube"
ALLOWED_MANUAL_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def _parse_publish_at(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    from datetime import timezone

    raw = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise HTTPException(400, "Format jadwal tayang tidak valid") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _run_manual_youtube_upload(
    channel_id: int,
    saved_paths: list[str],
    title: str,
    description: str,
    privacy: str,
    tags: list[str] | None,
    use_filename_as_title: bool,
    auto_thumbnail: bool = False,
    publish_at: datetime | None = None,
) -> dict:
    session = init_db(DB_PATH)
    try:
        channel = get_channel(session, channel_id)
        category_id = channel.default_category if channel else "22"
        return upload_manual_files(
            session,
            channel_id,
            [Path(p) for p in saved_paths],
            title=title,
            description=description,
            privacy=privacy,
            category_id=category_id,
            tags=tags,
            use_filename_as_title=use_filename_as_title,
            auto_thumbnail=auto_thumbnail,
            publish_at=publish_at,
        )
    finally:
        for path_str in saved_paths:
            Path(path_str).unlink(missing_ok=True)
        session.close()


@app.post("/api/youtube/channels/{channel_id}/upload-manual")
async def api_youtube_upload_manual(
    channel_id: int,
    files: list[UploadFile] = File(...),
    title: str = Form(""),
    description: str = Form(""),
    privacy: str = Form("private"),
    tags: str = Form(""),
    use_filename_as_title: bool = Form(True),
    auto_thumbnail: bool = Form(False),
    schedule_enabled: bool = Form(False),
    publish_at: str = Form(""),
    session: Session = Depends(get_session),
):
    channel = get_channel(session, channel_id)
    if not channel:
        raise HTTPException(404, "Channel tidak ditemukan")
    if not channel.refresh_token:
        raise HTTPException(400, "Channel belum terhubung")

    if not files:
        raise HTTPException(400, "Pilih minimal 1 file video")

    pub_dt = None
    if schedule_enabled:
        if len(files) != 1:
            raise HTTPException(400, "Jadwal tayang hanya untuk upload 1 file (satuan)")
        pub_dt = _parse_publish_at(publish_at)
        if not pub_dt:
            raise HTTPException(400, "Isi waktu tayang untuk jadwal")

    MANUAL_YT_DIR.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    for index, upload in enumerate(files):
        if not upload.filename:
            continue
        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_MANUAL_VIDEO_EXT:
            raise HTTPException(400, f"Format tidak didukung: {upload.filename}")

        dest = MANUAL_YT_DIR / f"{channel_id}_{int(time.time() * 1000)}_{index}_{upload.filename}"
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        saved_paths.append(str(dest))

    if not saved_paths:
        raise HTTPException(400, "Tidak ada file valid")

    job = job_manager.create("youtube-manual")
    job_manager.run(
        job,
        lambda: _run_manual_youtube_upload(
            channel_id,
            saved_paths,
            title,
            description,
            privacy,
            tag_list,
            use_filename_as_title,
            auto_thumbnail,
            pub_dt,
        ),
        f"Upload {len(saved_paths)} file ke YouTube...",
    )
    return job_manager.to_dict(job)


@app.post("/api/profiles/{profile_id}/youtube-upload")
def api_youtube_upload(profile_id: int, req: YouTubeUploadRequest):
    if req.schedule_enabled:
        if not _parse_publish_at(req.schedule_start):
            raise HTTPException(400, "Isi waktu tayang video pertama untuk jadwal bulk")
        if req.schedule_interval_hours < 0.5:
            raise HTTPException(400, "Interval jadwal minimal 0.5 jam")
    job = job_manager.create("youtube-upload")
    job_manager.run(
        job,
        lambda: _run_youtube_upload(profile_id, req),
        "Uploading videos ke YouTube...",
    )
    return job_manager.to_dict(job)


@app.post("/api/tiktok-shop/test")
def api_test_tiktok_shop(session: Session = Depends(get_session)):
    cfg = get_shop_config(session)
    if not cfg:
        raise HTTPException(400, "Belum setup TikTok Shop API. Isi App Key & Secret dulu.")

    try:
        client = TikTokShopClient(config_from_model(cfg))
        result = client.test_connection()
    except TikTokShopAPIError as e:
        raise HTTPException(400, str(e))

    return result


def _run_tiktok_shop_sync(profile_id: int, days: int) -> dict:
    session = init_db(DB_PATH)
    try:
        cfg = get_shop_config(session)
        if not cfg or not cfg.is_active:
            raise ValueError("TikTok Shop API belum dikonfigurasi")

        profile = get_profile(session, profile_id)
        if not profile:
            raise ValueError("Profil tidak ditemukan")

        client = TikTokShopClient(config_from_model(cfg))
        result = sync_gmv_from_api(session, client, profile_id, days=days)

        cfg.last_sync_at = datetime.utcnow()
        session.commit()
        result["last_sync_at"] = cfg.last_sync_at.isoformat()
        return result
    finally:
        session.close()


@app.post("/api/profiles/{profile_id}/sync-gmv-api")
def api_sync_gmv_api(profile_id: int, req: TikTokShopSyncRequest):
    job = job_manager.create("sync-gmv")
    job_manager.run(
        job,
        lambda: _run_tiktok_shop_sync(profile_id, req.days),
        f"Sync GMV dari TikTok Shop API ({req.days} hari)...",
    )
    return job_manager.to_dict(job)


@app.get("/api/cookies/status")
def api_cookies_status():
    raw = COOKIES_DIR / "cookies.txt"
    filtered = COOKIES_DIR / "tiktok_only.txt"
    status = validate_tiktok_cookies(filtered if filtered.exists() else raw)
    return status


@app.post("/api/cookies")
async def api_upload_cookies(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "File wajib diisi")

    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    raw = COOKIES_DIR / "cookies.txt"
    filtered = COOKIES_DIR / "tiktok_only.txt"

    with raw.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    count = filter_tiktok_cookies(raw, filtered)
    status = validate_tiktok_cookies(filtered)

    if count == 0:
        raise HTTPException(
            400,
            "Tidak ada cookie TikTok di file ini. "
            "Buka tiktok.com di browser → export cookies dari situ saja.",
        )

    return {
        "path": str(filtered),
        "tiktok_cookies": count,
        "message": status["message"],
        "ok": status["ok"],
    }


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan")
    return job_manager.to_dict(job)


def _get_owned_video(session: Session, video_id: int, user_id: int):
    from ..db.models import Profile, Video

    video = session.query(Video).filter_by(id=video_id).first()
    if not video:
        raise HTTPException(404, "Video tidak ditemukan")
    profile = session.query(Profile).filter_by(id=video.profile_id, user_id=user_id).first()
    if not profile:
        raise HTTPException(404, "Video tidak ditemukan")
    return video, profile


@app.get("/api/videos/{video_id}/direct-download")
def api_direct_download_video(
    video_id: int,
    quality: str = "best",
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    video, profile = _get_owned_video(session, video_id, user_id)
    cookies_file = None
    tiktok_only = COOKIES_DIR / "tiktok_only.txt"
    if tiktok_only.exists():
        cookies_file = str(tiktok_only)
    else:
        raw = COOKIES_DIR / "cookies.txt"
        if raw.exists():
            cookies_file = str(raw)

    try:
        source_url = resolve_direct_download_url(
            video,
            profile.platform,
            quality=quality,
            cookies_file=cookies_file if profile.platform == "instagram" else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"Gagal mengambil video: {e}") from e

    filename = direct_download_filename(video)
    referer = "https://www.tiktok.com/" if profile.platform == "tiktok" else "https://www.instagram.com/"
    headers = {
        "Content-Disposition": content_disposition_attachment(filename),
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        stream_remote_video(source_url, referer=referer),
        media_type="video/mp4",
        headers=headers,
    )


@app.get("/api/videos/{video_id}/file")
def api_serve_video(
    video_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    video, _profile = _get_owned_video(session, video_id, user_id)
    if not video.is_downloaded or not video.file_path:
        raise HTTPException(404, "Video belum di-download")

    path = Path(video.file_path)
    if not path.exists():
        raise HTTPException(404, "File tidak ditemukan")

    return FileResponse(path, media_type="video/mp4", filename=path.name)


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")