"""Guard the container deployment contracts and runtime restrictions."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_workflow_runs_redeploy_script_through_sudo() -> None:
    workflow = (ROOT / ".gitea/workflows/deploy.yml").read_text()
    assert "run: sudo /usr/local/sbin/pihole-manager-redeploy" in workflow
    assert workflow.count("sudo ") == 1


def test_sudoers_rule_matches_workflow() -> None:
    lines = (ROOT / "docs/deploy.md").read_text().splitlines()
    rule = "gitea-runner ALL=(root) NOPASSWD: /usr/local/sbin/pihole-manager-redeploy"
    assert lines.count(rule) == 1


def test_redeploy_script_is_executable() -> None:
    result = subprocess.run(
        ["git", "ls-files", "-s", "deploy/pihole-manager-redeploy"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert result.stdout.startswith("100755 ")


def test_quadlet_never_pulls_or_auto_updates() -> None:
    quadlet = (ROOT / "deploy/pihole-manager.container").read_text()
    assert "Pull=never" in quadlet.splitlines()
    assert "Image=localhost/pihole-manager:latest" in quadlet.splitlines()
    assert "AutoUpdate=" not in quadlet


def test_container_runs_as_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text().splitlines()
    assert dockerfile.index("USER 10001:10001") > max(
        index for index, line in enumerate(dockerfile) if line.startswith("RUN ")
    )
    assert "read_only: true" in (ROOT / "compose.yaml").read_text()
    assert "ReadOnly=true" in (ROOT / "deploy/pihole-manager.container").read_text().splitlines()
