"""Guard the dependency age gates and the hash-pinned production lock."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_npmrc_sets_min_release_age() -> None:
    settings = dict(
        line.strip().split("=", 1)
        for line in (ROOT / "frontend/.npmrc").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith(("#", ";"))
    )
    assert int(settings["min-release-age"]) >= 1


def test_deploy_lock_install_requires_hashes() -> None:
    for filename in (".gitea/workflows/deploy.yml", "docs/deploy.md"):
        installs = [
            line for line in (ROOT / filename).read_text().splitlines()
            if "pip install" in line and "-r requirements.lock" in line
        ]
        assert installs, f"No lock install found in {filename}"
        for line in installs:
            assert "--require-hashes" in line, f"{filename}: {line}"
            assert "--uploaded-prior-to" not in line, f"{filename}: {line}"


def test_requirements_lock_is_fully_hashed() -> None:
    text = (ROOT / "requirements.lock").read_text()
    entries = text.replace("\\\n", " ").splitlines()
    count = 0
    for entry in entries:
        entry = entry.strip()
        if not entry or entry.startswith("#"):
            continue
        tokens = entry.split()
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.!+_-]*", tokens[0])
        assert len(tokens) > 1, f"Missing hashes: {tokens[0]}"
        assert all(re.fullmatch(r"--hash=sha256:[0-9a-fA-F]{64}", token) for token in tokens[1:])
        count += 1
    assert count > 0


def test_pyproject_pins_match_lock() -> None:
    def normalize(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    lock = {
        normalize(match[1]): match[2]
        for match in re.finditer(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)",
            (ROOT / "requirements.lock").read_text(), re.MULTILINE,
        )
    }
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for dependency in project["project"]["dependencies"]:
        name, separator, version = dependency.partition("==")
        assert separator, f"Unpinned direct dependency: {dependency}"
        assert lock[normalize(name)] == version
