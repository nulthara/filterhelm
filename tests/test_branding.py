"""Guard the FilterHelm name and copies of the brand assets."""
from __future__ import annotations

import re

from test_repo_hygiene import ROOT, _tracked_texts

OLD_NAME = re.compile(r"pihole[-_ ]?manager|pi-hole manager", re.I)
OLD_NAME_EXEMPT = {
    "tests/test_branding.py",
    "docs/plans/0030-rebrand-filterhelm.md",
    "docs/plans/0032-old-name-cleanup.md",
}


def test_tracked_files_do_not_use_old_name() -> None:
    texts, findings = _tracked_texts()
    for name, text in texts:
        if name in OLD_NAME_EXEMPT:
            continue
        for match in OLD_NAME.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{name}:{line}: {match.group(0)}")
    assert not findings, "\n" + "\n".join(findings)


def test_frontend_mark_matches_brand_kit() -> None:
    icon = (ROOT / "branding/logos/filterhelm-icon-compact.svg").read_bytes()
    assert (ROOT / "frontend/src/assets/filterhelm-icon-compact.svg").read_bytes() == icon
    assert (ROOT / "frontend/public/favicon.svg").read_bytes() == icon
    assert (ROOT / "frontend/public/favicon-32.png").read_bytes() == (
        ROOT / "branding/icons/filterhelm-32.png"
    ).read_bytes()


def test_frontend_tokens_match_brand_kit() -> None:
    for filename in ("brand-tokens.json", "brand-tokens.css"):
        assert (ROOT / "frontend/src/assets" / filename).read_bytes() == (
            ROOT / "branding/tokens" / filename
        ).read_bytes()
