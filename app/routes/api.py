"""JSON API consumed by the React frontend.

Mutating settings follow read -> diff -> explicit apply; the apply endpoint
re-reads current state rather than trusting the preview returned earlier.
"""
from __future__ import annotations

from collections.abc import Sequence
import concurrent.futures
import hashlib
from datetime import datetime, timezone
import ipaddress
import logging
import os
import re
import time
from typing import Any, Callable, Literal, NoReturn, TypeVar
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from app.audit import record
from app.config import (
    CredentialStore,
    Instance,
    clear_gravity_pending,
    load_instances,
    load_pending_gravity,
    mark_gravity_pending,
    instances_mutation,
    save_instances,
)
from app.pihole_client import Adlist, DatabaseInfo, FtlAction, FtlInfo, PiholeActionUnconfirmedError, PiholeAPIError, PiholeClient, SensorsInfo, SystemInfo
from app.settings_fields import MANAGED_FIELDS, display_setting, get_setting, normalize_setting, set_setting
from app.url_policy import (
    normalize_instance_url,
    resolve_instance_destination,
    screen_adlist_url,
    URLPolicyError,
)

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
SUPPORTED_FTL_MAJOR = 6
_FANOUT_WORKERS = max(1, int(os.environ.get("FILTERHELM_FANOUT_WORKERS", "8")))
_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def _fanout(items: list[_Item], worker: Callable[[_Item], _Result]) -> list[_Result]:
    # issue #10: per-instance checks are independent reads — run them concurrently, not in a 15s-each sequence
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_FANOUT_WORKERS, len(items) or 1)
    ) as executor:
        return list(executor.map(worker, items))


class FtlActionResult(BaseModel):
    instance: str
    action: FtlAction


class InstanceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=2048)
    app_password: str = Field(min_length=1, max_length=512)  # issue #12
    notes: str = Field(default="", max_length=1000)
    verify_ssl: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_instance_url(value)


class InstanceUpdate(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    app_password: str | None = Field(default=None, min_length=1, max_length=512)  # issue #12
    notes: str = Field(default="", max_length=1000)
    verify_ssl: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_instance_url(value)


class SettingsInput(BaseModel):
    values: dict[str, Any]


class DNSRecordInput(BaseModel):
    instance: str = Field(min_length=1, max_length=100)
    hostname: str = Field(min_length=1, max_length=253)
    address: str = Field(min_length=1, max_length=45)


class DNSRecordUpdate(DNSRecordInput):
    old_hostname: str = Field(min_length=1, max_length=253)
    old_address: str = Field(min_length=1, max_length=45)


class CNAMERecordInput(BaseModel):
    instance: str = Field(min_length=1, max_length=100)
    hostname: str = Field(min_length=1, max_length=253)
    target: str = Field(min_length=1, max_length=253)
    ttl: int | None = Field(default=None, ge=0, le=604800)


class CNAMERecordUpdate(CNAMERecordInput):
    old_hostname: str = Field(min_length=1, max_length=253)
    old_target: str = Field(min_length=1, max_length=253)
    old_ttl: int | None = Field(default=None, ge=0, le=604800)


class BlockingInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    enabled: bool
    duration: Literal[10, 30, 300] | None = None

    @model_validator(mode="after")
    def validate_duration(self):
        if self.enabled and self.duration is not None:
            raise ValueError("A resume action must not include a duration")
        return self


def _adlist_key(url: str) -> str:
    # Normalize scheme/host case, port, and trailing slash while keeping raw userinfo,
    # query, and fragment. _match_adlist uses this fallback only after exact matching.
    # Unparseable addresses retain their raw string so they match only themselves.
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return url
    host = (parsed.hostname or "").lower()
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    userinfo, sep, _ = parsed.netloc.rpartition("@")
    if sep:
        netloc = f"{userinfo}@{netloc}"
    path = parsed.path.rstrip("/")
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=netloc, path=path).geturl()


class BulkResult(BaseModel):
    id: str
    success: bool
    changed: bool
    error: str | None


class BulkResponse(BaseModel):
    instance: str
    action: Literal["enable", "disable", "delete"]
    results: list[BulkResult]


class AdlistBulkResponse(BulkResponse):
    gravity_pending: bool


class DNSRecordBulkResponse(BulkResponse):
    action: Literal["delete"]


class CNAMERecordBulkResponse(BulkResponse):
    action: Literal["delete"]


class AdlistBulkInput(BaseModel):
    action: Literal["enable", "disable", "delete"]
    addresses: list[str] = Field(min_length=1, max_length=200)

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, value: list[str]) -> list[str]:
        if any(len(item) > 2048 for item in value):
            raise ValueError("Address too long")
        return value


class DNSRecordRef(BaseModel):
    hostname: str = Field(min_length=1, max_length=253)
    address: str = Field(min_length=1, max_length=45)


class DNSRecordBulkInput(BaseModel):
    instance: str = Field(min_length=1, max_length=100)
    action: Literal["delete"]
    records: list[DNSRecordRef] = Field(min_length=1, max_length=200)


class CNAMERecordRef(BaseModel):
    hostname: str = Field(min_length=1, max_length=253)
    target: str = Field(min_length=1, max_length=253)


class CNAMERecordBulkInput(BaseModel):
    instance: str = Field(min_length=1, max_length=100)
    action: Literal["delete"]
    records: list[CNAMERecordRef] = Field(min_length=1, max_length=200)


class AdlistAddInput(BaseModel):
    instances: list[str] = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=2048)
    comment: str = Field(default="", max_length=1000)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return screen_adlist_url(value)


class AdlistUpdateInput(BaseModel):
    address: str = Field(min_length=1, max_length=2048)
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
    def require_change(self) -> AdlistUpdateInput:
        if self.enabled is None and self.groups is None:
            raise ValueError("Provide enabled or groups")
        return self


class AdlistDeleteInput(BaseModel):
    address: str = Field(min_length=1, max_length=2048)


def _instance(name: str) -> Instance:
    instance = next((item for item in load_instances() if item.name == name), None)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return instance


