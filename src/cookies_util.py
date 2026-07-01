"""Cookie file helpers."""

from __future__ import annotations

from pathlib import Path


def filter_tiktok_cookies(src: Path, dest: Path) -> int:
    """Keep only tiktok.com cookies. Returns line count."""
    if not src.exists():
        return 0

    lines = src.read_text(encoding="utf-8", errors="ignore").splitlines()
    kept = []
    for line in lines:
        if line.startswith("#"):
            kept.append(line)
            continue
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 1 and "tiktok.com" in parts[0].lower():
            kept.append(line)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return len([l for l in kept if not l.startswith("#") and l.strip()])


def validate_tiktok_cookies(path: Path) -> dict:
    if not path.exists():
        return {"ok": False, "message": "Belum upload cookies", "count": 0}

    count = 0
    has_session = False
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and "tiktok.com" in parts[0].lower():
            count += 1
            if parts[5] in ("sessionid", "sid_tt"):
                has_session = True

    if count == 0:
        return {
            "ok": False,
            "message": "Tidak ada cookie TikTok. Export dari tiktok.com (bukan situs lain).",
            "count": 0,
        }

    return {
        "ok": has_session,
        "message": "Cookies TikTok OK" if has_session else "Cookies ada tapi tanpa sessionid — login TikTok dulu",
        "count": count,
        "has_session": has_session,
    }