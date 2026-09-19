"""Inbound browser request-integrity checks."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi.responses import JSONResponse


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _authority(value: str, *, origin: bool, scheme=None) -> str | None:
    if not value or value == "null":
        return None
    parsed = urlsplit(value if origin else f"//{value}")
    if origin and parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username is not None or parsed.password is not None or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme if origin else scheme
    if port in (80, 443) or port == _DEFAULT_PORTS.get(scheme):
        port = None
    host = parsed.hostname.rstrip(".").lower()
    return f"{host}:{port}" if port is not None else host


class SameOriginMiddleware:
    """Reject unsafe browser requests that are not demonstrably same-origin.

    Comparing authorities rather than schemes keeps this compatible with TLS
    termination at a reverse proxy. The proxy must preserve the public Host.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            headers: dict[str, list[str]] = {}
            for key, value in scope["headers"]:
                headers.setdefault(key.decode("latin-1").lower(), []).append(value.decode("latin-1"))
            host_values = headers.get("host", [])
            origin_values = headers.get("origin")
            source_values = origin_values if origin_values is not None else headers.get("referer", [])
            fetch_values = headers.get("sec-fetch-site", [])
            malformed = len(host_values) != 1 or len(source_values) != 1 or len(fetch_values) > 1
            fetch_site = fetch_values[0].lower() if fetch_values else ""
            host = _authority(host_values[0], origin=False, scheme=scope.get("scheme")) if len(host_values) == 1 else None
            source = source_values[0] if len(source_values) == 1 else ""
            source_authority = _authority(source, origin=True)
            if malformed or fetch_site == "cross-site" or host is None or source_authority != host:
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Cross-origin or missing-origin mutation rejected"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
