# 0007 — Group targeting for adlists and domains

Status: shipped           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0007-adlist-domain-group-targeting
Author: Kyrnveil
Date: 2026-09-05

## Revision note (2026-09-05, post-adversarial-review)

The first implementation had the "Edit groups" modal re-send the `enabled` value captured
at table-load time (`updateAdlist(instance, address, currentEnabled, groups)`). A Codex
adversarial review flagged this as a lost-update race: if another session toggles an
adlist/domain off between this user's page load and their group save, the group save
silently turns it back on — re-activating a disabled blocklist or allow exception without
intent. The backend re-fetches `before` but still wrote `payload.enabled`, so that read
gave no protection.

Fix: `enabled` becomes **optional** in `AdlistUpdateInput`/`DomainUpdateInput` too, with
the same omitted-means-preserve semantics `groups` now has. The "Edit groups" flow omits
`enabled` entirely, so the route preserves `before.enabled`; the enable/disable `Switch`
omits `groups`, so the route preserves `before.groups`. A payload carrying neither field
is rejected (422). Steps 3–11, Contracts, Tests, Invariant 2, and Acceptance criteria
below reflect this.

## Problem

Plan 0006 built group and client CRUD (`GroupsPage`, `ClientsPage`) but deliberately left
it inert: every adlist and domain override this app adds still hardcodes `groups: [0]`
(the default group), so groups exist but nothing can be scoped to them — there is still no
way, from this app, to say "this adlist only applies to the kids' devices." `Adlist`/
`Domain` already carry a `groups: number[]` field end-to-end (fetched and displayed
nowhere), so the data path exists; only the ability to *change* it is missing.

## Scope

In:
- Backend: `PiholeClient.update_adlist`/`update_domain` gain a required `groups: list[int]`
  keyword parameter (previously only `enabled`), sent as part of the same PUT body. The
  route always resolves both `enabled` and `groups` to concrete values before calling
  these, so the client signatures stay non-optional.
- Backend: `AdlistUpdateInput` (`app/routes/api.py`) and `DomainUpdateInput`
  (`app/routes/domains.py`) gain an optional `groups: list[int] | None` field, and
  `enabled` becomes optional too (`bool | None`, default `None`). Each field, when omitted,
  is preserved from the current record; when present, it replaces. A payload carrying
  neither `enabled` nor `groups` is rejected (422). Existing callers that send
  `{address, enabled}` (or `{domain, type, kind, enabled}`) are unchanged on the wire.
- Frontend: `updateAdlist`/`updateDomain` (`frontend/src/api.ts`): `enabled` becomes an
  optional parameter and an optional trailing `groups?: number[]` is added; the body
  carries only the fields actually passed.
