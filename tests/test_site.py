"""Guard the public docs site's content, delivery, and screenshot hygiene."""
from __future__ import annotations

import json
import re
import struct
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGES = {
    "start/first-run.mdx": "tutorial",
    "guides/install-options.mdx": "guide",
    "guides/manage-instances.mdx": "guide",
    "reference/pages.mdx": "reference",
    "understand/safety-model.mdx": "explanation",
}
SLOTS = {
    "drift", "first-run-instances", "add-instance", "diff-confirm",
    "audit-log", "local-dns", "lookup", "query",
}


def _workflow(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text())


def test_site_config_declares_public_origin() -> None:
    assert 'site: "https://filterhelm.nulthara.com"' in (SITE / "astro.config.mjs").read_text()


def test_site_config_overrides_only_planned_components() -> None:
    config = (SITE / "astro.config.mjs").read_text()
    block = re.search(r"components:\s*\{([^}]+)\}", config)
    assert block
    assert dict(re.findall(r'(\w+):\s*"([^"]+)"', block[1])) == {
        name: f"./src/components/{name}.astro" for name in ("Hero", "PageTitle", "Footer")
    }


def test_site_build_outputs_are_ignored() -> None:
    for path in ("node_modules/x", "dist/x", ".astro/x", "package-lock.json"):
        result = subprocess.run(
            ["git", "check-ignore", f"site/{path}"], cwd=ROOT, capture_output=True,
        )
        assert result.returncode == (1 if path == "package-lock.json" else 0)


def test_site_excluded_from_image_context() -> None:
    assert "site" in (ROOT / ".dockerignore").read_text().splitlines()


def test_site_node_pin_matches_ci() -> None:
    assert re.fullmatch(r"\d+\n?", (SITE / ".node-version").read_text())
    job = _workflow(".github/workflows/ci.yml")["jobs"]["site"]
    setup = next(step for step in job["steps"] if "setup-node@" in step.get("uses", ""))
    assert setup["with"]["node-version-file"] == "site/.node-version"


def test_ci_site_job_shape() -> None:
    job = _workflow(".github/workflows/ci.yml")["jobs"]["site"]
    assert job["defaults"]["run"]["working-directory"] == "site"
    assert [step["run"] for step in job["steps"] if "run" in step] == ["npm ci", "npm run build"]
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])


def test_deploy_workflow_ignores_site_only_pushes() -> None:
    push = _workflow(".gitea/workflows/deploy.yml")[True]["push"]
    assert push == {"branches": ["main"], "paths-ignore": ["site/**"]}
    assert (ROOT / ".gitea/workflows/deploy.yml").read_text().count("sudo ") == 1


def test_site_pages_match_content_map() -> None:
    docs = SITE / "src/content/docs"
    assert {str(path.relative_to(docs)) for path in docs.rglob("*.mdx")} == set(PAGES) | {"index.mdx"}
    for filename, kind in PAGES.items():
        data = yaml.safe_load((docs / filename).read_text().split("---")[1])
        assert data["title"]
        assert data["type"] == kind
    landing = yaml.safe_load((docs / "index.mdx").read_text().split("---")[1])
    assert landing["heroScreenshot"] == "drift"
    schema = (SITE / "src/content.config.ts").read_text()
    assert 'z.enum(["tutorial", "guide", "reference", "explanation"]).optional()' in schema
    assert 'heroScreenshot: z.string().optional()' in schema


def test_site_screenshot_slots_complete() -> None:
    shots = SITE / "src/assets/screenshots"
    files = list(shots.iterdir())
    assert {path.stem for path in files} == SLOTS
    assert all(path.is_file() and path.suffix in {".png", ".svg"} for path in files)


def test_site_screenshot_pngs_carry_no_metadata() -> None:
    for path in (SITE / "src/assets/screenshots").glob("*.png"):
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", path.name
        offset = 8
        chunks = []
        while offset < len(data):
            assert offset + 12 <= len(data), path.name
            length = struct.unpack_from(">I", data, offset)[0]
            chunk = data[offset + 4:offset + 8]
            chunks.append(chunk)
            assert chunk not in {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}, path.name
            offset += length + 12
            assert offset <= len(data), path.name
        assert chunks[-1] == b"IEND", path.name


def test_site_install_options_match_compose_sample() -> None:
    compose = yaml.safe_load((ROOT / "compose.ghcr.yaml").read_text())
    page = (SITE / "src/content/docs/guides/install-options.mdx").read_text()
    assert compose["services"]["filterhelm"]["image"] in page
    assert "https://raw.githubusercontent.com/nulthara/filterhelm/main/compose.ghcr.yaml" in page


def test_site_footer_carries_nonaffiliation_line() -> None:
    footer = (SITE / "src/components/Footer.astro").read_text()
    assert "FilterHelm is an independent, third-party project. It is not affiliated with or endorsed by Pi-hole." in footer
    assert "Released under the MIT License." in footer


def test_site_copy_avoids_banned_phrases() -> None:
    pages = list((SITE / "src/content/docs").rglob("*.mdx"))
    assert pages
    for path in pages:
        text = path.read_text()
        assert not re.search(r"\b(blazing|seamless|effortless|powerful|robust|supercharge|unlock|in minutes)\b", text, re.I), path
        assert not re.search("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f\u200d]", text), path


def test_site_has_no_email_addresses() -> None:
    paths = [SITE / "astro.config.mjs", SITE / "package.json"]
    paths += [path for folder in ("src", "public") for path in (SITE / folder).rglob("*") if path.is_file()]
    for path in paths:
        text = path.read_bytes().decode("utf-8", errors="replace")
        assert "mailto:" not in text.lower(), path
        assert not re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text), path


def test_site_selector_parser_override_pinned() -> None:
    package = json.loads((SITE / "package.json").read_text())
    version = package["overrides"]["postcss-selector-parser"]
    assert tuple(map(int, version.split("."))) >= (7, 1, 6)
    lock = json.loads((SITE / "package-lock.json").read_text())
    assert lock["packages"]["node_modules/postcss-selector-parser"]["version"] == version
