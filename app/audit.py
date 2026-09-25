"""Local audit log — one line per mutating action.

Cheap insurance given nebula-sync's silent damage (see docs/design.md §6):
every write this app makes should be reconstructable after the fact.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.config import DATA_DIR
from app.auth import get_current_user

AUDIT_FILE = DATA_DIR / "audit.log"
AUDIT_LIMIT_DEFAULT, AUDIT_LIMIT_MAX = 200, 1000


def record(instance: str, action: str, before: dict, after: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "instance": instance,
        "action": action,
        "actor": get_current_user(),
        "before": before,
        "after": after,
    }
    with open(AUDIT_FILE, "a") as fh:
        fh.write(json.dumps(entry) + "\n")
    try:
        os.chmod(AUDIT_FILE, 0o600)
    except OSError:
        pass


def _haystack(entry: dict[str, Any]) -> str:
    parts = [entry["instance"], entry["action"]]
    if entry["actor"] is not None:
        parts.append(entry["actor"])

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                parts.append(str(key))
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, (str, int, float, bool)):
            parts.append(str(value))

    collect(entry["before"])
    collect(entry["after"])
    return "\n".join(parts).lower()


def search(*, instance: str = "", action: str = "", actor: str = "",
           text: str = "", limit: int = AUDIT_LIMIT_DEFAULT) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    instances: set[str] = set()
    actions: set[str] = set()
    actors: set[str] = set()
    skipped = 0
    needle = text.lower()
    if AUDIT_FILE.exists():
        with open(AUDIT_FILE, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if not isinstance(raw, dict):
                    skipped += 1
                    continue
                entry = {
                    key: raw[key] if isinstance(raw.get(key), str) else ""
                    for key in ("ts", "instance", "action")
                }
                entry["actor"] = raw["actor"] if isinstance(raw.get("actor"), str) else None
                for key in ("before", "after"):
                    entry[key] = raw[key] if isinstance(raw.get(key), dict) else {}
                instances.add(entry["instance"])
                actions.add(entry["action"])
                if entry["actor"] is not None:
                    actors.add(entry["actor"])
                if instance and entry["instance"] != instance:
                    continue
                if action and entry["action"] != action:
                    continue
                if actor and entry["actor"] != actor:
                    continue
                if needle and needle not in _haystack(entry):
                    continue
                entries.append(entry)
    return {
        "entries": entries[-limit:][::-1], "matched": len(entries), "skipped": skipped,
        "instances": sorted(instances), "actions": sorted(actions), "actors": sorted(actors),
    }
