import asyncio
import sqlite3

from app.auth import AuthConfig, AuthManager
from app.auth_middleware import AuthenticationMiddleware


def local_manager(tmp_path):
    return AuthManager(tmp_path / "auth.db", AuthConfig(mode="local", cookie_secure=False))


def test_local_password_is_argon2_hashed_and_authenticates(tmp_path):
    manager = local_manager(tmp_path)
    manager.set_password("admin", "a sufficiently long password")

    with manager.connect() as database:
        stored = database.execute("SELECT password_hash FROM users WHERE username = 'admin'").fetchone()[0]

    assert stored.startswith("$argon2id$")
    assert "sufficiently long" not in stored
    assert manager.authenticate("admin", "a sufficiently long password", "127.0.0.1") is True
    assert manager.authenticate("admin", "wrong password", "127.0.0.1") is False


def test_new_auth_database_is_private(tmp_path):
    manager = local_manager(tmp_path)
    manager.initialize()

    assert manager.database.stat().st_mode & 0o777 == 0o600


def test_password_change_revokes_existing_sessions(tmp_path):
    manager = local_manager(tmp_path)
    manager.set_password("admin", "a sufficiently long password")
    token = manager.create_session("admin", now=100)
    assert manager.session_user(token, now=101) == "admin"

    manager.set_password("admin", "a different long password")

    assert manager.session_user(token, now=102) is None


def test_sessions_enforce_idle_and_absolute_expiration(tmp_path):
    config = AuthConfig(mode="local", cookie_secure=False, idle_seconds=10, absolute_seconds=20)
    manager = AuthManager(tmp_path / "auth.db", config)
    idle_token = manager.create_session("admin", now=100)
    absolute_token = manager.create_session("admin", now=100)

    assert manager.session_user(idle_token, now=111) is None
    assert manager.session_user(absolute_token, now=105) == "admin"
    assert manager.session_user(absolute_token, now=120) is None


def test_session_user_skips_last_seen_refresh_when_requested(tmp_path):
    manager = local_manager(tmp_path)
    token = manager.create_session("admin", now=100)

    assert manager.session_user(token, now=105, refresh_last_seen=False) == "admin"
    with manager.connect() as database:
        last_seen = database.execute(
            "SELECT last_seen FROM sessions WHERE token_hash = ?", (manager._token_hash(token),)
        ).fetchone()[0]
    assert last_seen == 100

    assert manager.session_user(token, now=106) == "admin"
    with manager.connect() as database:
        last_seen = database.execute(
            "SELECT last_seen FROM sessions WHERE token_hash = ?", (manager._token_hash(token),)
        ).fetchone()[0]
    assert last_seen == 106


def test_session_user_still_enforces_idle_expiry_when_refresh_skipped(tmp_path):
    manager = AuthManager(
        tmp_path / "auth.db",
        AuthConfig(mode="local", cookie_secure=False, idle_seconds=10),
    )
    token = manager.create_session("admin", now=100)

    assert manager.session_user(token, now=105, refresh_last_seen=False) == "admin"
    assert manager.session_user(token, now=111, refresh_last_seen=False) is None


def test_login_attempts_are_throttled(tmp_path):
    manager = local_manager(tmp_path)
    manager.set_password("admin", "a sufficiently long password")

    for _ in range(5):
        assert manager.authenticate("admin", "wrong", "192.0.2.1") is False

    assert manager.login_allowed("192.0.2.1", "admin") is False


def test_login_throttle_is_per_username_not_shared_ip(tmp_path):
    manager = local_manager(tmp_path)
    manager.set_password("alice", "a sufficiently long password")
    manager.set_password("bob", "a sufficiently long password")

    for _ in range(5):
        assert manager.authenticate("alice", "wrong", "192.0.2.1") is False

    assert manager.login_allowed("192.0.2.1", "alice") is False
    assert manager.login_allowed("192.0.2.1", "bob") is True


def test_existing_login_attempts_table_is_migrated(tmp_path):
    database_path = tmp_path / "auth.db"
    with sqlite3.connect(database_path) as database:
        database.execute(
            "CREATE TABLE login_attempts (remote_addr TEXT NOT NULL, attempted_at INTEGER NOT NULL)"
        )
    manager = local_manager(tmp_path)
    manager.set_password("admin", "a sufficiently long password")

    assert manager.authenticate("admin", "wrong", "192.0.2.1") is False
    assert manager.login_allowed("192.0.2.1", "admin") is True


