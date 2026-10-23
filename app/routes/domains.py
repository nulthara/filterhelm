"""Domain allow/deny management routes."""
from __future__ import annotations

import concurrent.futures
import logging
import os
import re
from collections.abc import Sequence
from typing import Any, Callable, Literal, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.audit import record
from app.config import CredentialStore, Instance, load_instances
from app.pihole_client import Domain, PiholeAPIError, PiholeClient

logger = logging.getLogger(__name__)


def _audit_landed_write(instance: str, action: str, before: dict, after: dict) -> None:
    """Audit a mutation that has already landed on the instance.

    The write is done and cannot be taken back, so a failure to log it must not raise.
    Raising would relabel a successful mutation as failed and, because every bulk route
    wraps its loop in `except Exception: raise _api_error(exc)`, would discard the whole
    batch's per-item results (plan 0012). The lost audit line goes to the server log.

    Deliberately module-local: resolving `record` from this module's globals keeps
    `monkeypatch.setattr(<module>, "record", ...)` working in the existing tests.
    """
    try:
        record(instance, action, before, after)
    except Exception:  # noqa: BLE001 - an audit failure must never mask a landed write
        logger.exception("audit write failed for %s on %s", action, instance)


router = APIRouter(prefix="/api")
credentials = CredentialStore()
_FANOUT_WORKERS = max(1, int(os.environ.get("FILTERHELM_FANOUT_WORKERS", "8")))
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


