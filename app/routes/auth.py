from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import AuthManager, SESSION_COOKIE
from app.audit import record

router = APIRouter(prefix="/api/auth")
manager = AuthManager()


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


@router.get("/status")
def status(request: Request):
    return {
        "mode": manager.config.mode,
        "configured": manager.configured(),
        "authenticated": bool(getattr(request.state, "user", None)),
        "user": getattr(request.state, "user", None),
    }


@router.post("/login")
def login(payload: LoginInput, request: Request, response: Response):
    if manager.config.mode != "local":
        raise HTTPException(status_code=409, detail="Login is managed by the reverse proxy")
    if not manager.configured():
        raise HTTPException(status_code=503, detail="No local administrator is configured")
    remote_addr = request.client.host if request.client else "unknown"
    if not manager.login_allowed(remote_addr, payload.username):
        record("application", "login_throttled", {}, {"username": payload.username, "remote_addr": remote_addr})
        raise HTTPException(status_code=429, detail="Too many login attempts; try again later")
    if not manager.authenticate(payload.username, payload.password, remote_addr):
        record("application", "login_failed", {}, {"username": payload.username, "remote_addr": remote_addr})
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = manager.create_session(payload.username)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=manager.config.cookie_secure,
        samesite="strict",
        max_age=manager.config.absolute_seconds,
        path="/",
    )
    record("application", "login", {}, {"username": payload.username, "remote_addr": remote_addr})
    return {"user": payload.username}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    username = getattr(request.state, "user", None)
    manager.delete_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/", secure=manager.config.cookie_secure, httponly=True, samesite="strict")
    record("application", "logout", {"username": username}, {})
