"""FastAPI web application for Affiliate Video Tool."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from ..auth import (
    check_rate_limit,
    clear_rate_limit,
    record_failed_login,
    setup_auth,
)
from ..cookies_util import filter_tiktok_cookies, validate_tiktok_cookies
from ..db.models import init_db
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
from ..services import (
    delete_profile,
    download_videos,
    get_hero_videos,
    get_profile,
    get_profile_stats,
    get_scraper,
    list_profiles,
    list_videos,
    profile_to_dict,
    sync_profile_videos,
    update_video_metrics,
    video_to_dict,
)
from .deps import BASE_DIR, COOKIES_DIR, DB_PATH, DOWNLOAD_DIR, STATIC_DIR, get_session
from .jobs import job_manager

app = FastAPI(title="Affiliate Video Tool", version="0.2.0")

auth_store, _session_secret = setup_auth(BASE_DIR)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1", "yes")

PUBLIC_PATHS = frozenset({
    "/login.html",
    "/api/auth/login",
    "/api/auth/me",
    "/api/health",
})


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


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
    if path in PUBLIC_PATHS:
        return await call_next(request)

    if not _is_authenticated(request):
        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return RedirectResponse("/login.html", status_code=302)

    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
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


class ScanRequest(BaseModel):
    platform: str
    username: str


class DownloadRequest(BaseModel):
    limit: Optional[int] = 10
    video_ids: Optional[list[str]] = None
    only_pending: bool = True
    quality: str = "best"


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


def _cookies_path() -> Optional[str]:
    tiktok_only = COOKIES_DIR / "tiktok_only.txt"
    if tiktok_only.exists():
        return str(tiktok_only)
    path = COOKIES_DIR / "cookies.txt"
    return str(path) if path.exists() else None


def _run_scan(platform: str, username: str, cookies_file: Optional[str] = None) -> dict:
    cookies_file = cookies_file or _cookies_path()
    session = init_db(DB_PATH)
    try:
        result = sync_profile_videos(session, platform, username, cookies_file)
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
            },
        }
    finally:
        session.close()


def _run_download(
    profile_id: int,
    limit: Optional[int],
    video_ids: Optional[list[str]],
    only_pending: bool,
    cookies_file: Optional[str],
    quality: str = "best",
) -> dict:
    cookies_file = cookies_file or _cookies_path()
    session = init_db(DB_PATH)
    try:
        profile = get_profile(session, profile_id)
        if not profile:
            raise ValueError("Profil tidak ditemukan")
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
        )
    finally:
        session.close()


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest, request: Request):
    ip = _client_ip(request)
    rate_msg = check_rate_limit(ip)
    if rate_msg:
        raise HTTPException(429, rate_msg)

    username = req.username.strip()
    if auth_store.verify(username, req.password):
        clear_rate_limit(ip)
        request.session.clear()
        request.session["authenticated"] = True
        request.session["username"] = auth_store.get_username()
        return {"ok": True, "username": auth_store.get_username()}

    record_failed_login(ip)
    raise HTTPException(401, "Username atau password salah")


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    if _is_authenticated(request):
        return {"authenticated": True, "username": request.session.get("username")}
    return {"authenticated": False}


@app.post("/api/auth/change-password")
async def auth_change_password(req: ChangePasswordRequest, request: Request):
    if not _is_authenticated(request):
        raise HTTPException(401, "Unauthorized")
    try:
        auth_store.change_password(req.current_password, req.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    request.session.clear()
    return {"ok": True, "message": "Password berhasil diubah. Silakan login ulang."}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/profiles")
def api_list_profiles(session: Session = Depends(get_session)):
    profiles = list_profiles(session)
    result = []
    for p in profiles:
        stats = get_profile_stats(session, p.id)
        result.append(profile_to_dict(p, stats))
    return result


@app.get("/api/profiles/{profile_id}")
def api_get_profile(profile_id: int, session: Session = Depends(get_session)):
    profile = get_profile(session, profile_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")
    stats = get_profile_stats(session, profile_id)
    return profile_to_dict(profile, stats)


@app.get("/api/profiles/{profile_id}/videos")
def api_list_videos(
    profile_id: int,
    status: str = "all",
    sort_by: str = "gmv",
    session: Session = Depends(get_session),
):
    profile = get_profile(session, profile_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")

    filter_status = None if status == "all" else status
    videos = list_videos(session, profile.platform, profile.username, filter_status, sort_by)
    return [video_to_dict(v) for v in videos]


@app.get("/api/profiles/{profile_id}/heroes")
def api_heroes(profile_id: int, top: int = 10, session: Session = Depends(get_session)):
    profile = get_profile(session, profile_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")

    videos = get_hero_videos(session, profile.platform, profile.username, top)
    has_gmv = any(v.gmv for v in videos)
    return {
        "ranked_by": "gmv" if has_gmv else "engagement",
        "videos": [video_to_dict(v) for v in videos],
    }


@app.post("/api/scan")
def api_scan(req: ScanRequest):
    if req.platform not in ("tiktok", "instagram"):
        raise HTTPException(400, "Platform harus tiktok atau instagram")

    username = get_scraper(req.platform).normalize_username(req.username)
    if not username:
        raise HTTPException(400, "Username wajib diisi")

    job = job_manager.create("scan")
    job_manager.run(
        job,
        lambda: _run_scan(req.platform, username),
        f"Scanning @{username}...",
    )
    return job_manager.to_dict(job)


@app.post("/api/profiles/{profile_id}/download")
def api_download(profile_id: int, req: DownloadRequest):
    job = job_manager.create("download")
    job_manager.run(
        job,
        lambda: _run_download(
            profile_id, req.limit, req.video_ids, req.only_pending, None, req.quality
        ),
        "Downloading videos...",
    )
    return job_manager.to_dict(job)


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(profile_id: int, session: Session = Depends(get_session)):
    try:
        result = delete_profile(session, profile_id, DOWNLOAD_DIR, delete_files=True)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return result


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
def api_import_gmv_text(profile_id: int, req: GmvTextRequest, session: Session = Depends(get_session)):
    profile = get_profile(session, profile_id)
    if not profile:
        raise HTTPException(404, "Profil tidak ditemukan")
    try:
        return import_gmv_text(session, req.text, profile_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/profiles/{profile_id}/import-gmv")
async def api_import_gmv(profile_id: int, file: UploadFile = File(...), session: Session = Depends(get_session)):
    profile = get_profile(session, profile_id)
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


@app.get("/api/videos/{video_id}/file")
def api_serve_video(video_id: int, session: Session = Depends(get_session)):
    from ..db.models import Video

    video = session.query(Video).filter_by(id=video_id).first()
    if not video or not video.is_downloaded or not video.file_path:
        raise HTTPException(404, "Video belum di-download")

    path = Path(video.file_path)
    if not path.exists():
        raise HTTPException(404, "File tidak ditemukan")

    return FileResponse(path, media_type="video/mp4", filename=path.name)


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")