def test_proxy_mode_requires_header_and_honors_allowlist(tmp_path):
    manager = AuthManager(
        tmp_path / "auth.db",
        AuthConfig(
            mode="proxy",
            proxy_header="X-Forwarded-User",
            proxy_users=frozenset({"kyrnveil"}),
            proxy_sources=("192.0.2.10",),
        ),
    )

    assert manager.proxy_user({}, "192.0.2.10") is None
    assert manager.proxy_user({"x-forwarded-user": "someone"}, "192.0.2.10") is None
    assert manager.proxy_user({"x-forwarded-user": "kyrnveil"}, "198.51.100.1") is None
    assert manager.proxy_user({"x-forwarded-user": "kyrnveil"}, "192.0.2.10") == "kyrnveil"


def middleware_status(manager, path, headers=None):
    messages = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(key.encode(), value.encode()) for key, value in (headers or [])],
        "client": ("192.0.2.10", 1234),
    }
    asyncio.run(AuthenticationMiddleware(downstream, manager)(scope, receive, send))
    return next(item["status"] for item in messages if item["type"] == "http.response.start")


def test_authentication_middleware_protects_api_but_serves_login_app(tmp_path):
    manager = local_manager(tmp_path)

    assert middleware_status(manager, "/api/instances") == 401
    assert middleware_status(manager, "/app/") == 204
    assert middleware_status(manager, "/api/auth/status") == 204


def test_authentication_middleware_matches_only_app_path_boundary(tmp_path):
    manager = local_manager(tmp_path)

    assert middleware_status(manager, "/apple") == 401
    assert middleware_status(manager, "/app-config") == 401
    assert middleware_status(manager, "/app") == 204
    assert middleware_status(manager, "/app/") == 204
    assert middleware_status(manager, "/app/assets/x.js") == 204


def test_authentication_middleware_poll_header_skips_last_seen_refresh(tmp_path, monkeypatch):
    manager = local_manager(tmp_path)
    token = manager.create_session("admin", now=100)
    cookie = f"pihole_manager_session={token}"
    monkeypatch.setattr("app.auth.time.time", lambda: 105)

    assert middleware_status(
        manager,
        "/api/status",
        [("cookie", cookie), ("x-pihole-manager-poll", "1")],
    ) == 204
    with manager.connect() as database:
        last_seen = database.execute(
            "SELECT last_seen FROM sessions WHERE token_hash = ?", (manager._token_hash(token),)
        ).fetchone()[0]
    assert last_seen == 100

    monkeypatch.setattr("app.auth.time.time", lambda: 106)
    assert middleware_status(manager, "/api/status", [("cookie", cookie)]) == 204
    with manager.connect() as database:
        last_seen = database.execute(
            "SELECT last_seen FROM sessions WHERE token_hash = ?", (manager._token_hash(token),)
        ).fetchone()[0]
    assert last_seen == 106


def test_authentication_middleware_poll_header_does_not_resurrect_expired_session(
    tmp_path, monkeypatch
):
    manager = AuthManager(
        tmp_path / "auth.db",
        AuthConfig(mode="local", cookie_secure=False, idle_seconds=10),
    )
    poll_token = manager.create_session("admin", now=100)
    regular_token = manager.create_session("admin", now=100)
    monkeypatch.setattr("app.auth.time.time", lambda: 111)

    assert middleware_status(
        manager,
        "/api/status",
        [
            ("cookie", f"pihole_manager_session={poll_token}"),
            ("x-pihole-manager-poll", "1"),
        ],
    ) == 401
    assert middleware_status(
        manager,
        "/api/status",
        [("cookie", f"pihole_manager_session={regular_token}")],
    ) == 401


def test_proxy_middleware_rejects_duplicate_identity_headers(tmp_path):
    manager = AuthManager(
        tmp_path / "auth.db",
        AuthConfig(mode="proxy", proxy_header="X-Forwarded-User", proxy_sources=("192.0.2.0/24",)),
    )

    assert middleware_status(
        manager,
        "/api/instances",
        [("x-forwarded-user", "kyrnveil"), ("x-forwarded-user", "attacker")],
    ) == 401
