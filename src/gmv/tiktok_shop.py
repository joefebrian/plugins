"""TikTok Shop Partner API client for GMV/commission sync."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..db.models import TikTokShopConfig, Video

# Regional API base URLs
REGION_BASE_URLS = {
    "ID": "https://open-api.tiktokglobalshop.com",
    "US": "https://open-api.tiktokglobalshop.com",
    "SG": "https://open-api.tiktokglobalshop.com",
    "UK": "https://open-api.tiktokglobalshop.com",
    "MY": "https://open-api.tiktokglobalshop.com",
    "TH": "https://open-api.tiktokglobalshop.com",
    "VN": "https://open-api.tiktokglobalshop.com",
    "PH": "https://open-api.tiktokglobalshop.com",
}

# Analytics endpoints (TikTok Shop Partner API 202409+)
ENDPOINTS = {
    "video_performance_list": "/analytics/202409/shop_videos/performance",
    "video_performance_overview": "/analytics/202409/shop_videos/overview",
    "affiliate_creator_performance": "/affiliate/202405/creator/performance",
}


@dataclass
class TikTokShopCredentials:
    app_key: str
    app_secret: str
    access_token: Optional[str] = None
    shop_cipher: Optional[str] = None
    shop_id: Optional[str] = None
    region: str = "ID"
    base_url: str = "https://open-api.tiktokglobalshop.com"


class TikTokShopAPIError(Exception):
    pass


class TikTokShopClient:
    def __init__(self, creds: TikTokShopCredentials):
        self.creds = creds
        self.base_url = creds.base_url.rstrip("/")

    def _sign(self, path: str, params: dict, body: str = "") -> str:
        """HMAC-SHA256 signature per TikTok Shop Partner API spec."""
        filtered = {
            k: str(v)
            for k, v in params.items()
            if k not in ("sign", "access_token") and v is not None
        }
        sorted_keys = sorted(filtered.keys())
        param_str = "".join(f"{k}{filtered[k]}" for k in sorted_keys)
        sign_string = f"{self.creds.app_secret}{path}{param_str}{body}{self.creds.app_secret}"
        return hmac.new(
            self.creds.app_secret.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        query: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> dict:
        if not self.creds.access_token:
            raise TikTokShopAPIError(
                "Access Token belum diisi. Dapatkan dari OAuth TikTok Shop Partner Center."
            )

        query = dict(query or {})
        body_str = json.dumps(body) if body else ""
        timestamp = str(int(time.time()))

        query.update({
            "app_key": self.creds.app_key,
            "timestamp": timestamp,
        })
        if self.creds.shop_cipher:
            query["shop_cipher"] = self.creds.shop_cipher

        query["sign"] = self._sign(path, query, body_str)

        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        headers = {
            "Content-Type": "application/json",
            "x-tts-access-token": self.creds.access_token,
        }

        req = urllib.request.Request(
            url,
            data=body_str.encode() if body_str else None,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode() if e.fp else str(e)
            raise TikTokShopAPIError(f"HTTP {e.code}: {err_body[:300]}") from e
        except urllib.error.URLError as e:
            raise TikTokShopAPIError(f"Koneksi gagal: {e.reason}") from e

        if payload.get("code") not in (0, "0", None):
            msg = payload.get("message") or payload.get("msg") or str(payload)
            raise TikTokShopAPIError(f"API error: {msg}")

        return payload.get("data") or payload

    def test_connection(self) -> dict:
        """Ping API dengan video performance overview (7 hari terakhir)."""
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        data = self.get_video_performance_overview(
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
        )
        return {"ok": True, "message": "Koneksi berhasil", "sample": data}

    def get_video_performance_overview(
        self, start_date: str, end_date: str
    ) -> dict:
        return self._request(
            "GET",
            ENDPOINTS["video_performance_overview"],
            query={
                "start_date_ge": start_date,
                "end_date_lt": end_date,
            },
        )

    def get_video_performance_list(
        self,
        start_date: str,
        end_date: str,
        page_size: int = 50,
        page_token: str = "",
        sort_field: str = "gmv",
        sort_order: str = "DESC",
    ) -> dict:
        query = {
            "start_date_ge": start_date,
            "end_date_lt": end_date,
            "page_size": str(page_size),
            "sort_field": sort_field,
            "sort_order": sort_order,
        }
        if page_token:
            query["page_token"] = page_token
        return self._request("GET", ENDPOINTS["video_performance_list"], query=query)

    def iter_all_video_performance(
        self,
        start_date: str,
        end_date: str,
        page_size: int = 50,
    ):
        """Paginate through all video performance records."""
        page_token = ""
        while True:
            data = self.get_video_performance_list(
                start_date, end_date, page_size=page_size, page_token=page_token
            )
            items = (
                data.get("videos")
                or data.get("video_list")
                or data.get("items")
                or data.get("list")
                or []
            )
            for item in items:
                yield item

            page_token = data.get("next_page_token") or data.get("page_token") or ""
            if not page_token or not items:
                break


def _parse_metric(item: dict, *keys) -> Optional[float]:
    for key in keys:
        val = item.get(key)
        if val is not None and val != "":
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        # nested stats object
        stats = item.get("stats") or item.get("performance") or {}
        if isinstance(stats, dict) and key in stats:
            try:
                return float(stats[key])
            except (TypeError, ValueError):
                pass
    return None


def _extract_video_id(item: dict) -> Optional[str]:
    for key in ("video_id", "item_id", "content_id", "id", "aweme_id"):
        val = item.get(key)
        if val:
            return str(val)
    video = item.get("video") or {}
    if isinstance(video, dict):
        for key in ("video_id", "id"):
            if video.get(key):
                return str(video[key])
    url = item.get("video_url") or item.get("url") or ""
    import re
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else None


def sync_gmv_from_api(
    session: Session,
    client: TikTokShopClient,
    profile_id: int,
    days: int = 30,
) -> dict:
    """Pull video performance from TikTok Shop API and update tracked videos."""
    end = datetime.utcnow()
    start = end - timedelta(days=days)

    updated = 0
    unmatched = 0
    api_total = 0

    for item in client.iter_all_video_performance(
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
    ):
        api_total += 1
        video_id = _extract_video_id(item)
        if not video_id:
            unmatched += 1
            continue

        video = (
            session.query(Video)
            .filter_by(profile_id=profile_id, platform_video_id=video_id)
            .first()
        )
        if not video:
            video = (
                session.query(Video)
                .filter(Video.profile_id == profile_id, Video.url.contains(video_id))
                .first()
            )

        if not video:
            unmatched += 1
            continue

        gmv = _parse_metric(item, "gmv", "total_gmv", "revenue", "gmv_amount")
        commission = _parse_metric(
            item, "commission", "creator_commission", "commission_amount", "earnings"
        )
        orders = _parse_metric(item, "orders", "order_count", "sku_orders")

        if gmv is not None:
            video.gmv = gmv
        if commission is not None:
            video.commission = commission
        if orders is not None:
            video.orders = int(orders)

        views = _parse_metric(item, "views", "video_views", "play_count")
        if views is not None and video.views is None:
            video.views = int(views)

        updated += 1

    session.commit()
    return {
        "updated": updated,
        "unmatched": unmatched,
        "api_records": api_total,
        "period_days": days,
    }


def config_from_model(cfg: TikTokShopConfig) -> TikTokShopCredentials:
    base = cfg.base_url or REGION_BASE_URLS.get(cfg.region, REGION_BASE_URLS["ID"])
    return TikTokShopCredentials(
        app_key=cfg.app_key,
        app_secret=cfg.app_secret,
        access_token=cfg.access_token,
        shop_cipher=cfg.shop_cipher,
        shop_id=cfg.shop_id,
        region=cfg.region,
        base_url=base,
    )


def get_shop_config(session: Session) -> Optional[TikTokShopConfig]:
    return session.query(TikTokShopConfig).filter_by(id=1).first()


def save_shop_config(session: Session, data: dict) -> TikTokShopConfig:
    cfg = get_shop_config(session)
    if not cfg:
        cfg = TikTokShopConfig(id=1)
        session.add(cfg)

    for field in (
        "app_key", "app_secret", "access_token", "refresh_token",
        "shop_cipher", "shop_id", "region", "base_url", "is_active",
    ):
        if field in data and data[field] is not None:
            setattr(cfg, field, data[field])

    if data.get("region") and not data.get("base_url"):
        cfg.base_url = REGION_BASE_URLS.get(data["region"], REGION_BASE_URLS["ID"])

    cfg.updated_at = datetime.utcnow()
    session.commit()
    return cfg


def config_to_dict(cfg: Optional[TikTokShopConfig], mask_secret: bool = True) -> dict:
    if not cfg:
        return {"configured": False}

    secret_display = "••••••••" if mask_secret and cfg.app_secret else cfg.app_secret
    token_display = "••••••••" if mask_secret and cfg.access_token else cfg.access_token

    return {
        "configured": True,
        "app_key": cfg.app_key,
        "app_secret": secret_display,
        "has_app_secret": bool(cfg.app_secret),
        "access_token": token_display,
        "has_access_token": bool(cfg.access_token),
        "shop_cipher": cfg.shop_cipher,
        "shop_id": cfg.shop_id,
        "region": cfg.region,
        "base_url": cfg.base_url,
        "is_active": cfg.is_active,
        "last_sync_at": cfg.last_sync_at.isoformat() if cfg.last_sync_at else None,
        "token_expires_at": cfg.token_expires_at.isoformat() if cfg.token_expires_at else None,
    }