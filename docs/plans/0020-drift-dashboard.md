# 0020 — Unified drift dashboard

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0020-drift-dashboard
Author: Claude (architect)
Date: 2026-09-22

## Problem

Drift between instances is visible today only if you open each screen in turn. Local DNS,
CNAMEs, Block Lists, Domains, Groups, and Clients each compute a per-row
`consistent | missing | conflict` state and show it inline, and managed settings are not
compared across instances at all. Answering "where do my Pi-holes disagree?" means visiting
six pages, reading six tables, and comparing four settings by hand — and each of those pages
fetches its own data, so the answer is assembled from six separate round trips that can
disagree with each other. One read-only page should answer the question in a single fetch.

## Scope

In:
- New backend route `GET /api/drift` in a new module `app/routes/drift.py`, returning a
  per-category summary plus the drifting items across seven categories: Local DNS A/AAAA,
  CNAME records, block lists, domains, groups, clients, managed settings.
- Drift semantics identical to what each page already computes (presence across reachable
  instances, plus `enabled` for adlists/domains/groups, address for A/AAAA, target+TTL for
  CNAMEs). Settings compare the four `MANAGED_FIELDS` values.
- New frontend page `frontend/src/pages/DriftPage.tsx`: per-category count cards, one table
  of drifting items with a column per reachable instance, category / state / text filters.
- `Drift` entry in the sidebar menu.
- `api.ts` types and `api.drift()`.
- `tests/test_drift.py`.
- `docs/design.md` §5.10 describing the screen; `docs/potential-features.md` moves the item
  to the shipped list.

Out:
- Any mutation. No "copy to other instance", no reconcile, no fix buttons. The page reports;
  the owning page acts.
- Navigation from a drift row to the owning page. `App.tsx` holds page state in a plain
  `useState` with no router, and threading a navigation prop is a separate change.
- Comment-text and group-assignment comparison. Group IDs differ per instance, so comparing
  them needs an ID-to-name map with its own edge cases; a comment typo is not drift.
- Gravity list *contents*. Only adlist definitions are compared, never the domains a list
  resolves to.
- Instance health, versions, and blocking state. Those already have the Instances and Status
  screens.
- Auto-refresh or polling.

## Approach

**One backend endpoint, not six frontend fetches.** The frontend could call the six existing
list endpoints and aggregate client-side, but that opens six concurrent authenticated
sessions per instance. Two simultaneous authenticated calls to one Pi-hole can race and
return 401, so the aggregation has to be sequential per instance — which belongs in the
backend, where `_fanout` already runs one worker per instance with a single
`with PiholeClient(...)` session inside it. A single endpoint also drops the per-instance
Pi-hole call count from six to five, because `/api/dns/records`, `/api/dns/cnames`, and the
settings read each fetch the same config tree separately today; one `get_config()` per
instance feeds all three.

**Reuse the existing state rules exactly, do not invent new ones.** A drift dashboard whose
counts disagree with the page it summarises is worse than no dashboard. `drift.py` therefore
imports the two pure tree parsers `_parse_dns_hosts` and `_parse_dns_cnames` from
`app.routes.api` rather than reimplementing them, and reproduces each page's state expression
verbatim, including its quirks:

- `conflict` outranks `missing`. An adlist enabled on one instance and disabled on another is
  `conflict` even if a third instance lacks it.
- A hostname carrying two different addresses on a *single* instance is `conflict`, because
  the existing rule takes the address set across all reachable instances.
- Adlists key on the raw stored `address`, exactly as `/api/adlists` does. Two instances
  holding the same list under addresses that differ only by a trailing slash therefore show
  as two `missing` rows, which is what the Block Lists page shows today.

Rejected: moving the parsers into a new shared `app/dns_entries.py`. It is the tidier module
boundary, but `tests/test_api.py` calls `api._cname_entry` directly in three places, so the
move needs re-export aliasing to stay green, and it widens the diff across a 1465-line file
for no behavioural gain. A one-way import `drift → api` creates no cycle; `api.py` imports
nothing from `drift`.

**An instance is reachable or it is not — no partial rows.** If any of an instance's five
reads fails, the instance goes into `errors` and is left out of `reachable` entirely. Entries
that exist only on the surviving instances are then `consistent`, not `missing`, because a
silent instance is unknown rather than absent. `values` carries a key only for reachable
instances, so `null` unambiguously means "absent here".

**Only drifting items cross the wire.** `items` excludes `consistent` entries; the category
summary carries the denominator. A wiped instance would otherwise make every entry drift, so
each category caps its items at `DRIFT_ITEMS_PER_CATEGORY = 200` and sets `truncated`, while
`drifting` stays exact. This follows the truncation precedent in `/api/lookup`.

