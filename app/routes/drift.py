"""Read-only comparison of managed entries across reachable Pi-hole instances."""
from __future__ import annotations

import concurrent.futures
import os
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, HTTPException

from app.config import CredentialStore, Instance, load_instances
from app.pihole_client import PiholeAPIError, PiholeClient
from app.routes.api import _parse_dns_cnames, _parse_dns_hosts
from app.settings_fields import MANAGED_FIELDS, display_setting, get_setting

router = APIRouter(prefix="/api")
credentials = CredentialStore()
_FANOUT_WORKERS = max(1, int(os.environ.get("PIHOLE_MANAGER_FANOUT_WORKERS", "8")))
_Item = TypeVar("_Item")
_Result = TypeVar("_Result")

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("dns", "Local DNS"), ("cname", "CNAME records"), ("adlist", "Block lists"),
    ("domain", "Domains"), ("group", "Groups"), ("client", "Clients"),
    ("setting", "Settings"),
)
DRIFT_ITEMS_PER_CATEGORY = 200


def _fanout(items: list[_Item], worker: Callable[[_Item], _Result]) -> list[_Result]:
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_FANOUT_WORKERS, len(items) or 1)
    ) as executor:
        return list(executor.map(worker, items))


def _client(instance: Instance) -> PiholeClient:
    password = credentials.get(instance.name)
    if not password:
        raise HTTPException(status_code=409, detail="No credential stored")
    return PiholeClient(instance.base_url, password, verify_ssl=instance.verify_ssl)


def _snapshot(instance: Instance) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        # One session, sequential reads: overlapping authentication can race to a 401.
        with _client(instance) as client:
            tree = client.get_config()
            adlists = client.get_adlists()
            domains = client.get_domains()
            groups = client.get_groups()
            clients = client.get_clients()
        snapshot = {
            "dns": _parse_dns_hosts(tree), "cname": _parse_dns_cnames(tree),
            "adlist": adlists, "domain": domains, "group": groups, "client": clients,
            "setting": {dotted: get_setting(tree, dotted) for dotted in MANAGED_FIELDS},
        }
        return instance.name, snapshot, None
    except HTTPException as exc:
        return instance.name, None, str(exc.detail)
    except PiholeAPIError as exc:
        return instance.name, None, str(exc)
    except Exception:  # noqa: BLE001 - transport details are intentionally hidden
        return instance.name, None, "Unable to reach Pi-hole instance"


@dataclass
class _Entry:
    id: str
    key: str
    detail: str = ""
    values: dict[str, str] = field(default_factory=dict)
    variants: set[Any] = field(default_factory=set)


def _dns_entries(snapshots: dict[str, dict[str, Any]]) -> list[_Entry]:
    entries: dict[str, _Entry] = {}
    for name, snapshot in snapshots.items():
        addresses: dict[str, list[str]] = {}
        for record in snapshot["dns"]:
            addresses.setdefault(record["hostname"], []).append(record["address"])
        for hostname, values in addresses.items():
            entry = entries.setdefault(hostname, _Entry(f"dns:{hostname}", hostname))
            entry.values[name] = ", ".join(sorted(values))
            entry.variants.update(values)
    return list(entries.values())


def _cname_entries(snapshots: dict[str, dict[str, Any]]) -> list[_Entry]:
    entries: dict[str, _Entry] = {}
    for name, snapshot in snapshots.items():
        displays: dict[str, list[str]] = {}
        for record in snapshot["cname"]:
            hostname, target, ttl = record["hostname"], record["target"], record["ttl"]
            entry = entries.setdefault(hostname, _Entry(f"cname:{hostname}", hostname))
            entry.variants.add((target, ttl))
            display = target if ttl is None else f"{target} (TTL {ttl})"
            displays.setdefault(hostname, []).append(display)
        for hostname, values in displays.items():
            entries[hostname].values[name] = ", ".join(values)
    return list(entries.values())


