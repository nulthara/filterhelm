"""Group management routes."""
from __future__ import annotations

import concurrent.futures
import os
from typing import Any, Callable, Literal, TypeVar

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.audit import record
from app.config import CredentialStore, Instance, load_instances, mark_gravity_pending
from app.pihole_client import Adlist, Domain, Group, PiholeAPIError, PiholeClient

router = APIRouter(prefix="/api")
credentials = CredentialStore()
_FANOUT_WORKERS = max(1, int(os.environ.get("PIHOLE_MANAGER_FANOUT_WORKERS", "8")))
_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def _fanout(items: list[_Item], worker: Callable[[_Item], _Result]) -> list[_Result]:
    # issue #10: per-instance checks are independent reads — run them concurrently, not in a 15s-each sequence
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_FANOUT_WORKERS, len(items) or 1)
    ) as executor:
        return list(executor.map(worker, items))


def _instance(name: str) -> Instance:
    instance = next((item for item in load_instances() if item.name == name), None)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


def _client(instance: Instance) -> PiholeClient:
    password = credentials.get(instance.name)
    if not password:
        raise HTTPException(status_code=409, detail="No credential stored")
    return PiholeClient(instance.base_url, password, verify_ssl=instance.verify_ssl)


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PiholeAPIError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=503, detail="Unable to reach Pi-hole instance")


def _validate_group_name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 100 or any(ord(character) < 32 for character in value):
        raise ValueError("Enter a valid group name")
    return value


class GroupAddInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    comment: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_group_name(value)


class GroupUpdateInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool


class GroupDeleteInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    orphans: Literal["ignore", "delete", "reassign"] = "ignore"
    confirm_default: bool = False


class GroupOrphanAdlist(BaseModel):
    address: str
    comment: str
    enabled: bool


class GroupOrphanDomain(BaseModel):
    domain: str
    type: Literal["allow", "deny"]
    kind: Literal["exact", "regex"]
    comment: str
    enabled: bool


class GroupOrphans(BaseModel):
    instance: str
    name: str
    group_id: int
    adlists: list[GroupOrphanAdlist]
    domains: list[GroupOrphanDomain]


class OrphanResult(BaseModel):
    kind: Literal["adlist", "domain"]
    id: str
    success: bool
    error: str | None


class GroupDeleteResult(BaseModel):
    instance: str
    name: str
    deleted: bool
    orphans: list[OrphanResult]
    gravity_pending: bool


def _group_data(item: Group) -> dict[str, Any]:
    return {"id": item.id, "comment": item.comment, "enabled": item.enabled, "date_added": item.date_added, "date_modified": item.date_modified}


