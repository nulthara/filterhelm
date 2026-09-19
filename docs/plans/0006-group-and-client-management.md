# 0006 — Group and client management

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Revision: 2026-09-05 — Manual testing against a live instance (pihole1) found `ClientsPage`'s
`Promise.all([listClients(), listGroups()])` intermittently 401s: two concurrent
`POST /api/auth` logins with the same app password against the same Pi-hole instance
race, and Pi-hole sometimes rejects the second one. Fixed by awaiting the two calls
sequentially instead of in parallel (commit `45de9dd`) — the same one-session-per-instance-
at-a-time pattern every other page in this app already follows. This is a real, confirmed
Pi-hole behavior worth remembering for any future page that fans out more than one
authenticated call per instance per load.
Branch: feature/0006-group-and-client-management
Author: Kyrnveil
Date: 2026-09-04

## Problem

Pi-hole scopes adlists and domain overrides to a subset of clients via *groups*
(`groups: [N, ...]` — already present on every `Adlist`/`Domain` this app fetches, but
always hardcoded to `[0]`, the default group, on add). Pi-hole v6 exposes group CRUD via
`/api/groups` and client→group assignment via `/api/clients`, neither of which this app
has ever called (`docs/potential-features.md` §1, "Group management"). Right now every
adlist and domain override applies to every client on an instance; there is no way, from
this app, to say "this adlist only applies to the kids' devices." This plan builds the
group and client CRUD surface — the prerequisite relationship (client ↔ group) — so a
follow-up plan can let Adlists/Domains target a group instead of always defaulting to 0.

## Scope

In:
- Backend: `app/pihole_client.py` gains `Group`/`Client` dataclasses and
  `get_groups`/`add_group`/`update_group`/`delete_group` +
  `get_clients`/`add_client`/`update_client`/`delete_client` methods against Pi-hole's
  `/api/groups` and `/api/clients` families.
- Backend: two new routers, `app/routes/groups.py` and `app/routes/clients.py`, each
  exposing a unified cross-instance list, add (one or more instances, sequential),
  per-instance update, and per-instance delete — same diff-before-write /
  one-instance-at-a-time shape as `domains.py`.
- Frontend: `frontend/src/api.ts` gains `Group`/`GroupRow`/`Client`/`ClientRow` types and
  `listGroups`/`addGroup`/`updateGroup`/`deleteGroup` +
  `listClients`/`addClient`/`updateClientGroups`/`deleteClient`.
- Frontend: two new pages, `frontend/src/pages/GroupsPage.tsx` and
  `frontend/src/pages/ClientsPage.tsx`, registered in `App.tsx`'s sidebar as "Groups" and
  "Clients".
- `docs/design.md` gains §5.6 "Group management" and §5.7 "Client management".
- `README.md`'s feature summary line gains a mention.

Out:
- **Letting Adlists or Domains target a non-default group.** Both still hardcode
  `groups: [0]` on add, exactly as today. This plan only builds the group/client CRUD
  surface those pages would need; wiring group-selection into `AdlistsPage`/`DomainsPage`
  is `docs/potential-features.md` §1's own follow-up, deliberately deferred (confirmed
  with Kyrnveil when scoping this plan — see plan sizing discussion, not recorded in this
  file).
- **Renaming a group in place.** Pi-hole's `PUT /api/groups/{name}` supports sending a
  different `name` to rename, but this app never does — mirrors `domains`'/`adlists`' "no
  edit in place, only enable/disable/delete/re-add" precedent. `update_group` always
  sends the group's own current `name` back unchanged.
- **A group-membership "conflict" state for clients.** Group IDs are per-instance
  (Pi-hole assigns them independently on each gravity database), so the same *name*
  ("Kids") can be id `5` on pihole1 and id `9` on pihole2. Comparing raw `groups: number[]`
  across instances would produce false conflicts. `ClientRow.state` is therefore only
  `consistent`/`missing` — see Approach.
