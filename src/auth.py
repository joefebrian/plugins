"""Authentication — bcrypt passwords, rate limiting, secure sessions."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

import bcrypt

BCRYPT_ROUNDS = 12

# Rate limit: max attempts per IP in window
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 900  # 15 menit
_lockouts: dict[str, list[float]] = {}

# Dummy hash for timing-safe failed username checks
_DUMMY_HASH = bcrypt.hashpw(b"invalid-placeholder", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _load_dotenv(base_dir: Path):
    env_path = base_dir / ".env"
    if not env_path.exists():
        example = base_dir / ".env.example"
        if example.exists():
            env_path.write_text(example.read_text())
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        pass


def get_or_create_secret(base_dir: Path) -> str:
    secret_file = base_dir / "data" / ".secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)

    env_secret = os.getenv("SECRET_KEY", "").strip()
    if env_secret and env_secret != "change-this-to-a-long-random-string":
        return env_secret

    if secret_file.exists():
        return secret_file.read_text().strip()

    generated = secrets.token_hex(32)
    secret_file.write_text(generated)
    secret_file.chmod(0o600)
    return generated


class AuthStore:
    def __init__(self, auth_file: Path):
        self.auth_file = auth_file
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.auth_file.exists():
            return {}
        try:
            return json.loads(self.auth_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict):
        self.auth_file.write_text(json.dumps(data, indent=2))
        self.auth_file.chmod(0o600)

    def initialize(self, username: str, password: str) -> None:
        data = self._load()
        if data.get("password_hash"):
            return
        data["username"] = username
        data["password_hash"] = _hash_password(password)
        self._save(data)

    def get_username(self) -> Optional[str]:
        return self._load().get("username")

    def verify(self, username: str, password: str) -> bool:
        data = self._load()
        stored_hash = data.get("password_hash")
        if not stored_hash:
            bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
            return False
        if username != data.get("username"):
            _verify_password(password, stored_hash)
            return False
        return _verify_password(password, stored_hash)

    def change_password(self, current: str, new_password: str) -> bool:
        data = self._load()
        stored_hash = data.get("password_hash")
        if not stored_hash:
            return False
        if not _verify_password(current, stored_hash):
            return False
        if len(new_password) < 8:
            raise ValueError("Password baru minimal 8 karakter")
        data["password_hash"] = _hash_password(new_password)
        self._save(data)
        return True


def check_rate_limit(client_ip: str) -> Optional[str]:
    now = time.time()
    attempts = _lockouts.get(client_ip, [])
    attempts = [t for t in attempts if now - t < LOGIN_WINDOW_SECONDS]
    _lockouts[client_ip] = attempts

    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        remaining = int(LOGIN_WINDOW_SECONDS - (now - attempts[0]))
        return f"Terlalu banyak percobaan login. Coba lagi dalam {remaining // 60 + 1} menit."

    return None


def record_failed_login(client_ip: str):
    now = time.time()
    attempts = _lockouts.get(client_ip, [])
    attempts = [t for t in attempts if now - t < LOGIN_WINDOW_SECONDS]
    attempts.append(now)
    _lockouts[client_ip] = attempts


def clear_rate_limit(client_ip: str):
    _lockouts.pop(client_ip, None)


def setup_auth(base_dir: Path) -> tuple[AuthStore, str]:
    _load_dotenv(base_dir)
    secret = get_or_create_secret(base_dir)
    store = AuthStore(base_dir / "data" / "auth.json")

    username = os.getenv("AUTH_USERNAME", "admin").strip()
    password = os.getenv("AUTH_PASSWORD", "Affiliate@2026").strip()
    store.initialize(username, password)

    return store, secret