def _enabled_entries(
    snapshots: dict[str, dict[str, Any]], category: str,
    identity: Callable[[Any], tuple[str, str, str]],
) -> list[_Entry]:
    entries: dict[str, _Entry] = {}
    for name, snapshot in snapshots.items():
        # The owning pages retain the last row for a repeated per-instance key.
        per_instance = {identity(item)[0]: item for item in snapshot[category]}
        for item_id, item in per_instance.items():
            _, key, detail = identity(item)
            entry = entries.setdefault(item_id, _Entry(item_id, key, detail))
            entry.values[name] = "Enabled" if item.enabled else "Disabled"
            entry.variants.add(item.enabled)
    return list(entries.values())


def _adlist_entries(snapshots: dict[str, dict[str, Any]]) -> list[_Entry]:
    return _enabled_entries(snapshots, "adlist", lambda item: (
        f"adlist:{item.address}", item.address, item.comment[:80],
    ))


def _domain_entries(snapshots: dict[str, dict[str, Any]]) -> list[_Entry]:
    return _enabled_entries(snapshots, "domain", lambda item: (
        f"domain:{item.type}:{item.kind}:{item.domain}", item.domain, f"{item.type} · {item.kind}",
    ))


def _group_entries(snapshots: dict[str, dict[str, Any]]) -> list[_Entry]:
    return _enabled_entries(snapshots, "group", lambda item: (
        f"group:{item.name}", item.name, item.comment[:80],
    ))


def _client_entries(snapshots: dict[str, dict[str, Any]]) -> list[_Entry]:
    entries: dict[str, _Entry] = {}
    for name, snapshot in snapshots.items():
        for item in snapshot["client"]:
            entry = entries.setdefault(item.client, _Entry(f"client:{item.client}", item.client))
            entry.values[name] = "Present"
    return list(entries.values())


def _hashable(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _setting_display(value: Any, kind: str) -> str:
    return "(not set)" if value is None else display_setting(value, kind) or "(empty)"


def _setting_entries(snapshots: dict[str, dict[str, Any]]) -> list[_Entry]:
    if not snapshots:
        return []
    entries = []
    for dotted, (label, kind) in MANAGED_FIELDS.items():
        entry = _Entry(f"setting:{dotted}", dotted, label)
        for name, snapshot in snapshots.items():
            value = snapshot["setting"][dotted]
            entry.values[name] = _setting_display(value, kind)
            entry.variants.add(_hashable(value))
        entries.append(entry)
    return entries


def _state(present: set[str], reachable: set[str], variants: set[Any]) -> str:
    return "conflict" if len(variants) > 1 else "missing" if present != reachable else "consistent"


_COLLECTORS = {
    "dns": _dns_entries, "cname": _cname_entries, "adlist": _adlist_entries,
    "domain": _domain_entries, "group": _group_entries, "client": _client_entries,
    "setting": _setting_entries,
}


@router.get("/drift")
def drift() -> dict[str, Any]:
    instances = load_instances()
    snapshots: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for name, snapshot, error in _fanout(instances, _snapshot):
        if error is not None:
            errors[name] = error
        elif snapshot is not None:
            snapshots[name] = snapshot
    instance_names = [instance.name for instance in instances]
    reachable = set(snapshots)
    reachable_names = [name for name in instance_names if name in reachable]
    categories = []
    items = []
    for category, label in CATEGORIES:
        entries = _COLLECTORS[category](snapshots)
        drifting = []
        for entry in sorted(entries, key=lambda entry: entry.key):
            state = _state(set(entry.values), reachable, entry.variants)
            if state != "consistent":
                drifting.append({
                    "id": entry.id, "category": category, "key": entry.key,
                    "detail": entry.detail, "state": state,
                    "values": {name: entry.values.get(name) for name in reachable_names},
                })
        categories.append({
            "category": category, "label": label, "total": len(entries),
            "drifting": len(drifting), "truncated": len(drifting) > DRIFT_ITEMS_PER_CATEGORY,
        })
        items.extend(drifting[:DRIFT_ITEMS_PER_CATEGORY])
    return {
        "instance_names": instance_names, "reachable": reachable_names,
        "comparable": len(reachable) >= 2, "errors": errors,
        "categories": categories, "items": items,
    }
