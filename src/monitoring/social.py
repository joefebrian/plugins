"""Aggregate social account metrics from monitoring_accounts (standalone)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .accounts import account_to_dict, list_accounts
from .metrics import refresh_account_metrics

MONITORED_PLATFORMS = ("youtube", "instagram", "tiktok", "kuaishou", "facebook", "threads", "twitter")


def _totals(accounts: list[dict]) -> dict[str, Any]:
    followers = sum(a["followers"] for a in accounts if a.get("followers") is not None)
    views = sum(a["views"] for a in accounts if a.get("views") is not None)
    uploads = sum(a.get("uploads") or 0 for a in accounts)
    revenue = sum(a["revenue"] for a in accounts if a.get("revenue") is not None)
    has_followers = any(a.get("followers") is not None for a in accounts)
    has_views = any(a.get("views") is not None for a in accounts)
    has_revenue = any(a.get("revenue") is not None for a in accounts)
    return {
        "accounts": len(accounts),
        "followers": followers if has_followers else None,
        "views": views if has_views else None,
        "uploads": uploads,
        "revenue": revenue if has_revenue else None,
    }


def _rows_from_accounts(accounts: list) -> list[dict]:
    rows = []
    for acc in accounts:
        row = account_to_dict(acc)
        row["source"] = "cache" if acc.metrics_updated_at else "db"
        row["error"] = acc.last_error
        rows.append(row)
    return rows


def platform_metrics(
    session: Session,
    user_id: int,
    platform: str,
    *,
    live: bool = True,
    cookies_file: str | None = None,
) -> dict[str, Any]:
    platform = platform.lower().strip()
    if platform not in MONITORED_PLATFORMS:
        raise ValueError(f"Platform tidak didukung: {platform}")

    accounts = list_accounts(session, user_id, platform)
    if live:
        for acc in accounts:
            refresh_account_metrics(session, acc, cookies_file=cookies_file)
        accounts = list_accounts(session, user_id, platform)

    rows = _rows_from_accounts(accounts)
    return {
        "platform": platform,
        "coming_soon": False,
        "accounts": rows,
        "totals": _totals(rows),
    }


def monitoring_overview(
    session: Session,
    user_id: int,
    *,
    live: bool = True,
    cookies_file: str | None = None,
) -> dict[str, Any]:
    platforms: dict[str, dict] = {}
    all_accounts: list[dict] = []

    for platform in MONITORED_PLATFORMS:
        data = platform_metrics(session, user_id, platform, live=live, cookies_file=cookies_file)
        platforms[platform] = {
            "coming_soon": False,
            "totals": data["totals"],
            "account_count": len(data["accounts"]),
        }
        all_accounts.extend(data["accounts"])

    return {
        "platforms": platforms,
        "totals": _totals(all_accounts),
        "accounts": all_accounts,
    }