def _version_tuple(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^v?(\d+(?:\.\d+)*)", value.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _update_available(local: Any, remote: Any) -> bool | None:
    local_version = _version_tuple(local)
    remote_version = _version_tuple(remote)
    if local_version is None or remote_version is None:
        return None
    width = max(len(local_version), len(remote_version))
    return local_version + (0,) * (width - len(local_version)) < remote_version + (0,) * (width - len(remote_version))


def _version_info(raw: dict[str, Any]) -> dict[str, Any]:
    components = {}
    update_states = []
    for name in ("core", "web", "ftl"):
        component = raw.get(name) if isinstance(raw.get(name), dict) else {}
        local_data = component.get("local") if isinstance(component.get("local"), dict) else {}
        remote_data = component.get("remote") if isinstance(component.get("remote"), dict) else {}
        local = local_data.get("version")
        remote = remote_data.get("version")
        update = _update_available(local, remote)
        components[name] = {"local": local, "remote": remote, "update_available": update}
        update_states.append(update)

    docker = raw.get("docker") if isinstance(raw.get("docker"), dict) else {}
    docker_update = _update_available(docker.get("local"), docker.get("remote"))
    components["docker"] = {
        "local": docker.get("local"),
        "remote": docker.get("remote"),
        "update_available": docker_update,
    }
    update_states.append(docker_update)

    ftl_version = _version_tuple(components["ftl"]["local"])
    if ftl_version is None:
        compatibility = "unknown"
        compatibility_detail = "Unable to determine the FTL API version"
    elif ftl_version[0] == SUPPORTED_FTL_MAJOR:
        compatibility = "compatible"
        compatibility_detail = "Supported FTL 6.x API"
    elif ftl_version[0] < SUPPORTED_FTL_MAJOR:
        compatibility = "incompatible"
        compatibility_detail = "FilterHelm requires the FTL 6.x API"
    else:
        compatibility = "untested"
        compatibility_detail = f"FTL {ftl_version[0]}.x is newer than the tested 6.x API"

    return {
        **components,
        "compatibility": compatibility,
        "compatibility_detail": compatibility_detail,
        "update_available": True if True in update_states else False if False in update_states else None,
    }


HEALTH_TEMPERATURE_WARNING_MARGIN = 5.0
HEALTH_RAM_WARNING_PERCENT = 80.0
HEALTH_RAM_CRITICAL_PERCENT = 95.0
HEALTH_SWAP_WARNING_PERCENT = 50.0
HEALTH_LOAD_WARNING_PER_CORE = 1.0
HEALTH_LOAD_CRITICAL_PER_CORE = 2.0


def _health_status(system: SystemInfo, sensors: SensorsInfo | None) -> tuple[
    Literal["healthy", "warning", "critical"], list[str],
]:
    reasons: list[str] = []
    critical = False
    warning = False
    if sensors is not None and sensors.cpu_temp is not None and sensors.hot_limit is not None:
        value, limit, unit = sensors.cpu_temp, sensors.hot_limit, sensors.unit
        if value >= limit:
            critical = True
            reasons.append(f"Temperature {value:.0f} °{unit} is at or above the {limit:.0f} °{unit} limit")
        elif value >= limit - HEALTH_TEMPERATURE_WARNING_MARGIN:
            warning = True
            reasons.append(
                f"Temperature {value:.0f} °{unit} is within {HEALTH_TEMPERATURE_WARNING_MARGIN:.0f} °{unit} "
                f"of the {limit:.0f} °{unit} limit"
            )
    if system.ram_percent >= HEALTH_RAM_CRITICAL_PERCENT:
        critical = True
        reasons.append(f"RAM {system.ram_percent:.1f}% used, at or above {HEALTH_RAM_CRITICAL_PERCENT:.0f}%")
    elif system.ram_percent >= HEALTH_RAM_WARNING_PERCENT:
        warning = True
        reasons.append(f"RAM {system.ram_percent:.1f}% used, at or above {HEALTH_RAM_WARNING_PERCENT:.0f}%")
    if system.swap_percent >= HEALTH_SWAP_WARNING_PERCENT:
        warning = True
        reasons.append(f"Swap {system.swap_percent:.1f}% used, at or above {HEALTH_SWAP_WARNING_PERCENT:.0f}%")
    if system.cores > 0:
        load = system.load[0]
        per_core = load / system.cores
        if per_core >= HEALTH_LOAD_CRITICAL_PER_CORE:
            critical = True
            reasons.append(
                f"Load {load:.2f} is {per_core:.1f} per core, at or above {HEALTH_LOAD_CRITICAL_PER_CORE:.1f}"
            )
        elif per_core >= HEALTH_LOAD_WARNING_PER_CORE:
            warning = True
            reasons.append(
                f"Load {load:.2f} is {per_core:.1f} per core, at or above {HEALTH_LOAD_WARNING_PER_CORE:.1f}"
            )
    return ("critical" if critical else "warning" if warning else "healthy", reasons)


def _health_info(system: SystemInfo, ftl: FtlInfo, database: DatabaseInfo,
                 sensors: SensorsInfo | None) -> dict[str, Any]:
    status, reasons = _health_status(system, sensors)
    return {
        "status": status, "reasons": reasons,
        "host_uptime": system.uptime,
        "ftl_uptime": int(ftl.uptime_ms // 1000),
        "memory": {"total": system.ram_total, "used": system.ram_used,
                   "available": system.ram_available, "percent": system.ram_percent},
        "swap": {"total": system.swap_total, "used": system.swap_used, "percent": system.swap_percent},
        "cpu": {"cores": system.cores, "percent": system.cpu_percent, "load": list(system.load)},
        "temperature": None if sensors is None or sensors.cpu_temp is None else {
            "value": sensors.cpu_temp, "limit": sensors.hot_limit, "unit": sensors.unit,
        },
        "ftl": {"cpu_percent": ftl.cpu_percent, "mem_percent": ftl.mem_percent},
        "database": {"size": database.size, "queries": database.queries_disk,
                     "sqlite_version": database.sqlite_version},
    }


def _connection(instance: Instance, *, health: bool = False) -> tuple[
    Literal["success", "warning", "error"], str, dict[str, Any] | None, dict[str, Any] | None,
]:
    password = credentials.get(instance.name)
    if not password:
        return "warning", "No credential stored", None, None
    try:
        with PiholeClient(instance.base_url, password, verify_ssl=instance.verify_ssl) as client:
            client.test_connection()
            # issue #9: a transient /api/info/version error must not flip a healthy instance to "error"
            try:
                versions = _version_info(client.get_versions())
            except PiholeAPIError:
                versions = None
            health_info = None
            if health:
                try:
                    system = client.get_system_info()
                    ftl = client.get_ftl_info()
                    database = client.get_database_info()
                    try:
                        sensors = client.get_sensors()
                    except PiholeAPIError:
                        sensors = None
                    health_info = _health_info(system, ftl, database, sensors)
                except PiholeAPIError:
                    health_info = None
        if versions is not None and versions["compatibility"] == "incompatible":
            return "warning", "Reachable; incompatible FTL API", versions, health_info
        detail = "Reachable"
        if versions is None:
            detail += "; version details unavailable"
        if health and health_info is None:
            detail += "; health details unavailable"
        return "success", detail, versions, health_info
    except PiholeAPIError as exc:
        return "error", str(exc), None, None
    except Exception:  # noqa: BLE001 - convert transport internals to a stable public error
        return "error", "Unable to reach Pi-hole instance", None, None


def _client(instance: Instance) -> PiholeClient:
    password = credentials.get(instance.name)
    if not password:
        raise HTTPException(status_code=409, detail="No credential stored")
    return PiholeClient(instance.base_url, password, verify_ssl=instance.verify_ssl)


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PiholeAPIError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=503, detail="Unable to reach Pi-hole instance")


def _dns_record(hostname: str, address: str) -> str:
    hostname = hostname.strip().lower().rstrip(".")
    address = address.strip()
    try:
        ipaddress.ip_address(address)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter a valid IPv4 or IPv6 address") from exc
    if not _HOSTNAME_RE.fullmatch(hostname):
        raise HTTPException(status_code=422, detail="Enter a valid DNS hostname")
    return f"{address} {hostname}"


def _canonical_addr(text: str) -> str:
    try:
        return ipaddress.ip_address(text.strip()).compressed
    except ValueError:
        return text.strip()


def _host_entry(raw: str) -> tuple[str, list[str]] | None:
    parts = raw.split()
    if len(parts) < 2:
        return None
    return _canonical_addr(parts[0]), [hostname.lower().rstrip(".") for hostname in parts[1:]]


def _rewrite_without(entry_raw: str, drop_host: str) -> str | None:
    parts = entry_raw.split()
    hosts = [host for host in parts[1:] if host.lower().rstrip(".") != drop_host.lower().rstrip(".")]
    return f"{parts[0]} {' '.join(hosts)}" if hosts else None


_HOSTNAME_RE = re.compile(r"(?=.{1,253}$)(?!-)[a-z0-9_-]+(?:\.(?!-)[a-z0-9_-]+)*", re.IGNORECASE)


def _cname_record(hostname: str, target: str, ttl: int | None) -> str:
    hostname = hostname.strip().lower().rstrip(".")
    target = target.strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(target)
    except ValueError:
        pass
    else:
        raise HTTPException(status_code=422, detail="A CNAME target must be a hostname, not an IP address")
    if not _HOSTNAME_RE.fullmatch(hostname) or not _HOSTNAME_RE.fullmatch(target):
        raise HTTPException(status_code=422, detail="Enter a valid DNS hostname")
    if hostname == target:
        raise HTTPException(status_code=422, detail="A CNAME cannot point at itself")
    if ttl is not None and not 0 <= ttl <= 604800:
        raise HTTPException(status_code=422, detail="TTL must be between 0 and 604800")
    return f"{hostname},{target}" + (f",{ttl}" if ttl is not None else "")


def _cname_entry(raw: str) -> tuple[str, str, int | None] | None:
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) not in (2, 3) or not all(parts):
        return None
    ttl = None
    if len(parts) == 3:
        try:
            ttl = int(parts[2])
        except ValueError:
            return None
        if ttl < 0:
            return None
    return parts[0].lower().rstrip("."), parts[1].lower().rstrip("."), ttl


