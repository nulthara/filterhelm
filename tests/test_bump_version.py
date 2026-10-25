"""Exercise version bumps against temporary copies of the version files."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "bump_version.py")
assert SPEC is not None and SPEC.loader is not None
bump_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bump_version)

FILES = (bump_version.APP_INIT, bump_version.PACKAGE, bump_version.LOCK)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for path in FILES:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / path, tmp_path / path)
    return tmp_path


def versions(root: Path) -> list[str]:
    return bump_version.read_versions({path: (root / path).read_text() for path in FILES})


def test_explicit_version_updates_all_fields_and_only_those_lines(root: Path) -> None:
    before = {path: (root / path).read_text().splitlines() for path in FILES}
    assert bump_version.main(["99.0.0"], root) == 0
    assert versions(root) == ["99.0.0"] * 4
    changed = {
        path: sum(a != b for a, b in zip(before[path], (root / path).read_text().splitlines()))
        for path in FILES
    }
    assert changed == {bump_version.APP_INIT: 1, bump_version.PACKAGE: 1, bump_version.LOCK: 2}


@pytest.mark.parametrize(
    ("target", "expected"),
    [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0"), ("1.10.0", "1.10.0")],
)
def test_next_version(target: str, expected: str) -> None:
    assert bump_version.next_version("1.2.3", target) == expected


@pytest.mark.parametrize("target", ["1.2.3", "1.2.2", "0.9.9", "1.2", "v1.2.4", "1.2.4-rc1"])
def test_next_version_rejects_invalid_or_lower(target: str) -> None:
    with pytest.raises(bump_version.BumpError):
        bump_version.next_version("1.2.3", target)


def test_refuses_and_writes_nothing_when_fields_disagree(root: Path) -> None:
    package = root / bump_version.PACKAGE
    data = json.loads(package.read_text())
    data["version"] = "0.0.1"
    package.write_text(json.dumps(data, indent=2) + "\n")
    before = {path: (root / path).read_text() for path in FILES}
    assert bump_version.main(["patch"], root) == 1
    assert {path: (root / path).read_text() for path in FILES} == before


def test_refuses_lower_version_without_writing(root: Path) -> None:
    before = {path: (root / path).read_text() for path in FILES}
    assert bump_version.main(["0.0.1"], root) == 1
    assert {path: (root / path).read_text() for path in FILES} == before


def test_usage_error() -> None:
    assert bump_version.main([]) == 2

