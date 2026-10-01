"""Guard the container deployment contracts and runtime restrictions."""
from __future__ import annotations

import os
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


def test_quadlet_pins_data_path() -> None:
    lines = (ROOT / "deploy/pihole-manager.container").read_text().splitlines()
    assert "Environment=PIHOLE_MANAGER_DATA=/data" in lines
    assert "Volume=/var/lib/pihole-manager:/data" in lines


def _run_redeploy(
    tmp_path: Path, *, build_result: int = 0, image_exists_result: int = 0,
    restart_result: int = 0, probe_result: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    restart_marker = tmp_path / "restarted"
    scripts = {
        "podman": '''case "$1 $2" in
    "build "*) exit "$BUILD_RESULT" ;;
    "image exists") exit "$IMAGE_EXISTS_RESULT" ;;
esac
''',
        "systemctl": '''if [ ! -e "$RESTART_MARKER" ]; then
    touch "$RESTART_MARKER"
    exit "$RESTART_RESULT"
fi
''',
        "curl": 'exit "$PROBE_RESULT"\n',
        "sleep": '',
    }
    for name, body in scripts.items():
        stub = bin_dir / name
        stub.write_text(
            '#!/bin/sh\n'
            f'printf "%s\\n" "{name} $*" >> "$COMMAND_LOG"\n'
            + body + 'exit 0\n'
        )
        stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "RESTART_MARKER": str(restart_marker),
        "BUILD_RESULT": str(build_result),
        "IMAGE_EXISTS_RESULT": str(image_exists_result),
        "RESTART_RESULT": str(restart_result),
        "PROBE_RESULT": str(probe_result),
    }
    result = subprocess.run(
        ["sh", str(ROOT / "deploy/pihole-manager-redeploy")],
        env=env, capture_output=True, text=True, check=False,
    )
    return result, log.read_text().splitlines()


def test_redeploy_promotes_after_healthy_probe(tmp_path: Path) -> None:
    result, log = _run_redeploy(tmp_path)
    assert result.returncode == 0
    assert log == [
        "podman build --network=host --pull=missing --tag localhost/pihole-manager:latest /opt/pihole-manager",
        "systemctl restart pihole-manager.service",
        "curl -fsS -o /dev/null http://127.0.0.1:8420/api/auth/status",
        "podman tag localhost/pihole-manager:latest localhost/pihole-manager:good",
        "podman image prune --force",
    ]


def test_redeploy_rolls_back_when_probe_fails(tmp_path: Path) -> None:
    result, log = _run_redeploy(tmp_path, probe_result=1)
    assert result.returncode == 1
    rollback = "podman tag localhost/pihole-manager:good localhost/pihole-manager:latest"
    assert rollback in log
    assert log[log.index(rollback) + 1] == "systemctl restart pihole-manager.service"
    assert log.count("systemctl restart pihole-manager.service") == 2
    assert log.count("curl -fsS -o /dev/null http://127.0.0.1:8420/api/auth/status") == 10
    assert log.count("sleep 2") == 10
    assert not any(":latest localhost/pihole-manager:good" in line for line in log)
    assert "podman image prune --force" not in log


def test_redeploy_rolls_back_when_restart_fails(tmp_path: Path) -> None:
    result, log = _run_redeploy(tmp_path, restart_result=1)
    assert result.returncode == 1
    rollback = "podman tag localhost/pihole-manager:good localhost/pihole-manager:latest"
    assert rollback in log
    assert log[log.index(rollback) + 1] == "systemctl restart pihole-manager.service"
    assert not any(line.startswith("curl ") for line in log)
    assert not any(":latest localhost/pihole-manager:good" in line for line in log)


def test_redeploy_without_good_image_fails_without_rollback(tmp_path: Path) -> None:
    result, log = _run_redeploy(tmp_path, image_exists_result=1, probe_result=1)
    assert result.returncode == 1
    assert "podman image exists localhost/pihole-manager:good" in log
    assert not any(line.startswith("podman tag ") for line in log)
    assert log.count("systemctl restart pihole-manager.service") == 1


def test_redeploy_stops_on_build_failure(tmp_path: Path) -> None:
    result, log = _run_redeploy(tmp_path, build_result=1)
    assert result.returncode != 0
    assert log == [
        "podman build --network=host --pull=missing --tag localhost/pihole-manager:latest /opt/pihole-manager",
    ]
