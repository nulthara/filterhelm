"""Read-only query log and 24h statistics across every instance."""
from __future__ import annotations

import concurrent.futures
import os
import time
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import CredentialStore, Instance, load_instances
from app.pihole_client import (
    BLOCKED_STATUSES, QUERY_STATUSES, PiholeAPIError, PiholeClient, QueryRecord,
    StatsSummary, TopClients, TopDomains, Upstreams,
)

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


def _client(instance: Instance) -> PiholeClient:
    password = credentials.get(instance.name)
    if not password:
        raise HTTPException(status_code=409, detail="No credential stored")
    return PiholeClient(instance.base_url, password, verify_ssl=instance.verify_ssl)


WINDOW_MIN, WINDOW_MAX, WINDOW_DEFAULT = 60, 86_400, 3_600
LENGTH_MIN, LENGTH_MAX, LENGTH_DEFAULT = 1, 500, 100
TOP_COUNT = 10
_IP_CHARS = frozenset("0123456789abcdefABCDEF.:*")


def _now() -> float:
    return time.time()


class QueryLogQuery(BaseModel):
    window: int = Field(default=WINDOW_DEFAULT, ge=WINDOW_MIN, le=WINDOW_MAX)
    length: int = Field(default=LENGTH_DEFAULT, ge=LENGTH_MIN, le=LENGTH_MAX)
    domain: str = Field(default="", max_length=253)
    client: str = Field(default="", max_length=253)
    status: str = Field(default="", max_length=32)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        term = value.strip().lower()
        if any(ch.isspace() for ch in term) or "/" in term:
            raise ValueError("A domain filter cannot contain spaces or slashes")
        return term

    @field_validator("client")
    @classmethod
    def validate_client(cls, value: str) -> str:
        term = value.strip()
        if any(ch.isspace() for ch in term) or "/" in term:
            raise ValueError("A client filter cannot contain spaces or slashes")
        return term

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        term = value.strip().upper()
        if term in ("", "BLOCKED"):
            return term.lower()
        if term not in QUERY_STATUSES:
            raise ValueError("Unknown query status")
        return term


def _client_filter(value: str) -> tuple[str | None, str | None]:
    """(client_ip, client_name) for FTL. Exactly one is set when value is non-empty."""
    if not value:
        return None, None
    looks_like_ip = set(value) <= _IP_CHARS and ("." in value or ":" in value)
    return (value, None) if looks_like_ip else (None, value)


def _summary(item: StatsSummary) -> dict[str, Any]:
    return {"total": item.total, "blocked": item.blocked, "percent_blocked": item.percent_blocked,
            "unique_domains": item.unique_domains, "forwarded": item.forwarded, "cached": item.cached,
            "active_clients": item.active_clients, "total_clients": item.total_clients,
            "gravity_domains": item.gravity_domains, "gravity_last_update": item.gravity_last_update}


def _top_domains(item: TopDomains) -> list[dict[str, Any]]:
    return [{"domain": d.domain, "count": d.count} for d in item.domains]


def _top_clients(item: TopClients) -> list[dict[str, Any]]:
    return [{"ip": c.ip, "name": c.name, "count": c.count} for c in item.clients]


def _upstreams(item: Upstreams) -> list[dict[str, Any]]:
    return [{"ip": u.ip, "name": u.name, "port": u.port, "count": u.count,
             "response": u.response, "variance": u.variance} for u in item.upstreams]


def _row(instance: str, item: QueryRecord) -> dict[str, Any]:
    return {"instance": instance, "id": item.id, "time": item.time, "type": item.type,
            "domain": item.domain, "cname": item.cname, "status": item.status,
            "blocked": item.status in BLOCKED_STATUSES,
            "client_ip": item.client_ip, "client_name": item.client_name,
            "upstream": item.upstream, "reply_type": item.reply_type, "reply_time": item.reply_time}


def _query_log_instance(
    instance: Instance, query: QueryLogQuery, since: float,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], str | None]:
    client_ip, client_name = _client_filter(query.client)
    statuses = sorted(BLOCKED_STATUSES) if query.status == "blocked" else [query.status or None]
    try:
        with _client(instance) as client:
            summary = client.get_stats_summary()
            top_domains = {flag: client.get_top_domains(blocked=flag, count=TOP_COUNT) for flag in (False, True)}
            top_clients = {flag: client.get_top_clients(blocked=flag, count=TOP_COUNT) for flag in (False, True)}
            upstreams = client.get_upstreams()
            pages = [
                client.get_queries(start=since, length=query.length, domain=query.domain or None,
                                   client_ip=client_ip, client_name=client_name, status=status)
                for status in statuses
            ]
    except HTTPException as exc:
        return instance.name, None, [], str(exc.detail)
    except PiholeAPIError as exc:
        return instance.name, None, [], str(exc)
    except Exception:  # noqa: BLE001 - transport details are intentionally hidden
        return instance.name, None, [], "Unable to reach Pi-hole instance"
    records = sorted((r for page in pages for r in page.queries), key=lambda r: (-r.time, -r.id))[:query.length]
    rows = [_row(instance.name, r) for r in records]
    return instance.name, {
        "summary": _summary(summary),
        "top_domains": {"permitted": _top_domains(top_domains[False]), "blocked": _top_domains(top_domains[True])},
        "top_clients": {"permitted": _top_clients(top_clients[False]), "blocked": _top_clients(top_clients[True])},
        "upstreams": _upstreams(upstreams),
        "records_filtered": sum(page.records_filtered for page in pages),
        "returned": len(rows),
        "earliest_timestamp": pages[0].earliest_timestamp,
    }, rows, None


def _combined(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not summaries:
        return None
    total = sum(s["total"] for s in summaries)
    blocked = sum(s["blocked"] for s in summaries)
    return {"total": total, "blocked": blocked,
            "percent_blocked": round(blocked * 100 / total, 1) if total else 0.0,
            "instances_reporting": len(summaries)}


@router.get("/query-log")
def query_log(window: int = WINDOW_DEFAULT, length: int = LENGTH_DEFAULT,
              domain: str = "", client: str = "", status: str = "") -> dict[str, Any]:
    try:
        query = QueryLogQuery(window=window, length=length, domain=domain, client=client, status=status)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
    since = _now() - query.window
    instances = load_instances()
    results, errors, rows = {}, {}, []
    for name, payload, instance_rows, error in _fanout(instances, lambda inst: _query_log_instance(inst, query, since)):
        if error is not None:
            errors[name] = error
        else:
            results[name] = payload
            rows.extend(instance_rows)
    rows.sort(key=lambda r: (-r["time"], -r["id"], r["instance"]))
    return {
        "window": query.window, "length": query.length, "status": query.status,
        "domain": query.domain, "client": query.client, "since": since,
        "top_count": TOP_COUNT,
        "instance_names": [i.name for i in instances],
        "instances": results,
        "combined": _combined([r["summary"] for r in results.values()]),
        "queries": rows,
        "errors": errors,
    }
