"""Background auto-post scheduler for Threads (low idle cost)."""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_started = False


def _tick_seconds() -> int:
    """How often to wake for due autoposts.

    Railway default 300s — avoids opening the DB every minute when idle.
    Override with THREADS_AUTOPOST_TICK_SECONDS.
    """
    raw = os.getenv("THREADS_AUTOPOST_TICK_SECONDS", "").strip()
    if raw.isdigit():
        return max(30, int(raw))
    # On Railway prefer longer idle ticks
    if os.getenv("RAILWAY_ENVIRONMENT"):
        return 300
    return 60


def _has_enabled_autopost(session) -> bool:
    """Skip heavy work when no Threads autopost is enabled."""
    try:
        from ..db.models import ThreadsAutoPostConfig
    except Exception:
        # Older DBs / missing model — fall through to full run
        return True
    try:
        return (
            session.query(ThreadsAutoPostConfig.id)
            .filter(ThreadsAutoPostConfig.enabled.is_(True))
            .limit(1)
            .first()
            is not None
        )
    except Exception:
        return True


def start_autopost_scheduler(base_url: str = "http://localhost:8080") -> None:
    global _started
    if _started or os.getenv("THREADS_AUTOPOST_DISABLE", "").lower() in ("1", "true", "yes"):
        return
    _started = True

    interval = _tick_seconds()

    def _loop():
        from ..db.models import init_db
        from ..web.deps import DB_PATH
        from .autopost import run_due_autoposts

        url = os.getenv("PUBLIC_BASE_URL", base_url).strip()
        while True:
            try:
                session = init_db(DB_PATH)
                try:
                    if not _has_enabled_autopost(session):
                        # No config enabled — long sleep, almost zero cost
                        pass
                    else:
                        results = run_due_autoposts(session, base_url=url)
                        posted = [r for r in results if r.get("posted")]
                        if posted:
                            logger.info("Threads autopost: %s posts", len(posted))
                finally:
                    session.close()
            except Exception as e:
                logger.warning("Threads autopost tick error: %s", e)
            time.sleep(interval)

    t = threading.Thread(target=_loop, name="threads-autopost", daemon=True)
    t.start()
    logger.info(
        "Threads auto-post scheduler started (tick=%ss)",
        interval,
    )
