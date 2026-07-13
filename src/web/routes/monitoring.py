"""Social Monitoring API — standalone connect + metrics per platform."""

from __future__ import annotations

import os
import time
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db.models import init_db
from ...facebook.client import (
    FacebookAPIError,
    build_auth_url as fb_build_auth_url,
    exchange_code_for_token as fb_exchange_code,
    exchange_long_lived_token as fb_exchange_long,
    get_app_config as get_fb_app_config,
    get_user_pages,
)
from ...monitoring.accounts import (
    account_to_dict,
    delete_account,
    get_account,
    list_accounts,
    upsert_account,
)
from ...monitoring.metrics import refresh_account_metrics
from ...monitoring.oauth import create_oauth_state, pop_oauth_state_meta
from ...monitoring.scan import scan_username_metrics
from ...monitoring.social import MONITORED_PLATFORMS, monitoring_overview, platform_metrics
from ...monitoring.twitter_client import (
    TwitterAPIError,
    build_auth_url as twitter_build_auth_url,
    config_to_dict as twitter_config_to_dict,
    exchange_code_for_token as twitter_exchange_code,
    fetch_user_metrics,
    get_twitter_config,
    pkce_pair,
    save_twitter_config,
)
from ...facebook.client import exchange_code_for_token as fb_exchange_short
from ...threads.client import (
    ThreadsAPIError,
    build_auth_url as threads_build_auth_url,
    exchange_long_lived_token as threads_exchange_long,
    fetch_threads_profile,
)
from ...youtube.client import (
    YouTubeAPIError,
    YouTubeChannel,
    YouTubeClient,
    build_auth_url as yt_build_auth_url,
    credentials_from_channel,
    exchange_code_for_tokens,
)
from ...youtube.quota import get_oauth_app, is_app_available, pick_available_app, record_grant
from ..auth_deps import get_current_user_id
from ..deps import COOKIES_DIR, DB_PATH, get_session

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


class UsernameConnectRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    label: Optional[str] = None


class OAuthConnectRequest(BaseModel):
    label: Optional[str] = None
    oauth_app_id: Optional[int] = None


class TwitterConfigRequest(BaseModel):
    client_id: str
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None


def _cookies_path() -> Optional[str]:
    tiktok_only = COOKIES_DIR / "tiktok_only.txt"
    if tiktok_only.exists():
        return str(tiktok_only)
    path = COOKIES_DIR / "cookies.txt"
    return str(path) if path.exists() else None


def _redirect_base(request: Request, env_key: str, path: str) -> str:
    env_redirect = os.getenv(env_key, "").strip()
    if env_redirect:
        return env_redirect
    return str(request.base_url).rstrip("/") + path


def _monitoring_redirect(view_platform: str, **params: str) -> str:
    q = urllib.parse.urlencode({"view": "monitoring", "platform": view_platform, **params})
    return f"/index.html?{q}"


