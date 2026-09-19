"""Read-only domain lookup: which list entries match a domain on each instance."""
from __future__ import annotations

import concurrent.futures
import os
from typing import Any, Callable, Literal, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import CredentialStore, Instance, load_instances
from app.pihole_client import Domain, GravityMatch, PiholeAPIError, PiholeClient, SearchResult

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


def _client(instance: Instance) -> PiholeClient:
    password = credentials.get(instance.name)
    if not password:
        raise HTTPException(status_code=409, detail="No credential stored")
    return PiholeClient(instance.base_url, password, verify_ssl=instance.verify_ssl)


LOOKUP_LIMIT_EXACT = 1000
LOOKUP_LIMIT_PARTIAL = 100


def _limit_for(partial: bool) -> int:
    return LOOKUP_LIMIT_PARTIAL if partial else LOOKUP_LIMIT_EXACT


class LookupQuery(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    partial: bool = False

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        term = value.strip().lower().rstrip(".")
        if not term:
            raise ValueError("Enter a domain to look up")
        if any(ch.isspace() for ch in term) or "/" in term:
            raise ValueError("A lookup term cannot contain spaces or slashes")
        return term


def _domain_match(item: Domain) -> dict[str, Any]:
    return {"domain": item.domain, "type": item.type, "kind": item.kind,
            "comment": item.comment, "enabled": item.enabled, "groups": list(item.groups)}


def _gravity_match(item: GravityMatch) -> dict[str, Any]:
    return {"domain": item.domain, "address": item.address, "type": item.type,
            "comment": item.comment, "enabled": item.enabled, "groups": list(item.groups)}


def _verdict(result: SearchResult) -> tuple[Literal["blocked", "allowed", "unlisted"], str]:
    """FTL check_domain_blocked order; disabled entries never count.

    Callers must pass a complete result set. A capped result can omit a
    higher-precedence entry and invert the answer, so Step 6 withholds the
    verdict instead of calling this.
    """
    domains = [d for d in result.domains if d.enabled]
    gravity = [g for g in result.gravity if g.enabled]
    exact_allow = next((d for d in domains if d.type == "allow" and d.kind == "exact"), None)
    if exact_allow:
        return "allowed", f"Exact allow entry {exact_allow.domain}"
    gravity_allow = next((g for g in gravity if g.type == "allow"), None)
    if gravity_allow:
        return "allowed", f"Allow list {gravity_allow.address}"
    exact_deny = next((d for d in domains if d.type == "deny" and d.kind == "exact"), None)
    if exact_deny:
        return "blocked", f"Exact deny entry {exact_deny.domain}"
    regex_allow = next((d for d in domains if d.type == "allow" and d.kind == "regex"), None)
    if regex_allow:
        return "allowed", f"Regex allow entry {regex_allow.domain}"
    gravity_block = next((g for g in gravity if g.type == "block"), None)
    if gravity_block:
        return "blocked", f"Block list {gravity_block.address}"
    regex_deny = next((d for d in domains if d.type == "deny" and d.kind == "regex"), None)
    if regex_deny:
        return "blocked", f"Regex deny entry {regex_deny.domain}"
    return "unlisted", "No enabled entry matches this domain"


def _lookup_instance(instance: Instance, query: LookupQuery) -> tuple[str, dict[str, Any] | None, str | None]:
    limit = _limit_for(query.partial)
    try:
        with _client(instance) as client:
            result = client.search_domain(query.domain, partial=query.partial, limit=limit)
    except HTTPException as exc:
        return instance.name, None, str(exc.detail)
    except PiholeAPIError as exc:
        return instance.name, None, str(exc)
    except Exception:  # noqa: BLE001 - transport details are intentionally hidden
        return instance.name, None, "Unable to reach Pi-hole instance"
    returned = len(result.domains) + len(result.gravity)
    truncated = result.counts.total > returned
    if query.partial or truncated:
        # A capped result can hide the entry that decides the answer.
        verdict, reason = None, None
    else:
        verdict, reason = _verdict(result)
    return instance.name, {
        "verdict": verdict,
        "reason": reason,
        "domains": [_domain_match(item) for item in result.domains],
        "gravity": [_gravity_match(item) for item in result.gravity],
        "counts": {
            "domains_exact": result.counts.domains_exact,
            "domains_regex": result.counts.domains_regex,
            "gravity_allow": result.counts.gravity_allow,
            "gravity_block": result.counts.gravity_block,
            "total": result.counts.total,
        },
        "truncated": truncated,
    }, None


@router.get("/lookup")
def lookup(domain: str, partial: bool = False) -> dict[str, Any]:
    try:
        query = LookupQuery(domain=domain, partial=partial)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
    instances = load_instances()
    results, errors = {}, {}
    for name, payload, error in _fanout(instances, lambda inst: _lookup_instance(inst, query)):
        if error is not None:
            errors[name] = error
        else:
            results[name] = payload
    return {
        "term": query.domain,
        "partial": query.partial,
        "limit": _limit_for(query.partial),
        "instance_names": [i.name for i in instances],
        "instances": results,
        "errors": errors,
    }
