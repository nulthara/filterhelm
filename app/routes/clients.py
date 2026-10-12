"""Client management routes."""
from __future__ import annotations

import concurrent.futures
import os
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.audit import record
from app.config import CredentialStore, Instance, load_instances
from app.pihole_client import Client, PiholeAPIError, PiholeClient

router = APIRouter(prefix="/api")
credentials = CredentialStore()
_FANOUT_WORKERS = max(1, int(os.environ.get("FILTERHELM_FANOUT_WORKERS", "8")))
_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def _fanout(items: list[_Item], worker: Callable[[_Item], _Result]) -> list[_Result]:
    # issue #10: per-instance checks are independent reads — run them concurrently, not in a 15s-each sequence
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(_FANOUT_WORKERS, len(items) or 1)) as executor:
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


def _validate_client_identifier(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 253 or any(ord(character) < 32 for character in value):
        raise ValueError("Enter a valid client identifier")
    return value


class ClientAddInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    client: str = Field(min_length=1, max_length=253)
    comment: str = Field(default="", max_length=1000)

    @field_validator("client")
    @classmethod
    def validate_client(cls, value: str) -> str:
        return _validate_client_identifier(value)


class ClientGroupsUpdateInput(BaseModel):
    client: str = Field(min_length=1, max_length=253)
    groups: list[int] = Field(max_length=100)

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


class ClientDeleteInput(BaseModel):
    client: str = Field(min_length=1, max_length=253)


def _client_data(item: Client) -> dict[str, Any]:
    return {"comment": item.comment, "groups": list(item.groups), "date_added": item.date_added, "date_modified": item.date_modified}


def _find_client(client: PiholeClient, identifier: str) -> Client:
    item = next((item for item in client.get_clients() if item.client.strip().lower() == identifier.strip().lower()), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Client not found on this instance")
    return item


@router.get("/clients")
def clients_list():
    instances = load_instances()
    clients_by_instance = {}
    errors = {}

    def worker(instance: Instance):
        try:
            with _client(instance) as client:
                return instance.name, client.get_clients(), None
        except HTTPException as exc:
            return instance.name, None, str(exc.detail)
        except PiholeAPIError as exc:
            return instance.name, None, str(exc)
        except Exception:  # noqa: BLE001
            return instance.name, None, "Unable to reach Pi-hole instance"

    for instance_name, items, error in _fanout(instances, worker):
        if error is not None:
            errors[instance_name] = error
        else:
            clients_by_instance[instance_name] = items
    rows = {}
    for instance_name, items in clients_by_instance.items():
        for item in items:
            row = rows.setdefault(item.client, {"client": item.client, "instances": {}})
            row["instances"][instance_name] = _client_data(item)
    reachable = set(clients_by_instance)
    for row in rows.values():
        row["state"] = "missing" if set(row["instances"]) != reachable else "consistent"
    return {"instance_names": [instance.name for instance in instances], "clients": sorted(rows.values(), key=lambda row: row["client"]), "errors": errors}


@router.post("/clients", status_code=201)
def client_add(payload: ClientAddInput):
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
                client.add_client(payload.client, payload.comment)
            record(name, "client_add", {}, {"client": payload.client, "comment": payload.comment, "groups": [0]})
            results.append({"name": name, "success": True, "error": None})
        except HTTPException as exc:
            results.append({"name": name, "success": False, "error": str(exc.detail)})
        except PiholeAPIError as exc:
            results.append({"name": name, "success": False, "error": str(exc)})
        except Exception:  # noqa: BLE001
            results.append({"name": name, "success": False, "error": "Unable to update Pi-hole clients"})
    return {"results": results}


@router.patch("/clients/{instance_name}")
def client_update_groups(instance_name: str, payload: ClientGroupsUpdateInput):
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_client(client, payload.client)
            client.update_client(before, groups=payload.groups)
        record(instance_name, "client_update_groups", _client_data(before), {**_client_data(before), "groups": payload.groups})
        return {"instance": instance_name, "client": payload.client, "groups": payload.groups}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.delete("/clients/{instance_name}")
def client_delete(instance_name: str, payload: ClientDeleteInput):
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_client(client, payload.client)
            client.delete_client(before.client)
        record(instance_name, "client_delete", _client_data(before) | {"client": before.client}, {})
        return {"instance": instance_name, "client": payload.client}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
