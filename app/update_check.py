"""Lazy, cached checks for published FilterHelm releases."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
import re
import threading
import time

import httpx

from app import __version__
from app.pihole_client import _PinnedTransport
from app.url_policy import resolve_update_check_destination

UPDATE_CHECK_ENV = "FILTERHELM_UPDATE_CHECK"
RELEASES_LATEST_PATH = "/repos/nulthara/filterhelm/releases/latest"
SUCCESS_TTL_SECONDS = 6 * 3600
FAILURE_TTL_SECONDS = 15 * 60
TIMEOUT_SECONDS = 5.0
logger = logging.getLogger(__name__)


def update_check_enabled() -> bool:
    return os.environ.get(UPDATE_CHECK_ENV, "").strip().lower() not in {"off", "0", "false", "no"}


def parse_version(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def _open_client() -> httpx.Client:
    destination = resolve_update_check_destination()
    return httpx.Client(
        base_url=destination.url,
        transport=_PinnedTransport(destination, verify=True),
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"FilterHelm/{__version__}"},
    )


def fetch_latest_release() -> str | None:
    with _open_client() as client:
        response = client.get(RELEASES_LATEST_PATH)
        if response.status_code != 200:
            return None
        body = response.json()
    if not isinstance(body, dict) or body.get("draft") or body.get("prerelease"):
        return None
    version = parse_version(body.get("tag_name"))
    return ".".join(str(part) for part in version) if version is not None else None


@dataclass(frozen=True)
class UpdateStatus:
    enabled: bool
    current: str
    latest: str | None
    update_available: bool


_cache: tuple[float, str | None] | None = None
_lock = threading.Lock()


def check_for_update(*, clock: Callable[[], float] = time.monotonic) -> UpdateStatus:
    global _cache
    if not update_check_enabled():
        return UpdateStatus(False, __version__, None, False)
    with _lock:
        if _cache is None or clock() >= _cache[0]:
            try:
                latest = fetch_latest_release()
            except Exception as exc:
                logger.warning("update check failed: %s", type(exc).__name__)
                latest = None
            ttl = SUCCESS_TTL_SECONDS if latest is not None else FAILURE_TTL_SECONDS
            _cache = (clock() + ttl, latest)
        latest = _cache[1]
    current_version = parse_version(__version__)
    latest_version = parse_version(latest)
    available = current_version is not None and latest_version is not None and latest_version > current_version
    return UpdateStatus(True, __version__, latest, available)


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None
