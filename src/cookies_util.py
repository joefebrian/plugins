"""Per-platform cookie storage, validation, and import helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CookiePlatform:
    id: str
    label: str
    domains: tuple[str, ...]
    filename: str
    legacy_filenames: tuple[str, ...] = ()
    session_cookie_names: tuple[str, ...] = ()
    export_site: str = ""
    hint: str = ""


COOKIE_PLATFORMS: tuple[CookiePlatform, ...] = (
    CookiePlatform(
        id="tiktok",
        label="TikTok",
        domains=("tiktok.com",),
        filename="tiktok.txt",
        legacy_filenames=("tiktok_only.txt",),
        session_cookie_names=("sessionid", "sid_tt"),
        export_site="tiktok.com",
        hint="Login di tiktok.com → export cookies Netscape (.txt).",
    ),
    CookiePlatform(
        id="instagram",
        label="Instagram",
        domains=("instagram.com",),
        filename="instagram.txt",
        session_cookie_names=("sessionid", "ds_user_id"),
        export_site="instagram.com",
        hint="Login di instagram.com → export cookies. Wajib untuk scan profil & download.",
    ),
    CookiePlatform(
        id="kuaishou",
        label="Kuaishou",
        domains=("kuaishou.com", "gifshow.com", "chenzhongtech.com"),
        filename="kuaishou.txt",
        session_cookie_names=("userId", "kpf", "clientid"),
        export_site="kuaishou.com",
        hint="Login di kuaishou.com → export cookies untuk scan profil & download.",
    ),
    CookiePlatform(
        id="rednote",
        label="RedNote",
        domains=("rednote.com", "xiaohongshu.com"),
        filename="rednote.txt",
        session_cookie_names=("a1", "web_session"),
        export_site="rednote.com",
        hint="Login di rednote.com atau xiaohongshu.com → export cookies untuk scan & download.",
    ),
)

_PLATFORM_BY_ID = {p.id: p for p in COOKIE_PLATFORMS}
_MASTER_EXPORT = "cookies.txt"


def get_cookie_platform(platform: str) -> CookiePlatform:
    key = (platform or "").strip().lower()
    if key not in _PLATFORM_BY_ID:
        supported = ", ".join(_PLATFORM_BY_ID)
        raise ValueError(f"Platform cookies tidak dikenal: {platform}. Pilih: {supported}")
    return _PLATFORM_BY_ID[key]


def _iter_cookie_lines(path: Path) -> Iterable[tuple[str, list[str]]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        yield line, line.split("\t")


def _domain_matches(domain: str, needles: tuple[str, ...]) -> bool:
    cleaned = domain.lstrip(".").lower()
    return any(needle in cleaned for needle in needles)


def filter_cookies_by_domains(src: Path, dest: Path, domains: tuple[str, ...]) -> int:
    """Keep only cookies for the given domains. Returns data line count."""
    if not src.exists():
        return 0

    kept: list[str] = []
    for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#"):
            kept.append(line)
            continue
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 1 and _domain_matches(parts[0], domains):
            kept.append(line)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return len([line for line in kept if not line.startswith("#") and line.strip()])


def filter_tiktok_cookies(src: Path, dest: Path) -> int:
    """Backward-compatible TikTok-only filter."""
    platform = get_cookie_platform("tiktok")
    return filter_cookies_by_domains(src, dest, platform.domains)


def _platform_file_paths(cookies_dir: Path, platform: CookiePlatform) -> list[Path]:
    paths = [cookies_dir / platform.filename]
    paths.extend(cookies_dir / name for name in platform.legacy_filenames)
    return paths


def platform_cookie_file(cookies_dir: Path, platform: str) -> Path:
    meta = get_cookie_platform(platform)
    return cookies_dir / meta.filename


def resolve_cookies_file(cookies_dir: Path, platform: str) -> str | None:
    """Return best cookie file path for a platform, if any."""
    meta = get_cookie_platform(platform)
    cookies_dir = Path(cookies_dir)
    for candidate in _platform_file_paths(cookies_dir, meta):
        if candidate.exists() and candidate.stat().st_size > 0:
            return str(candidate)

    master = cookies_dir / _MASTER_EXPORT
    if master.exists():
        filtered = platform_cookie_file(cookies_dir, platform)
        count = filter_cookies_by_domains(master, filtered, meta.domains)
        if count > 0:
            return str(filtered)
    return None


def validate_platform_cookies(cookies_dir: Path, platform: str) -> dict:
    meta = get_cookie_platform(platform)
    cookies_dir = Path(cookies_dir)
    path_str = resolve_cookies_file(cookies_dir, platform)
    if not path_str:
        return {
            "platform": meta.id,
            "label": meta.label,
            "ok": False,
            "message": f"Belum upload cookies {meta.label}",
            "count": 0,
            "export_site": meta.export_site,
            "hint": meta.hint,
            "updated_at": None,
        }

    path = Path(path_str)
    count = 0
    has_session = False
    for _, parts in _iter_cookie_lines(path):
        if len(parts) < 7:
            continue
        if not _domain_matches(parts[0], meta.domains):
            continue
        count += 1
        if parts[5] in meta.session_cookie_names:
            has_session = True

    if count == 0:
        return {
            "platform": meta.id,
            "label": meta.label,
            "ok": False,
            "message": f"Tidak ada cookie {meta.label} di file",
            "count": 0,
            "export_site": meta.export_site,
            "hint": meta.hint,
            "updated_at": None,
        }

    if meta.session_cookie_names:
        ok = has_session
        if ok:
            message = f"Cookies {meta.label} OK"
        elif meta.id == "rednote":
            message = "Cookies ada — disarankan login ulang jika scan gagal (butuh a1 + web_session)"
            ok = True
        else:
            message = f"Cookies {meta.label} ada tapi sesi login belum terdeteksi"
    else:
        ok = count > 0
        message = f"Cookies {meta.label} OK" if ok else f"Cookies {meta.label} kosong"

    updated_at = None
    try:
        updated_at = datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z"
    except OSError:
        pass

    return {
        "platform": meta.id,
        "label": meta.label,
        "ok": ok,
        "message": message,
        "count": count,
        "has_session": has_session,
        "export_site": meta.export_site,
        "hint": meta.hint,
        "updated_at": updated_at,
        "path": str(path),
    }


def all_platforms_status(cookies_dir: Path) -> list[dict]:
    return [validate_platform_cookies(cookies_dir, platform.id) for platform in COOKIE_PLATFORMS]


def cookies_summary(cookies_dir: Path) -> dict:
    platforms = all_platforms_status(cookies_dir)
    uploaded = [p for p in platforms if p["count"] > 0]
    ready = [p for p in platforms if p["ok"]]
    if not uploaded:
        message = "Belum upload cookies"
        ok = False
    else:
        message = f"{len(ready)}/{len(platforms)} platform cookies siap"
        ok = len(ready) > 0
    return {
        "ok": ok,
        "message": message,
        "ready_count": len(ready),
        "uploaded_count": len(uploaded),
        "total": len(platforms),
        "platforms": platforms,
    }


def import_cookies_export(cookies_dir: Path, src: Path) -> dict[str, dict]:
    """Split a browser export into per-platform cookie files."""
    cookies_dir = Path(cookies_dir)
    cookies_dir.mkdir(parents=True, exist_ok=True)
    master = cookies_dir / _MASTER_EXPORT
    if src.resolve() != master.resolve():
        master.write_bytes(src.read_bytes())

    results: dict[str, dict] = {}
    for platform in COOKIE_PLATFORMS:
        dest = cookies_dir / platform.filename
        count = filter_cookies_by_domains(master, dest, platform.domains)
        status = validate_platform_cookies(cookies_dir, platform.id)
        results[platform.id] = {
            "count": count,
            "ok": status["ok"],
            "message": status["message"],
        }
    return results


def save_platform_cookies(cookies_dir: Path, platform: str, src: Path) -> dict:
    meta = get_cookie_platform(platform)
    cookies_dir = Path(cookies_dir)
    cookies_dir.mkdir(parents=True, exist_ok=True)
    dest = cookies_dir / meta.filename
    count = filter_cookies_by_domains(src, dest, meta.domains)
    if count == 0:
        raise ValueError(
            f"Tidak ada cookie {meta.label} di file ini. Export dari {meta.export_site} setelah login."
        )
    status = validate_platform_cookies(cookies_dir, platform)
    return {"count": count, **status}


def delete_platform_cookies(cookies_dir: Path, platform: str) -> bool:
    meta = get_cookie_platform(platform)
    cookies_dir = Path(cookies_dir)
    removed = False
    for candidate in _platform_file_paths(cookies_dir, meta):
        if candidate.exists():
            candidate.unlink()
            removed = True
    return removed


def validate_tiktok_cookies(path: Path) -> dict:
    """Backward-compatible TikTok validator."""
    count = 0
    has_session = False
    if not path.exists():
        return {"ok": False, "message": "Belum upload cookies", "count": 0}

    for _, parts in _iter_cookie_lines(path):
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