"""Hash-pin a lock file (requirements.lock by default) using PyPI files at least 24 hours old."""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import URLError

MIN_AGE = timedelta(hours=24)
LOCK_PATH = Path(__file__).resolve().parent.parent / "requirements.lock"


class LockError(Exception):
    """The lock or a release cannot satisfy the hash and age requirements."""


def parse_lock(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    header: list[str] = []
    pins: list[tuple[str, str]] = []
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if not pins:
                header.append(raw_line)
            continue
        line = line.removesuffix("\\").rstrip()
        if not line or line.startswith("--hash="):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", line)
        if match is None:
            raise LockError(f"line {number}: expected name==version, got {line!r}")
        pins.append((match[1], match[2]))
    return header, pins


def fetch_release_files(name: str, version: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{name}/{version}/json", timeout=30,
    ) as response:
        try:
            files = json.load(response)["urls"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LockError(f"invalid PyPI release response: {exc}") from exc
    if not isinstance(files, list):
        raise LockError("PyPI release files must be a list")
    return files


def select_hashes(
    name: str, version: str, files: list[dict[str, Any]], now: datetime,
) -> list[str]:
    package = f"{name}=={version}"
    if not files:
        raise LockError(f"{package}: release has no files")
    uploads: list[tuple[datetime, str, str]] = []
    for file in files:
        try:
            uploaded = datetime.fromisoformat(file["upload_time_iso_8601"])
            digest = file["digests"]["sha256"]
            filename = file["filename"]
            if uploaded.tzinfo is None or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("expected timezone-aware upload time and sha256 hex digest")
        except (KeyError, TypeError, ValueError) as exc:
            raise LockError(f"{package}: invalid file metadata: {exc}") from exc
        uploads.append((uploaded, digest, filename))
    cutoff = now - MIN_AGE
    if min(uploaded for uploaded, _, _ in uploads) > cutoff:
        raise LockError(f"{package}: release is younger than 24 hours")
    hashes: set[str] = set()
    for uploaded, digest, filename in uploads:
        if uploaded > cutoff:
            print(f"{package}: dropping {filename}: younger than 24 hours", file=sys.stderr)
        else:
            hashes.add(digest)
    if not hashes:
        raise LockError(f"{package}: no files are at least 24 hours old")
    return sorted(hashes)


def render_lock(header: list[str], entries: list[tuple[str, str, list[str]]]) -> str:
    lines = list(header)
    for name, version, hashes in entries:
        lines.append(f"{name}=={version} \\")
        for index, digest in enumerate(hashes):
            continuation = " \\" if index < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{continuation}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) > 1:
        print("usage: lock_hashes.py [LOCK]", file=sys.stderr)
        return 2
    path = Path(argv[0]) if argv else LOCK_PATH
    package = str(path)
    try:
        header, pins = parse_lock(path.read_text(encoding="utf-8"))
        now = datetime.now(UTC)
        entries: list[tuple[str, str, list[str]]] = []
        for name, version in pins:
            package = f"{name}=={version}"
            files = fetch_release_files(name, version)
            entries.append((name, version, select_hashes(name, version, files, now)))
        path.write_text(render_lock(header, entries), encoding="utf-8")
    except (LockError, URLError, OSError, HTTPException) as exc:
        print(f"{package}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
