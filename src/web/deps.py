"""FastAPI dependencies."""

from pathlib import Path

from sqlalchemy.orm import Session

from ..db.models import init_db

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "affiliate.db"
DOWNLOAD_DIR = BASE_DIR / "data" / "downloads"
STATIC_DIR = BASE_DIR / "static"
COOKIES_DIR = BASE_DIR / "data" / "cookies"
COOKIES_DIR.mkdir(parents=True, exist_ok=True)


def get_session():
    session = init_db(DB_PATH)
    try:
        yield session
    finally:
        session.close()