@router.get("/overview")
def api_monitoring_overview(
    live: bool = Query(True),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    return monitoring_overview(session, user_id, live=live, cookies_file=_cookies_path())


@router.get("/accounts")
def api_list_monitoring_accounts(
    platform: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    accounts = list_accounts(session, user_id, platform)
    return [account_to_dict(a) for a in accounts]


@router.delete("/accounts/{account_id}")
def api_delete_monitoring_account(
    account_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    if not delete_account(session, account_id, user_id):
        raise HTTPException(404, "Akun monitoring tidak ditemukan")
    return {"ok": True, "message": "Akun monitoring dihapus"}


@router.post("/accounts/{account_id}/refresh")
def api_refresh_monitoring_account(
    account_id: int,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    acc = get_account(session, account_id, user_id)
    if not acc:
        raise HTTPException(404, "Akun monitoring tidak ditemukan")
    refresh_account_metrics(session, acc, cookies_file=_cookies_path())
    return account_to_dict(acc)


# --- TikTok / Instagram: username connect ---

@router.post("/tiktok/connect")
def api_monitoring_connect_tiktok(
    req: UsernameConnectRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        data = scan_username_metrics("tiktok", req.username, _cookies_path())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    acc = upsert_account(
        session,
        user_id=user_id,
        platform="tiktok",
        external_id=data["external_id"],
        label=req.label or data["name"],
        name=data["name"],
        handle=data["handle"],
        profile_url=data["profile_url"],
    )
    refresh_account_metrics(session, acc, cookies_file=_cookies_path())
    return {"ok": True, "account": account_to_dict(acc)}


@router.post("/instagram/connect")
def api_monitoring_connect_instagram(
    req: UsernameConnectRequest,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    try:
        data = scan_username_metrics("instagram", req.username, _cookies_path())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    acc = upsert_account(
        session,
        user_id=user_id,
        platform="instagram",
        external_id=data["external_id"],
        label=req.label or data["name"],
        name=data["name"],
        handle=data["handle"],
        profile_url=data["profile_url"],
    )
    refresh_account_metrics(session, acc, cookies_file=_cookies_path())
    return {"ok": True, "account": account_to_dict(acc)}


# --- YouTube OAuth (monitoring-only tokens) ---

@router.post("/youtube/connect")
def api_monitoring_youtube_connect(
    request: Request,
    req: OAuthConnectRequest = OAuthConnectRequest(),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    app_cfg = pick_available_app(session, for_grant=True) if not req.oauth_app_id else get_oauth_app(session, req.oauth_app_id)
    if not app_cfg or not app_cfg.client_id or not app_cfg.client_secret:
        raise HTTPException(400, "Google OAuth App belum dikonfigurasi di Multiupload → YouTube.")
    if not is_app_available(app_cfg, for_grant=True):
        raise HTTPException(400, f"OAuth App '{app_cfg.label}' limit habis.")

    redirect_uri = _redirect_base(request, "MONITORING_YOUTUBE_REDIRECT_URI", "/api/monitoring/youtube/oauth/callback")
    state = create_oauth_state("youtube", user_id=user_id, label=req.label or "", oauth_app_id=app_cfg.id)
    return {
        "auth_url": yt_build_auth_url(app_cfg.client_id, redirect_uri, state),
        "oauth_app_id": app_cfg.id,
    }


@router.get("/youtube/oauth/callback")
def api_monitoring_youtube_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(_monitoring_redirect("youtube", youtube="error", msg=error))
    meta = pop_oauth_state_meta(state or "")
    if not code or not meta or meta.get("platform") != "youtube":
        return RedirectResponse(_monitoring_redirect("youtube", youtube="error", msg="invalid_oauth_state"))
    if time.time() - meta.get("created_at", 0) > 600:
        return RedirectResponse(_monitoring_redirect("youtube", youtube="error", msg="oauth_expired"))

    session = init_db(DB_PATH)
    try:
        app_cfg = get_oauth_app(session, meta.get("oauth_app_id")) or pick_available_app(session)
        if not app_cfg:
            return RedirectResponse(_monitoring_redirect("youtube", youtube="error", msg="config_missing"))

        redirect_uri = _redirect_base(request, "MONITORING_YOUTUBE_REDIRECT_URI", "/api/monitoring/youtube/oauth/callback")
        tokens = exchange_code_for_tokens(app_cfg.client_id, app_cfg.client_secret, redirect_uri, code)
        record_grant(session, app_cfg)

        temp = YouTubeChannel(
            refresh_token=tokens.get("refresh_token"),
            access_token=tokens["access_token"],
            token_expires_at=tokens["token_expires_at"],
        )
        client = YouTubeClient(credentials_from_channel(app_cfg, temp))
        info = client.get_channel_info()
        stats = client.get_channel_statistics(info["channel_id"])

        acc = upsert_account(
            session,
            user_id=int(meta["user_id"]),
            platform="youtube",
            external_id=info["channel_id"],
            label=meta.get("label") or info["channel_title"],
            name=info["channel_title"],
            handle=info["channel_id"],
            thumbnail=info.get("channel_thumbnail"),
            profile_url=f"https://www.youtube.com/channel/{info['channel_id']}",
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_expires_at=tokens["token_expires_at"],
            oauth_app_id=app_cfg.id,
        )
        refresh_account_metrics(session, acc)
    except YouTubeAPIError as e:
        return RedirectResponse(
            _monitoring_redirect("youtube", youtube="error", msg=urllib.parse.quote(str(e)))
        )
    finally:
        session.close()

    return RedirectResponse(_monitoring_redirect("youtube", youtube="connected"))


# --- Facebook OAuth ---

@router.post("/facebook/connect")
def api_monitoring_facebook_connect(
    request: Request,
    req: OAuthConnectRequest = OAuthConnectRequest(),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    cfg = get_fb_app_config(session)
    if not cfg or not cfg.app_id or not cfg.app_secret:
        raise HTTPException(400, "Facebook App belum dikonfigurasi di Multiupload → Facebook.")

    redirect_uri = _redirect_base(request, "MONITORING_FACEBOOK_REDIRECT_URI", "/api/monitoring/facebook/oauth/callback")
    state = create_oauth_state("facebook", user_id=user_id, label=req.label or "")
    return {"auth_url": fb_build_auth_url(cfg.app_id, redirect_uri, state)}


@router.get("/facebook/oauth/callback")
def api_monitoring_facebook_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(_monitoring_redirect("facebook", facebook="error", msg=error))
    meta = pop_oauth_state_meta(state or "")
    if not code or not meta or meta.get("platform") != "facebook":
        return RedirectResponse(_monitoring_redirect("facebook", facebook="error", msg="invalid_oauth_state"))
    if time.time() - meta.get("created_at", 0) > 600:
        return RedirectResponse(_monitoring_redirect("facebook", facebook="error", msg="oauth_expired"))

    session = init_db(DB_PATH)
    try:
        cfg = get_fb_app_config(session)
        if not cfg:
            return RedirectResponse(_monitoring_redirect("facebook", facebook="error", msg="config_missing"))

        redirect_uri = _redirect_base(request, "MONITORING_FACEBOOK_REDIRECT_URI", "/api/monitoring/facebook/oauth/callback")
        short = fb_exchange_code(cfg.app_id, cfg.app_secret, redirect_uri, code)
        long_lived = fb_exchange_long(cfg.app_id, cfg.app_secret, short["access_token"])
        pages = get_user_pages(long_lived["access_token"])
        if not pages:
            return RedirectResponse(_monitoring_redirect("facebook", facebook="error", msg="no_facebook_pages"))

        user_id = int(meta["user_id"])
        connected = 0
        for page in pages:
            acc = upsert_account(
                session,
                user_id=user_id,
                platform="facebook",
                external_id=page["page_id"],
                label=page["page_name"],
                name=page["page_name"],
                handle=page["page_id"],
                thumbnail=page.get("page_thumbnail"),
                profile_url=f"https://facebook.com/{page['page_id']}",
                access_token=page["page_access_token"],
                token_expires_at=long_lived["token_expires_at"],
            )
            refresh_account_metrics(session, acc)
            connected += 1
    except FacebookAPIError as e:
        return RedirectResponse(
            _monitoring_redirect("facebook", facebook="error", msg=urllib.parse.quote(str(e)))
        )
    finally:
        session.close()

    return RedirectResponse(_monitoring_redirect("facebook", facebook="connected", count=str(connected)))


# --- Threads OAuth ---

@router.post("/threads/connect")
def api_monitoring_threads_connect(
    request: Request,
    req: OAuthConnectRequest = OAuthConnectRequest(),
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    cfg = get_fb_app_config(session)
    if not cfg or not cfg.app_id or not cfg.app_secret:
        raise HTTPException(400, "Meta App belum dikonfigurasi (pakai App ID Facebook yang sama).")

    redirect_uri = _redirect_base(request, "MONITORING_THREADS_REDIRECT_URI", "/api/monitoring/threads/oauth/callback")
    state = create_oauth_state("threads", user_id=user_id, label=req.label or "")
    return {"auth_url": threads_build_auth_url(cfg.app_id, redirect_uri, state)}


@router.get("/threads/oauth/callback")
def api_monitoring_threads_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(_monitoring_redirect("threads", threads="error", msg=error))
    meta = pop_oauth_state_meta(state or "")
    if not code or not meta or meta.get("platform") != "threads":
        return RedirectResponse(_monitoring_redirect("threads", threads="error", msg="invalid_oauth_state"))
    if time.time() - meta.get("created_at", 0) > 600:
        return RedirectResponse(_monitoring_redirect("threads", threads="error", msg="oauth_expired"))

    session = init_db(DB_PATH)
    try:
        cfg = get_fb_app_config(session)
        if not cfg:
            return RedirectResponse(_monitoring_redirect("threads", threads="error", msg="config_missing"))

        redirect_uri = _redirect_base(request, "MONITORING_THREADS_REDIRECT_URI", "/api/monitoring/threads/oauth/callback")
        short = fb_exchange_short(cfg.app_id, cfg.app_secret, redirect_uri, code)
        long_lived = threads_exchange_long(cfg.app_id, cfg.app_secret, short["access_token"])
        profile = fetch_threads_profile(long_lived["access_token"])
        handle = f"@{profile['username']}" if profile.get("username") else profile["threads_user_id"]

        acc = upsert_account(
            session,
            user_id=int(meta["user_id"]),
            platform="threads",
            external_id=profile["threads_user_id"],
            label=meta.get("label") or handle,
            name=handle,
            handle=handle,
            thumbnail=profile.get("profile_picture"),
            profile_url=f"https://www.threads.net/@{profile.get('username')}" if profile.get("username") else None,
            access_token=long_lived["access_token"],
            token_expires_at=long_lived["token_expires_at"],
        )
        refresh_account_metrics(session, acc)
    except (ThreadsAPIError, FacebookAPIError) as e:
        return RedirectResponse(
            _monitoring_redirect("threads", threads="error", msg=urllib.parse.quote(str(e)))
        )
    finally:
        session.close()

    return RedirectResponse(_monitoring_redirect("threads", threads="connected"))


# --- X / Twitter OAuth ---

@router.get("/twitter/config")
def api_monitoring_twitter_config(session: Session = Depends(get_session)):
    return twitter_config_to_dict(get_twitter_config(session))


@router.post("/twitter/config")
def api_monitoring_save_twitter_config(
    req: TwitterConfigRequest,
    session: Session = Depends(get_session),
):
    existing = get_twitter_config(session)
    data = req.model_dump()
    if existing and (not data.get("client_secret") or str(data.get("client_secret", "")).startswith("••")):
        data.pop("client_secret", None)
    if not data.get("client_secret") and not (existing and existing.client_secret):
        raise HTTPException(400, "Client Secret wajib diisi")
    cfg = save_twitter_config(session, data)
    return {"message": "X API credentials tersimpan", "config": twitter_config_to_dict(cfg)}


@router.post("/twitter/connect")
def api_monitoring_twitter_connect(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    session: Session = Depends(get_session),
):
    cfg = get_twitter_config(session)
    if not cfg or not cfg.client_id or not cfg.client_secret:
        raise HTTPException(400, "X API belum dikonfigurasi. Isi Client ID & Secret di tab X.")

    redirect_uri = cfg.redirect_uri or _redirect_base(
        request, "MONITORING_TWITTER_REDIRECT_URI", "/api/monitoring/twitter/oauth/callback"
    )
    verifier, challenge = pkce_pair()
    state = create_oauth_state("twitter", user_id=user_id, code_verifier=verifier)
    return {
        "auth_url": twitter_build_auth_url(cfg.client_id, redirect_uri, state, challenge),
    }


@router.get("/twitter/oauth/callback")
def api_monitoring_twitter_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(_monitoring_redirect("twitter", twitter="error", msg=error))
    meta = pop_oauth_state_meta(state or "")
    if not code or not meta or meta.get("platform") != "twitter":
        return RedirectResponse(_monitoring_redirect("twitter", twitter="error", msg="invalid_oauth_state"))
    if time.time() - meta.get("created_at", 0) > 600:
        return RedirectResponse(_monitoring_redirect("twitter", twitter="error", msg="oauth_expired"))

    session = init_db(DB_PATH)
    try:
        cfg = get_twitter_config(session)
        if not cfg:
            return RedirectResponse(_monitoring_redirect("twitter", twitter="error", msg="config_missing"))

        redirect_uri = cfg.redirect_uri or _redirect_base(
            request, "MONITORING_TWITTER_REDIRECT_URI", "/api/monitoring/twitter/oauth/callback"
        )
        tokens = twitter_exchange_code(
            cfg.client_id,
            cfg.client_secret,
            redirect_uri,
            code,
            meta.get("code_verifier") or "",
        )
        metrics = fetch_user_metrics(tokens["access_token"])
        if not metrics.get("external_id"):
            return RedirectResponse(_monitoring_redirect("twitter", twitter="error", msg="profile_missing"))

        acc = upsert_account(
            session,
            user_id=int(meta["user_id"]),
            platform="twitter",
            external_id=metrics["external_id"],
            label=metrics.get("name"),
            name=metrics.get("name"),
            handle=metrics.get("handle"),
            thumbnail=metrics.get("thumbnail"),
            profile_url=metrics.get("profile_url"),
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_expires_at=tokens["token_expires_at"],
        )
        refresh_account_metrics(session, acc)
    except TwitterAPIError as e:
        return RedirectResponse(
            _monitoring_redirect("twitter", twitter="error", msg=urllib.parse.quote(str(e)))
        )
    finally:
        session.close()

    return RedirectResponse(_monitoring_redirect("twitter", twitter="connected"))


@router.get("/{platform}")
def api_monitoring_platform(
    platform: str,
    live: bool = Query(True),
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
        return platform_metrics(
            session, user_id, platform, live=live, cookies_file=_cookies_path()
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e