def _parse_dns_cnames(tree: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for raw in get_setting(tree, "dns.cnameRecords") or []:
        raw_text = str(raw)
        entry = _cname_entry(raw_text)
        if entry is None:
            continue
        hostname, target, ttl = entry
        records.append({"hostname": hostname, "target": target, "ttl": ttl, "raw": raw_text})
    return records


def _cname_settings_tree(instance: Instance) -> dict[str, Any]:
    for attempt in range(4):
        try:
            with _client(instance) as client:
                return client.get_config()
        except httpx.TransportError as exc:
            if attempt == 3:
                raise _api_error(exc) from exc
            time.sleep(1.5)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _api_error(exc) from exc


def _cname_write_add(instance: Instance, client: PiholeClient, record: str) -> None:
    intended = _cname_entry(record)
    for attempt in range(4):
        try:
            client.add_dns_cname(record)
            return
        except httpx.TransportError:
            if attempt == 3:
                raise
            time.sleep(1.5)
        except PiholeAPIError as exc:
            if attempt > 0 and exc.status_code == 400:
                tree = _cname_settings_tree(instance)
                stored = [
                    _cname_entry(str(raw))
                    for raw in get_setting(tree, "dns.cnameRecords") or []
                ]
                if intended in stored:
                    return  # verified: the exact intended record is what's stored
            raise


def _cname_write_delete(instance: Instance, client: PiholeClient, record: str) -> None:
    for attempt in range(4):
        try:
            client.delete_dns_cname(record)
            return
        except httpx.TransportError:
            if attempt == 3:
                raise
            time.sleep(1.5)
        except PiholeAPIError as exc:
            if attempt > 0 and exc.status_code == 404:
                return  # this exact value is gone; our own retried attempt confirms it
            raise


def _partial_dns_failure(
    instance: str,
    event: str,
    *,
    removed: list[str],
    added: list[str],
    setting_key: str = "dns.hosts",
) -> NoReturn:
    # issue #12: a rollback failure must be reported with the exact lost lines, never masked as a generic 502/503
    record(instance, event, {setting_key: removed}, {setting_key: added})
    details = []
    if removed:
        lines = ", ".join(repr(line) for line in removed)
        details.append(
            f"the line{'s' if len(removed) != 1 else ''} {lines} "
            f"{'were' if len(removed) != 1 else 'was'} removed and could not be restored; "
            f"re-add {'them' if len(removed) != 1 else 'it'} in the Pi-hole Local DNS settings"
        )
    if added:
        lines = ", ".join(repr(line) for line in added)
        details.append(
            f"the line{'s' if len(added) != 1 else ''} {lines} "
            f"{'were' if len(added) != 1 else 'was'} added and could not be rolled back; "
            f"remove {'them' if len(added) != 1 else 'it'} in the Pi-hole Local DNS settings"
        )
    raise HTTPException(status_code=502, detail=f"Partially applied — {'; '.join(details)}.")


def _parse_dns_hosts(tree: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for raw in get_setting(tree, "dns.hosts") or []:
        raw_text = str(raw)
        entry = _host_entry(raw_text)
        if entry is None:
            continue
        entry_ip, entry_hosts = entry
        address = raw_text.split()[0]
        for hostname in entry_hosts:
            records.append(
                {
                    "hostname": hostname,
                    "address": address,
                    "entry_ip": entry_ip,
                    "entry_hosts": entry_hosts,
                    "raw": raw_text,
                }
            )
    return records


@router.get("/instances")
def instances_list(health: bool = False):
    def worker(instance: Instance) -> dict[str, Any]:
        status, status_detail, versions, health_info = _connection(instance, health=health)
        return {
            "name": instance.name,
            "base_url": instance.base_url,
            "notes": instance.notes,
            "verify_ssl": instance.verify_ssl,
            "status": status,
            "status_detail": status_detail,
            "versions": versions,
            "health": health_info,
        }

    result = _fanout(load_instances(), worker)
    return {"instances": result}


@router.post("/instances", status_code=201)
def instance_save(payload: InstanceInput):
    # Temporarily unresolvable instance hostnames cannot be saved.
    try:
        resolve_instance_destination(payload.base_url)
    except URLPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with instances_mutation():
        instances = [item for item in load_instances() if item.name != payload.name]
        instance = Instance(
            name=payload.name,
            base_url=payload.base_url.rstrip("/"),
            notes=payload.notes,
            verify_ssl=payload.verify_ssl,
        )
        instances.append(instance)
        save_instances(instances)
        credentials.set(payload.name, payload.app_password)
    return {
        "name": instance.name,
        "base_url": instance.base_url,
        "notes": instance.notes,
        "verify_ssl": instance.verify_ssl,
    }


@router.delete("/instances/{name}", status_code=204)
def instance_delete(name: str):
    with instances_mutation():
        _instance(name)
        save_instances([item for item in load_instances() if item.name != name])
        credentials.delete(name)
        clear_gravity_pending(name)


@router.patch("/instances/{name}")
def instance_update(name: str, payload: InstanceUpdate):
    _instance(name)
    # Temporarily unresolvable instance hostnames cannot be saved.
    try:
        resolve_instance_destination(payload.base_url)
    except URLPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with instances_mutation():
        _instance(name)
        updated = Instance(
            name=name,
            base_url=payload.base_url.rstrip("/"),
            notes=payload.notes,
            verify_ssl=payload.verify_ssl,
        )
        save_instances([updated if item.name == name else item for item in load_instances()])
        if payload.app_password:
            credentials.set(name, payload.app_password)
    return {
        "name": updated.name,
        "base_url": updated.base_url,
        "notes": updated.notes,
        "verify_ssl": updated.verify_ssl,
    }


@router.post("/instances/{name}/test")
def instance_test(name: str):
    instance = _instance(name)
    status, detail, versions, _health = _connection(instance)
    return {"status": status, "detail": detail, "versions": versions}


def _backup_filename(name: str, now: datetime) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "instance"
    return f"pihole-{slug}-{now.strftime('%Y%m%d-%H%M%SZ')}.zip"


@router.post("/instances/{name}/backup")
def instance_backup(name: str) -> Response:
    instance = _instance(name)
    try:
        with _client(instance) as client:
            archive = client.get_teleporter()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    try:
        record(name, "backup_download", {},
               {"bytes": len(archive), "sha256": hashlib.sha256(archive).hexdigest()})
    except Exception as exc:  # noqa: BLE001 - no audit line, no archive
        logger.exception("audit write failed for backup_download on %s", name)
        raise HTTPException(503, detail="Backup not delivered: the audit log could not be written") from exc
    filename = _backup_filename(name, datetime.now(timezone.utc))
    return Response(archive, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


@router.post("/instances/{name}/actions/{action}", response_model=FtlActionResult)
def instance_action(name: str, action: FtlAction) -> dict[str, str]:
    instance = _instance(name)
    try:
        with _client(instance) as client:
            try:
                client.run_action(action)
            except httpx.HTTPError as exc:
                _audit_landed_write(name, f"ftl_{action}", {}, {"status": "unconfirmed", "reason": "transport_error"})
                raise HTTPException(
                    503,
                    detail="The action was sent but Pi-hole did not confirm it; it may have run. Check the instance.",
                ) from exc
            except PiholeActionUnconfirmedError as exc:
                reason = f"http_{exc.status_code}" if exc.status_code is not None and exc.status_code >= 500 else "invalid_response"
                _audit_landed_write(name, f"ftl_{action}", {}, {"status": "unconfirmed", "reason": reason})
                raise HTTPException(
                    502, detail=f"{exc}; the action may have partly run. Check the instance."
                ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    _audit_landed_write(name, f"ftl_{action}", {}, {"status": "success"})
    return {"instance": name, "action": action}


def _blocking_state(instance: Instance) -> dict[str, Any]:
    try:
        with _client(instance) as client:
            state = client.get_blocking()
        return {"name": instance.name, "reachable": True, "enabled": state.enabled, "timer": state.timer, "error": None}
    except HTTPException as exc:
        return {"name": instance.name, "reachable": False, "enabled": None, "timer": None, "error": str(exc.detail)}
    except PiholeAPIError as exc:
        return {"name": instance.name, "reachable": False, "enabled": None, "timer": None, "error": str(exc)}
    except Exception:  # noqa: BLE001 - transport details are intentionally hidden
        return {"name": instance.name, "reachable": False, "enabled": None, "timer": None, "error": "Unable to reach Pi-hole instance"}


@router.get("/status")
def status_list():
    return {"instances": _fanout(load_instances(), _blocking_state)}


@router.post("/blocking")
def blocking_update(payload: BlockingInput):
    configured = {instance.name: instance for instance in load_instances()}
    names = list(dict.fromkeys(payload.instances))
    unknown = [name for name in names if name not in configured]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Instances not found: {', '.join(unknown)}")

    results = []
    # sequential on purpose: mutation fan-out (ARCHITECT.md invariant #1)
    for name in names:
        instance = configured[name]
        try:
            with _client(instance) as client:
                before = client.get_blocking()
                after = client.set_blocking(payload.enabled, payload.duration)
            before_audit = {"enabled": before.enabled, "timer": before.timer}
            after_audit = {"enabled": after.enabled, "timer": after.timer, "requested_duration": payload.duration}
            record(name, "blocking_change", before_audit, after_audit)
            results.append({"name": name, "success": True, "enabled": after.enabled, "timer": after.timer, "error": None})
        except HTTPException as exc:
            results.append({"name": name, "success": False, "enabled": None, "timer": None, "error": str(exc.detail)})
        except PiholeAPIError as exc:
            results.append({"name": name, "success": False, "enabled": None, "timer": None, "error": str(exc)})
        except Exception:  # noqa: BLE001 - transport details are intentionally hidden
            results.append({"name": name, "success": False, "enabled": None, "timer": None, "error": "Unable to reach Pi-hole instance"})
    return {"results": results}


def _adlist_data(adlist) -> dict[str, Any]:
    return {
        "address": adlist.address,
        "comment": adlist.comment,
        "enabled": adlist.enabled,
        "groups": list(adlist.groups),
        "date_updated": adlist.date_updated,
        "number": adlist.number,
        "invalid_domains": adlist.invalid_domains,
    }


@router.get("/adlists")
def adlists_list():
    instances = load_instances()
    lists_by_instance = {}
    errors = {}
    def worker(instance: Instance):
        try:
            with _client(instance) as client:
                return instance.name, client.get_adlists(), None
        except HTTPException as exc:
            return instance.name, None, str(exc.detail)
        except PiholeAPIError as exc:
            return instance.name, None, str(exc)
        except Exception:  # noqa: BLE001 - transport details are intentionally hidden
            return instance.name, None, "Unable to reach Pi-hole instance"

    for instance_name, adlists, error in _fanout(instances, worker):
        if error is not None:
            errors[instance_name] = error
        else:
            lists_by_instance[instance_name] = adlists

    rows = {}
    for instance_name, adlists in lists_by_instance.items():
        for adlist in adlists:
            row = rows.setdefault(adlist.address, {"address": adlist.address, "instances": {}})
            row["instances"][instance_name] = _adlist_data(adlist)
    reachable = set(lists_by_instance)
    for row in rows.values():
        present = set(row["instances"])
        enabled = {item["enabled"] for item in row["instances"].values()}
        row["state"] = "conflict" if len(enabled) > 1 else "missing" if present != reachable else "consistent"
    return {
        "instance_names": [instance.name for instance in instances],
        "adlists": sorted(rows.values(), key=lambda row: row["address"]),
        "errors": errors,
        "gravity_pending": sorted(load_pending_gravity()),
    }


@router.post("/adlists")
def adlist_add(payload: AdlistAddInput):
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
                client.add_adlist(payload.address, payload.comment)
            mark_gravity_pending(name)
            record(name, "adlist_add", {}, {"address": payload.address, "comment": payload.comment, "enabled": True})
            results.append({"name": name, "success": True, "error": None})
        except HTTPException as exc:
            results.append({"name": name, "success": False, "error": str(exc.detail)})
        except PiholeAPIError as exc:
            results.append({"name": name, "success": False, "error": str(exc)})
        except Exception:  # noqa: BLE001
            results.append({"name": name, "success": False, "error": "Unable to update Pi-hole block lists"})
    return {"results": results}


class AdlistAmbiguousError(Exception):
    """A canonical address matches more than one stored block list."""


def _match_adlist(items: Sequence[Adlist], address: str) -> Adlist | None:
    for item in items:
        if item.address == address:
            return item
    key = _adlist_key(address)
    matches = [item for item in items if _adlist_key(item.address) == key]
    if len(matches) >= 2:
        raise AdlistAmbiguousError
    return matches[0] if matches else None


def _find_adlist(client: PiholeClient, address: str) -> Adlist:
    # Exact match comes first; the canonical key is the fallback.
    try:
        adlist = _match_adlist(client.get_adlists(), address)
    except AdlistAmbiguousError as exc:
        raise HTTPException(
            status_code=409,
            detail="Address matches more than one block list on this instance; use the exact stored address",
        ) from exc
    if adlist is None:
        raise HTTPException(status_code=404, detail="Block list not found on this instance")
    return adlist


@router.post("/adlists/{instance_name}/bulk", response_model=AdlistBulkResponse)
def adlist_bulk(instance_name: str, payload: AdlistBulkInput) -> dict[str, Any]:
    instance = _instance(instance_name)
    results: list[dict[str, Any]] = []
    gravity_marked = False
    try:
        with _client(instance) as client:
            current = client.get_adlists()
            claimed: dict[str, str] = {}
            for address in dict.fromkeys(payload.addresses):
                result = {"id": address, "success": False, "changed": False, "error": None}
                try:
                    item = _match_adlist(current, address)
                except AdlistAmbiguousError:
                    result["error"] = "Matches more than one block list on this instance; use the exact stored address"
                    results.append(result)
                    continue
                if item is None:
                    result["error"] = "No longer present on this instance"
                    results.append(result)
                    continue
                if item.address in claimed:
                    result["error"] = f"Same block list as {claimed[item.address]}"
                    results.append(result)
                    continue
                claimed[item.address] = address
                target = payload.action == "enable"
                try:
                    if payload.action == "delete":
                        client.delete_adlist(item.address)
                    elif item.enabled != target:
                        client.update_adlist(item, enabled=target, groups=list(item.groups))
                    else:
                        result["success"] = True          # already in the requested state; nothing to audit
                        results.append(result)
                        continue
                except PiholeAPIError as exc:
                    result["error"] = str(exc)
                    results.append(result)
                    continue
                except Exception:  # noqa: BLE001 - hide transport details
                    result["error"] = "Unable to update Pi-hole block lists"
                    results.append(result)
                    continue
                # The write landed — settle the result and flag gravity from the write's own outcome.
                # A failing audit must never relabel a mutation that succeeded (plan 0012).
                result["success"] = True
                result["changed"] = True
                results.append(result)
                if not gravity_marked:
                    mark_gravity_pending(instance_name)
                    gravity_marked = True
                if payload.action == "delete":
                    _audit_landed_write(instance_name, "adlist_delete", _adlist_data(item), {"bulk": payload.action})
                else:
                    _audit_landed_write(instance_name, "adlist_update", _adlist_data(item), {
                        **_adlist_data(item), "enabled": target, "bulk": payload.action,
                    })
        return {
            "instance": instance_name, "action": payload.action, "results": results,
            "gravity_pending": instance_name in load_pending_gravity(),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.patch("/adlists/{instance_name}")
def adlist_update(instance_name: str, payload: AdlistUpdateInput):
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_adlist(client, payload.address)
            target_enabled = payload.enabled if payload.enabled is not None else before.enabled
            target_groups = payload.groups if payload.groups is not None else list(before.groups)
            client.update_adlist(before, enabled=target_enabled, groups=target_groups)
        mark_gravity_pending(instance_name)
        record(instance_name, "adlist_update", _adlist_data(before), {**_adlist_data(before), "enabled": target_enabled, "groups": target_groups})
        return {"instance": instance_name, "address": payload.address, "enabled": target_enabled, "groups": target_groups, "gravity_pending": True}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.delete("/adlists/{instance_name}")
def adlist_delete(instance_name: str, payload: AdlistDeleteInput):
    instance = _instance(instance_name)
    try:
        with _client(instance) as client:
            before = _find_adlist(client, payload.address)
            client.delete_adlist(before.address)
        mark_gravity_pending(instance_name)
        record(instance_name, "adlist_delete", _adlist_data(before), {})
        return {"instance": instance_name, "address": payload.address, "gravity_pending": True}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.post("/adlists/{instance_name}/gravity")
def gravity_update(instance_name: str):
    instance = _instance(instance_name)
    if instance_name not in load_pending_gravity():
        raise HTTPException(status_code=409, detail="No pending block-list changes for this instance")
    try:
        with _client(instance) as client:
            client.update_gravity()
        clear_gravity_pending(instance_name)
        record(instance_name, "gravity_update", {"pending": True}, {"pending": False})
        return {"instance": instance_name, "gravity_pending": False}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.get("/dns/records")
def dns_records_list():
    instances = load_instances()
    records_by_instance: dict[str, list[dict[str, str]]] = {}
    errors: dict[str, str] = {}
    def worker(instance: Instance):
        try:
            return instance.name, _parse_dns_hosts(_settings_tree(instance)), None
        except HTTPException as exc:
            return instance.name, None, str(exc.detail)

    for instance_name, records, error in _fanout(instances, worker):
        if error is not None:
            errors[instance_name] = error
        else:
            records_by_instance[instance_name] = records

    hostnames: dict[str, dict[str, list[dict[str, str]]]] = {}
    for instance_name, records in records_by_instance.items():
        for record in records:
            public_record = {
                "hostname": record["hostname"],
                "address": record["address"],
                "raw": record["raw"],
            }
            hostnames.setdefault(record["hostname"], {}).setdefault(instance_name, []).append(public_record)

    reachable = set(records_by_instance)
    rows = []
    for hostname, per_instance in sorted(hostnames.items()):
        addresses = {record["address"] for records in per_instance.values() for record in records}
        present = set(per_instance)
        state = "conflict" if len(addresses) > 1 else "missing" if present != reachable else "consistent"
        rows.append({"hostname": hostname, "instances": per_instance, "state": state})
    return {
        "instance_names": [instance.name for instance in instances],
        "records": rows,
        "errors": errors,
    }


@router.post("/dns/records", status_code=201)
def dns_record_add(payload: DNSRecordInput):
    instance = _instance(payload.instance)
    dns_record = _dns_record(payload.hostname, payload.address)
    tree = _settings_tree(instance)
    rows = _parse_dns_hosts(tree)
    hostname = payload.hostname.strip().lower().rstrip(".")
    # issue #6: structural match, not reconstructed-string equality
    if any(
        row["entry_ip"] == _canonical_addr(payload.address) and hostname in row["entry_hosts"]
        for row in rows
    ):
        raise HTTPException(status_code=409, detail="That record already exists on this instance")
    try:
        with _client(instance) as client:
            client.add_dns_host(dns_record)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    record_audit = {"dns.hosts": dns_record}
    record(payload.instance, "dns_record_add", {}, record_audit)
    return {"instance": payload.instance, "record": dns_record}


def _delete_dns_host_record(
    client: PiholeClient, owner: dict[str, Any], hostname: str, instance: str,
) -> str | None:
    # issue #7: rewrite the whole dns.hosts line so sibling hostnames survive
    client.delete_dns_host(owner["raw"])
    replacement = _rewrite_without(owner["raw"], hostname)
    if replacement is not None:
        try:
            client.add_dns_host(replacement)
        except Exception:
            try:
                client.add_dns_host(owner["raw"])
            except Exception:
                _partial_dns_failure(
                    instance,
                    "dns_record_delete_orphaned",
                    removed=[owner["raw"]],
                    added=[],
                )
            raise
    return replacement


@router.post("/dns/records/bulk", response_model=DNSRecordBulkResponse)
def dns_records_bulk(payload: DNSRecordBulkInput) -> dict[str, Any]:
    instance = _instance(payload.instance)
    results: list[dict[str, Any]] = []
    requested: dict[tuple[str, str], DNSRecordRef] = {}
    for ref in payload.records:
        requested.setdefault((_canonical_addr(ref.address), ref.hostname.strip().lower().rstrip(".")), ref)
    try:
        with _client(instance) as client:
            rows = _parse_dns_hosts(client.get_config())
            for (entry_ip, hostname), ref in requested.items():
                result = {"id": f"{ref.address} {ref.hostname}", "success": False, "changed": False, "error": None}
                owner = next((row for row in rows if row["entry_ip"] == entry_ip and hostname in row["entry_hosts"]), None)
                if owner is None:
                    result["error"] = "Record no longer exists on this instance"
                    results.append(result)
                    continue
                try:
                    replacement = _delete_dns_host_record(client, owner, hostname, payload.instance)
                except HTTPException as exc:
                    deleted = [str(item["id"]) for item in results if item["changed"]]
                    detail = (
                        " Already deleted in this batch: " + ", ".join(repr(item) for item in deleted) + "."
                        if deleted else " Nothing else in this batch was applied."
                    )
                    raise HTTPException(status_code=exc.status_code, detail=str(exc.detail) + detail) from exc
                except PiholeAPIError as exc:
                    result["error"] = str(exc)
                    results.append(result)
                    continue
                except Exception:  # noqa: BLE001 - hide transport details
                    result["error"] = "Unable to update Pi-hole DNS records"
                    results.append(result)
                    continue
                rows.remove(owner)
                for sibling in rows[:]:
                    if sibling["raw"] == owner["raw"]:
                        if replacement is None:
                            rows.remove(sibling)
                        else:
                            sibling["raw"] = replacement
                            sibling["entry_hosts"] = [host for host in sibling["entry_hosts"] if host != hostname]
                result["success"] = True
                result["changed"] = True
                results.append(result)
                _audit_landed_write(payload.instance, "dns_record_delete", {"dns.hosts": owner["raw"]}, {"bulk": "delete"})
        return {"instance": payload.instance, "action": "delete", "results": results}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.post("/dns/cnames/bulk", response_model=CNAMERecordBulkResponse)
def dns_cnames_bulk(payload: CNAMERecordBulkInput) -> dict[str, Any]:
    instance = _instance(payload.instance)
    results: list[dict[str, Any]] = []
    requested: dict[tuple[str, str], CNAMERecordRef] = {}
    for ref in payload.records:
        key = (
            ref.hostname.strip().lower().rstrip("."),
            ref.target.strip().lower().rstrip("."),
        )
        requested.setdefault(key, ref)  # first-seen wins, matches next(...) in the single-record route
    try:
        with _client(instance) as client:
            rows = _parse_dns_cnames(client.get_config())
            for (hostname, target), ref in requested.items():
                result: dict[str, Any] = {
                    "id": f"{hostname},{target}",
                    "success": False,
                    "changed": False,
                    "error": None,
                }
                owner = next(
                    (row for row in rows if row["hostname"] == hostname and row["target"] == target),
                    None,
                )
                if owner is None:
                    result["error"] = "CNAME no longer exists on this instance"
                    results.append(result)
                    continue
                try:
                    _cname_write_delete(instance, client, owner["raw"])
                except PiholeAPIError as exc:
                    result["error"] = str(exc)
                    results.append(result)
                    continue
                except Exception:  # noqa: BLE001 - hide transport details
                    result["error"] = "Unable to update Pi-hole CNAME records"
                    results.append(result)
                    continue
                # Revision 1: the delete landed — settle the result from the write's own
                # outcome and append it BEFORE auditing. A failing record() must never
                # relabel a delete that succeeded (docs/potential-features.md §6).
                # Plan 0012 contains audit failures so the batch response survives too.
                result["success"] = True
                result["changed"] = True
                results.append(result)
                _audit_landed_write(
                    payload.instance,
                    "dns_cname_delete",
                    {"dns.cnameRecords": owner["raw"]},
                    {"bulk": "delete"},
                )
        return {"instance": payload.instance, "action": "delete", "results": results}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


@router.delete("/dns/records")
def dns_record_delete(payload: DNSRecordInput):
    instance = _instance(payload.instance)
    _ = _dns_record(payload.hostname, payload.address)  # validate
    tree = _settings_tree(instance)
    hostname = payload.hostname.strip().lower().rstrip(".")
    entry_ip = _canonical_addr(payload.address)
    # issue #6: structural match, not reconstructed-string equality
    owner = next(
        (
            row
            for row in _parse_dns_hosts(tree)
            if row["entry_ip"] == entry_ip and hostname in row["entry_hosts"]
        ),
        None,
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="Record no longer exists on this instance")
    try:
        with _client(instance) as client:
            _delete_dns_host_record(client, owner, hostname, payload.instance)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    record(payload.instance, "dns_record_delete", {"dns.hosts": owner["raw"]}, {})
    return {"deleted": owner["raw"]}


@router.patch("/dns/records")
def dns_record_update(payload: DNSRecordUpdate):
    instance = _instance(payload.instance)
    old_record = _dns_record(payload.old_hostname, payload.old_address)
    new_record = _dns_record(payload.hostname, payload.address)
    if old_record == new_record:
        return {"changed": False, "record": new_record}
    tree = _settings_tree(instance)
    rows = _parse_dns_hosts(tree)
    old_hostname = payload.old_hostname.strip().lower().rstrip(".")
    new_hostname = payload.hostname.strip().lower().rstrip(".")
    # issue #6: structural match, not reconstructed-string equality
    old_owner = next(
        (
            row
            for row in rows
            if row["entry_ip"] == _canonical_addr(payload.old_address)
            and old_hostname in row["entry_hosts"]
        ),
        None,
    )
    if old_owner is None:
        raise HTTPException(status_code=409, detail="The original record changed; refresh and try again")
    if any(
        row["entry_ip"] == _canonical_addr(payload.address)
        and new_hostname in row["entry_hosts"]
        for row in rows
    ):
        raise HTTPException(status_code=409, detail="The replacement record already exists")
    old_replacement = _rewrite_without(old_owner["raw"], old_hostname)
    try:
        with _client(instance) as client:
            client.add_dns_host(new_record)
            try:
                # issue #7: rewrite the whole dns.hosts line so sibling hostnames survive
                client.delete_dns_host(old_owner["raw"])
            except Exception:
                try:
                    client.delete_dns_host(new_record)
                except Exception:
                    _partial_dns_failure(
                        payload.instance,
                        "dns_record_update_orphaned",
                        removed=[],
                        added=[new_record],
                    )
                raise
            if old_replacement is not None:
                try:
                    client.add_dns_host(old_replacement)
                except Exception:
                    try:
                        client.add_dns_host(old_owner["raw"])
                        client.delete_dns_host(new_record)
                    except Exception:
                        # issue #12: keep the audit log truthful when rollback can't complete
                        _partial_dns_failure(
                            payload.instance,
                            "dns_record_update_orphaned",
                            removed=[old_owner["raw"]],
                            added=[new_record],
                        )
                    raise
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    record(payload.instance, "dns_record_update", {"dns.hosts": old_owner["raw"]}, {"dns.hosts": new_record})
    return {"changed": True, "record": new_record}


@router.get("/dns/cnames")
def dns_cnames_list() -> dict[str, Any]:
    instances = load_instances()
    records_by_instance: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}

    def worker(instance: Instance) -> tuple[str, list[dict[str, Any]] | None, str | None]:
        try:
            return instance.name, _parse_dns_cnames(_cname_settings_tree(instance)), None
        except HTTPException as exc:
            return instance.name, None, str(exc.detail)

    for instance_name, records, error in _fanout(instances, worker):
        if error is not None:
            errors[instance_name] = error
        else:
            records_by_instance[instance_name] = records

    hostnames: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for instance_name, records in records_by_instance.items():
        for cname in records:
            hostnames.setdefault(cname["hostname"], {}).setdefault(instance_name, []).append(cname)

    reachable = set(records_by_instance)
    rows = []
    for hostname, per_instance in sorted(hostnames.items()):
        values = {(cname["target"], cname["ttl"]) for records in per_instance.values() for cname in records}
        present = set(per_instance)
        state = "conflict" if len(values) > 1 else "missing" if present != reachable else "consistent"
        rows.append({"hostname": hostname, "instances": per_instance, "state": state})
    return {
        "instance_names": [instance.name for instance in instances],
        "records": rows,
        "errors": errors,
    }


@router.post("/dns/cnames", status_code=201)
def dns_cname_add(payload: CNAMERecordInput) -> dict[str, str]:
    instance = _instance(payload.instance)
    cname_record = _cname_record(payload.hostname, payload.target, payload.ttl)
    rows = _parse_dns_cnames(_cname_settings_tree(instance))
    hostname = cname_record.split(",")[0]
    if any(row["hostname"] == hostname for row in rows):
        raise HTTPException(status_code=409, detail="That CNAME already exists on this instance")
    try:
        with _client(instance) as client:
            client.add_dns_cname(cname_record)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    record(payload.instance, "dns_cname_add", {}, {"dns.cnameRecords": cname_record})
    return {"instance": payload.instance, "record": cname_record}


@router.delete("/dns/cnames")
def dns_cname_delete(payload: CNAMERecordInput) -> dict[str, str]:
    instance = _instance(payload.instance)
    cname_record = _cname_record(payload.hostname, payload.target, payload.ttl)
    hostname, target = cname_record.split(",")[:2]
    owner = next(
        (row for row in _parse_dns_cnames(_cname_settings_tree(instance))
         if row["hostname"] == hostname and row["target"] == target),
        None,
    )
    if owner is None:
        raise HTTPException(status_code=404, detail="CNAME no longer exists on this instance")
    try:
        with _client(instance) as client:
            client.delete_dns_cname(owner["raw"])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    record(payload.instance, "dns_cname_delete", {"dns.cnameRecords": owner["raw"]}, {})
    return {"deleted": owner["raw"]}


@router.patch("/dns/cnames")
def dns_cname_update(payload: CNAMERecordUpdate) -> dict[str, Any]:
    instance = _instance(payload.instance)
    old_record = _cname_record(payload.old_hostname, payload.old_target, payload.old_ttl)
    new_record = _cname_record(payload.hostname, payload.target, payload.ttl)
    if old_record == new_record:
        return {"changed": False, "record": new_record}
    rows = _parse_dns_cnames(_cname_settings_tree(instance))
    old_hostname, old_target = old_record.split(",")[:2]
    new_hostname = new_record.split(",")[0]
    old_owner = next(
        (row for row in rows if row["hostname"] == old_hostname
         and row["target"] == old_target and row["ttl"] == payload.old_ttl),
        None,
    )
    if old_owner is None:
        raise HTTPException(status_code=409, detail="The original CNAME changed; refresh and try again")
    if any(row is not old_owner and row["hostname"] == new_hostname for row in rows):
        raise HTTPException(status_code=409, detail="The replacement CNAME already exists")
    try:
        with _client(instance) as client:
            _cname_write_delete(instance, client, old_owner["raw"])
            try:
                _cname_write_add(instance, client, new_record)
            except Exception:
                try:
                    _cname_write_add(instance, client, old_owner["raw"])
                except Exception:
                    _partial_dns_failure(
                        payload.instance,
                        "dns_cname_update_orphaned",
                        removed=[old_owner["raw"]],
                        added=[],
                        setting_key="dns.cnameRecords",
                    )
                raise
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    record(payload.instance, "dns_cname_update", {"dns.cnameRecords": old_owner["raw"]}, {"dns.cnameRecords": new_record})
    return {"changed": True, "record": new_record}


def _settings_tree(instance: Instance) -> dict[str, Any]:
    try:
        with _client(instance) as client:
            return client.get_config()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc


def _changes(tree: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    unknown = set(values) - set(MANAGED_FIELDS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings: {', '.join(sorted(unknown))}")
    changes = []
    for dotted, submitted in values.items():
        label, kind = MANAGED_FIELDS[dotted]
        value = normalize_setting(dotted, submitted)
        old = get_setting(tree, dotted)
        if old != value:
            changes.append(
                {
                    "key": dotted,
                    "label": label,
                    "before": old,
                    "after": value,
                    "before_display": display_setting(old, kind),
                    "after_display": display_setting(value, kind),
                }
            )
    return changes


@router.get("/settings/{name}")
def settings_get(name: str):
    instance = _instance(name)
    tree = _settings_tree(instance)
    fields = []
    values = {}
    for dotted, (label, kind) in MANAGED_FIELDS.items():
        fields.append({"key": dotted, "label": label, "kind": kind})
        values[dotted] = get_setting(tree, dotted)
    return {"instance": name, "fields": fields, "values": values}


@router.post("/settings/{name}/diff")
def settings_diff(name: str, payload: SettingsInput):
    instance = _instance(name)
    return {"changes": _changes(_settings_tree(instance), payload.values)}


@router.post("/settings/{name}/apply")
def settings_apply(name: str, payload: SettingsInput):
    instance = _instance(name)
    try:
        with _client(instance) as client:
            tree = client.get_config()
            changes = _changes(tree, payload.values)
            if not changes:
                return {"applied": 0, "changes": []}
            patch: dict[str, Any] = {}
            before = {}
            after = {}
            for change in changes:
                set_setting(patch, change["key"], change["after"])
                before[change["key"]] = change["before"]
                after[change["key"]] = change["after"]
            client.patch_config(patch)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _api_error(exc) from exc
    record(name, "settings_change", before, after)
    return {"applied": len(changes), "changes": changes}
