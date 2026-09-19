"""Local audit log — one line per mutating action.

Cheap insurance given nebula-sync's silent damage (see docs/design.md §6):
every write this app makes should be reconstructable after the fact.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR
from app.auth import get_current_user

AUDIT_FILE = DATA_DIR / "audit.log"


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


def recent(limit: int = 20) -> list[dict]:
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text().splitlines()
    return [json.loads(line) for line in lines[-limit:]][::-1]
