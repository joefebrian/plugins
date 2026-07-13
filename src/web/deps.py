"""FastAPI dependencies."""

import os
from pathlib import Path
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from ..db.models import init_db

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "affiliate.db"
DOWNLOAD_DIR = BASE_DIR / "data" / "downloads"
STATIC_DIR = BASE_DIR / "static"
COOKIES_DIR = BASE_DIR / "data" / "cookies"
COOKIES_DIR.mkdir(parents=True, exist_ok=True)


def resolve_public_base_url(request: Optional[Request] = None) -> str:
    """Public HTTPS URL for OAuth callbacks and signed media links."""
    explicit = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        return f"https://{railway_domain}"
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://localhost:8080"


def get_session():
    session = init_db(DB_PATH)
    try:
        yield session
    finally:
        session.close()