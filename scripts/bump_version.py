"""Set the app version in every field test_versions_agree checks."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_INIT = Path("app/__init__.py")
PACKAGE = Path("frontend/package.json")
LOCK = Path("frontend/package-lock.json")
PARTS = ("major", "minor", "patch")


class BumpError(Exception):
    """The current fields or the requested version cannot be bumped safely."""


def parse_version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        raise BumpError(f"expected X.Y.Z, got {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def next_version(current: str, target: str) -> str:
    major, minor, patch = parse_version(current)
    if target == "major":
        return f"{major + 1}.0.0"
    if target == "minor":
        return f"{major}.{minor + 1}.0"
    if target == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if parse_version(target) <= (major, minor, patch):
        raise BumpError(f"{target} is not higher than {current}")
    return target


def read_versions(texts: dict[Path, str]) -> list[str]:
    match = re.search(r'^__version__ = "([^"]*)"$', texts[APP_INIT], re.MULTILINE)
    if match is None:
        raise BumpError(f"{APP_INIT}: no __version__ line")
    package = json.loads(texts[PACKAGE])
    lock = json.loads(texts[LOCK])
    return [match[1], package["version"], lock["version"], lock["packages"][""]["version"]]


def replace_once(text: str, pattern: str, value: str, path: Path) -> str:

    # Each pattern captures the text before and after the version string.

    new, count = re.subn(pattern, rf"\g<1>{value}\g<2>", text, flags=re.MULTILINE)
    if count != 1:
        raise BumpError(f"{path}: expected one version field, found {count}")
    return new


def bump(texts: dict[Path, str], current: str, new: str) -> dict[Path, str]:
    old = re.escape(current)
    lock = replace_once(texts[LOCK], rf'^(  "version": "){old}(",)$', new, LOCK)
    lock = replace_once(
        lock, rf'^(    "": \{{\n(?:      .*\n)*?      "version": "){old}(",)$', new, LOCK,
    )
    return {
        APP_INIT: replace_once(texts[APP_INIT], rf'^(__version__ = "){old}(")$', new, APP_INIT),
        PACKAGE: replace_once(texts[PACKAGE], rf'^(  "version": "){old}(",)$', new, PACKAGE),
        LOCK: lock,
    }


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 1:
        print("usage: bump_version.py X.Y.Z|major|minor|patch", file=sys.stderr)
        return 2
    try:
        texts = {path: (root / path).read_text(encoding="utf-8") for path in (APP_INIT, PACKAGE, LOCK)}
        versions = read_versions(texts)
        if len(set(versions)) != 1:
            raise BumpError(f"version fields disagree: {', '.join(versions)}")
        current = versions[0]
        new = next_version(current, argv[0])

        # Check every edit before writing any file.

        updated = bump(texts, current, new)
        if read_versions(updated) != [new] * 4:
            raise BumpError("edited fields do not all read the new version")
        for path, text in updated.items():
            (root / path).write_text(text, encoding="utf-8")
    except (BumpError, OSError, ValueError, KeyError) as exc:
        print(f"bump_version: {exc}", file=sys.stderr)
        return 1
    print(f"{current} -> {new}")
    print("Next: run pytest, commit, and after merge tag the commit as described in docs/deploy.md § Releasing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