- **Selecting target groups when adding a client to multiple instances at once.** Adding
  a client always uses Pi-hole's own default (`groups: [0]`), matching how
  `add_domain`/`add_adlist` never expose a group picker either. Editing a client's group
  membership is a separate, single-instance action (`ClientsPage`'s "Edit groups" button),
  because the picker must show that one instance's own groups.
- **Client suggestions** (`GET /api/clients/suggestions` — unconfigured clients Pi-hole
  has seen). Read-only convenience, not needed for CRUD; a candidate for a later plan.
- **Preventing deletion of a group or client still referenced elsewhere** (e.g. deleting
  the "Default" group while adlists still reference it). This app doesn't do
  dependency checks before delete anywhere else (adlists/domains delete unconditionally
  too) — Pi-hole is the authority on what that does.
- **Batch delete endpoints** (`POST /api/groups/batchDelete`, `POST /api/clients` batch
  delete). Per-item delete already exists and is what every other page in this app uses;
  batch/multi-select actions are `docs/potential-features.md` §1's separate "Bulk /
  multi-select actions" item.
- Rewriting `docs/design.md`'s historical "MVP scope" narrative (§11) — only §5.6/§5.7 are
  added.

## Approach

**Client shape mirrors `get_domains`/`add_domain`/`update_domain`/`delete_domain`, not
the DNS-hosts pattern.** Both groups and clients are addressed individually —
`/api/groups/{name}` and `/api/clients/{client}` (URI-encoded) — the same
per-item-addressable shape as `/api/domains/{type}/{kind}/{domain}` and `/api/lists/{address}`.

**Endpoint shapes are taken from Pi-hole's own FTL OpenAPI spec**
(`pi-hole/FTL:src/api/docs/content/specs/{groups,clients}.yaml`, fetched during planning
2026-09-04 — same source and same "hypothesis until confirmed live" status `pihole_client.py`'s
docstring already applies to every endpoint here):

| Resource | Operation | Method | Path | Success |
|---|---|---|---|---|
| Group | List all | GET | `/api/groups` | 200, body `{"groups": [...], "took": ...}` |
| Group | Add | POST | `/api/groups` | 201, body `{"name": str, "comment": str\|null, "enabled": bool}` |
| Group | Replace | PUT | `/api/groups/{name}` | 200, body `{"name": str, "comment": str\|null, "enabled": bool}` |
| Group | Delete | DELETE | `/api/groups/{name}` | 204, no body |
| Client | List all | GET | `/api/clients` | 200, body `{"clients": [...], "took": ...}` |
| Client | Add | POST | `/api/clients` | 201, body `{"client": str, "comment": str\|null, "groups": int[]}` |
| Client | Replace | PUT | `/api/clients/{client}` | 200, body `{"comment": str\|null, "groups": int[]}` (no `client` field — identity isn't renameable) |
| Client | Delete | DELETE | `/api/clients/{client}` | 204, no body |

Group object fields: `name`, `comment` (nullable), `enabled`, `id` (readOnly),
`date_added`/`date_modified` (readOnly). Client object fields: `client`, `comment`
(nullable), `groups` (`int[]`, default `[0]`), `id` (readOnly), `name` (readOnly,
resolved hostname, nullable), `date_added`/`date_modified` (readOnly). A client
identifier may be an IPv4/IPv6 address, a CIDR subnet (`192.168.2.0/24`), a MAC address,
a hostname, or an interface (`:eth0`) — per the spec, Pi-hole is the authority on shape,
so validation here is length/control-character only (see Steps), the same lenient
treatment `domains.py` already gives `kind="regex"` values.

**Groups and clients are two routers and two pages, not one.** Rejected alternative: a
single `GroupsPage` with a "Groups"/"Clients" tab, since the two are tightly coupled
(clients reference group IDs). Rejected because every other record type in this app
(DNS hosts, adlists, domains) gets its own sidebar entry despite similar conceptual
adjacency — `ClientsPage` fetching `listGroups()` independently to resolve names is a
small cost for staying consistent with that convention.

**`ClientsPage` resolves a client's raw `groups: number[]` to names on the frontend, not
the backend.** `_group_data` (unlike `_domain_data`/`_adlist_data`) includes the group's
`id` in its per-instance payload — every other per-instance payload in this app omits
`id` because nothing needs to join on it, but `ClientsPage` needs an
`instance → group id → group name` lookup to render a client's memberships as names
instead of bare integers, and to populate the "Edit groups" picker's options. Rejected
alternative: have the backend resolve names into the clients response. Rejected because
that would require `clients_list`'s worker to also call `get_groups()` per instance
(doubling the request count) and would duplicate data already available from
`GET /api/groups` — `ClientsPage` calls both `listGroups()` and `listClients()` on load
and joins them client-side.

**Gravity.** This plan assumes group `enabled` toggles and client group-membership
changes take effect without a gravity rebuild, same as domains (0003) — *unconfirmed*.
Unlike domains (a runtime allow/deny check), group scoping determines which *adlists*
apply to a client, and adlist content changes (0003's sibling feature, already shipped)
do require gravity. Per Codex's sandbox having no LAN egress to pihole1/pihole2 (see
0003's Revision note), this is a **manual pre-merge verification step** (Acceptance
criteria), not a build-time assumption. If a live instance shows group/client changes
need a gravity rebuild to take effect, that invalidates this plan's "no gravity" Approach
and Scope — stop and revise before merging, do not bolt on an unreviewed gravity call.

**Both new routers duplicate `_fanout`/`_instance`/`_client`/`_api_error` rather than
importing them**, for the same reason `domains.py` does (documented there): a shared
helper module would close over its own globals, silently detaching it from
`tests/test_api.py`'s per-module monkeypatches on `api.*`. `CredentialStore()` is safe to
instantiate a third and fourth time (stateless besides a cheap keyring probe).

**No pre-existence check on add**, for both resources — mirrors `add_domain`/`add_adlist`:
the route calls the client method directly and reports per-instance success/failure.
Update/delete pre-fetch (via `_find_group`/`_find_client`) for the diff-before-write
"before" state, same as `_find_domain`.

**Validation.** Group `name`: strip, `1–100` chars, reject control characters (arbitrary
free text, no hostname shape). Client `client`: strip, `1–253` chars, reject control
characters — no shape validation, for the reason given above.

## Files

- `app/pihole_client.py` — add `Group`/`Client` dataclasses; `get_groups`, `add_group`,
  `update_group`, `delete_group`, `get_clients`, `add_client`, `update_client`,
  `delete_client` methods; extend the module docstring's endpoint list.
- `app/routes/groups.py` — **new**. Router, Pydantic input models, `_instance`,
  `_client`, `_api_error`, `_fanout`, `_validate_group_name`, `_group_data`,
  `_find_group`, the four route handlers.
- `app/routes/clients.py` — **new**. Same shape for clients:
  `_validate_client_identifier`, `_client_data`, `_find_client`, four route handlers.
- `app/main.py` — extend `from app.routes import api, auth, domains` to add `groups,
  clients`; two more `app.include_router(...)` calls.
- `frontend/src/api.ts` — `Group`, `GroupRow`, `Client`, `ClientRow` types;
  `listGroups`/`addGroup`/`updateGroup`/`deleteGroup`,
  `listClients`/`addClient`/`updateClientGroups`/`deleteClient` functions.
- `frontend/src/pages/GroupsPage.tsx` — **new**. Mirrors `DomainsPage.tsx` minus the
  type/kind tags.
- `frontend/src/pages/ClientsPage.tsx` — **new**. Mirrors `DomainsPage.tsx`'s structure,
  with a per-instance "Edit groups" modal instead of an enable/disable `Switch`.
- `frontend/src/App.tsx` — two sidebar `Menu` items + page-switch branches.
- `tests/test_pihole_client.py` — `test_group_requests_use_name_path_segment`,
  `test_client_requests_use_client_path_segment`.
- `tests/test_groups.py` — **new**. Route-level tests, mirroring `test_domains.py`.
- `tests/test_clients.py` — **new**. Route-level tests, mirroring `test_domains.py`.
- `docs/design.md` — new §5.6 "Group management", §5.7 "Client management".
- `README.md` — feature summary line gains "group and client management".
- `docs/plans/0006-group-and-client-management.md` — this plan.

## Steps

1. `app/pihole_client.py`: add, directly below `Domain`:
   ```python
   @dataclass(frozen=True)
   class Group:
       id: int | None
       name: str
       comment: str
       enabled: bool
       date_added: int | None
       date_modified: int | None

   @dataclass(frozen=True)
   class Client:
       id: int | None
       client: str
       comment: str
       groups: tuple[int, ...]
       date_added: int | None
       date_modified: int | None
   ```
   (`Client` omits the readOnly `name` (resolved hostname) field — this app has no use
   for it and every other dataclass here already omits fields it doesn't act on.)
2. `app/pihole_client.py::get_groups(self) -> list[Group]`: `GET /api/groups`, same
   defensive-parsing style as `get_domains` (`body.get("groups")`, each field
   individually type-checked, `comment` defaulted to `""`, `id`/dates optional-int-or-None).
3. `app/pihole_client.py::add_group(self, name: str, comment: str = "") -> None`:
   `POST /api/groups` json `{"name": name, "comment": comment, "enabled": True}`.
   Success `resp.status_code in (200, 201)`, else raise `PiholeAPIError`.
4. `app/pihole_client.py::update_group(self, group: Group, *, enabled: bool) -> None`:
   `PUT /api/groups/{quote(group.name, safe='')}` json `{"name": group.name, "comment":
   group.comment, "enabled": enabled}` (name unchanged — no rename, per Scope "Out").
   Success `resp.status_code == 200`.
5. `app/pihole_client.py::delete_group(self, name: str) -> None`:
   `DELETE /api/groups/{quote(name, safe='')}`. Success `resp.status_code in (200, 204)`.
6. `app/pihole_client.py::get_clients(self) -> list[Client]`: `GET /api/clients`,
   `body.get("clients")`, same defensive parsing (`groups` list of non-bool ints,
   `comment` defaulted to `""`).
7. `app/pihole_client.py::add_client(self, client: str, comment: str = "") -> None`:
   `POST /api/clients` json `{"client": client, "comment": comment, "groups": [0]}`.
   Success `resp.status_code in (200, 201)`.
8. `app/pihole_client.py::update_client(self, item: Client, *, groups: list[int]) -> None`:
   `PUT /api/clients/{quote(item.client, safe='')}` json `{"comment": item.comment,
   "groups": groups}` (comment preserved from `item`, only `groups` changes — mirrors
   `update_domain`'s "send the full object back, only the target field differs" shape).
   Success `resp.status_code == 200`.
9. `app/pihole_client.py::delete_client(self, client: str) -> None`:
   `DELETE /api/clients/{quote(client, safe='')}`. Success `resp.status_code in (200, 204)`.
10. `app/pihole_client.py`: extend the module docstring's endpoint list with a `Group
    management (VERIFY against a live instance — not yet round-tripped from this
    codebase):` block and a `Client management (VERIFY...)` block, each naming the four
    paths from the Approach table.
11. Create `app/routes/groups.py`. Header/imports mirror `app/routes/domains.py`'s.
    Router: `router = APIRouter(prefix="/api")`. `credentials = CredentialStore()`.
    `_FANOUT_WORKERS`/`_fanout`/`_instance`/`_client`/`_api_error` copied verbatim from
    `domains.py`.
12. `app/routes/groups.py::_validate_group_name(value: str) -> str`: strip; if not
    `1 <= len(value) <= 100` or any `ord(c) < 32` for `c` in value: raise `ValueError`.
13. `app/routes/groups.py`: Pydantic input models —
    ```python
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
    ```
    (`GroupUpdateInput`/`GroupDeleteInput` skip the strict validator — identifying an
    existing group for enable/disable/delete must not depend on its name still passing
    today's rules, mirrors `DomainUpdateInput`/`DomainDeleteInput`.)
14. `app/routes/groups.py::_group_data(item: Group) -> dict[str, Any]`: `{"id": item.id,
    "comment": item.comment, "enabled": item.enabled, "date_added": item.date_added,
    "date_modified": item.date_modified}` (includes `id`, unlike `_domain_data` — see
    Approach; `name` is the row/lookup key, omitted from the payload like
    `_domain_data` omits `domain`).
15. `app/routes/groups.py::_find_group(client, name) -> Group`: `next((g for g in
    client.get_groups() if g.name == name), None)`; `HTTPException(404, "Group not found
    on this instance")` if `None`. Exact match, not case-folded — group names are
    arbitrary free text (no case-normalization convention like hostnames).
16. `app/routes/groups.py::GET /groups` (`groups_list`): fan out `load_instances()`,
    worker calls `client.get_groups()` inside `_client(instance)`, same
    try/except → `(name, groups, error)` triple shape as `domains_list`'s worker. Rows
    keyed by `item.name`: `rows.setdefault(item.name, {"name": item.name,
    "instances": {}})`, `row["instances"][instance_name] = _group_data(item)`. State
    computed the same way as `domains_list` (conflict when `enabled` differs across
    instances that have the row, missing when the present set isn't every reachable
    instance). Response: `{"instance_names": [...], "groups": sorted(rows.values(),
    key=lambda r: r["name"]), "errors": errors}`.
17. `app/routes/groups.py::POST /groups` (`group_add`): same shape as `domain_add` —
    resolve `payload.instances`, 404 on unknown names, sequential `for` loop
    (`# sequential on purpose: mutation fan-out (ARCHITECT.md invariant #1)`) calling
    `client.add_group(payload.name, payload.comment)`, `record(name, "group_add", {},
    {"name": payload.name, "comment": payload.comment, "enabled": True})` on success,
    same three-way exception handling as `domain_add`. Returns `{"results": [...]}`.
18. `app/routes/groups.py::PATCH /groups/{instance_name}` (`group_update`): `_instance`,
    `_client`, `_find_group(client, payload.name)` for `before`,
    `client.update_group(before, enabled=payload.enabled)`, `record(..., "group_update",
    _group_data(before), {**_group_data(before), "enabled": payload.enabled})`. Returns
    `{"instance": instance_name, "name": payload.name, "enabled": payload.enabled}`.
19. `app/routes/groups.py::DELETE /groups/{instance_name}` (`group_delete`):
    `_find_group` for `before`, `client.delete_group(before.name)`, `record(...,
    "group_delete", _group_data(before) | {"name": before.name}, {})`. Returns
    `{"instance": instance_name, "name": payload.name}`.
20. Create `app/routes/clients.py`, same header/import/helper shape as `groups.py`.
21. `app/routes/clients.py::_validate_client_identifier(value: str) -> str`: strip; if
    not `1 <= len(value) <= 253` or any `ord(c) < 32` for `c` in value: raise `ValueError`.
22. `app/routes/clients.py`: Pydantic input models —
    ```python
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

    class ClientDeleteInput(BaseModel):
        client: str = Field(min_length=1, max_length=253)
    ```
    (`ClientGroupsUpdateInput.groups`: each must be a non-negative int — add
    `@field_validator("groups")` rejecting any `bool` or negative entry, same defensive
    style `get_domains` uses when parsing groups from Pi-hole.)
23. `app/routes/clients.py::_client_data(item: Client) -> dict[str, Any]`: `{"comment":
    item.comment, "groups": list(item.groups), "date_added": item.date_added,
    "date_modified": item.date_modified}` (no `id` — nothing needs to join on a client's
    own id, unlike a group's id which `ClientsPage` needs; `client` is the row/lookup key).
24. `app/routes/clients.py::_find_client(client, identifier) -> Client`: `next((c for c
    in client.get_clients() if c.client.strip().lower() == identifier.strip().lower()),
    None)`; `HTTPException(404, "Client not found on this instance")` if `None`
    (lower/strip match, same leniency precedent as `_find_domain`).
25. `app/routes/clients.py::GET /clients` (`clients_list`): same fan-out shape. Rows keyed
    by `item.client`: `rows.setdefault(item.client, {"client": item.client,
    "instances": {}})`, `row["instances"][instance_name] = _client_data(item)`. State is
    `"missing"` if the present set isn't every reachable instance, else `"consistent"` —
    **no conflict state** (Scope "Out"). Response: `{"instance_names": [...], "clients":
    sorted(rows.values(), key=lambda r: r["client"]), "errors": errors}`.
26. `app/routes/clients.py::POST /clients` (`client_add`): same shape as `group_add` —
    sequential loop calling `client.add_client(payload.client, payload.comment)`,
    `record(name, "client_add", {}, {"client": payload.client, "comment":
    payload.comment, "groups": [0]})`. Returns `{"results": [...]}`.
27. `app/routes/clients.py::PATCH /clients/{instance_name}` (`client_update_groups`):
    `_find_client(client, payload.client)` for `before`,
    `client.update_client(before, groups=payload.groups)`, `record(...,
    "client_update_groups", _client_data(before), {**_client_data(before), "groups":
    payload.groups})`. Returns `{"instance": instance_name, "client": payload.client,
    "groups": payload.groups}`.
28. `app/routes/clients.py::DELETE /clients/{instance_name}` (`client_delete`):
    `_find_client` for `before`, `client.delete_client(before.client)`, `record(...,
    "client_delete", _client_data(before) | {"client": before.client}, {})`. Returns
    `{"instance": instance_name, "client": payload.client}`.
29. `app/main.py`: `from app.routes import api, auth, domains, groups, clients`; add
    `app.include_router(groups.router)` and `app.include_router(clients.router)`.
30. `frontend/src/api.ts`: add types directly below `DomainRow`:
    ```ts
    export interface Group {
      id: number | null
      comment: string
      enabled: boolean
      date_added: number | null
      date_modified: number | null
    }

    export interface GroupRow {
      name: string
      instances: Record<string, Group>
      state: 'consistent' | 'missing' | 'conflict'
    }

    export interface Client {
      comment: string
      groups: number[]
      date_added: number | null
      date_modified: number | null
    }

    export interface ClientRow {
      client: string
      instances: Record<string, Client>
      state: 'consistent' | 'missing'
    }
    ```
31. `frontend/src/api.ts`: add to the `api` object, mirroring the domains block:
    ```ts
    listGroups: () => request<{
      instance_names: string[]
      groups: GroupRow[]
      errors: Record<string, string>
    }>('/api/groups'),
    addGroup: (instances: string[], name: string, comment: string) =>
      request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/groups', {
        method: 'POST', body: JSON.stringify({ instances, name, comment }),
      }),
    updateGroup: (instance: string, name: string, enabled: boolean) =>
      request<{ instance: string; name: string; enabled: boolean }>(
        `/api/groups/${encodeURIComponent(instance)}`,
        { method: 'PATCH', body: JSON.stringify({ name, enabled }) },
      ),
    deleteGroup: (instance: string, name: string) =>
      request<{ instance: string; name: string }>(
        `/api/groups/${encodeURIComponent(instance)}`,
        { method: 'DELETE', body: JSON.stringify({ name }) },
      ),
    listClients: () => request<{
      instance_names: string[]
      clients: ClientRow[]
      errors: Record<string, string>
    }>('/api/clients'),
    addClient: (instances: string[], client: string, comment: string) =>
      request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/clients', {
        method: 'POST', body: JSON.stringify({ instances, client, comment }),
      }),
    updateClientGroups: (instance: string, client: string, groups: number[]) =>
      request<{ instance: string; client: string; groups: number[] }>(
        `/api/clients/${encodeURIComponent(instance)}`,
        { method: 'PATCH', body: JSON.stringify({ client, groups }) },
      ),
    deleteClient: (instance: string, client: string) =>
      request<{ instance: string; client: string }>(
        `/api/clients/${encodeURIComponent(instance)}`,
        { method: 'DELETE', body: JSON.stringify({ client }) },
      ),
    ```
32. Create `frontend/src/pages/GroupsPage.tsx`, structured like `DomainsPage.tsx`:
    - `rowKey="name"`. Columns: "Name" (with comment as secondary text, same layout as
      `DomainsPage`'s domain column minus the type/kind `Tag`s), one column per instance
      (enable `Switch` + delete `Popconfirm`), "State" (`stateTag` map literal, same three
      values as `DomainsPage`).
    - The "Default" group's row gets a `Tooltip` on its `Switch`: "Disabling the Default
      group disables every list and domain still assigned to it." (Every adlist/domain
      this app adds today lands in group 0 — see Problem — so this is a real, easy-to-hit
      footgun worth one line of UI, not a new invariant or backend check.)
    - Add modal `Form`: `instances` (multi-`Select`), `name` (`Input`), `comment`
      (`Input.TextArea`, optional).
    - `load`/`add`/`toggle`/`remove` mirror `DomainsPage`'s functions 1:1.
33. Create `frontend/src/pages/ClientsPage.tsx`:
    - On mount and on `load()`, call both `api.listClients()` and `api.listGroups()`
      (`Promise.all`); store `groupRows: GroupRow[]` in state alongside `rows: ClientRow[]`.
    - `groupNamesByInstance: Record<string, Record<number, string>>` computed from
      `groupRows` (per Approach): for each `GroupRow`, for each `[instance, group]` in
      `row.instances`, if `group.id != null` set `map[instance][group.id] = row.name`.
      Plain function called in `load()`/from `groupRows`, not `useMemo` (matches this
      app's existing preference for computing derived values inline rather than adding
      memoization without a measured need).
    - `rowKey="client"`. Columns: "Client" (identifier + comment secondary text, no
      type/kind tags), one column per instance showing the resolved group names as
      `Tag`s (`groupNamesByInstance[instance]?.[id] ?? '#' + id` for each id in
      `item.groups`, so a group deleted outside this app still renders instead of
      crashing) plus an "Edit groups" `Button` and a delete `Popconfirm`, "State"
      (`consistent`/`missing` only — two-entry `stateTag` map, not three).
    - "Edit groups" opens a `Modal` scoped to one `(instance, row)` pair: a `Select
      mode="multiple"` whose `options` come from `Object.entries(groupNamesByInstance[instance]
      ?? {})`, initial value `item.groups`. On submit, `api.updateClientGroups(instance,
      row.client, selectedIds)`.
    - Add modal `Form`: `instances` (multi-`Select`), `client` (`Input`, placeholder
      "192.168.1.50, AA:BB:CC:DD:EE:FF, hostname, or :eth0"), `comment`
      (`Input.TextArea`, optional) — no group picker (Scope "Out").
    - `load`/`add`/`remove` mirror `DomainsPage`'s functions; the toggle function is
      replaced by `editGroups`.
34. `frontend/src/App.tsx`: import `GroupsPage`, `ClientsPage`, and two unused
    `@ant-design/icons` exports (`TeamOutlined` for Groups, `DesktopOutlined` for
    Clients); add `{ key: 'groups', icon: <TeamOutlined />, label: 'Groups' }` and
    `{ key: 'clients', icon: <DesktopOutlined />, label: 'Clients' }` to the `Menu` items
    array between `'adlists'` and `'settings'`; extend the page-switch ternary chain
    accordingly.
35. `docs/design.md`: insert `### 5.6 Group management` and `### 5.7 Client management`
    after §5.5 (before the `---` separator), in §5.5's style — cross-instance list,
    add/enable/disable/delete for groups; cross-instance list, add/edit-groups/delete for
    clients; explicit note that client group membership is resolved to names client-side
    since group IDs aren't portable across instances; note on gravity per Approach.
36. `README.md`: feature summary line gains "group and client management".
37. Add the tests below; `./.venv/bin/pytest`; `cd frontend && npm run build && npm run
    lint`; commit the regenerated `app/static/ui/`.

## Contracts

**New backend routes** (all behind the existing `AuthenticationMiddleware`):

- `GET /api/groups` →
  ```json
  {
    "instance_names": ["pihole1", "pihole2"],
    "groups": [
      {
        "name": "Kids",
        "instances": {
          "pihole1": {"id": 5, "comment": "", "enabled": true, "date_added": 1700000000, "date_modified": 1700000000}
        },
        "state": "missing"
      }
    ],
    "errors": {"pihole2": "Unable to reach Pi-hole instance"}
  }
  ```
- `POST /api/groups` body `{"instances": ["pihole1"], "name": "Kids", "comment": ""}` →
  `{"results": [{"name": "pihole1", "success": true, "error": null}]}`.
- `PATCH /api/groups/{instance_name}` body `{"name": "Kids", "enabled": false}` →
  `{"instance": "pihole1", "name": "Kids", "enabled": false}`.
- `DELETE /api/groups/{instance_name}` body `{"name": "Kids"}` →
  `{"instance": "pihole1", "name": "Kids"}`.
- `GET /api/clients` →
  ```json
  {
    "instance_names": ["pihole1", "pihole2"],
    "clients": [
      {
        "client": "192.168.1.50",
        "instances": {
          "pihole1": {"comment": "", "groups": [0, 5], "date_added": 1700000000, "date_modified": 1700000000}
        },
        "state": "missing"
      }
    ],
    "errors": {}
  }
  ```
- `POST /api/clients` body `{"instances": ["pihole1"], "client": "192.168.1.50",
  "comment": ""}` → `{"results": [{"name": "pihole1", "success": true, "error": null}]}`.
- `PATCH /api/clients/{instance_name}` body `{"client": "192.168.1.50", "groups": [0, 5]}`
  → `{"instance": "pihole1", "client": "192.168.1.50", "groups": [0, 5]}`.
- `DELETE /api/clients/{instance_name}` body `{"client": "192.168.1.50"}` →
  `{"instance": "pihole1", "client": "192.168.1.50"}`.
- Errors: `404` unknown instance(s) (add) or not-found-on-instance (update/delete);
  `422` invalid `name`/`client` shape (length, control characters) or invalid `groups`
  entries; `409` no credential stored; `502`/`503` via `_api_error` for a Pi-hole/
  transport failure.

**`frontend/src/api.ts`** — `Group`, `GroupRow`, `Client`, `ClientRow` interfaces and
`listGroups`/`addGroup`/`updateGroup`/`deleteGroup`,
`listClients`/`addClient`/`updateClientGroups`/`deleteClient` functions exactly as in
Steps 30–31.

**No config/DB schema change.** Group/client data is never cached — fetched live from
each Pi-hole on every `GET`, same as adlists/domains.

**New internal API surface (backend only, not `api.ts`-facing):**
`app.pihole_client.Group`/`.Client`, `.get_groups`/`.add_group`/`.update_group`/
`.delete_group`, `.get_clients`/`.add_client`/`.update_client`/`.delete_client`;
`app.routes.groups.router`, `app.routes.clients.router`.

## Tests

- `tests/test_pihole_client.py::test_group_requests_use_name_path_segment` — mock
  transport; assert `GET /api/groups`; `POST /api/groups` body `{"name": "Kids",
  "comment": "", "enabled": true}`; `PUT /api/groups/Kids` body `{"name": "Kids",
  "comment": "Test", "enabled": false}`; `DELETE /api/groups/Kids`.
- `tests/test_pihole_client.py::test_client_requests_use_client_path_segment` — mock
  transport; assert `GET /api/clients`; `POST /api/clients` body `{"client":
  "192.168.1.50", "comment": "", "groups": [0]}`; `PUT
  /api/clients/192.168.1.50` body `{"comment": "Test", "groups": [0, 5]}`; `DELETE
  /api/clients/192.168.1.50`; and one case with a client value containing `/`
  (e.g. `"192.168.2.0/24"`) asserting the path segment is percent-encoded (`%2F`).
- `tests/test_groups.py` (new, mirrors `test_domains.py`):
  - `setup_groups(monkeypatch)` / `FakeGroupsPiholeClient` — same shape as
    `setup_domains`/`FakeDomainsPiholeClient`.
  - `test_groups_are_returned_in_unified_view` — consistent/missing/conflict states via
    `enabled` differing across instances.
  - `test_group_add_reports_per_instance_results` — one instance fails, `results`
    reflects both outcomes.
  - `test_group_update_toggles_enabled_and_preserves_comment` and `name`.
  - `test_group_delete_removes_by_name`.
  - `test_group_name_rejects_control_characters` and empty/over-length names → `422`.
  - `test_group_not_found_on_instance_returns_404` (update and delete).
- `tests/test_clients.py` (new, mirrors `test_domains.py`):
  - `setup_clients(monkeypatch)` / `FakeClientsPiholeClient`.
  - `test_clients_are_returned_in_unified_view` — consistent/missing only; a client
    present on both instances with *different* raw `groups` still comes back
    `"consistent"` (regression guard proving there's no conflict state — Scope "Out").
  - `test_client_add_reports_per_instance_results` and defaults `groups` to `[0]`.
  - `test_client_update_groups_replaces_list_and_preserves_comment`.
  - `test_client_delete_removes_by_identifier`, including one case using a lower/upper
    case mismatch between the stored and submitted identifier (mirrors
    `test_domain_delete_uses_stored_form_not_submitted_case`).
  - `test_client_identifier_rejects_control_characters` and empty/over-length values →
    `422`; a CIDR (`192.168.2.0/24`), a MAC, and an interface (`:eth0`) value are all
    *accepted* (regression guard proving no hostname-shape validation is applied).
  - `test_client_not_found_on_instance_returns_404` (update and delete).

## Invariants at risk

(ARCHITECT.md § "Project invariants")

1. **One instance mutated at a time.** `group_add`/`client_add`'s multi-instance loops
   are sequential `for` loops, marked with the same `# sequential on purpose` comment
   convention as `domain_add`. `group_update`/`group_delete`/`client_update_groups`/
   `client_delete` each touch exactly one instance (path parameter). Kept.
2. **Diff before write.** All four update/delete routes fetch the current item via
   `_find_group`/`_find_client` before mutating, and that fetched state is what's
   audited as "before." Kept.
3. **Gravity updates gated behind real changes.** No gravity call is added (Scope
   "Out"), pending the manual pre-merge check (Acceptance criteria) confirming group/
   client changes don't need one. If that check disagrees, this plan's Approach is
   invalidated — revise and iterate before merging, per the note in Approach. At risk,
   explicitly tracked.
4. **No background scheduler.** All new routes are request-scoped; `_fanout`'s thread
   pool is created and shut down per request. Kept.
5. **FastAPI owns all credentials; browser talks only to `/api`.** `groups.py`/
   `clients.py` each get their own `credentials = CredentialStore()`; route responses
   carry only group/client fields, never a password. Kept.
6. **All outbound requests go through `app/url_policy.py`.** Unchanged — neither router
   constructs a new outbound URL; both call `PiholeClient`, which resolves through
   `resolve_instance_destination` for every instance. A group name or client identifier
   is a request-body/path value against an already-validated destination, not a target
   to screen (same reasoning `domains.py`'s Approach gives for domain strings). Kept.
7. **Every route behind auth middleware.** No `/api/auth/*`-style public exemption is
   added; `AuthenticationMiddleware`'s allowlist is untouched. Kept.
8. **Compiled frontend committed.** `frontend/` changes (Steps 30–34) are rebuilt and
   `app/static/ui/` committed alongside the source change (Step 37). Kept.
9. **`main` is production.** This work lands on
   `feature/0006-group-and-client-management`, merged only after an Accept verdict. Kept.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including every test in the Tests section.
- [ ] `cd frontend && npm run build && npm run lint` clean; `app/static/ui/` committed.
- [ ] The Groups page lists groups across both configured instances with
      consistent/missing/conflict states; the Clients page lists clients with
      consistent/missing states only (no conflict tag ever rendered).
- [ ] Adding a group or client to two instances at once produces two sequential
      per-instance results (a `for` loop, not `_fanout`, in `group_add`/`client_add`).
- [ ] Enabling/disabling a group, or editing a client's groups, on one instance does not
      touch any other instance.
- [ ] A client identifier containing `/`, `:`, or a MAC's `:`-separated hex pairs is
      accepted; a value with a control character or over 253 characters is rejected with
      `422`.
- [ ] Editing a client's groups shows that instance's own group names (not raw integers)
      in the picker, sourced from `GroupsPage`'s same `/api/groups` data.
- [ ] `git diff main...HEAD` is limited to the Files list (+ this plan).
- [ ] Every new `/api/groups*`/`/api/clients*` route 401s with no session cookie.
- [ ] **Manual, before merge:** Kyrnveil confirms against a real pihole1 or pihole2
      instance that (a) `name`/`client` are genuinely path segments as implemented, and
      (b) toggling a group's `enabled` state or changing a client's group membership
      takes effect (as observed via `dig` against a domain covered by a group-scoped
      adlist, or equivalent) without a gravity run. Either finding disagreeing with this
      plan is a stop-and-revise, not a silent code change.
