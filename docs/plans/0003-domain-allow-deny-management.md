# 0003 — Domain allow/deny-list management

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Revision: 2026-09-04 — (a) the live-instance round-trip originally assigned to Codex
during implementation turned out not to be possible: Codex's sandbox has no filesystem
access to `~/.config/pihole-manager` (instance config/credentials live outside the repo
working directory) and no LAN egress to reach pihole1/pihole2, so it correctly stopped
rather than guess. Implementation proceeded directly from the FTL OpenAPI spec fetched
during planning (Approach, below); the live GET/POST/PUT/DELETE round-trip and the
gravity-requirement check moved to a **manual pre-merge verification step** (see
Acceptance criteria), performed by Kyrnveil against pihole1 and confirmed correct. (b) A
Codex adversarial review of the branch diff returned `needs-attention` with two
high-severity findings: `domain_add`'s sequential multi-instance loop
(`app/routes/domains.py`) gives no per-instance recovery signal if the client times out
mid-loop, and `update_domain`'s fetch-then-full-PUT (`app/pihole_client.py`) can
silently clobber a concurrent comment/group edit made outside this app. Both are real,
but both are the exact pattern this plan's Approach deliberately mirrored from the
already-shipped `adlist_add`/`update_adlist` — not a domains-specific regression.
Decision: merge as-is, accepting the same risk profile domains now shares with
adlists; tracked for a future cross-feature hardening pass in `docs/design.md` §15's
"TODO — safety tests" line rather than expanding this plan's scope.
Branch: feature/0003-domain-allow-deny-management
Author: Kyrnveil
Date: 2026-09-04

## Problem

Pi-hole manages two distinct kinds of "what's blocked": subscribed adlists (already
built, §5.3 of `docs/design.md`) and individual per-domain allow/deny overrides — exact
hostnames or regex patterns added one at a time, independent of any subscribed list. Pi-hole
v6 exposes these through `/api/domains`, which this app has never called
(`docs/potential-features.md` §1). Right now the only way to allow or block a single
domain is to edit it directly on each Pi-hole's web UI, once per instance, with no
cross-instance visibility — the exact blind spot the DNS-hosts and adlist pages already
solved for their respective record types.

## Scope

In:
- Backend: `app/pihole_client.py` gains a `Domain` dataclass and
  `get_domains`/`add_domain`/`update_domain`/`delete_domain` methods against Pi-hole's
  `/api/domains` family.
- Backend: a new router `app/routes/domains.py` exposing a unified cross-instance list,
  add (one or more instances, sequential), per-instance enable/disable toggle, and
  per-instance delete — same diff-before-write / one-instance-at-a-time shape as adlists.
- Frontend: `frontend/src/api.ts` gains `Domain`/`DomainRow` types and
  `listDomains`/`addDomain`/`updateDomain`/`deleteDomain`.
- Frontend: a new `frontend/src/pages/DomainsPage.tsx`, registered in `App.tsx`'s sidebar
  as "Domains".
- `docs/design.md` gains a §5.5 describing the feature, mirroring §5.3's shape.
- `README.md`'s feature summary line gains a mention.

Out:
- **Groups** (`/api/clients`, per-client group assignment) — domains are added to the
  default group (`groups: [0]`) only, exactly like adlists today. Group scoping is
  `docs/potential-features.md` §1's separate, larger "Group management" item.
- **CNAME records** — a different Pi-hole config surface (`dns.cnameRecords`), not
  `/api/domains`; a separate future item per `docs/potential-features.md`.
- **Gravity integration** — per `docs/potential-features.md`, domain overrides are
  expected to take effect without a gravity run (unlike adlists). This plan does **not**
  add a gravity-pending banner or `POST /api/domains/{instance}/gravity` endpoint. If
  the manual pre-merge check against a live instance (see Approach and Acceptance
  criteria) finds domain changes actually do need gravity, that is a plan-invalidating
  discovery — revise the plan before merging, do not silently add a gravity step.
- Editing a domain's **comment** after creation, or changing its `type`/`kind` in place
  — Pi-hole's `PUT /api/domains/{type}/{kind}/{domain}` addresses one type+kind+domain
  triple; changing type or kind is a delete + re-add, out of scope for v1 (mirrors
  adlists, which also has no "edit in place" — only enable/disable/delete/re-add).
