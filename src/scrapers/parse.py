"""Parse username from various input formats (handle, @handle, full URL)."""

from __future__ import annotations

import re

_TIKTOK_HANDLE = re.compile(r"@([A-Za-z0-9._]+)")
_INSTAGRAM_PATH = re.compile(r"instagram\.com/([A-Za-z0-9._]+)", re.I)
_TIKTOK_PATH = re.compile(r"tiktok\.com/@?([A-Za-z0-9._]+)", re.I)
_KUAISHOU_PROFILE = re.compile(r"kuaishou\.com/profile/([^/?#]+)", re.I)
_KUAISHOU_USER = re.compile(r"(?:gifshow|chenzhongtech)\.com/user/([^/?#]+)", re.I)
_REDNOTE_PROFILE = re.compile(r"rednote\.com/user/profile/([^/?#]+)", re.I)
_XHS_PROFILE = re.compile(r"xiaohongshu\.com/user/profile/([^/?#]+)", re.I)
_SHOPEE_SHOP = re.compile(r"shopee\.co\.id/([A-Za-z0-9._-]+)", re.I)
_SHOPEE_SV_PROFILE = re.compile(r"sv\.shopee\.co\.id/profile/([^/?#]+)", re.I)

_IG_RESERVED = frozenset(
    {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct", "about"}
)


def _last_handle(value: str) -> str | None:
    handles = _TIKTOK_HANDLE.findall(value)
    # Skip invalid handles produced by broken URLs like @https://...
    valid = [h for h in handles if h.lower() not in ("http", "https", "www")]
    return valid[-1] if valid else None


def parse_tiktok_username(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw

    handle = _last_handle(raw)
    if handle:
        return handle

    paths = [m for m in _TIKTOK_PATH.findall(raw) if m.lower() not in ("www",)]
    if paths:
        return paths[-1]

    return raw.lstrip("@").strip().split("/")[0].split("?")[0]


def parse_instagram_username(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw

    paths = [
        m
        for m in _INSTAGRAM_PATH.findall(raw)
        if m.lower() not in _IG_RESERVED
    ]
    if paths:
        return paths[-1]

    handle = _last_handle(raw)
    if handle:
        return handle

    return raw.lstrip("@").strip().split("/")[0].split("?")[0]


def parse_rednote_username(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw

    profiles = _REDNOTE_PROFILE.findall(raw) + _XHS_PROFILE.findall(raw)
    if profiles:
        return profiles[-1]

    handle = _last_handle(raw)
    if handle:
        return handle

    cleaned = raw.lstrip("@").strip().split("/")[0].split("?")[0]
    if cleaned and not cleaned.lower().startswith("http"):
        return cleaned
    return cleaned


_SHOPEE_RESERVED = frozenset(
    {
        "buy",
        "cart",
        "search",
        "help",
        "seller",
        "mall",
        "official",
        "flash_sale",
        "daily_discover",
        "promotion",
        "user",
        "me",
        "checkout",
    }
)


def parse_shopee_username(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw

    sv_profiles = _SHOPEE_SV_PROFILE.findall(raw)
    if sv_profiles:
        return sv_profiles[-1]

    shops = [
        m
        for m in _SHOPEE_SHOP.findall(raw)
        if m.lower() not in _SHOPEE_RESERVED and "-i." not in m
    ]
    if shops:
        return shops[-1].split("-i.")[0]

    handle = _last_handle(raw)
    if handle:
        return handle

    cleaned = raw.lstrip("@").strip().split("/")[0].split("?")[0]
    if cleaned and not cleaned.lower().startswith("http"):
        return cleaned
    return cleaned


def parse_kuaishou_username(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw

    profiles = _KUAISHOU_PROFILE.findall(raw)
    if profiles:
        return profiles[-1]

    legacy = _KUAISHOU_USER.findall(raw)
    if legacy:
        return legacy[-1]

    handle = _last_handle(raw)
    if handle:
        return handle

    cleaned = raw.lstrip("@").strip().split("/")[0].split("?")[0]
    if cleaned and not cleaned.lower().startswith("http"):
        return cleaned
    return cleaned