def _find_group(client: PiholeClient, name: str) -> Group:
    item = next((group for group in client.get_groups() if group.name == name), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Group not found on this instance")
    return item


def _orphans(client: PiholeClient, group_id: int) -> tuple[list[Adlist], list[Domain]]:
    """Return entries whose only group is group_id; multi-group entries survive."""
    adlists = [item for item in client.get_adlists() if tuple(item.groups) == (group_id,)]
    domains = [item for item in client.get_domains() if tuple(item.groups) == (group_id,)]
    return adlists, domains


def _orphan_adlist_data(item: Adlist) -> dict[str, Any]:
    return {"address": item.address, "comment": item.comment, "enabled": item.enabled}


def _orphan_domain_data(item: Domain) -> dict[str, Any]:
    return {"domain": item.domain, "type": item.type, "kind": item.kind, "comment": item.comment, "enabled": item.enabled}


@router.get("/groups/{instance_name}/orphans", response_model=GroupOrphans)
def group_orphans(instance_name: str, name: str = Query(min_length=1, max_length=100)) -> dict[str, Any]:
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            group = _find_group(client, name)
            if group.id is None:
                raise HTTPException(status_code=409, detail="This group has no id on this instance")
            adlists, domains = _orphans(client, group.id)
        return {
            "instance": instance_name, "name": name, "group_id": group.id,
            "adlists": [_orphan_adlist_data(item) for item in adlists],
            "domains": [_orphan_domain_data(item) for item in domains],
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.get("/groups")
def groups_list():
    instances = load_instances()
    groups_by_instance = {}
    errors = {}

    def worker(instance: Instance):
        try:
            with _client(instance) as client:
                return instance.name, client.get_groups(), None
        except HTTPException as exc:
            return instance.name, None, str(exc.detail)
        except PiholeAPIError as exc:
            return instance.name, None, str(exc)
        except Exception:  # noqa: BLE001 - transport details are intentionally hidden
            return instance.name, None, "Unable to reach Pi-hole instance"

    for instance_name, items, error in _fanout(instances, worker):
        if error is not None:
            errors[instance_name] = error
        else:
            groups_by_instance[instance_name] = items
    rows = {}
    for instance_name, items in groups_by_instance.items():
        for item in items:
            row = rows.setdefault(item.name, {"name": item.name, "instances": {}})
            row["instances"][instance_name] = _group_data(item)
    reachable = set(groups_by_instance)
    for row in rows.values():
        present = set(row["instances"])
        enabled = {item["enabled"] for item in row["instances"].values()}
        row["state"] = "conflict" if len(enabled) > 1 else "missing" if present != reachable else "consistent"
    return {"instance_names": [instance.name for instance in instances], "groups": sorted(rows.values(), key=lambda row: row["name"]), "errors": errors}


@router.post("/groups", status_code=201)
def group_add(payload: GroupAddInput):
    configured = {instance.name: instance for instance in load_instances()}
    names = list(dict.fromkeys(payload.instances))
    unknown = [name for name in names if name not in configured]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Instances not found: {', '.join(unknown)}")
    results = []
    # sequential on purpose: mutation fan-out (ARCHITECT.md invariant #1)
    for name in names:
        try:
            with _client(configured[name]) as client:
                client.add_group(payload.name, payload.comment)
            record(name, "group_add", {}, {"name": payload.name, "comment": payload.comment, "enabled": True})
            results.append({"name": name, "success": True, "error": None})
        except HTTPException as exc:
            results.append({"name": name, "success": False, "error": str(exc.detail)})
        except PiholeAPIError as exc:
            results.append({"name": name, "success": False, "error": str(exc)})
        except Exception:  # noqa: BLE001
            results.append({"name": name, "success": False, "error": "Unable to update Pi-hole groups"})
    return {"results": results}


@router.patch("/groups/{instance_name}")
def group_update(instance_name: str, payload: GroupUpdateInput):
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_group(client, payload.name)
            client.update_group(before, enabled=payload.enabled)
        record(instance_name, "group_update", _group_data(before), {**_group_data(before), "enabled": payload.enabled})
        return {"instance": instance_name, "name": payload.name, "enabled": payload.enabled}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.delete("/groups/{instance_name}", response_model=GroupDeleteResult)
def group_delete(instance_name: str, payload: GroupDeleteInput) -> dict[str, Any]:
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_group(client, payload.name)
            if before.id == 0 and not payload.confirm_default:
                raise HTTPException(status_code=400, detail="Deleting the Default group can leave this "
                                    "Pi-hole instance in an inconsistent state. You may be able to recover it by "
                                    "recreating the group, naming it 'Default', and reassociating it with all hosts "
                                    "and rules. This is not guaranteed to work, and you may end up needing to "
                                    "reinstall. Confirm you understand this before proceeding.")
            if before.id == 0 and payload.orphans == "reassign":
                raise HTTPException(status_code=400, detail="Cannot reassign entries to the Default "
                                    "group while deleting it; choose 'delete' or 'ignore' instead.")
            results: list[dict[str, Any]] = []
            if payload.orphans != "ignore":
                if before.id is None:
                    raise HTTPException(status_code=409, detail="This group has no id on this instance")
                adlists, domains = _orphans(client, before.id)
                # One instance, one session, orphan writes first and group deletion last.
                for item in [*adlists, *domains]:
                    is_adlist = isinstance(item, Adlist)
                    kind = "adlist" if is_adlist else "domain"
                    identifier = item.address if is_adlist else item.domain
                    error = None
                    try:
                        if payload.orphans == "delete":
                            if is_adlist:
                                client.delete_adlist(item.address)
                            else:
                                client.delete_domain(item.domain, item.type, item.kind)
                        elif is_adlist:
                            client.update_adlist(item, enabled=item.enabled, groups=[0])
                        else:
                            client.update_domain(item, enabled=item.enabled, groups=[0])
                        operation = "delete" if payload.orphans == "delete" else "update"
                        data = _orphan_adlist_data(item) if is_adlist else _orphan_domain_data(item)
                        record(instance_name, f"{kind}_{operation}", data | {"groups": list(item.groups)},
                               {"cascade": f"group_delete:{payload.name}"})
                    except PiholeAPIError as exc:
                        error = str(exc)
                    except Exception:  # noqa: BLE001 - hide transport details
                        error = "Unable to update Pi-hole entry"
                    results.append({"kind": kind, "id": identifier, "success": error is None, "error": error})
            gravity_pending = any(item["kind"] == "adlist" and item["success"] for item in results)
            if gravity_pending:
                mark_gravity_pending(instance_name)
            deleted = not any(not item["success"] for item in results)
            if deleted:
                client.delete_group(before.name)
                after = {} if payload.orphans == "ignore" else {"orphans": payload.orphans, "orphan_count": len(results)}
                record(instance_name, "group_delete", _group_data(before) | {"name": before.name}, after)
        return {"instance": instance_name, "name": payload.name, "deleted": deleted,
                "orphans": results, "gravity_pending": gravity_pending}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