- Rewriting `docs/design.md`'s historical "MVP scope" narrative (§11) — only §5.5 is
  added.

## Approach

**Client shape mirrors `get_adlists`/`add_adlist`/`update_adlist`/`delete_adlist`, not
the DNS-hosts pattern.** Pi-hole addresses each domain entry individually via
`/api/domains/{type}/{kind}/{domain}` (confirmed against the FTL OpenAPI spec — see
below), so there's no shared-line encoding to reconstruct or roll back the way
`dns.hosts` requires. This is the same per-item-addressable shape as `/api/lists/{address}`.

**Endpoint shapes are taken from Pi-hole's own FTL OpenAPI spec**
(`pi-hole/FTL:src/api/docs/content/specs/{main,domains}.yaml`), fetched during planning:

| Operation | Method | Path |
|---|---|---|
| List all | GET | `/api/domains` |
| Add | POST | `/api/domains/{type}/{kind}` |
| Update one | PUT | `/api/domains/{type}/{kind}/{domain}` |
| Delete one | DELETE | `/api/domains/{type}/{kind}/{domain}` |

Domain object fields per the spec: `domain`, `unicode`, `type` (`allow`/`deny`), `kind`
(`exact`/`regex`), `comment` (nullable), `groups` (`int[]`), `enabled`, `id` (readOnly),
`date_added` (readOnly), `date_modified` (readOnly). This app already treats every
Pi-hole endpoint as a hypothesis to confirm on first real use
(`app/pihole_client.py`'s header docstring); per the Revision note above, that
confirmation happens as a **manual pre-merge step against pihole1 or pihole2** (see
Acceptance criteria) rather than during implementation — Codex's sandbox cannot reach
either the instance config or the instances themselves. Implementation proceeds
directly from the spec. The two things most worth double-checking live, before merge:
whether `type`/`kind` are genuinely path segments (not query params, unlike adlists'
`?type=block`), and whether an add/remove is visible in DNS resolution without a gravity
run (see Scope "Out"). If either disagrees with the spec, fix the code before merging —
do not merge on the strength of the spec alone.

**`app/routes/domains.py` is a new router, not an addition to `app/routes/api.py`.**
`api.py` is already 856 lines covering five feature areas. `app/routes/auth.py` already
establishes the pattern of a self-contained router module with its own singleton
(`manager = AuthManager()`) rather than everything living in one file — `domains.py`
follows that same shape with its own `credentials = CredentialStore()`.

**`domains.py` duplicates `_instance`/`_client`/`_api_error`/`_fanout`/`_FANOUT_WORKERS`
from `api.py` rather than importing them.** Rejected alternative: extract these into a
shared `app/routes/common.py`. That would break `tests/test_api.py`'s `setup_api()`,
which monkeypatches `api.credentials`, `api.load_instances`, `api.PiholeClient`, and
`api.record` as module attributes on `app.routes.api` — a helper relocated to a shared
module would close over that module's own globals instead, silently detaching it from
those monkeypatches. `CredentialStore()` is safe to instantiate twice (verified: it's
stateless besides a cheap keyring-backend probe in `__init__`; storage itself is an
external keyring/file, not in-process state), so a second instance in `domains.py` costs
nothing and keeps `tests/test_domains.py` able to monkeypatch `domains.*` independently,
exactly like `test_api.py` does for `api.*`. `_fanout` is the one helper with no
closed-over state (`items`/`worker` are plain arguments) — it is still duplicated rather
than imported, for symmetry with the other three and because `app/routes/auth.py`
already establishes "each router module is self-contained" as this repo's convention.

**No pre-existence check on add.** Mirrors `adlist_add`: the route calls
`client.add_domain(...)` directly and reports per-instance success/failure from
whatever Pi-hole returns, rather than pre-fetching `get_domains()` to check for a
duplicate. `PATCH`/`DELETE`, like `adlist_update`/`adlist_delete`, do pre-fetch (via a
new `_find_domain`) to get the current item for the audit "before" state and to satisfy
diff-before-write (invariant #2) — same reasoning as `_find_adlist`.

**Validation.** `kind="exact"` domains are validated as DNS hostnames (same pattern as
`_dns_record`'s hostname regex in `api.py`, lower-cased). `kind="regex"` values are not
hostname-validated (they legitimately contain `^`, `$`, `.*`, backslashes) — only
length-capped and checked for control characters, matching this app's existing default
of validating for injection/length, not semantic correctness, at points where Pi-hole
itself is the authority (see `screen_adlist_url`'s syntactic-only checks). Because every
value is `quote(..., safe='')`-encoded into the URL path before use, there is no path-
injection risk from either case; the checks exist so bad input fails as a clean `422`
instead of an opaque Pi-hole error.

Rejected alternative: reuse `screen_adlist_url`'s SSRF-style resolution screen for
domains. Not applicable — a domain string here is never used to construct an outbound
fetch URL (unlike an adlist address); it is a value inside a JSON body / URL path
segment against an already-validated instance destination.

## Files

- `app/pihole_client.py` — add `Domain` dataclass; `get_domains`, `add_domain`,
  `update_domain`, `delete_domain` methods; extend the module docstring's endpoint list.
- `app/routes/domains.py` — **new**. Router, Pydantic input models, `_instance`,
  `_client`, `_api_error`, `_fanout`, `_find_domain`, `_domain_data`, the four route
  handlers.
- `app/main.py` — `from app.routes import domains` (or extend the existing `from
  app.routes import api, auth` import); `app.include_router(domains.router)`.
- `frontend/src/api.ts` — `Domain`, `DomainRow` types; `listDomains`, `addDomain`,
  `updateDomain`, `deleteDomain` functions.
- `frontend/src/pages/DomainsPage.tsx` — **new**. Mirrors `AdlistsPage.tsx`.
- `frontend/src/App.tsx` — sidebar `Menu` item + page switch branch for `domains`.
- `tests/test_pihole_client.py` — `test_domain_requests_use_type_kind_path_segments`.
- `tests/test_domains.py` — **new**. Route-level tests, mirroring `test_api.py`'s
  adlist tests.
- `docs/design.md` — new §5.5 "Domain allow/deny management".
- `README.md` — feature summary line (line 4) gains "domain allow/deny overrides".
- `docs/plans/0003-domain-allow-deny-management.md` — this plan.

## Steps

1. `app/pihole_client.py`: add
   ```python
   @dataclass(frozen=True)
   class Domain:
       id: int | None
       domain: str
       type: str  # "allow" | "deny"
       kind: str  # "exact" | "regex"
       comment: str
       enabled: bool
       groups: tuple[int, ...]
       date_added: int | None
       date_modified: int | None
   ```
   directly below the existing `Adlist` dataclass.
2. `app/pihole_client.py::get_domains(self) -> list[Domain]`: `GET /api/domains`
   (no `type`/`kind` filter — fetch everything in one call), same defensive-parsing
   style as `get_adlists` (raise `PiholeAPIError` on any structurally wrong item; each
   field individually type-checked with a safe default where the existing code does
   that for adlists — `comment`, `id`/`date_added`/`date_modified` optional-int-or-None).
   Response body key is `domains` (per the FTL spec) — `body.get("domains")`.
3. `app/pihole_client.py::add_domain(self, domain: str, type: str, kind: str, comment:
   str = "") -> None`: `POST /api/domains/{type}/{kind}` (both path-quoted with
   `quote(..., safe='')` even though they're validated enums, for defense in depth) with
   json `{"domain": domain, "comment": comment, "enabled": True, "groups": [0]}`.
   Success is `resp.status_code in (200, 201)`, else raise `PiholeAPIError`.
4. `app/pihole_client.py::update_domain(self, item: Domain, *, enabled: bool) -> None`:
   `PUT /api/domains/{quote(item.type)}/{quote(item.kind)}/{quote(item.domain,
   safe='')}` with json `{"comment": item.comment, "enabled": enabled, "groups":
   list(item.groups)}` (mirrors `update_adlist`'s "send the full object back, only the
   toggled field differs" shape). Success `resp.status_code == 200`.
5. `app/pihole_client.py::delete_domain(self, domain: str, type: str, kind: str) ->
   None`: `DELETE /api/domains/{quote(type)}/{quote(kind)}/{quote(domain, safe='')}`.
   Success `resp.status_code in (200, 204)`.
6. `app/pihole_client.py`: extend the module docstring's endpoint list with a `Domain
   allow/deny (VERIFY against a live instance — path shape per the FTL OpenAPI spec, not
   yet round-tripped from this codebase):` block naming the four paths from the Approach
   table.
7. Create `app/routes/domains.py`. Header/imports mirror `app/routes/api.py`'s: `from
   __future__ import annotations`, `concurrent.futures`, `os`, `from typing import Any,
   Callable, Literal, TypeVar`, `from fastapi import APIRouter, HTTPException`, `from
   pydantic import BaseModel, Field`, `from app.audit import record`, `from app.config
   import CredentialStore, Instance, load_instances`, `from app.pihole_client import
   Domain, PiholeAPIError, PiholeClient`. Router: `router = APIRouter(prefix="/api")`.
   `credentials = CredentialStore()`. `_FANOUT_WORKERS` + `_fanout` copied verbatim from
   `api.py` (same env var name `PIHOLE_MANAGER_FANOUT_WORKERS` — it's one process-wide
   knob, sharing the name is intentional even though the constant is duplicated).
   `_instance(name)`, `_client(instance)`, `_api_error(exc)` copied verbatim from
   `api.py` (identical bodies).
8. `app/routes/domains.py`: hostname validator, reusing `_dns_record`'s regex from
   `api.py` but as a standalone `_validate_exact_domain(value: str) -> str` (strip,
   lower, `rstrip(".")`, `re.fullmatch(r"(?=.{1,253}$)(?!-)[a-z0-9_-]+(?:\.(?!-)
   [a-z0-9_-]+)*", value, re.IGNORECASE)` else raise `ValueError`). A separate
   `_validate_regex_domain(value: str) -> str` (strip; `1 <= len <= 1000`; reject any
   character with `ord(c) < 32`; else raise `ValueError`) for `kind="regex"` — no
   hostname-shape check.
9. `app/routes/domains.py`: Pydantic input models —
   ```python
   class DomainAddInput(BaseModel):
       instances: list[str] = Field(min_length=1, max_length=100)
       domain: str = Field(min_length=1, max_length=1000)
       type: Literal["allow", "deny"]
       kind: Literal["exact", "regex"]
       comment: str = Field(default="", max_length=1000)

       @model_validator(mode="after")
       def validate_domain(self):
           self.domain = (_validate_exact_domain if self.kind == "exact" else _validate_regex_domain)(self.domain)
           return self

   class DomainUpdateInput(BaseModel):
       domain: str = Field(min_length=1, max_length=1000)
       type: Literal["allow", "deny"]
       kind: Literal["exact", "regex"]
       enabled: bool

   class DomainDeleteInput(BaseModel):
       domain: str = Field(min_length=1, max_length=1000)
       type: Literal["allow", "deny"]
       kind: Literal["exact", "regex"]
   ```
   (`DomainUpdateInput`/`DomainDeleteInput` intentionally skip domain-shape validation —
   same reasoning as `AdlistUpdateInput`/`AdlistDeleteInput` calling `screen_adlist_url`
   with `resolve=False`: identifying an existing record for enable/disable/delete must
   not depend on it still passing today's syntax rules if it was added by some other
   means. `_find_domain` matches on the raw submitted string, structurally.)
10. `app/routes/domains.py::_domain_data(item: Domain) -> dict[str, Any]`: mirrors
    `_adlist_data` — `{"comment": item.comment, "enabled": item.enabled, "groups":
    list(item.groups), "date_added": item.date_added, "date_modified":
    item.date_modified}` (no `domain`/`type`/`kind`/`id` — those are the row/lookup keys,
    not per-instance payload, matching how `_adlist_data` omits `address`).
11. `app/routes/domains.py::_find_domain(client, domain, type, kind) -> Domain`: `next((d
    for d in client.get_domains() if d.type == type and d.kind == kind and
    d.domain.lower().rstrip(".") == domain.lower().rstrip(".")), None)`; `HTTPException(404,
    "Domain not found on this instance")` if `None`.
12. `app/routes/domains.py::GET /domains` (`domains_list`): fan out `load_instances()`
    with a worker calling `client.get_domains()` inside `_client(instance)`, same
    try/except → `(name, domains, error)` triple shape as `adlists_list`'s worker. Build
    rows keyed by `(type, kind, domain)`: `rows.setdefault((item.type, item.kind,
    item.domain), {"domain": item.domain, "type": item.type, "kind": item.kind,
    "instances": {}})`, `row["instances"][instance_name] = _domain_data(item)`. State
    (`consistent`/`missing`/`conflict`) computed the same way as `adlists_list` — conflict
    when `enabled` differs across instances that have the row, missing when the present
    set isn't every reachable instance. Response: `{"instance_names": [...], "domains":
    sorted(rows.values(), key=lambda r: (r["type"], r["kind"], r["domain"])), "errors":
    errors}`.
13. `app/routes/domains.py::POST /domains` (`domain_add`): same shape as `adlist_add` —
    resolve `payload.instances` against `{i.name: i for i in load_instances()}`, 404 on
    unknown names, then a **sequential `for` loop** (comment: `# sequential on purpose:
    mutation fan-out (ARCHITECT.md invariant #1)`) calling `client.add_domain(payload.domain,
    payload.type, payload.kind, payload.comment)` inside `_client(instance)`, `record(name,
    "domain_add", {}, {"domain": payload.domain, "type": payload.type, "kind": payload.kind,
    "comment": payload.comment, "enabled": True})` on success, same three-way
    `HTTPException`/`PiholeAPIError`/bare `Exception` catch as `adlist_add`. Returns
    `{"results": [...]}`.
14. `app/routes/domains.py::PATCH /domains/{instance_name}` (`domain_update`): `_instance`,
    `_client`, `_find_domain(client, payload.domain, payload.type, payload.kind)` for the
    `before` state, `client.update_domain(before, enabled=payload.enabled)`, `record(...,
    "domain_update", _domain_data(before), {**_domain_data(before), "enabled":
    payload.enabled})`. Returns `{"instance": instance_name, "domain": payload.domain,
    "type": payload.type, "kind": payload.kind, "enabled": payload.enabled}`. Same
    `except HTTPException: raise` / `except Exception as exc: raise _api_error(exc)` wrapper
    as `adlist_update`.
15. `app/routes/domains.py::DELETE /domains/{instance_name}` (`domain_delete`): same shape —
    `_find_domain` for the `before` audit state, `client.delete_domain(payload.domain,
    payload.type, payload.kind)`, `record(..., "domain_delete", _domain_data(before) | {"domain":
    before.domain, "type": before.type, "kind": before.kind}, {})`. Returns `{"instance":
    instance_name, "domain": payload.domain, "type": payload.type, "kind": payload.kind}`.
16. `app/main.py`: change `from app.routes import api, auth` to `from app.routes import
    api, auth, domains`; add `app.include_router(domains.router)` next to the existing
    `app.include_router(api.router)` line.
17. `frontend/src/api.ts`: add types
    ```ts
    export interface Domain {
      comment: string
      enabled: boolean
      groups: number[]
      date_added: number | null
      date_modified: number | null
    }

    export interface DomainRow {
      domain: string
      type: 'allow' | 'deny'
      kind: 'exact' | 'regex'
      instances: Record<string, Domain>
      state: 'consistent' | 'missing' | 'conflict'
    }
    ```
    directly below `AdlistRow`.
18. `frontend/src/api.ts`: add to the `api` object, mirroring the adlist block:
    ```ts
    listDomains: () => request<{
      instance_names: string[]
      domains: DomainRow[]
      errors: Record<string, string>
    }>('/api/domains'),
    addDomain: (instances: string[], domain: string, type: 'allow' | 'deny', kind: 'exact' | 'regex', comment: string) =>
      request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/domains', {
        method: 'POST', body: JSON.stringify({ instances, domain, type, kind, comment }),
      }),
    updateDomain: (instance: string, domain: string, type: 'allow' | 'deny', kind: 'exact' | 'regex', enabled: boolean) =>
      request<{ instance: string; domain: string; type: string; kind: string; enabled: boolean }>(
        `/api/domains/${encodeURIComponent(instance)}`,
        { method: 'PATCH', body: JSON.stringify({ domain, type, kind, enabled }) },
      ),
    deleteDomain: (instance: string, domain: string, type: 'allow' | 'deny', kind: 'exact' | 'regex') =>
      request<{ instance: string; domain: string; type: string; kind: string }>(
        `/api/domains/${encodeURIComponent(instance)}`,
        { method: 'DELETE', body: JSON.stringify({ domain, type, kind }) },
      ),
    ```
19. Create `frontend/src/pages/DomainsPage.tsx`, structured like `AdlistsPage.tsx`:
    - `rowKey` is a composite: `` `${row.type}:${row.kind}:${row.domain}` `` (a `Table`
      `rowKey` function, not a plain field, since `dataIndex="domain"` alone isn't
      unique).
    - Columns: "Domain" (with a `Tag` for `type` — green "Allow" / red "Deny" — and a
      `Tag` for `kind` — "Exact"/"Regex" — next to the domain text), one column per
      instance (enable `Switch` + delete `Popconfirm`, same as `AdlistsPage`'s per-
      instance column — no "domains" count/tooltip since Pi-hole domain entries don't
      carry that metadata), "State" (reuse the same `stateTag` map literal as
      `AdlistsPage`/`DNSPage`).
    - No gravity-pending `Alert` block (Scope "Out").
    - Add modal `Form`: `instances` (multi-`Select`, same as adlists), `domain`
      (`Input`), `type` (`Radio.Group` "Allow"/"Deny", required, no default — forces a
      deliberate choice), `kind` (`Radio.Group` "Exact"/"Regex", default `"exact"`),
      `comment` (`Input.TextArea`, optional).
    - `load`/`add`/`toggle`/`remove` mirror `AdlistsPage`'s functions 1:1 (same
      `modal.confirm` pattern for add/toggle, same `message.success`/`message.error`
      handling, same `working` boolean gate).
20. `frontend/src/App.tsx`: import `DomainsPage` and `SafetyCertificateOutlined` (or
    `FilterOutlined` — pick one `@ant-design/icons` export not already used by another
    menu item) from `@ant-design/icons`; add `{ key: 'domains', icon:
    <FilterOutlined />, label: 'Domains' }` to the `Menu` `items` array between
    `'dns'` and `'adlists'` (so instance → DNS → domains → adlists → settings follows the
    existing "records first, lists second" ordering); extend the page-switch ternary
    chain with `page === 'domains' ? <DomainsPage /> : ...` in the same position.
21. `docs/design.md`: insert a new `### 5.5 Domain allow/deny management` section after
    §5.4 (before the `---` separator at line 158), in §5.3's style:
    ```
    ### 5.5 Domain allow/deny management
    - Per instance: list individual allow/deny domain entries (exact hostname or
      regex), each with its enabled/disabled state — distinct from subscribed adlists
      (§5.3).
    - Cross-instance view mirroring §5.2/§5.3's shape: which domain overrides exist on
      which instances.
    - Add / remove / enable / disable a domain override on a selected instance (or
      apply the same addition across instances, sequentially per §4).
    - No gravity step — domain overrides take effect without a gravity run, unlike
      adlists (§5.3), confirmed against a live instance before this feature merged.
    ```
22. `README.md` line 4: `"record comparison, adlist management, and per-instance
    settings."` → `"record comparison, adlist management, domain allow/deny overrides,
    and per-instance settings."`
23. Add the tests below; `./.venv/bin/pytest`; `cd frontend && npm run build && npm run
    lint`; commit the regenerated `app/static/ui/`.

## Contracts

**New backend routes** (all behind the existing `AuthenticationMiddleware` — no public
allowlist entry added):

- `GET /api/domains` →
  ```json
  {
    "instance_names": ["pihole1", "pihole2"],
    "domains": [
      {
        "domain": "ads.example.com",
        "type": "deny",
        "kind": "exact",
        "instances": {
          "pihole1": {"comment": "", "enabled": true, "groups": [0], "date_added": 1700000000, "date_modified": 1700000000}
        },
        "state": "missing"
      }
    ],
    "errors": {"pihole2": "Unable to reach Pi-hole instance"}
  }
  ```
- `POST /api/domains` body `{"instances": ["pihole1"], "domain": "ads.example.com",
  "type": "deny", "kind": "exact", "comment": ""}` → `{"results": [{"name": "pihole1",
  "success": true, "error": null}]}`.
- `PATCH /api/domains/{instance_name}` body `{"domain": "ads.example.com", "type":
  "deny", "kind": "exact", "enabled": false}` → `{"instance": "pihole1", "domain":
  "ads.example.com", "type": "deny", "kind": "exact", "enabled": false}`.
- `DELETE /api/domains/{instance_name}` body `{"domain": "ads.example.com", "type":
  "deny", "kind": "exact"}` → `{"instance": "pihole1", "domain": "ads.example.com",
  "type": "deny", "kind": "exact"}`.
- Errors: `404` unknown instance/instances (add) or domain-not-found-on-instance
  (update/delete, from `_find_domain`); `422` invalid domain shape (exact-kind syntax,
  length, control characters) or invalid `type`/`kind` enum value; `409` no credential
  stored for the instance (`_client`); `502`/`503` via `_api_error` for a Pi-hole/
  transport failure.

**`frontend/src/api.ts`** — `Domain`, `DomainRow` interfaces and `listDomains`,
`addDomain`, `updateDomain`, `deleteDomain` functions exactly as in Steps 17–18.

**No config/DB schema change.** Domain data is never cached — fetched live from each
Pi-hole on every `GET /api/domains`, same as adlists and DNS records.

**New internal API surface (backend only, not `api.ts`-facing):**
`app.pihole_client.Domain`, `.get_domains`, `.add_domain`, `.update_domain`,
`.delete_domain`; `app.routes.domains.router`.

## Tests

- `tests/test_pihole_client.py::test_domain_requests_use_type_kind_path_segments` —
  mock transport; assert `GET /api/domains` (no query params); `POST
  /api/domains/deny/exact` with body `{"domain": "ads.example.com", "comment": "New",
  "enabled": true, "groups": [0]}`; `PUT /api/domains/deny/exact/ads.example.com` with
  body `{"comment": "Test", "enabled": false, "groups": [0, 2]}`; `DELETE
  /api/domains/deny/exact/ads.example.com`. Mirrors
  `test_adlist_requests_include_block_type_and_preserve_groups`'s structure.
- `tests/test_domains.py` (new, mirroring `test_api.py`'s adlist section):
  - `setup_domains(monkeypatch)` helper: same shape as `setup_api` — a
    `FakeDomainsPiholeClient` implementing `get_domains`/`add_domain`/`update_domain`/
    `delete_domain`, monkeypatching `domains.load_instances`, `domains.credentials`,
    `domains.PiholeClient`, `domains.record`.
  - `test_domains_are_returned_in_unified_view` — two instances, one shared domain row
    → `state: "consistent"`; a domain present on only one → `state: "missing"`; same
    domain+type+kind with `enabled` differing across instances → `state: "conflict"`.
  - `test_domain_add_reports_per_instance_results` — `POST /api/domains` across two
    instances, one instance's fake client raises `PiholeAPIError` → `results` shows one
    `success: true`, one `success: false` with that instance's error, and the successful
    instance's fake client recorded the add.
  - `test_domain_update_toggles_enabled_and_preserves_comment` — `PATCH
    /api/domains/{name}` with `enabled: false` on a domain whose fake-client record has
    `comment: "existing"` → the client's `update_domain` was called with `enabled=False`
    and the unchanged `Domain` object (comment preserved).
  - `test_domain_delete_removes_by_type_kind_domain` — `DELETE /api/domains/{name}`
    removes only the matching `(type, kind, domain)` triple, leaving a same-named domain
    under a different `type`/`kind` untouched (regression guard for the composite key).
  - `test_domain_exact_kind_rejects_invalid_hostname` — `POST /api/domains` with
    `kind: "exact"`, `domain: "not a host!"` → `422`.
  - `test_domain_regex_kind_accepts_non_hostname_pattern` — `POST /api/domains` with
    `kind: "regex"`, `domain: "^ads[0-9]+\\.example\\.com$"` → `201`/success (proves the
    regex path does not run hostname validation).
  - `test_domain_regex_kind_rejects_control_characters` — a domain value containing
    `"\x00"` → `422`, for both `kind` values.
  - `test_domain_not_found_on_instance_returns_404` — `PATCH`/`DELETE` for a
    `(type, kind, domain)` triple absent from the fake client's list → `404`.

## Invariants at risk

(ARCHITECT.md § "Project invariants")

1. **One instance mutated at a time.** `domain_add`'s multi-instance loop is a
   sequential `for`, marked with the same `# sequential on purpose` comment convention
   as `adlist_add`/`blocking_update`. `domain_update`/`domain_delete` each touch exactly
   one instance (path parameter), same as `adlist_update`/`adlist_delete`. Kept.
2. **Diff before write.** `domain_update`/`domain_delete` fetch the current item via
   `_find_domain` before mutating, and that fetched state is what's audited as
   "before" — same shape as `_find_adlist`. Kept.
3. **Gravity updates gated behind real changes.** N/A by design for this feature — no
   gravity call is added at all (Scope "Out"), so there's no gate to get wrong. If the
   manual pre-merge live-instance check (Acceptance criteria) shows domains *do* require
   gravity, that invalidates this plan's Approach — revise the plan and iterate before
   merging, rather than bolting on an unreviewed gravity call.
4. **No background scheduler.** All four new routes are request-scoped; `_fanout`'s
   thread pool is created and `shutdown()` per request, identical to the copy in
   `api.py`. Kept.
5. **FastAPI owns all credentials; browser talks only to `/api`.** `domains.py`'s
   `credentials = CredentialStore()` never sends a password or app-password to the
   frontend — route responses carry only `comment`/`enabled`/`groups`/dates. Kept.
6. **All outbound requests go through `app/url_policy.py`.** Unchanged — `domains.py`
   never constructs a new outbound URL; it calls `PiholeClient`, which already resolves
   through `resolve_instance_destination` for every instance. A domain string is a
   request-body/path value against an already-validated destination, not a target to
   screen. Kept.
7. **Every route behind auth middleware.** `domains.router` has no `/api/auth/*`-style
   public exemption; `AuthenticationMiddleware`'s allowlist in `app/auth_middleware.py`
   is untouched by this plan, so every `/api/domains*` path requires a session. Kept.
8. **Compiled frontend committed.** `frontend/` changes (Steps 17–20) are rebuilt
   (`npm run build`) and `app/static/ui/` committed alongside the source change (Step
   23). Kept.
9. **`main` is production.** This work lands on `feature/0003-domain-allow-deny-
   management`, merged only after an Accept verdict. Kept.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including every test in the Tests section.
- [ ] `cd frontend && npm run build && npm run lint` clean; `app/static/ui/` committed.
- [ ] The Domains page lists exact and regex entries across both configured instances,
      with consistent/missing/conflict states matching the DNS/Adlist pages' visual
      language.
- [ ] Adding an exact-kind domain to two instances at once produces two sequential
      per-instance results, not a single fan-out call (verifiable in the diff: a `for`
      loop, not `_fanout`, in `domain_add`).
- [ ] Enabling/disabling a domain on one instance does not touch any other instance.
- [ ] A regex-kind domain containing `.` `*` `^` `$` is accepted; a malformed exact-kind
      "domain" is rejected with `422`.
- [ ] No `gravity_pending` field, banner, or endpoint exists anywhere in the new code.
- [ ] `git diff main...HEAD` is limited to the Files list (+ this plan).
- [ ] Every new `/api/domains*` route 401s with no session cookie (auth middleware
      applies with no new allowlist entry).
- [x] **Manual, before merge:** Kyrnveil confirms against a real pihole1 or pihole2
      instance that (a) `type`/`kind` are genuinely path segments as implemented, and
      (b) adding/removing a domain override takes effect without a gravity run. Either
      finding disagreeing with this plan is a stop-and-revise, not a silent code change.
      **Confirmed 2026-09-04**: `POST /api/domains/deny/exact` with body
      `{"domain": "manager-verify-test.invalid", ...}` against pihole1
      (192.168.1.2), immediately followed by `dig @192.168.1.2
      manager-verify-test.invalid` (no gravity run in between), returned `0.0.0.0` —
      the path shape is correct as implemented and no gravity step is needed.
