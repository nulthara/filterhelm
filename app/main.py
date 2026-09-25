"""FastAPI entrypoint. Run with: uvicorn app.main:app --host 127.0.0.1 --port 8420

Binds to loopback by default in the run command above, not baked into the
app itself — see docs/design.md §6/§7 for the localhost-only rule when run
on a workstation, versus a hosted deployment behind a reverse proxy where it needs
to bind the container's own interface instead. Keep that decision in the
process manager / systemd unit, not here.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.auth_middleware import AuthenticationMiddleware
from app.http_hardening import (
    HardeningConfig,
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.routes import api, audit_log, auth, clients, domains, drift, groups, lookup, query_log
from app.security import SameOriginMiddleware

hardening = HardeningConfig.from_environment()
app = FastAPI(title="Pi-hole Manager")
app.add_middleware(SameOriginMiddleware)
app.add_middleware(
    RateLimitMiddleware,
    requests=hardening.rate_limit_requests,
    window_seconds=hardening.rate_limit_window_seconds,
)
app.add_middleware(AuthenticationMiddleware, manager=auth.manager)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=hardening.max_body_bytes)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=hardening.trusted_hosts, www_redirect=False)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(api.router)
app.include_router(auth.router)
app.include_router(domains.router)
app.include_router(groups.router)
app.include_router(clients.router)
app.include_router(drift.router)
app.include_router(lookup.router)
app.include_router(query_log.router)
app.include_router(audit_log.router)
app.mount("/app", StaticFiles(directory="app/static/ui", html=True), name="ui")


@app.get("/")
def index():
    return RedirectResponse("/app/")
