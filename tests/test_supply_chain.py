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


def test_image_lock_install_requires_hashes() -> None:
    installs = [
        line for line in (ROOT / "Dockerfile").read_text().splitlines()
        if "pip install" in line and "-r requirements.lock" in line
    ]
    assert installs, "No lock install found in Dockerfile"
    for line in installs:
        assert "--require-hashes" in line
        assert "--only-binary=:all:" in line
        assert "--uploaded-prior-to" not in line


def test_deploy_workflow_does_not_pip_install() -> None:
    assert "pip install" not in (ROOT / ".gitea/workflows/deploy.yml").read_text()


def test_dockerfile_base_images_pinned_by_digest() -> None:
    images = [
        line for line in (ROOT / "Dockerfile").read_text().splitlines()
        if line.startswith("FROM ")
    ]
    assert images, "No base image found in Dockerfile"
    assert all(re.search(r"@sha256:[0-9a-f]{64}(?:\s|$)", line) for line in images)


def _assert_fully_hashed(path: Path) -> None:
    text = path.read_text()
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


def test_versions_agree() -> None:
    import json
    from app import __version__

    package = json.loads((ROOT / "frontend/package.json").read_text())
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert __version__ == package["version"] == lock["version"] == lock["packages"][""]["version"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in project["project"]
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "app.__version__"


def test_publish_workflow_pins_actions_by_sha() -> None:
    text = (ROOT / ".github/workflows/publish-image.yml").read_text()
    actions = [line for line in text.splitlines() if "uses:" in line]
    assert actions
    assert all(re.search(r"@[0-9a-f]{40}\s+# v\d+\.\d+\.\d+\s*$", line) for line in actions)


def test_publish_workflow_shape() -> None:
    import yaml

    text = (ROOT / ".github/workflows/publish-image.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow[True] == {"push": {"tags": ["v*"]}}
    assert set(workflow["jobs"]) == {"publish"}
    job = workflow["jobs"]["publish"]
    assert job["permissions"] == {
        "contents": "read", "packages": "write", "id-token": "write",
        "attestations": "write", "artifact-metadata": "write",
    }
    build = next(step for step in job["steps"] if step.get("uses", "").startswith("docker/build-push-action@"))
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert build["with"]["provenance"] is False
    assert build["with"]["sbom"] is False
    assert build["id"] == "build"
    steps = job["steps"]
    sbom, provenance, attestation = steps[steps.index(build) + 1:]
    assert sbom["uses"].startswith("anchore/sbom-action@")
    assert sbom["with"]["image"] == "${{ env.IMAGE }}@${{ steps.build.outputs.digest }}"
    assert sbom["with"]["format"] == "spdx-json"
    assert sbom["with"]["output-file"] == "sbom.spdx.json"
    assert sbom["with"]["syft-version"] == "v1.54.0"
    assert sbom["with"]["upload-artifact"] is False
    assert sbom["with"]["upload-release-assets"] is False
    for step in (provenance, attestation):
        assert step["uses"].startswith("actions/attest@")
        assert step["with"]["subject-name"] == "${{ env.IMAGE }}"
        assert step["with"]["subject-digest"] == "${{ steps.build.outputs.digest }}"
        assert step["with"]["push-to-registry"] is True
    assert "sbom-path" not in provenance["with"]
    assert attestation["with"]["sbom-path"] == "sbom.spdx.json"
    assert "pip install" not in text
    assert set(re.findall(r"secrets\.[A-Za-z0-9_]+", text)) == {"secrets.GITHUB_TOKEN"}


def test_requirements_lock_is_fully_hashed() -> None:
    _assert_fully_hashed(ROOT / "requirements.lock")


def test_dev_lock_is_fully_hashed() -> None:
    _assert_fully_hashed(ROOT / "requirements-dev.lock")


def test_dev_extra_matches_dev_lock() -> None:
    def normalize(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    lock = {
        normalize(match[1]): match[2]
        for match in re.finditer(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)",
            (ROOT / "requirements-dev.lock").read_text(), re.MULTILINE,
        )
    }
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for dependency in project["project"]["optional-dependencies"]["dev"]:
        name, separator, version = dependency.partition("==")
        assert separator, f"Unpinned dev dependency: {dependency}"
        assert lock[normalize(name)] == version


def test_dev_lock_does_not_overlap_runtime_lock() -> None:
    def names(path: Path) -> set[str]:
        return {
            re.sub(r"[-_.]+", "-", match[1]).lower()
            for match in re.finditer(
                r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", path.read_text(), re.MULTILINE,
            )
        }

    assert not names(ROOT / "requirements.lock") & names(ROOT / "requirements-dev.lock")


def test_ci_workflow_pins_actions_by_sha() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    actions = [line for line in text.splitlines() if "uses:" in line]
    assert actions
    assert all(re.search(r"@[0-9a-f]{40}\s+# v\d+\.\d+\.\d+\s*$", line) for line in actions)


def test_ci_workflow_shape() -> None:
    import yaml

    text = (ROOT / ".github/workflows/ci.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow[True] == {
        "pull_request": {"branches": ["main"]}, "push": {"branches": ["main"]},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"backend", "frontend"}
    assert "secrets." not in text
    for name, job in workflow["jobs"].items():
        assert "permissions" not in job
        checkouts = [
            step for step in job["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        assert checkouts
        for checkout in checkouts:
            assert checkout["with"]["persist-credentials"] is False
            if name == "backend":
                assert checkout["with"]["fetch-depth"] == 0

    backend = workflow["jobs"]["backend"]["steps"]
    install_index = next(
        index for index, step in enumerate(backend)
        if "pip install" in step.get("run", "")
    )
    install = backend[install_index]["run"]
    for required in (
        "--require-hashes", "--only-binary=:all:",
        "-r requirements.lock", "-r requirements-dev.lock",
    ):
        assert required in install
    assert any("pytest" in step.get("run", "") for step in backend[install_index + 1:])

    frontend = workflow["jobs"]["frontend"]["steps"]
    previous = -1
    for command in ("npm ci", "npm run lint", "npm test", "npm run build"):
        index = next(index for index, step in enumerate(frontend) if step.get("run") == command)
        assert index > previous
        previous = index
    assert any(
        "git status --porcelain" in step.get("run", "")
        and "app/static/ui" in step["run"]
        for step in frontend[previous + 1:]
    )