- Frontend: `AdlistsPage.tsx` and `DomainsPage.tsx` each gain: a per-instance-cell display
  of the record's current groups (resolved to names), and a new "Edit groups" action
  (single instance, same shape as `ClientsPage`'s) to change them.
- `docs/design.md` §5.3 (Adlist management) and §5.5 (Domain allow/deny management) each
  gain a bullet describing group-scoping.

Out:
- **Picking a group when adding a new adlist or domain override**, single- or
  multi-instance. Add still always lands in the default group (`groups: [0]`), exactly as
  today — confirmed with Kyrnveil when scoping this plan. Group IDs are per-instance (the
  same reason `ClientsPage`'s add flow never offered a picker either — see 0006 Approach),
  so there is no single group list to source options from once more than one instance is
  selected. Assigning groups happens only after the fact, one instance at a time.
- **Renaming, creating, or deleting groups from these pages.** That's `GroupsPage`'s job;
  `AdlistsPage`/`DomainsPage` only ever populate a picker from an instance's existing
  groups (via `listGroups()`), never mutate `/api/groups`.
- **A "groups" cross-instance consistency state.** Same reasoning 0006 gave for
  `ClientRow`: group IDs aren't portable across instances, so comparing raw
  `groups: number[]` between two instances would produce false conflicts. This plan does
  not add a groups-based state to `AdlistRow`/`DomainRow` — the existing
  `consistent`/`missing`/`conflict` state continues to reflect `enabled` only, unchanged.
- **Bulk / multi-select group reassignment** (e.g., "move these five adlists to Kids at
  once"). Per-row, per-instance only, matching every other mutation shape in this app;
  bulk actions are `docs/potential-features.md` §1's separate item.

## Approach

**One PATCH endpoint per resource, not a second one.** Rejected alternative: a dedicated
`adlist_update_groups`/`domain_update_groups` endpoint, mirroring `clients.py`'s
`client_update_groups`. Rejected because clients have no `enabled` field at all, so a
groups-only endpoint was the *only* mutation clients needed. Adlists and domains already
have a PATCH endpoint that sends "the full object back, only the toggled field differs"
(`update_adlist`/`update_domain`'s existing shape) — extending that same shape to
optionally carry `groups` too avoids a second near-duplicate route per resource. The
existing enable/disable `Switch` keeps calling `updateAdlist(instance, address, enabled)`
exactly as today (groups omitted → preserved); the new "Edit groups" modal calls
`updateAdlist(instance, address, undefined, newGroups)` — `enabled` omitted so the route
preserves `before.enabled`, groups replaced. Both hit the same route. `enabled` is
optional for the same reason `groups` is: re-sending a value read at page-load time across
a PATCH is a lost-update hazard (see Revision note), so the flow that is not changing a
field does not send it, and the route fills it from `before`.

**Gravity: adlists always mark pending, domains never do — but the domain side is
unconfirmed.** `adlist_update` already calls `mark_gravity_pending(instance_name)`
unconditionally on every update, so a groups-only change automatically gets the same
treatment as an enabled toggle — no new logic needed, and erring toward "gravity pending
when not strictly required" is the safe failure mode (an extra manual gravity click,
never a stale blocking state). `domain_update` has no gravity call today, confirmed
correct for enable/disable and add/remove by 0003's live-instance test — but that test
never touched group membership, and group scoping might route through gravity even for
domain overrides (unlike a bare allow/deny toggle). Per this app's "VERIFY BEFORE
TRUSTING" rule, this is a **new manual pre-merge check**, not an assumption to build on
silently (see Acceptance criteria).

**Frontend duplicates the group-name-resolution helper a third time, not a shared
module.** `ClientsPage.tsx`'s `groupNameMap(rows: GroupRow[])` gets copied verbatim into
both `AdlistsPage.tsx` and `DomainsPage.tsx`. Rejected alternative: extract a shared
`frontend/src/groupNames.ts`. Rejected for consistency with this codebase's established
convention — the backend already duplicates `_fanout`/`_instance`/`_client`/`_api_error`
across `api.py`/`domains.py`/`groups.py`/`clients.py` rather than sharing them (for
monkeypatch-independence in tests); there is no existing shared-frontend-utility module
in this codebase, and introducing the first one is a bigger structural change than a
~10-line pure function warrants.

**Both pages fetch `listGroups()` sequentially after their own list call, never via
`Promise.all`.** Per the finding from 0006 (recorded in that plan's Revision note): two
concurrent authenticated fanout calls against the same Pi-hole instance can race and
intermittently 401. `AdlistsPage`/`DomainsPage` must `await listAdlists()` (or
`listDomains()`) and then `await listGroups()`, exactly like `ClientsPage` does after its
fix, not the two-call `Promise.all` `ClientsPage` originally shipped with.

**Validation.** `AdlistUpdateInput.groups`/`DomainUpdateInput.groups`: `list[int] | None`,
default `None`; when present, each entry must be a non-negative, non-bool int and the list
capped at 100 entries — the same `field_validator(..., mode="before")` shape as
`ClientGroupsUpdateInput`, duplicated per this router's existing convention rather than
imported. `mode="before"` matters: the default "after" mode runs post-coercion, by which
point Pydantic has already turned `[True]` / `["5"]` / `[5.0]` into a valid `list[int]`.

`enabled` becomes `bool | None = Field(default=None)`. Both routers also get a
`model_validator(mode="after")` — `raise ValueError("Provide enabled or groups")` when
both `enabled is None` and `groups is None` — duplicated per-router like the field
validator (both `api.py` and `domains.py` already import `model_validator`).

## Files

- `app/pihole_client.py` — `update_adlist`/`update_domain` gain a required `groups:
  list[int]` keyword parameter, included in the PUT body.
- `app/routes/api.py` — `AdlistUpdateInput` gains `groups: list[int] | None`, `enabled`
  becomes `bool | None`, plus a `model_validator` requiring at least one; `adlist_update`
  resolves both target values from `before` and passes them through; response and audit
  record extended.
- `app/routes/domains.py` — same shape: `DomainUpdateInput` gains `groups`, `enabled`
  becomes optional, same `model_validator`; `domain_update` resolves both and passes them
  through; response and audit record extended.
- `frontend/src/api.ts` — `updateAdlist`/`updateDomain`: `enabled` parameter becomes
  optional, optional trailing `groups?: number[]` added, body carries only passed fields.
- `frontend/src/pages/AdlistsPage.tsx` — fetch `listGroups()` (sequential), resolve/display
  group names per instance, new "Edit groups" modal and button.
- `frontend/src/pages/DomainsPage.tsx` — same additions as `AdlistsPage.tsx`.
- `tests/test_pihole_client.py` — extend the existing adlist/domain PUT-body assertions to
  cover a `groups` argument.
- `tests/test_api.py` — new adlist tests: groups replaced while enabled preserved; enabled
  replaced while groups preserved; groups omitted preserves existing; enabled omitted
  preserves existing; empty payload → 422; gravity still marked pending on a groups-only
  change.
- `tests/test_domains.py` — the same tests for domains (minus gravity), plus a regression
  asserting no gravity-pending side effect exists (there is no gravity call to assert
  against — assert the response/behavior is otherwise unchanged from today).
- `docs/design.md` — §5.3 and §5.5 each gain a group-scoping bullet.
- `docs/plans/0007-adlist-domain-group-targeting.md` — this plan.

## Steps

1. `app/pihole_client.py::update_adlist(self, adlist: Adlist, *, enabled: bool, groups:
   list[int]) -> None`: add the `groups` parameter; PUT body becomes `{"comment":
   adlist.comment, "enabled": enabled, "groups": groups}` (was `list(adlist.groups)`,
   now the caller-supplied list — callers that want "unchanged" pass `list(adlist.groups)`
   themselves).
2. `app/pihole_client.py::update_domain(self, item: Domain, *, enabled: bool, groups:
   list[int]) -> None`: same change — PUT body's `"groups"` becomes the parameter instead
   of `list(item.groups)`.
3. `app/routes/api.py::AdlistUpdateInput`: add `groups: list[int] | None = Field(default=None,
   max_length=100)` with a `field_validator("groups", mode="before")` rejecting any `bool`,
   non-`int`, or negative entry — the same validator body as `clients.py`'s
   `ClientGroupsUpdateInput.validate_groups`, duplicated here. `mode="before"` is required
   (not the default "after" mode): it must run before Pydantic coerces `[True]` / `["5"]` /
   `[5.0]` into a valid `list[int]`. The `isinstance(value, list)` guard already makes it a
   no-op when the field is omitted (`value is None`), matching `ClientGroupsUpdateInput`.
   Also change `enabled: bool` to `enabled: bool | None = Field(default=None)`, and add
   `@model_validator(mode="after")` `def require_change(self)` raising
   `ValueError("Provide enabled or groups")` when `self.enabled is None and self.groups is
   None` (return `self` otherwise).
4. `app/routes/api.py::adlist_update`: after fetching `before = _find_adlist(...)`, compute
   `target_groups = payload.groups if payload.groups is not None else list(before.groups)`
   and `target_enabled = payload.enabled if payload.enabled is not None else before.enabled`;
   call `client.update_adlist(before, enabled=target_enabled, groups=target_groups)`;
   `record(instance_name, "adlist_update", _adlist_data(before), {**_adlist_data(before),
   "enabled": target_enabled, "groups": target_groups})`; response reports `"enabled":
   target_enabled` and gains `"groups": target_groups`. `mark_gravity_pending(instance_name)`
   call is unchanged (already unconditional).
5. `app/routes/domains.py::DomainUpdateInput`: same additions as step 3 — `groups: list[int]
   | None = Field(default=None, max_length=100)` + the duplicated `mode="before"` validator,
   `enabled` → `bool | None = Field(default=None)`, and the duplicated
   `model_validator(mode="after")` requiring at least one of the two.
6. `app/routes/domains.py::domain_update`: same shape as step 4 — resolve `target_groups`
   and `target_enabled`, call `client.update_domain(before, enabled=target_enabled,
   groups=target_groups)`, extend the audit record and response with `"enabled":
   target_enabled` and `"groups": target_groups`. No gravity call (unchanged).
7. `frontend/src/api.ts::updateAdlist`: signature becomes `(instance: string, address:
   string, enabled?: boolean, groups?: number[])`; body is
   `JSON.stringify({ address, ...(enabled !== undefined ? { enabled } : {}),
   ...(groups ? { groups } : {}) })`; response type gains `groups: number[]`. The `Switch`
   path still calls `updateAdlist(instance, address, enabled)`; "Edit groups" calls
   `updateAdlist(instance, address, undefined, groups)`.
8. `frontend/src/api.ts::updateDomain`: same treatment — `enabled` parameter becomes
   `enabled?: boolean`, `groups?: number[]` added, body built the same conditional way with
   `domain`/`type`/`kind` always present, response type gains `groups: number[]`.
9. `frontend/src/pages/AdlistsPage.tsx`: add `groupRows: GroupRow[]` state and a
   `groupNameMap` helper copied verbatim from `ClientsPage.tsx`. In `load()` and the mount
   effect, `await api.listAdlists()` then `await api.listGroups()` (sequential — see
   Approach), merging both results' `errors`. Add an `editing` state (`{ instance: string;
   row: AdlistRow; groups: number[] } | undefined`) and an `editGroups` handler calling
   `api.updateAdlist(editing.instance, editing.row.address, undefined, editing.groups)` —
   no `enabled` argument (the modal never changes it; re-sending a stale value is the bug
   this revision fixes).
10. `frontend/src/pages/AdlistsPage.tsx` columns: in the per-instance cell (inside the
    existing `Space direction="vertical"` block), add a `Space wrap` row of `Tag`s showing
    `adlist.groups.map((id) => groupNamesByInstance[instance]?.[id] ?? '#' + id)`, and an
    "Edit groups" `Button` next to the existing enable/disable `Switch` that sets
    `editing` to open the modal, mirroring `ClientsPage`'s edit-groups `Modal` (`Select
    mode="multiple"`, options from `groupNamesByInstance[instance]`, initial value
    `adlist.groups`).
11. `frontend/src/pages/DomainsPage.tsx`: identical additions to steps 9–10, adapted for
    `DomainRow`/`Domain`; `editGroups` calls `api.updateDomain(instance, domain, type,
    kind, undefined, groups)` — `enabled` omitted, same as step 9.
12. `docs/design.md` §5.3: add a bullet — "An adlist can be assigned to one or more groups
    per instance via 'Edit groups' (§5.6); changing group membership marks that instance's
    gravity pending, same as any other adlist edit."
13. `docs/design.md` §5.5: add a bullet — "A domain override can be assigned to groups per
    instance via 'Edit groups' (§5.6); confirmed against a live instance before merge that
    this does not require a gravity run (see plan 0007's Acceptance criteria)."
14. Add the tests below; `./.venv/bin/pytest`; `cd frontend && npm run build && npm run
    lint`; commit the regenerated `app/static/ui/`.

## Contracts

- `PATCH /api/adlists/{instance_name}`: `enabled` becomes optional and an optional
  `groups: number[]` is added — both are omitted-means-preserve; a body with neither is
  422. `address` still required. Response reports the resolved `"enabled"` and gains
  `"groups": number[]` alongside `instance`/`address`/`gravity_pending`.
- `PATCH /api/domains/{instance_name}`: same — `enabled` optional, `groups` added, a body
  with neither is 422, `domain`/`type`/`kind` still required. Response reports the resolved
  `"enabled"` and gains `"groups": number[]`.
- `frontend/src/api.ts::updateAdlist`/`updateDomain`: `enabled` parameter becomes optional
  and an optional trailing `groups?: number[]` is added, exactly as in Steps 7–8. Existing
  call sites passing `(instance, address, enabled)` / `(instance, domain, type, kind,
  enabled)` are unchanged.
- No new routes, no config/DB schema change. `Adlist`/`Domain`/`AdlistRow`/`DomainRow`
  TypeScript interfaces are unchanged (`groups: number[]` already exists on both).

## Tests

- `tests/test_pihole_client.py` — extend
  `test_adlist_requests_include_block_type_and_preserve_groups` (or add a sibling) to
  assert `update_adlist(..., groups=[0, 5])`'s PUT body carries `"groups": [0, 5]`
  regardless of the object's own `.groups`. Same for `update_domain` in
  `test_domain_requests_use_type_kind_path_segments`'s sibling.
- `tests/test_api.py`:
  - `test_adlist_update_replaces_groups_and_preserves_enabled` — `PATCH
    /api/adlists/{name}` with `groups: [0, 5]` and no `enabled` key → fake client's
    `update_adlist` called with the new groups and the *unchanged* current `enabled` value
    (from `before`, not the payload).
  - `test_adlist_update_replaces_enabled_and_preserves_groups` — `PATCH` with only
    `enabled` (the existing `Switch` shape) → fake client's `update_adlist` called with the
    record's original `groups`, unchanged (regression guard for the groups-preserve path).
  - `test_adlist_update_omits_enabled_preserves_existing` — `PATCH` with only `groups`,
    where `before.enabled` differs from what a stale client snapshot would have sent →
    `update_adlist` called with `before.enabled`, proving the payload never carries a stale
    enabled (the lost-update guard from the Revision note).
  - `test_adlist_update_rejects_empty_payload` — `PATCH` with only `address` → 422.
  - `test_adlist_update_marks_gravity_pending_on_groups_only_change` — a groups-only
    PATCH still results in the instance appearing in `gravity_pending`.
- `tests/test_domains.py`:
  - `test_domain_update_replaces_groups_and_preserves_enabled` — mirrors the adlist test.
  - `test_domain_update_replaces_enabled_and_preserves_groups` — mirrors the adlist test.
  - `test_domain_update_omits_enabled_preserves_existing` — mirrors the adlist test.
  - `test_domain_update_rejects_empty_payload` — `PATCH` with only `domain`/`type`/`kind`
    → 422.

## Invariants at risk

(ARCHITECT.md § "Project invariants")

1. **One instance mutated at a time.** "Edit groups" PATCHes exactly one instance (path
   parameter), same as the existing enable/disable toggle. Kept.
2. **Diff before write.** `adlist_update`/`domain_update` already fetch `before` first.
   Both mutable fields (`enabled`, `groups`) are now resolved from `before` when absent
   from the payload, so a field the caller did not send is never overwritten from a stale
   client-side value — this revision tightens the invariant rather than risking it (the
   original wrote `payload.enabled` unconditionally). Kept.
3. **Gravity updates gated behind real changes.** Adlists: unchanged — `mark_gravity_pending`
   already fires on every `adlist_update`, so a real groups change is covered by existing
   logic, not a new gate to get wrong. Domains: no gravity call is added. **Confirmed
   2026-09-06** against a live instance — a group-scoped domain deny takes effect
   immediately on the API `PUT` (`dig` returned `0.0.0.0`) with no `pihole -g` run, so the
   "no gravity for domains" approach holds. Kept.
4. **No background scheduler.** No new request-independent code path added. Kept.
5. **FastAPI owns all credentials; browser talks only to `/api`.** Unchanged — no new
   credential-handling code. Kept.
6. **All outbound requests go through `app/url_policy.py`.** Unchanged — `groups` is a
   JSON body field passed straight to `PiholeClient`, not a URL. Kept.
7. **Every route behind auth middleware.** No new routes added at all (existing PATCH
   endpoints extended in place); no allowlist change. Kept.
8. **Compiled frontend committed.** `frontend/` changes (Steps 7–11) rebuilt and
   `app/static/ui/` committed (Step 14). Kept.
9. **`main` is production.** Work lands on `feature/0007-adlist-domain-group-targeting`,
   merged only after an Accept verdict. Kept.

## Acceptance criteria

- [x] `./.venv/bin/pytest` green, including every test in the Tests section. (217 passed.)
- [x] `cd frontend && npm run build && npm run lint` clean; `app/static/ui/` committed.
      (1 pre-existing `App.tsx` warning, untouched.)
- [x] Editing an adlist's groups on one instance does not touch any other instance, and
      does not change its `enabled` state. (`test_adlist_update_group_targeting`.)
- [x] Editing an adlist's groups marks that instance's gravity pending (visible in the
      existing "Gravity update required" banner).
- [x] Editing a domain override's groups produces no gravity-pending side effect anywhere
      (no such state exists for domains today, and this plan doesn't add one).
- [x] Toggling enabled/disabled on an adlist or domain via the existing `Switch`, without
      touching "Edit groups", leaves that record's groups unchanged (regression guard for
      the default-`None`-preserves-existing path).
- [x] Saving groups via "Edit groups" sends no `enabled` field; if another session toggled
      the record off after page load, the group save leaves it off (route preserves
      `before.enabled`). Covered by the `null_enabled_preserves_existing` case.
- [x] `PATCH /api/adlists|domains` with a body carrying neither `enabled` nor `groups`
      returns 422.
- [x] `AdlistsPage`/`DomainsPage` fetch `listGroups()` sequentially after their own list
      call — verifiable in the diff as two separate `await`s, not `Promise.all`.
- [x] `git diff main...HEAD` is limited to the Files list (+ this plan).
- [x] **Manual, before merge (done 2026-09-06):** confirmed against a live instance that a
      group-scoped domain deny takes effect on the API `PUT` with no gravity run — `dig`
      returned `0.0.0.0` once the test client was matched. Note: the client had to be
      registered **by IP**; a MAC-address client entry did not match the machine's queries
      (FTL saw it as Default-group only), so the first attempt did not block. That is a
      Pi-hole client-identification quirk, not an app bug.
