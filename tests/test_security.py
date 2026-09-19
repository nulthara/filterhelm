import asyncio

import pytest
from fastapi import HTTPException

from app.settings_fields import normalize_setting
from app.security import SameOriginMiddleware


def request(method: str, headers: list[tuple[str, str]] | None = None, scheme: str = "https") -> int:
    messages = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    raw_headers = headers or []
    if not any(key.lower() == "host" for key, _value in raw_headers):
        raw_headers = [("host", "manager.example"), *raw_headers]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": scheme,
        "path": "/not-a-route",
        "raw_path": b"/not-a-route",
        "query_string": b"",
        "server": ("manager.example", 443),
        "client": ("127.0.0.1", 12345),
        "headers": [(key.lower().encode(), value.encode()) for key, value in raw_headers],
    }
    asyncio.run(SameOriginMiddleware(downstream)(scope, receive, send))
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def test_mutation_rejects_missing_origin():
    assert request("POST") == 403


def test_mutation_rejects_cross_origin():
    assert request("POST", [("origin", "https://attacker.example")]) == 403


def test_mutation_rejects_cross_site_fetch_metadata():
    assert request(
        "POST",
        [("origin", "https://manager.example"), ("sec-fetch-site", "cross-site")],
    ) == 403


def test_mutation_accepts_same_origin():
    assert request("POST", [("origin", "https://manager.example")]) == 404


def test_mutation_accepts_redundant_https_default_port():
    assert request(
        "POST", [("host", "manager.example:443"), ("origin", "https://manager.example")]
    ) == 404


def test_mutation_accepts_redundant_http_default_port():
    assert request(
        "POST", [("host", "manager.example:80"), ("origin", "http://manager.example")], scheme="http"
    ) == 404


def test_mutation_accepts_same_origin_referer_fallback():
    assert request("POST", [("referer", "https://manager.example/app/")]) == 404


def test_mutation_rejects_duplicate_origin_headers():
    assert request(
        "POST",
        [("origin", "https://manager.example"), ("origin", "https://manager.example")],
    ) == 403


def test_safe_request_does_not_require_origin():
    assert request("GET") == 404


def test_settings_reject_unknown_config_paths():
    with pytest.raises(HTTPException) as caught:
        normalize_setting("dangerous.unknown", True)

    assert caught.value.status_code == 422


def test_settings_use_shared_type_validation():
    with pytest.raises(HTTPException) as caught:
        normalize_setting("misc.privacylevel", 4)

    assert caught.value.status_code == 422
