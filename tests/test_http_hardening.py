import asyncio

import pytest

from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.http_hardening import (
    _BodyTooLarge,
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
)


def run_middleware(middleware, *, path="/api/test", headers=None, chunks=None, client="192.0.2.10"):
    messages = []
    incoming = list(chunks or [b""])

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        body = incoming.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(incoming)}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or [])],
        "client": (client, 1234),
        "state": {},
    }
    asyncio.run(middleware(downstream)(scope, receive, send))
    return messages


def status(messages):
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def test_body_limit_rejects_declared_oversize_body():
    messages = run_middleware(
        lambda app: RequestBodyLimitMiddleware(app, max_bytes=4),
        headers=[("content-length", "5")],
        chunks=[b"12345"],
    )
    assert status(messages) == 413


def test_body_limit_counts_streamed_body_without_content_length():
    messages = run_middleware(
        lambda app: RequestBodyLimitMiddleware(app, max_bytes=4),
        chunks=[b"12", b"345"],
    )
    assert status(messages) == 413


def test_body_limit_allows_body_at_limit():
    messages = run_middleware(
        lambda app: RequestBodyLimitMiddleware(app, max_bytes=4),
        headers=[("content-length", "4")],
        chunks=[b"12", b"34"],
    )
    assert status(messages) == 204


def test_body_limit_does_not_start_a_second_response_after_stream_started():
    messages = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()

    async def receive():
        return {"type": "http.request", "body": b"12345", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/test", "headers": []}
    with pytest.raises(_BodyTooLarge):
        asyncio.run(RequestBodyLimitMiddleware(downstream, max_bytes=4)(scope, receive, send))

    assert [message["type"] for message in messages] == ["http.response.start"]


def test_body_limit_rejects_ambiguous_request_framing():
    messages = run_middleware(
        lambda app: RequestBodyLimitMiddleware(app, max_bytes=4),
        headers=[("content-length", "4"), ("transfer-encoding", "chunked")],
        chunks=[b"1234"],
    )
    assert status(messages) == 400


def test_rate_limit_is_per_authenticated_user_and_returns_retry_after():
    clock = lambda: 100.0

    def build(app):
        limiter = RateLimitMiddleware(app, requests=2, window_seconds=60, clock=clock)

        async def identify(scope, receive, send):
            scope["state"]["user"] = "admin"
            await limiter(scope, receive, send)

        return identify

    # Reuse one limiter instance across requests.
    captured = {}

    def persistent(app):
        if "middleware" not in captured:
            captured["middleware"] = build(app)
        return captured["middleware"]

    assert status(run_middleware(persistent)) == 204
    assert status(run_middleware(persistent, client="198.51.100.20")) == 204
    messages = run_middleware(persistent)
    assert status(messages) == 429
    headers = dict(next(message["headers"] for message in messages if message["type"] == "http.response.start"))
    assert headers[b"retry-after"] == b"60"


def test_rate_limit_excludes_application_assets():
    limiter = None

    def build(app):
        nonlocal limiter
        limiter = limiter or RateLimitMiddleware(app, requests=1, window_seconds=60, clock=lambda: 100.0)
        return limiter

    assert status(run_middleware(build, path="/app/")) == 204
    assert status(run_middleware(build, path="/app/assets/app.js")) == 204


def test_trusted_host_rejects_unconfigured_host():
    messages = run_middleware(
        lambda app: TrustedHostMiddleware(app, allowed_hosts=["manager.example"], www_redirect=False),
        headers=[("host", "attacker.example")],
    )
    assert status(messages) == 400


def test_security_headers_are_added_to_responses():
    messages = run_middleware(SecurityHeadersMiddleware)
    headers = dict(next(message["headers"] for message in messages if message["type"] == "http.response.start"))
    assert headers[b"content-security-policy"].startswith(b"default-src 'self'")
    assert headers[b"strict-transport-security"] == b"max-age=31536000"
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