def _validate_exact_domain(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    if not re.fullmatch(
        r"(?=.{1,253}$)(?!-)[a-z0-9_-]+(?:\.(?!-)[a-z0-9_-]+)*",
        value,
        re.IGNORECASE,
    ):
        raise ValueError("Enter a valid DNS hostname")
    return value


def _validate_regex_domain(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 1000 or any(ord(character) < 32 for character in value):
        raise ValueError("Enter a valid domain regex")
    return value


class DomainBulkResult(BaseModel):
    id: str
    success: bool
    changed: bool
    error: str | None


class DomainBulkResponse(BaseModel):
    instance: str
    action: Literal["enable", "disable", "delete"]
    results: list[DomainBulkResult]


class DomainRef(BaseModel):
    domain: str = Field(min_length=1, max_length=1000)
    type: Literal["allow", "deny"]
    kind: Literal["exact", "regex"]


class DomainBulkInput(BaseModel):
    action: Literal["enable", "disable", "delete"]
    domains: list[DomainRef] = Field(min_length=1, max_length=200)


class DomainAmbiguousError(Exception):
    pass


def _match_domain(items: Sequence[Domain], domain: str, type: str, kind: str) -> Domain | None:
    candidates = [item for item in items if item.type == type and item.kind == kind]
    for item in candidates:
        if item.domain == domain:
            return item
    if kind != "exact":
        return None
    matches = [
        item for item in candidates
        if item.domain.lower().rstrip(".") == domain.lower().rstrip(".")
    ]
    if len(matches) >= 2:
        raise DomainAmbiguousError
    return matches[0] if matches else None


class DomainAddInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    domain: str = Field(min_length=1, max_length=1000)
    type: Literal["allow", "deny"]
    kind: Literal["exact", "regex"]
    comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_domain(self):
        validator = _validate_exact_domain if self.kind == "exact" else _validate_regex_domain
        self.domain = validator(self.domain)
        return self


class DomainUpdateInput(BaseModel):
    domain: str = Field(min_length=1, max_length=1000)
    type: Literal["allow", "deny"]
    kind: Literal["exact", "regex"]
    enabled: bool | None = Field(default=None)
    groups: list[int] | None = Field(default=None, max_length=100)

    @field_validator("groups", mode="before")
    @classmethod
    def validate_groups(cls, value: Any) -> Any:
        if isinstance(value, list) and any(
            isinstance(group, bool)
            or not isinstance(group, int)
            or group < 0
            for group in value
        ):
            raise ValueError("Groups must be non-negative integers")
        return value

    @model_validator(mode="after")
    def require_change(self) -> DomainUpdateInput:
        if self.enabled is None and self.groups is None:
            raise ValueError("Provide enabled or groups")
        return self


class DomainDeleteInput(BaseModel):
    domain: str = Field(min_length=1, max_length=1000)
    type: Literal["allow", "deny"]
    kind: Literal["exact", "regex"]


def _domain_data(item: Domain) -> dict[str, Any]:
    return {
        "comment": item.comment,
        "enabled": item.enabled,
        "groups": list(item.groups),
        "date_added": item.date_added,
        "date_modified": item.date_modified,
    }


def _find_domain(client: PiholeClient, domain: str, type: str, kind: str) -> Domain:
    try:
        item = _match_domain(client.get_domains(), domain, type, kind)
    except DomainAmbiguousError as exc:
        raise HTTPException(
            status_code=409,
            detail="Domain matches more than one record on this instance; use the exact stored value",
        ) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Domain not found on this instance")
    return item


@router.get("/domains")
def domains_list():
    instances = load_instances()
    domains_by_instance = {}
    errors = {}

    def worker(instance: Instance):
        try:
            with _client(instance) as client:
                return instance.name, client.get_domains(), None
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
            domains_by_instance[instance_name] = items

    rows = {}
    for instance_name, items in domains_by_instance.items():
        for item in items:
            key = (item.type, item.kind, item.domain)
            row = rows.setdefault(
                key,
                {"domain": item.domain, "type": item.type, "kind": item.kind, "instances": {}},
            )
            row["instances"][instance_name] = _domain_data(item)
    reachable = set(domains_by_instance)
    for row in rows.values():
        present = set(row["instances"])
        enabled = {item["enabled"] for item in row["instances"].values()}
        row["state"] = "conflict" if len(enabled) > 1 else "missing" if present != reachable else "consistent"
    return {
        "instance_names": [instance.name for instance in instances],
        "domains": sorted(rows.values(), key=lambda row: (row["type"], row["kind"], row["domain"])),
        "errors": errors,
    }


@router.post("/domains", status_code=201)
def domain_add(payload: DomainAddInput):
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
                client.add_domain(payload.domain, payload.type, payload.kind, payload.comment)
            _audit_landed_write(
                name,
                "domain_add",
                {},
                {
                    "domain": payload.domain,
                    "type": payload.type,
                    "kind": payload.kind,
                    "comment": payload.comment,
                    "enabled": True,
                },
            )
            results.append({"name": name, "success": True, "error": None})
        except HTTPException as exc:
            results.append({"name": name, "success": False, "error": str(exc.detail)})
        except PiholeAPIError as exc:
            results.append({"name": name, "success": False, "error": str(exc)})
        except Exception:  # noqa: BLE001 - transport details are intentionally hidden
            results.append({"name": name, "success": False, "error": "Unable to update Pi-hole domains"})
    return {"results": results}


@router.post("/domains/{instance_name}/bulk", response_model=DomainBulkResponse)
def domain_bulk(instance_name: str, payload: DomainBulkInput) -> dict[str, Any]:
    instance = _instance(instance_name)
    results: list[dict[str, Any]] = []
    try:
        with _client(instance) as client:
            current = client.get_domains()
            claimed: dict[tuple[str, str, str], str] = {}
            for type, kind, domain in dict.fromkeys(
                (ref.type, ref.kind, ref.domain) for ref in payload.domains
            ):
                result = {"id": f"{type}:{kind}:{domain}", "success": False, "changed": False, "error": None}
                try:
                    item = _match_domain(current, domain, type, kind)
                except DomainAmbiguousError:
                    result["error"] = "Matches more than one record on this instance; use the exact stored value"
                    results.append(result)
                    continue
                if item is None:
                    result["error"] = "No longer present on this instance"
                    results.append(result)
                    continue
                key = (item.type, item.kind, item.domain)
                if key in claimed:
                    result["error"] = f"Same domain as {claimed[key]}"
                    results.append(result)
                    continue
                claimed[key] = domain
                target = payload.action == "enable"
                try:
                    if payload.action == "delete":
                        client.delete_domain(item.domain, item.type, item.kind)
                    elif item.enabled != target:
                        client.update_domain(item, enabled=target, groups=list(item.groups))
                    else:
                        result["success"] = True          # already in the requested state; nothing to audit
                        results.append(result)
                        continue
                except PiholeAPIError as exc:
                    result["error"] = str(exc)
                    results.append(result)
                    continue
                except Exception:  # noqa: BLE001 - hide transport details
                    result["error"] = "Unable to update Pi-hole domains"
                    results.append(result)
                    continue
                # The write landed — settle the result from the write's own outcome.
                # A failing audit must never relabel a mutation that succeeded (plan 0012).
                result["success"] = True
                result["changed"] = True
                results.append(result)
                if payload.action == "delete":
                    _audit_landed_write(instance_name, "domain_delete", {**_domain_data(item), "domain": item.domain, "type": item.type, "kind": item.kind}, {"bulk": payload.action})
                else:
                    _audit_landed_write(instance_name, "domain_update", _domain_data(item), {
                        **_domain_data(item), "domain": item.domain, "type": item.type, "kind": item.kind, "enabled": target, "bulk": payload.action,
                    })
        return {
            "instance": instance_name, "action": payload.action, "results": results,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.patch("/domains/{instance_name}")
def domain_update(instance_name: str, payload: DomainUpdateInput):
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_domain(client, payload.domain, payload.type, payload.kind)
            target_enabled = payload.enabled if payload.enabled is not None else before.enabled
            target_groups = payload.groups if payload.groups is not None else list(before.groups)
            client.update_domain(before, enabled=target_enabled, groups=target_groups)
        _audit_landed_write(
            instance_name,
            "domain_update",
            _domain_data(before),
            {**_domain_data(before), "enabled": target_enabled, "groups": target_groups},
        )
        return {
            "instance": instance_name,
            "domain": payload.domain,
            "type": payload.type,
            "kind": payload.kind,
            "enabled": target_enabled,
            "groups": target_groups,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.delete("/domains/{instance_name}")
def domain_delete(instance_name: str, payload: DomainDeleteInput):
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_domain(client, payload.domain, payload.type, payload.kind)
            # Delete the matched record's stored value because the exact-kind fallback can match a different spelling.
            client.delete_domain(before.domain, before.type, before.kind)
        _audit_landed_write(
            instance_name,
            "domain_delete",
            _domain_data(before)
            | {"domain": before.domain, "type": before.type, "kind": before.kind},
            {},
        )
        return {
            "instance": instance_name,
            "domain": payload.domain,
            "type": payload.type,
            "kind": payload.kind,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