**Fewer than two reachable instances is not a comparison.** The response sets
`comparable: false` and the page shows a warning banner. Items are still computed and
returned, so the single-instance self-conflict case above still surfaces, but the user is
told the cross-instance answer is unavailable.

## Files

- `app/routes/drift.py` — new. `GET /api/drift`, the per-instance snapshot read, and the
  per-category comparison.
- `app/main.py` — import and `include_router(drift.router)`.
- `frontend/src/api.ts` — drift types and `api.drift()`.
- `frontend/src/pages/DriftPage.tsx` — new page.
- `frontend/src/App.tsx` — `Drift` menu item and page in the render chain.
- `app/static/ui/**` — rebuilt frontend bundle (invariant #8).
- `tests/test_drift.py` — new.
- `docs/design.md` — new §5.10 "Drift dashboard".
- `docs/potential-features.md` — move "Unified drift dashboard" into the shipped list.

## Steps

1. **Create `app/routes/drift.py` scaffolding.** Mirror `app/routes/lookup.py`:
   `router = APIRouter(prefix="/api")`, module-level `credentials = CredentialStore()`, a
   module-local `_client(instance)` and `_fanout(items, worker)` (both duplicated from
   `lookup.py` so tests can monkeypatch this module's names). Import
   `_parse_dns_cnames` and `_parse_dns_hosts` from `app.routes.api`, and `MANAGED_FIELDS`,
   `display_setting`, `get_setting` from `app.settings_fields`.

2. **Define the category constants.**
   ```python
   CATEGORIES: tuple[tuple[str, str], ...] = (
       ("dns", "Local DNS"), ("cname", "CNAME records"), ("adlist", "Block lists"),
       ("domain", "Domains"), ("group", "Groups"), ("client", "Clients"),
       ("setting", "Settings"),
   )
   DRIFT_ITEMS_PER_CATEGORY = 200
   ```

3. **Write `_snapshot(instance) -> tuple[str, dict[str, Any] | None, str | None]`.** One
   `with _client(instance) as client:` block, five sequential calls in this order —
   `client.get_config()`, `client.get_adlists()`, `client.get_domains()`,
   `client.get_groups()`, `client.get_clients()`. Sequential inside one session is
   deliberate; concurrent authenticated calls to one instance can race to a 401. From the
   config tree derive `_parse_dns_hosts(tree)`, `_parse_dns_cnames(tree)`, and
   `{dotted: get_setting(tree, dotted) for dotted in MANAGED_FIELDS}`. Return
   `(instance.name, snapshot, None)` or `(instance.name, None, message)` using the same
   `except HTTPException / PiholeAPIError / Exception` ladder as `lookup._lookup_instance`,
   with the generic `"Unable to reach Pi-hole instance"` fallback so transport detail never
   leaks.

4. **Write the per-category collectors.** Each takes `snapshots: dict[str, snapshot]` and
   returns `list[_Entry]`, where `_Entry` carries `id`, `key`, `detail`, `values` (a
   `dict[str, str]` holding only the instances that have the entry) and `variants` (the set
   used for the conflict test).

   | Category | Row key | `detail` | Per-instance display | Conflict set |
   | --- | --- | --- | --- | --- |
   | `dns` | hostname | `""` | addresses joined `", "`, sorted | every address across reachable instances |
   | `cname` | hostname | `""` | `target` or `target (TTL 300)`, joined | every `(target, ttl)` pair |
   | `adlist` | raw `address` | comment, truncated to 80 chars | `Enabled` / `Disabled` | `{enabled}` |
   | `domain` | domain | `f"{type} · {kind}"` | `Enabled` / `Disabled` | `{enabled}` |
   | `group` | group name | comment, truncated to 80 chars | `Enabled` / `Disabled` | `{enabled}` |
   | `client` | client identifier | `""` | `Present` | empty — presence only |
   | `setting` | dotted key | the `MANAGED_FIELDS` label | `_setting_display(value, kind)` | `{_hashable(raw value)}` |

   **The raw value decides drift; the display string is presentation only.** Settings compare
   the value `get_setting` returns, matching `_changes` in `api.py`, so reordered
   `dns.upstreams` counts as drift. A raw list is unhashable, so the conflict set is built
   through `_hashable(value)`, which returns `tuple(value)` for a list and `value` otherwise.
   No other category needs it — addresses are strings, CNAMEs are `(target, ttl)` tuples,
   and the rest are bools.

   `_setting_display(value, kind)` returns `"(not set)"` for `None`, `"(empty)"` when
   `display_setting` renders to an empty string, and `display_setting(value, kind)` otherwise.
   Two raw values can still render identically — `[]` and `[""]` both display as `"(empty)"` —
   in which case the row is reported as a `conflict` with two identical-looking cells. That is
   deliberate: silently under-reporting drift is worse than a rare puzzling row, and the user
   can open Settings on each instance to see the difference.

   Item `id` is `f"{category}:{key}"`, except `domain`, which is
   `f"domain:{type}:{kind}:{domain}"` because the Domains page keys on all three.

5. **Write `_state(present, reachable, variants)`** reproducing the page expression exactly:
   ```python
   return "conflict" if len(variants) > 1 else "missing" if present != reachable else "consistent"
   ```
   Clients pass `variants=set()`, which collapses it to the presence-only rule in
   `clients_list`. Settings are never `missing` — they key off `MANAGED_FIELDS`, so every
   reachable instance always contributes a value.

6. **Write the `GET /api/drift` handler.** `load_instances()`, `_fanout(instances, _snapshot)`,
   split into `snapshots` and `errors`. `reachable = set(snapshots)`. For each category in
   `CATEGORIES`, run its collector, compute each entry's state, count `total` (every key) and
   `drifting` (state != `consistent`), then append the non-consistent entries sorted by `key`
   up to `DRIFT_ITEMS_PER_CATEGORY`, setting the category's `truncated`. Return the response
   in Contracts below. `instance_names` preserves configured order; `reachable` is that order
   filtered.

7. **Register the router** in `app/main.py`: add `drift` to the
   `from app.routes import ...` line and `app.include_router(drift.router)` after
   `clients.router`. No auth allowlist change — `/api/drift` is not in
   `auth_middleware`'s public set, so it is authenticated by default.

8. **Add the `api.ts` types and call** exactly as written in Contracts. `api.drift()` is
   `request<DriftResponse>('/api/drift', { signal })` with an optional `AbortSignal`, matching
   `api.lookupDomain`.

9. **Build `DriftPage.tsx`.** Functional component, `App.useApp()` for `message`, one
   `useEffect` on mount that loads the response through an `AbortController` and cleans up on
   unmount. State: `data: DriftResponse | undefined`, `loading`, `category: DriftCategory | 'all'`,
   `state: 'all' | 'missing' | 'conflict'`, `search: string`.
   - Heading `Drift` plus a one-line description and a `Refresh` button (`ReloadOutlined`).
   - One `Alert type="error"` per entry in `errors`, same shape as `LookupPage`.
   - `Alert type="warning"` when `!data.comparable`: "Fewer than two instances answered, so
     this is not a cross-instance comparison."
   - Summary: `Row gutter={[16, 16]}` of `Col` holding `Card size="small"` with
     `Statistic title={label} value={drifting} suffix={`/ ${total}`}`, value styled red when
     `drifting > 0`. Clicking a card sets `category` to it; clicking the active one resets to
     `all`.
   - Filters: `Segmented` for state (`All` / `Missing` / `Differs`) and an
     `Input` with `allowClear` filtering on `key` and `detail`, case-insensitive.
   - `Table` with `rowKey="id"`, `size="small"`, `scroll={{ x: 'max-content' }}`. Fixed
     columns: `Category` (a `Tag` with the category label), `Item` (key as
     `Typography.Text`, `detail` beneath as `Typography.Text type="secondary"`), `State`
     (`Tag color="orange"` "Differs" for `conflict`, `Tag color="blue"` "Missing" for
     `missing`). Then one column per name in `data.reachable`, rendering
     `item.values[name] ?? <Typography.Text type="secondary">—</Typography.Text>`. Build the
     dynamic columns as a named variable, not inline in the JSX.
   - `Empty description="Every instance agrees on every managed entry."` when `items` is
     empty and `comparable` is true.
   - Per-category `truncated` renders a `Typography.Text type="secondary"` line under the
     table naming the capped categories.

10. **Wire it into `App.tsx`.** Import `DriftPage` and `DiffOutlined`; add
    `{ key: 'drift', icon: <DiffOutlined />, label: 'Drift' }` to the menu after `queries`;
    add `page === 'drift' ? <DriftPage /> :` to the render chain.

11. **Docs.** `docs/design.md` gains §5.10 "Drift dashboard" describing the read-only
    aggregation, the seven categories, the parity rule, and the reachability rule.
    `docs/potential-features.md` moves the bullet out of §2 into the shipped list and updates
    the "Suggested next pick" trailer, which now names only the Teleporter backup.

12. **Build and commit the bundle.** `cd frontend && npm run build && npm run lint`, then
    commit the regenerated `app/static/ui/`.

## Contracts

### `GET /api/drift`

No query parameters. Authenticated like every other `/api` route.

Response (backend emits plain `dict[str, Any]`, following the `/api/lookup` and
`/api/query-log` fan-out precedent rather than a `response_model`):

```jsonc
{
  "instance_names": ["pihole1", "pihole2"],   // every configured instance, config order
  "reachable": ["pihole1"],                    // answered all five reads, config order
  "comparable": false,                         // len(reachable) >= 2
  "errors": { "pihole2": "Unable to reach Pi-hole instance" },
  "categories": [
    { "category": "dns", "label": "Local DNS", "total": 12, "drifting": 2, "truncated": false }
    // ...one per entry in CATEGORIES, always all seven, always in CATEGORIES order
  ],
  "items": [
    {
      "id": "dns:nas.lan",
      "category": "dns",
      "key": "nas.lan",
      "detail": "",
      "state": "conflict",                     // "missing" | "conflict" only
      "values": { "pihole1": "192.168.1.10", "pihole2": "192.168.1.11" }
    },
    {
      "id": "domain:deny:exact:ads.example.com",
      "category": "domain",
      "key": "ads.example.com",
      "detail": "deny · exact",
      "state": "missing",
      "values": { "pihole1": "Enabled", "pihole2": null }
    }
  ]
}
```

`values` holds a key for every name in `reachable` and no others; `null` means the entry is
absent on that instance. `items` is ordered by category (`CATEGORIES` order) then by `key`.

### `frontend/src/api.ts`

```ts
export type DriftCategory = 'dns' | 'cname' | 'adlist' | 'domain' | 'group' | 'client' | 'setting'
export type DriftState = 'missing' | 'conflict'

export interface DriftCategorySummary {
  category: DriftCategory
  label: string
  total: number
  drifting: number
  truncated: boolean
}

export interface DriftItem {
  id: string
  category: DriftCategory
  key: string
  detail: string
  state: DriftState
  values: Record<string, string | null>
}

export interface DriftResponse {
  instance_names: string[]
  reachable: string[]
  comparable: boolean
  errors: Record<string, string>
  categories: DriftCategorySummary[]
  items: DriftItem[]
}
```

```ts
  drift: (signal?: AbortSignal) => request<DriftResponse>('/api/drift', { signal }),
```

No config key, no DB change, no Pi-hole request shape change — every Pi-hole call used here
already exists on `PiholeClient`.

## Tests

`tests/test_drift.py`, built on a `FakeDriftPiholeClient` in the style of
`tests/test_lookup.py::FakeLookupPiholeClient`: per-instance canned config tree, adlists,
domains, groups, and clients, a `fail` map, and a `calls` log recording
`(instance, method)` plus session open/close so ordering can be asserted.

- `test_drift_flags_missing_and_conflicting_entries_in_every_category` — two instances, one
  drifting entry per category; asserts each item's `category`, `id`, `state`, and `values`.
- `test_drift_omits_consistent_entries_from_items_but_counts_them_in_totals` — an entry
  present and identical on both instances appears in the category `total`, never in `items`.
- `test_drift_conflict_outranks_missing` — an adlist enabled on `pihole1`, disabled on
  `pihole2` is `conflict`; one present only on `pihole1` is `missing`. Same two fixtures fed
  to `api.adlists_list` produce the same two states, so the parity claim is asserted, not
  assumed.
- `test_drift_dns_hostname_with_two_addresses_on_one_instance_is_a_conflict` — pins the
  inherited quirk so a future refactor cannot change it silently.
- `test_drift_settings_compare_raw_values_and_render_display_strings` — `dns.upstreams`
  differing yields a `setting` item whose `values` are the comma-joined display strings;
  identical upstreams yield none; reordered upstreams yield a conflict; a `None` value renders
  `"(not set)"` and an empty list renders `"(empty)"`.
- `test_drift_settings_conflict_on_raw_values_that_display_identically` — `[]` on one instance
  and `[""]` on the other is a `conflict`, pinning that the raw value decides and the display
  string does not.
- `test_drift_unreachable_instance_is_reported_and_not_counted_as_missing` — `pihole2` raises;
  it lands in `errors`, is absent from `reachable`, `values` has no key for it, and entries
  held only by `pihole1` come back `consistent`.
- `test_drift_partial_instance_failure_drops_the_whole_instance` — `get_groups` raises while
  the other four reads succeed; the instance is an error, not a partial snapshot.
- `test_drift_single_reachable_instance_sets_comparable_false` — one configured instance,
  `comparable is False`, `categories` still present with all seven entries.
- `test_drift_caps_items_per_category_and_flags_truncation` — 250 drifting adlists yield 200
  `adlist` items, `truncated is True`, and `drifting == 250`.
- `test_drift_reads_the_config_tree_once_per_instance` — `get_config` appears exactly once per
  instance in the call log, proving DNS, CNAMEs, and settings share one fetch.
- `test_drift_reads_each_instance_sequentially_inside_one_session` — the call log shows one
  `__enter__` … five reads … `__exit__` block per instance with no interleaving within an
  instance, guarding against the concurrent-auth 401 race.
- `test_drift_requires_authentication` — an unauthenticated `GET /api/drift` returns 401,
  matching the existing per-route auth assertions.
- `test_drift_with_no_configured_instances_returns_empty_categories` — `load_instances()`
  empty; `comparable is False`, `items == []`, seven zeroed categories, no exception from
  `_fanout`.

## Invariants at risk

1. **One instance mutated at a time** — no mutation exists in this change. `/api/drift` is
   `GET` and calls only `get_*` methods on `PiholeClient`.
2. **Diff before write** — nothing is written. The endpoint *is* a diff, presented read-only;
   the user acts on the owning page, which keeps its existing confirm path.
3. **Gravity gated behind real changes** — untouched. No adlist is created, updated, or
   deleted, so no gravity run is triggered and `load_pending_gravity` is not consulted.
4. **No background scheduler** — the page fetches once on mount and once per explicit
   Refresh click. No interval, no polling, no `X-Pihole-Manager-Poll` header, no startup job.
5. **FastAPI owns all credentials** — `drift.py` gets passwords from its own
   `CredentialStore()` exactly as `lookup.py` does. The response carries instance *names*,
   entry keys, and display strings only. No `base_url`, no password, no app-password, and the
   generic `"Unable to reach Pi-hole instance"` fallback keeps transport detail out of the
   error strings.
6. **All outbound requests go through `url_policy`** — no new outbound URL. Every call goes
   through `PiholeClient`, whose pinned transport already resolves through
   `resolve_instance_destination`. The drift page never fetches an adlist address; it compares
   the stored string.
7. **Every route behind auth middleware** — `/api/drift` is not in `auth_middleware`'s public
   set (`/`, `/app`, `/api/auth/login`, `/api/auth/status`, `/app/*`), so it authenticates by
   default. `test_drift_requires_authentication` pins that.
8. **Compiled frontend is committed** — `frontend/` changes, so Step 12 runs
   `npm run build && npm run lint` and commits `app/static/ui/`.
9. **`main` is production** — all work on `feature/0020-drift-dashboard`, merged only after an
   Accept verdict.

## Acceptance criteria

- [ ] `GET /api/drift` returns the exact shape in Contracts, with all seven categories always
      present in `CATEGORIES` order.
- [ ] Every item in `items` has `state` of `missing` or `conflict`; no `consistent` item is
      returned, and each category's `total` counts consistent entries too.
- [ ] For the same fixture data, the drift states for adlists, domains, groups, clients, DNS
      records, and CNAMEs equal the states returned by `/api/adlists`, `/api/domains`,
      `/api/groups`, `/api/clients`, `/api/dns/records`, and `/api/dns/cnames`.
- [ ] An instance that fails any read appears in `errors`, is absent from `reachable`, and
      contributes no `values` key; entries held only by the surviving instances read
      `consistent`, not `missing`.
- [ ] `get_config` is called exactly once per instance per request, and an instance's five
      reads happen sequentially inside a single client session.
- [ ] `comparable` is `false` when fewer than two instances answer, and the page shows the
      warning banner in that case.
- [ ] Live check against the configured `lab-pihole` instance: the Drift page loads, shows the
      "fewer than two instances answered" warning (the fleet has one instance), and renders
      seven category cards with non-zero `total` for Local DNS, block lists, domains, and
      groups. A hostname deliberately given two addresses on `lab-pihole` shows as a `dns`
      conflict, and removing it clears the row on refresh.
- [ ] No Pi-hole URL, password, or app-password appears anywhere in the response body.
- [ ] `Drift` appears in the sidebar between `Queries` and `Instances` and renders without a
      console error.
- [ ] `./.venv/bin/pytest` green. Baseline on `main` at 2026-09-22 is 520 passed, 0 failed —
      no pre-existing failure is acceptable.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
