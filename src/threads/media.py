"""Signed public media URLs for Threads video upload (Meta requires public URL)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional


def _signing_secret() -> bytes:
    key = os.getenv("SECRET_KEY", "dev-secret-change-me").encode()
    return key


def make_public_media_token(video_id: int, *, ttl_seconds: int = 7200) -> tuple[int, str]:
    exp = int(time.time()) + ttl_seconds
    payload = f"{video_id}:{exp}"
    sig = hmac.new(_signing_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return exp, sig


def verify_public_media_token(video_id: int, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    payload = f"{video_id}:{exp}"
    expected = hmac.new(_signing_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig or "")


def build_public_video_url(base_url: str, video_id: int) -> str:
    exp, sig = make_public_media_token(video_id)
    base = base_url.rstrip("/")
    return f"{base}/api/threads/public-media/{video_id}?exp={exp}&sig={sig}"