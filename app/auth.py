"""Application authentication: local sessions or trusted-proxy identity."""
from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import ipaddress
import os
import secrets
import sqlite3
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import DATA_DIR, _ensure_data_dir

AUTH_DB = DATA_DIR / "auth.db"
SESSION_COOKIE = "filterhelm_session"
POLL_HEADER = "x-filterhelm-poll"
current_user: ContextVar[str | None] = ContextVar("current_user", default=None)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "local"
    proxy_header: str = "Remote-User"
    proxy_users: frozenset[str] = frozenset()
    proxy_sources: tuple[str, ...] = ()
    cookie_secure: bool = True
    idle_seconds: int = 30 * 60
    absolute_seconds: int = 8 * 60 * 60

    @classmethod
    def from_env(cls) -> "AuthConfig":
        mode = os.environ.get("FILTERHELM_AUTH_MODE", "local").strip().lower()
        if mode not in {"local", "proxy"}:
            raise RuntimeError("FILTERHELM_AUTH_MODE must be 'local' or 'proxy'")
        users = frozenset(filter(None, (item.strip() for item in os.environ.get("FILTERHELM_PROXY_USERS", "").split(","))))
        sources = tuple(filter(None, (item.strip() for item in os.environ.get("FILTERHELM_PROXY_TRUSTED_IPS", "").split(","))))
        return cls(
            mode=mode,
            proxy_header=os.environ.get("FILTERHELM_PROXY_HEADER", "Remote-User").strip(),
            proxy_users=users,
            proxy_sources=sources,
            cookie_secure=_env_bool("FILTERHELM_COOKIE_SECURE", True),
        )


class AuthManager:
    def __init__(self, database: Path = AUTH_DB, config: AuthConfig | None = None):
        self.database = database
        self.config = config or AuthConfig.from_env()
        self.hasher = PasswordHasher()
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        self._initialized = False

    def connect(self) -> sqlite3.Connection:
        self.initialize()
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        _ensure_data_dir() if self.database == AUTH_DB else self.database.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileExistsError, OSError):
            os.close(os.open(self.database, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600))
        # SQLite sidecars (-wal / -journal) rely on the 0700 data directory.
        with sqlite3.connect(self.database) as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    remote_addr TEXT NOT NULL,
                    username TEXT,
                    attempted_at INTEGER NOT NULL
                );
                """
            )
            # issue #1: existing DBs predate the username column; login_attempts rows are ephemeral
            with contextlib.suppress(sqlite3.OperationalError):
                database.execute("ALTER TABLE login_attempts ADD COLUMN username TEXT")
        self._initialized = True
        try:
            os.chmod(self.database, 0o600)
        except OSError:
            pass

    def configured(self) -> bool:
        if self.config.mode == "proxy":
            return bool(self.config.proxy_header and self.config.proxy_sources)
        with self.connect() as database:
            return database.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def set_password(self, username: str, password: str) -> None:
        username = username.strip()
        if not username or len(username) > 100:
            raise ValueError("Username must contain 1-100 characters")
        if len(password) < 12:
            raise ValueError("Password must contain at least 12 characters")
        password_hash = self.hasher.hash(password)
        with self.connect() as database:
            database.execute(
                "INSERT INTO users(username, password_hash) VALUES(?, ?) "
                "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash",
                (username, password_hash),
            )
            database.execute("DELETE FROM sessions WHERE username = ?", (username,))

    def login_allowed(self, remote_addr: str, username: str, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        with self.connect() as database:
            database.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - 15 * 60,))
            # issue #1: gate on username — the client IP is the shared proxy's, so an IP gate here locks out every account
            count = database.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE username = ?", (username,)
            ).fetchone()[0]
        return count < 5

    def authenticate(self, username: str, password: str, remote_addr: str) -> bool:
        now = int(time.time())
        if not self.login_allowed(remote_addr, username, now):
            return False
        with self.connect() as database:
            row = database.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
        valid = False
        try:
            verified = self.hasher.verify(row["password_hash"] if row is not None else self._dummy_hash, password)
            valid = row is not None and verified
        except VerifyMismatchError:
            pass
        if valid:
            with self.connect() as database:
                database.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
            return True
        with self.connect() as database:
            database.execute(
                "INSERT INTO login_attempts(remote_addr, username, attempted_at) VALUES(?, ?, ?)",
                (remote_addr, username, now),
            )
        return False

    def create_session(self, username: str, now: int | None = None) -> str:
        now = int(time.time()) if now is None else now
        token = secrets.token_urlsafe(32)
        with self.connect() as database:
            database.execute(
                "INSERT INTO sessions VALUES(?, ?, ?, ?, ?)",
                (self._token_hash(token), username, now, now, now + self.config.absolute_seconds),
            )
        return token

    def session_user(
        self, token: str | None, now: int | None = None, *, refresh_last_seen: bool = True
    ) -> str | None:
        if not token:
            return None
        now = int(time.time()) if now is None else now
        token_hash = self._token_hash(token)
        with self.connect() as database:
            row = database.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
            if row is None or row["expires_at"] <= now or row["last_seen"] + self.config.idle_seconds <= now:
                database.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            if refresh_last_seen:
                database.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))
        return str(row["username"])

    def delete_session(self, token: str | None) -> None:
        if token:
            with self.connect() as database:
                database.execute("DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))

    def proxy_user(self, headers: dict[str, str], remote_addr: str) -> str | None:
        try:
            address = ipaddress.ip_address(remote_addr)
            trusted = any(address in ipaddress.ip_network(source, strict=False) for source in self.config.proxy_sources)
        except ValueError:
            trusted = False
        if not trusted:
            return None
        username = headers.get(self.config.proxy_header.lower(), "").strip()
        if not username or (self.config.proxy_users and username not in self.config.proxy_users):
            return None
        return username

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


def get_current_user() -> str | None:
    return current_user.get()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage FilterHelm authentication")
    parser.add_argument("command", choices=["set-password"])
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    AuthManager().set_password(args.username, password)
    print(f"Password set for {args.username}; existing sessions were revoked")


if __name__ == "__main__":
    main()
