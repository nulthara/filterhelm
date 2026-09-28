"""Exercise lock parsing, per-file age selection, and hash rendering offline."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

SPEC = importlib.util.spec_from_file_location(
    "lock_hashes", Path(__file__).resolve().parent.parent / "scripts" / "lock_hashes.py",
)
assert SPEC is not None and SPEC.loader is not None
lock_hashes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lock_hashes)

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def release_file(filename: str, age: int, digest: str) -> dict[str, Any]:
    return {
        "filename": filename,
        "upload_time_iso_8601": (NOW - timedelta(hours=age)).isoformat(),
        "digests": {"sha256": digest},
    }


def test_parse_lock_reads_header_and_pins() -> None:
    assert lock_hashes.parse_lock(
        "# Runtime lock\n# Install first\n\nanyio==4.15.1\nidna==3.20\n",
    ) == (["# Runtime lock", "# Install first"], [("anyio", "4.15.1"), ("idna", "3.20")])


def test_parse_lock_ignores_existing_hash_lines() -> None:
    text = (
        "# Runtime lock\nanyio==4.15.1 \\\n"
        f"    --hash=sha256:{'a' * 64} \\\n"
        f"    --hash=sha256:{'b' * 64}\n"
        "idna==3.20 \\\n"
        f"    --hash=sha256:{'c' * 64}\n"
    )
    assert lock_hashes.parse_lock(text) == (
        ["# Runtime lock"], [("anyio", "4.15.1"), ("idna", "3.20")],
    )


def test_parse_lock_rejects_unpinned_line() -> None:
    with pytest.raises(lock_hashes.LockError):
        lock_hashes.parse_lock("fastapi>=0.1\n")


def test_select_hashes_keeps_files_older_than_24h() -> None:
    files = [
        release_file("one.whl", 48, "b" * 64),
        release_file("two.tar.gz", 48, "a" * 64),
        release_file("duplicate.whl", 48, "b" * 64),
        release_file("boundary.whl", 24, "c" * 64),
    ]
    assert lock_hashes.select_hashes("anyio", "4.15.1", files, NOW) == [
        "a" * 64, "b" * 64, "c" * 64,
    ]


def test_select_hashes_drops_late_added_file(capsys: pytest.CaptureFixture[str]) -> None:
    files = [
        release_file("old.whl", 48, "a" * 64),
        release_file("late.whl", 2, "b" * 64),
    ]
    assert lock_hashes.select_hashes("anyio", "4.15.1", files, NOW) == ["a" * 64]
    assert "late.whl" in capsys.readouterr().err


def test_select_hashes_rejects_young_release() -> None:
    with pytest.raises(lock_hashes.LockError):
        lock_hashes.select_hashes(
            "anyio", "4.15.1", [release_file("young.whl", 23, "a" * 64)], NOW,
        )


def test_select_hashes_rejects_empty_release() -> None:
    with pytest.raises(lock_hashes.LockError):
        lock_hashes.select_hashes("anyio", "4.15.1", [], NOW)


def test_render_lock_round_trips() -> None:
    header = ["# Runtime lock", "# Install first"]
    entries = [("anyio", "4.15.1", ["a" * 64, "b" * 64]), ("idna", "3.20", ["c" * 64])]
    text = lock_hashes.render_lock(header, entries)
    assert text == (
        "# Runtime lock\n# Install first\nanyio==4.15.1 \\\n"
        f"    --hash=sha256:{'a' * 64} \\\n"
        f"    --hash=sha256:{'b' * 64}\n"
        "idna==3.20 \\\n"
        f"    --hash=sha256:{'c' * 64}\n"
    )
    assert lock_hashes.parse_lock(text) == (header, [("anyio", "4.15.1"), ("idna", "3.20")])
