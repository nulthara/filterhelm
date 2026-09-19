"""Instance registry + credential storage.

Non-secret instance metadata (name, base URL, notes) lives in a small YAML
file. Credentials never go in that file — see CredentialStore below.

Design doc: docs/design.md §7.3.
"""
from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.url_policy import normalize_instance_url, URLPolicyError

DATA_DIR = Path(os.environ.get("PIHOLE_MANAGER_DATA", Path.home() / ".config" / "pihole-manager"))
INSTANCES_FILE = DATA_DIR / "instances.yml"
SECRETS_FALLBACK_FILE = DATA_DIR / "secrets.yml"  # used only if no keyring backend is available
PENDING_GRAVITY_FILE = DATA_DIR / "pending-gravity.yml"
_pending_gravity_lock = threading.Lock()
_instances_lock = threading.Lock()


@dataclass
class Instance:
    name: str
    base_url: str
    notes: str = ""
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        try:
            self.base_url = normalize_instance_url(self.base_url)
        except URLPolicyError:
            # issue #2: a non-canonical stored base_url must not blow up load_instances() for every route
            self.base_url = self.base_url.strip()


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Best-effort: keep the directory private. Not fatal if the filesystem
    # doesn't support it (e.g. some container overlays).
    try:
        os.chmod(DATA_DIR, stat.S_IRWXU)  # 0700
    except OSError:
        pass


def _atomic_write(path, render, *, mode=0o600) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            render(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


@contextlib.contextmanager
def instances_mutation() -> Iterator[None]:
    with _instances_lock:
        yield


def load_instances() -> list[Instance]:
    _ensure_data_dir()
    if not INSTANCES_FILE.exists():
        return []
    with open(INSTANCES_FILE) as fh:
        raw = yaml.safe_load(fh) or []
    return [Instance(**entry) for entry in raw]


def save_instances(instances: list[Instance]) -> None:
    _ensure_data_dir()
    raw = [
        {"name": i.name, "base_url": i.base_url, "notes": i.notes, "verify_ssl": i.verify_ssl}
        for i in instances
    ]
    _atomic_write(INSTANCES_FILE, lambda fh: yaml.safe_dump(raw, fh, sort_keys=False))


def load_pending_gravity() -> set[str]:
    _ensure_data_dir()
    with _pending_gravity_lock:
        if not PENDING_GRAVITY_FILE.exists():
            return set()
        with open(PENDING_GRAVITY_FILE) as fh:
            raw = yaml.safe_load(fh) or []
        return {str(name) for name in raw if isinstance(name, str)}


def _save_pending_gravity(names: set[str]) -> None:
    _atomic_write(PENDING_GRAVITY_FILE, lambda fh: yaml.safe_dump(sorted(names), fh, sort_keys=False))


def mark_gravity_pending(instance_name: str) -> None:
    _ensure_data_dir()
    with _pending_gravity_lock:
        if PENDING_GRAVITY_FILE.exists():
            with open(PENDING_GRAVITY_FILE) as fh:
                names = {str(name) for name in (yaml.safe_load(fh) or []) if isinstance(name, str)}
        else:
            names = set()
        names.add(instance_name)
        _save_pending_gravity(names)


def clear_gravity_pending(instance_name: str) -> None:
    _ensure_data_dir()
    with _pending_gravity_lock:
        if not PENDING_GRAVITY_FILE.exists():
            return
        with open(PENDING_GRAVITY_FILE) as fh:
            names = {str(name) for name in (yaml.safe_load(fh) or []) if isinstance(name, str)}
        names.discard(instance_name)
        _save_pending_gravity(names)


class CredentialStore:
    """Stores each instance's app password.

    Tries the OS keyring first (right call when this runs on a desktop
    session). Falls back to a chmod-0600 local YAML file when no keyring
    backend is available — the expected case for a headless LXC deployment.
    The fallback file is root-owned, 0600, and never committed.
    See docs/design.md §7.3.
    """

    SERVICE_NAME = "pihole-manager"

    def __init__(self) -> None:
        self._keyring = self._try_keyring()

    @staticmethod
    def _try_keyring():
        try:
            import keyring
            import keyring.errors

            # Confirms a real backend exists rather than keyring's null
            # backend, which silently no-ops instead of raising.
            backend = keyring.get_keyring()
            if backend.__class__.__module__.startswith("keyring.backends.fail"):
                return None
            return keyring
        except Exception:
            return None

    def get(self, instance_name: str) -> str | None:
        if self._keyring is not None:
            try:
                return self._keyring.get_password(self.SERVICE_NAME, instance_name)
            except Exception:
                pass
        return self._load_fallback().get(instance_name)

    def set(self, instance_name: str, password: str) -> None:
        if self._keyring is not None:
            try:
                self._keyring.set_password(self.SERVICE_NAME, instance_name, password)
                return
            except Exception:
                pass
        secrets = self._load_fallback()
        secrets[instance_name] = password
        self._save_fallback(secrets)

    def delete(self, instance_name: str) -> None:
        if self._keyring is not None:
            try:
                self._keyring.delete_password(self.SERVICE_NAME, instance_name)
            except Exception:
                pass
        secrets = self._load_fallback()
        secrets.pop(instance_name, None)
        self._save_fallback(secrets)

    @staticmethod
    def _load_fallback() -> dict:
        _ensure_data_dir()
        if not SECRETS_FALLBACK_FILE.exists():
            return {}
        with open(SECRETS_FALLBACK_FILE) as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _save_fallback(secrets: dict) -> None:
        _ensure_data_dir()
        _atomic_write(SECRETS_FALLBACK_FILE, lambda fh: yaml.safe_dump(secrets, fh, sort_keys=False))
