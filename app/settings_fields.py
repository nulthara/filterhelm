"""Managed Pi-hole settings metadata and value normalization."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

MANAGED_FIELDS = {
    "dns.upstreams": ("Upstream DNS servers", "list"),
    "dns.queryLogging": ("Query logging", "bool"),
    "misc.privacylevel": ("Privacy level (0-3)", "int"),
    "dns.revServers": ("Conditional forwarding (one reverse server per line)", "lines"),
}


def get_setting(tree: dict, dotted: str):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_setting(patch: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = patch
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def display_setting(value, kind: str) -> str:
    if value is None:
        return ""
    if kind == "list":
        return ", ".join(value)
    if kind == "lines":
        return "\n".join(value)
    return str(value)


def normalize_setting(dotted: str, value: Any) -> Any:
    if dotted not in MANAGED_FIELDS:
        raise HTTPException(status_code=422, detail=f"Unknown setting: {dotted}")
    _, kind = MANAGED_FIELDS[dotted]
    if kind == "bool":
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "on", "1", "yes"}:
                return True
            if normalized in {"false", "off", "0", "no", ""}:
                return False
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"{dotted} must be a boolean")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(status_code=422, detail=f"{dotted} must be an integer")
        if dotted == "misc.privacylevel" and not 0 <= value <= 3:
            raise HTTPException(status_code=422, detail="Privacy level must be between 0 and 3")
        return value
    if kind in {"list", "lines"}:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise HTTPException(status_code=422, detail=f"{dotted} must be a list of strings")
        return [item.strip() for item in value if item.strip()]
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{dotted} must be a string")
    return value.strip()
