"""Import GMV/commission data from TikTok Shop affiliate exports."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from sqlalchemy.orm import Session

from ..db.models import Video


def _parse_amount(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d.,-]", "", str(value))
    if not cleaned:
        return None
    # Handle Indonesian format: 1.234.567,89
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d-]", "", str(value))
    try:
        return int(cleaned)
    except ValueError:
        return None


def _normalize_header(header: str) -> str:
    return re.sub(r"\s+", "_", header.strip().lower())


# Flexible column mapping for various TikTok Shop export formats
COLUMN_ALIASES = {
    "video_id": {"video_id", "content_id", "item_id", "id_video", "video"},
    "video_url": {"video_url", "content_url", "url", "link_video", "link"},
    "gmv": {"gmv", "total_gmv", "gross_merchandise_value", "revenue", "omzet"},
    "commission": {"commission", "komisi", "earnings", "pendapatan", "creator_commission"},
    "orders": {"orders", "order_count", "jumlah_pesanan", "total_orders", "pesanan"},
    "views": {"views", "video_views", "tayangan", "play_count"},
}


def _find_column(headers: list[str], aliases: set[str]) -> str | None:
    normalized = {_normalize_header(h): h for h in headers}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def _extract_video_id_from_url(url: str) -> str | None:
    match = re.search(r"/video/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"(\d{15,})", url)
    if match:
        return match.group(1)
    return None


def _apply_metrics(session: Session, video_id: str, gmv, commission, orders=None, views=None) -> bool:
    video = session.query(Video).filter_by(platform_video_id=video_id).first()
    if not video:
        video = session.query(Video).filter(Video.url.contains(video_id)).first()
    if not video:
        return False

    if gmv is not None:
        video.gmv = gmv
    if commission is not None:
        video.commission = commission
    if orders is not None:
        video.orders = orders
    if views is not None and video.views is None:
        video.views = views
    return True


def import_gmv_rows(session: Session, rows: list[dict], profile_id: int | None = None) -> dict:
    """Import from list of dicts with keys: video_id, gmv, commission, orders."""
    updated = 0
    unmatched = 0

    for row in rows:
        video_id = str(row.get("video_id") or "").strip()
        if not video_id and row.get("video_url"):
            video_id = _extract_video_id_from_url(row["video_url"]) or ""

        if not video_id:
            unmatched += 1
            continue

        query = session.query(Video).filter_by(platform_video_id=video_id)
        if profile_id:
            query = query.filter_by(profile_id=profile_id)
        video = query.first()

        if not video:
            q2 = session.query(Video).filter(Video.url.contains(video_id))
            if profile_id:
                q2 = q2.filter_by(profile_id=profile_id)
            video = q2.first()

        if not video:
            unmatched += 1
            continue

        if row.get("gmv") is not None:
            video.gmv = _parse_amount(str(row["gmv"])) if not isinstance(row["gmv"], (int, float)) else row["gmv"]
        if row.get("commission") is not None:
            val = row["commission"]
            video.commission = _parse_amount(str(val)) if not isinstance(val, (int, float)) else val
        if row.get("orders") is not None:
            video.orders = _parse_int(str(row["orders"])) if not isinstance(row["orders"], int) else row["orders"]
        if row.get("views") is not None and video.views is None:
            val = row["views"]
            video.views = _parse_int(str(val)) if not isinstance(val, int) else val

        updated += 1

    session.commit()
    return {"updated": updated, "unmatched": unmatched}


def import_gmv_text(session: Session, text: str, profile_id: int | None = None) -> dict:
    """
    Parse pasted text. Formats supported per line:
      video_id, gmv, commission
      video_id<TAB>gmv<TAB>commission
      video_id;gmv;commission
    Lines starting with # are ignored.
    """
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        for sep in ("\t", ",", ";"):
            if sep in line:
                parts = [p.strip() for p in line.split(sep)]
                break
        else:
            parts = line.split()

        if len(parts) < 2:
            continue

        video_id = parts[0]
        if "tiktok.com" in video_id:
            video_id = _extract_video_id_from_url(video_id) or video_id

        row = {"video_id": video_id}
        if len(parts) >= 2 and parts[1]:
            row["gmv"] = parts[1]
        if len(parts) >= 3 and parts[2]:
            row["commission"] = parts[2]
        if len(parts) >= 4 and parts[3]:
            row["orders"] = parts[3]
        rows.append(row)

    if not rows:
        raise ValueError("Tidak ada data valid. Format: video_id, gmv, komisi")

    return import_gmv_rows(session, rows, profile_id)


def import_gmv_csv(session: Session, csv_path: Path, profile_id: int | None = None) -> dict:
    """Import GMV data and match to tracked videos by video_id or URL."""
    if not csv_path.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {csv_path}")

    unmatched = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV kosong atau tidak punya header")

        col_video_id = _find_column(reader.fieldnames, COLUMN_ALIASES["video_id"])
        col_video_url = _find_column(reader.fieldnames, COLUMN_ALIASES["video_url"])
        col_gmv = _find_column(reader.fieldnames, COLUMN_ALIASES["gmv"])
        col_commission = _find_column(reader.fieldnames, COLUMN_ALIASES["commission"])
        col_orders = _find_column(reader.fieldnames, COLUMN_ALIASES["orders"])
        col_views = _find_column(reader.fieldnames, COLUMN_ALIASES["views"])

        if not col_gmv and not col_commission:
            raise ValueError(
                "CSV harus punya kolom GMV atau Commission. "
                f"Header ditemukan: {reader.fieldnames}"
            )

        parsed_rows = []
        for row in reader:
            video_id = row.get(col_video_id, "").strip() if col_video_id else ""
            video_url = row.get(col_video_url, "").strip() if col_video_url else ""

            if not video_id and video_url:
                video_id = _extract_video_id_from_url(video_url) or ""

            if not video_id:
                unmatched += 1
                continue

            parsed_rows.append({
                "video_id": video_id,
                "video_url": video_url,
                "gmv": row.get(col_gmv) if col_gmv else None,
                "commission": row.get(col_commission) if col_commission else None,
                "orders": row.get(col_orders) if col_orders else None,
                "views": row.get(col_views) if col_views else None,
            })

    result = import_gmv_rows(session, parsed_rows, profile_id)
    result["unmatched"] += unmatched
    return result