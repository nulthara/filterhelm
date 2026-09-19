from __future__ import annotations

from http.cookies import SimpleCookie

from fastapi.responses import JSONResponse

from app.auth import AuthManager, POLL_HEADER, SESSION_COOKIE, current_user


class AuthenticationMiddleware:
    def __init__(self, app, manager: AuthManager):
        self.app = app
        self.manager = manager

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header_values: dict[str, list[str]] = {}
        for key, value in scope["headers"]:
            header_values.setdefault(key.decode("latin-1").lower(), []).append(value.decode("latin-1"))
        headers = {key: values[-1] for key, values in header_values.items()}
        path = scope.get("path", "")
        public = path in {"/", "/app", "/api/auth/login", "/api/auth/status"} or path.startswith("/app/")
        user = None
        if self.manager.config.mode == "proxy":
            identity_values = header_values.get(self.manager.config.proxy_header.lower(), [])
            client = scope.get("client")
            remote_addr = client[0] if client else ""
            user = self.manager.proxy_user(headers, remote_addr) if len(identity_values) == 1 else None
        else:
            cookies = SimpleCookie(headers.get("cookie", ""))
            morsel = cookies.get(SESSION_COOKIE)
            is_poll = headers.get(POLL_HEADER, "") == "1"
            user = self.manager.session_user(
                morsel.value if morsel else None, refresh_last_seen=not is_poll
            )
        if user is None and not public:
            response = JSONResponse(status_code=401, content={"detail": "Authentication required"})
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["user"] = user
        token = current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(token)
