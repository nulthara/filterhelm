"""Guard horizontal scrolling and compact Instances actions."""
from __future__ import annotations

import re
from pathlib import Path

PAGES = Path(__file__).resolve().parent.parent / "frontend/src/pages"
SCROLL = "scroll={{ x: 'max-content' }}"
# These pages are explicitly out of scope for 0042.
SCROLL_EXEMPTIONS = {"AuditPage.tsx", "SettingsPage.tsx"}


def _table_tags(source: str) -> list[str]:
    tags = []
    for match in re.finditer(r"<Table(?=[\s<])", source):
        start = match.start()
        braces = 0
        angles = 0
        quote = None
        escaped = False
        for index in range(match.end(), len(source)):
            char = source[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in "\"'`":
                quote = char
            elif char == "{":
                braces += 1
            elif char == "}":
                braces -= 1
            elif not braces:
                if char == "<":
                    angles += 1
                elif char == ">":
                    if angles:
                        angles -= 1
                    else:
                        tags.append(source[start:index + 1])
                        break
        else:
            raise AssertionError("Unclosed Table tag")
    return tags


def test_page_tables_scroll_horizontally() -> None:
    for name in SCROLL_EXEMPTIONS:
        path = PAGES / name
        assert path.is_file(), name
        tags = _table_tags(path.read_text())
        assert tags and all(SCROLL not in tag for tag in tags), name
    for path in PAGES.glob("*.tsx"):
        if path.name not in SCROLL_EXEMPTIONS:
            for tag in _table_tags(path.read_text()):
                assert SCROLL in tag, path.name


def test_instances_actions_have_accessible_names() -> None:
    source = (PAGES / "InstancesPage.tsx").read_text()
    assert "Grid.useBreakpoint()" in source
    for label in ("Test", "Backup", "Edit", "Service actions for"):
        assert f'aria-label={{`{label} ${{item.name}}`}}' in source


def test_instances_health_panel_uses_visible_width() -> None:
    source = (PAGES / "InstancesPage.tsx").read_text()
    assert 'className="instances-table"' in source
    assert 'className="health-details"' in source
    styles = (PAGES.parent / "styles.css").read_text()
    container = re.search(r"\.instances-table \.ant-table-container\s*\{([^}]*)\}", styles)
    assert container is not None
    assert "container-type: inline-size" in container.group(1)
    panel = re.search(r"\.health-details\s*\{([^}]*)\}", styles)
    assert panel is not None
    assert "position: sticky" in panel.group(1)
    assert "100cqi" in panel.group(1)
