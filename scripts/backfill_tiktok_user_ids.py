#!/usr/bin/env python3
"""
One-shot: backfill profiles.platform_user_id for all TikTok profiles.

Resolves numeric user id (preferred) or secUid via:
  1) TikWM using a sample video URL from DB
  2) yt-dlp on that video
  3) TikWM using profile @username URL

Usage:
  cd /Users/joefebrian/affiliate-video-tool
  .venv/bin/python scripts/backfill_tiktok_user_ids.py
  .venv/bin/python scripts/backfill_tiktok_user_ids.py --db data/affiliate.db
  .venv/bin/python scripts/backfill_tiktok_user_ids.py --force   # re-resolve even if set
  .venv/bin/python scripts/backfill_tiktok_user_ids.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cookies_util import resolve_cookies_file
from src.db.models import Profile, Video, init_db, run_migrations
from src.scrapers.tiktok_ids import resolve_tiktok_platform_user_id, tiktok_scan_input


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill TikTok platform_user_id")
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data" / "affiliate.db",
        help="Path to SQLite DB (default: data/affiliate.db)",
    )
    parser.add_argument(
        "--cookies-dir",
        type=Path,
        default=ROOT / "data" / "cookies",
        help="Cookies directory",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-resolve even when platform_user_id already set",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and print only — do not write DB",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.8,
        help="Seconds between profiles (rate limit courtesy)",
    )
    args = parser.parse_args()

    db_path: Path = args.db
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return 1

    run_migrations(db_path)
    session = init_db(db_path)
    cookies = resolve_cookies_file(args.cookies_dir, "tiktok")
    print(f"DB:      {db_path}")
    print(f"Cookies: {cookies or '(none)'}")
    print(f"Mode:    {'DRY-RUN' if args.dry_run else 'WRITE'}{' FORCE' if args.force else ''}")
    print()

    q = session.query(Profile).filter(Profile.platform == "tiktok")
    profiles = q.order_by(Profile.id.asc()).all()
    if not profiles:
        print("No TikTok profiles found.")
        return 0

    ok = skip = fail = 0
    for i, profile in enumerate(profiles, 1):
        username = profile.username
        existing = (profile.platform_user_id or "").strip()
        if existing and not args.force:
            print(f"[{i}/{len(profiles)}] @{username}  SKIP already={existing}")
            skip += 1
            continue

        sample = (
            session.query(Video)
            .filter(Video.profile_id == profile.id)
            .order_by(Video.id.desc())
            .first()
        )
        sample_url = None
        if sample:
            sample_url = sample.url
            if sample_url and "://" not in sample_url and sample.platform_video_id:
                sample_url = (
                    f"https://www.tiktok.com/@{username}/video/{sample.platform_video_id}"
                )
            elif sample.platform_video_id and "/video/" not in (sample_url or ""):
                sample_url = (
                    f"https://www.tiktok.com/@{username}/video/{sample.platform_video_id}"
                )

        print(
            f"[{i}/{len(profiles)}] @{username}  "
            f"videos={profile.video_count}  sample={'(yes)' if sample_url else '(no)'} …",
            end=" ",
            flush=True,
        )

        try:
            resolved = resolve_tiktok_platform_user_id(
                username=username,
                sample_video_url=sample_url,
                cookies_file=cookies,
            )
        except Exception as e:
            print(f"FAIL {e}")
            fail += 1
            if args.sleep:
                time.sleep(args.sleep)
            continue

        if not resolved:
            print("FAIL no id resolved")
            fail += 1
            if args.sleep:
                time.sleep(args.sleep)
            continue

        scan = tiktok_scan_input(username, resolved)
        print(f"OK {resolved}  →  {scan}")
        if not args.dry_run:
            profile.platform_user_id = resolved
            # Keep public URL as @handle
            profile.url = f"https://www.tiktok.com/@{username}"
            session.commit()
        ok += 1
        if args.sleep and i < len(profiles):
            time.sleep(args.sleep)

    print()
    print(f"Done. ok={ok} skip={skip} fail={fail} total={len(profiles)}")
    if args.dry_run:
        print("(dry-run — nothing written)")
    return 0 if fail == 0 or ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
