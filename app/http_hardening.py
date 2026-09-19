"""Inbound HTTP size, rate, host, and browser-security controls."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import os
import threading
import time

from fastapi.responses import JSONResponse


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class HardeningConfig:
    max_body_bytes: int = 1024 * 1024
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    trusted_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")

    @classmethod
    def from_environment(cls) -> "HardeningConfig":
        hosts = tuple(
            value.strip().lower()
            for value in os.environ.get(
                "PIHOLE_MANAGER_TRUSTED_HOSTS", "localhost,127.0.0.1,testserver"
            ).split(",")
            if value.strip()
        )
        if not hosts:
            raise RuntimeError("PIHOLE_MANAGER_TRUSTED_HOSTS must contain at least one host")
        return cls(
            max_body_bytes=_positive_int("PIHOLE_MANAGER_MAX_BODY_BYTES", 1024 * 1024),
            rate_limit_requests=_positive_int("PIHOLE_MANAGER_RATE_LIMIT_REQUESTS", 120),
            rate_limit_window_seconds=_positive_int("PIHOLE_MANAGER_RATE_LIMIT_WINDOW_SECONDS", 60),
            trusted_hosts=hosts,
        )


class _BodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
        transfer_encodings = [value for key, value in scope["headers"] if key.lower() == b"transfer-encoding"]
        if lengths and transfer_encodings:
            await self._reject(scope, receive, send, 400, "Conflicting request framing headers")
            return
        if len(lengths) > 1:
            await self._reject(scope, receive, send, 400, "Invalid Content-Length header")
            return
        if lengths:
            if not lengths[0].isdigit():
                await self._reject(scope, receive, send, 400, "Invalid Content-Length header")
                return
            declared = int(lengths[0])
            if declared > self.max_bytes:
                await self._reject(scope, receive, send, 413, "Request body too large")
                return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def guarded_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            # issue #12: a second http.response.start is an ASGI protocol error — let the server abort instead
            if response_started:
                raise
            await self._reject(scope, receive, send, 413, "Request body too large")

    @staticmethod
    async def _reject(scope, receive, send, status: int, detail: str):
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


class RateLimitMiddleware:
    """A process-local sliding-window limit for dynamic HTTP requests."""

    def __init__(self, app, requests: int, window_seconds: int, clock=time.monotonic):
        self.app = app
        self.requests = requests
        self.window_seconds = window_seconds
        self.clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_cleanup = self.clock()

    def _key(self, scope) -> str:
        user = scope.get("state", {}).get("user")
        if user:
            return f"user:{user}"
        client = scope.get("client")
        return f"client:{client[0] if client else 'unknown'}"

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or path == "/app" or path.startswith("/app/"):
            await self.app(scope, receive, send)
            return
        now = self.clock()
        key = self._key(scope)
        with self._lock:
            if now - self._last_cleanup >= self.window_seconds:
                cutoff = now - self.window_seconds
                self._events = defaultdict(
                    deque,
                    (item for item in self._events.items() if item[1] and item[1][-1] > cutoff),
                )
                self._last_cleanup = now
            events = self._events[key]
            cutoff = now - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            allowed = len(events) < self.requests
            if allowed:
                events.append(now)
        if not allowed:
            response = JSONResponse(
                {"detail": "Too many requests; try again later"},
                status_code=429,
                headers={"Retry-After": str(self.window_seconds)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    HEADERS = (
        (b"content-security-policy", b"default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"referrer-policy", b"no-referrer"),
        (b"strict-transport-security", b"max-age=31536000"),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {key.lower() for key, _ in message.setdefault("headers", [])}
                message["headers"].extend((key, value) for key, value in self.HEADERS if key not in names)
            await send(message)

        await self.app(scope, receive, send_with_headers)
