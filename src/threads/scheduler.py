"""Background auto-post scheduler for Threads."""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_started = False


def start_autopost_scheduler(base_url: str = "http://localhost:8080") -> None:
    global _started
    if _started or os.getenv("THREADS_AUTOPOST_DISABLE", "").lower() in ("1", "true", "yes"):
        return
    _started = True

    def _loop():
        from ..db.models import init_db
        from ..web.deps import DB_PATH
        from .autopost import run_due_autoposts

        url = os.getenv("PUBLIC_BASE_URL", base_url).strip()
        while True:
            try:
                session = init_db(DB_PATH)
                try:
                    results = run_due_autoposts(session, base_url=url)
                    posted = [r for r in results if r.get("posted")]
                    if posted:
                        logger.info("Threads autopost: %s posts", len(posted))
                finally:
                    session.close()
            except Exception as e:
                logger.warning("Threads autopost tick error: %s", e)
            time.sleep(60)

    t = threading.Thread(target=_loop, name="threads-autopost", daemon=True)
    t.start()
    logger.info("Threads auto-post scheduler started")