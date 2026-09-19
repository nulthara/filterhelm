# 0010 — Bulk multi-select actions on Block Lists, Domains, and Local DNS

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Revision 5: 2026-09-07 — Systematic architect pass, requested after four plan-review stops:
every claim the plan makes about existing code, every `PiholeClient` signature, every line
reference, and the Pi-hole v6 endpoints behind them were checked against the source. All
signatures, line refs, error contracts, page hooks, styles, doc sections, and Pi-hole endpoint
shapes held. Eight gaps found and fixed, none reaching a wrong write but several Codex would
have stopped on: (1) Files and step 4 disagreed on `_delete_dns_host_record`'s parameters —
step 4 is authoritative. (2) Step 5 said "for each deduped record" without a dedupe key or the
`hostname.strip().lower().rstrip(".")` / `_canonical_addr` normalization that the single-record
route applies and that `_host_entry`'s lowercased, canonicalized rows *require* to match — an
un-normalized ref would have been reported "no longer exists". Now specified, with tests.
(3) Steps 9-11 said "collect results" but `api.ts`'s `request()` throws on any non-2xx, so a
rollback 502 or a 409 on one instance would have silently skipped every later instance; now
per-instance `try/catch`, continue, and report — with chunks for a failed DNS instance stopped.
(4) Revision 2 made two tables' pagination controlled but did not carry over `DNSHostsTab`'s
`setCurrentPage(1)` in `load()`, so a bulk delete emptying the last page would strand the user
on a blank one. (5) Steps 9/10 told the implementer to clear selection on filter changes that
`AdlistsPage` / `DomainsPage` do not have. (6) Two Revision 4 tests were filed under
`test_domains.py` though they exercise adlist and DNS routes; refiled, and a requested new
single-record assertion that `test_dns_delete_preserves_sibling_hostnames` already makes was
dropped. (7) Pi-hole API alignment: v6 has `lists:batchDelete` / `domains:batchDelete`; the plan
now rejects them explicitly (delete-only, 204 with no per-item outcome, unwrapped by the client)
rather than leaving Codex to raise it. (8) Wording: the per-record config re-read is
`_settings_tree`'s, not `_parse_dns_hosts`'s; the Revision 4 regex note over-claimed about FTL
case matching where only record identity matters. Also made explicit: send stored per-instance
adlist addresses, `BulkActionBar` in `pages/` follows 0008's precedent, and `working` gates
the bar during a run. No scope was added or removed.
Revision 4: 2026-09-07 — Codex cleared Revisions 2 and 3 and then reported three further
defects together (having been asked to batch them rather than stop at the first). All three
verified against the source and fixed here. (a) **Regex records must not be normalized.**
Step 6's `(type, kind, domain.lower().rstrip("."))` key treats the regex patterns `ads` and
`ads.` as one record, though `.` matches any character and Pi-hole stores case-differing
patterns as distinct records — so selecting one could silently mutate the other. The key is now `kind`-dependent:
normalized for `exact`, verbatim for `regex`. `_find_domain` carries the same flaw today and
is deliberately **left alone** (single-record endpoints are out of scope); the bulk route is
stricter and reports a missing regex record rather than guessing, and the divergence is
recorded in Contracts. Relatedly, both steps built their lookup map with a dict
comprehension, which resolves to the **last** duplicate, while the single-record paths use
`next(...)` and resolve to the **first**; both now build first-match-wins. (b) **Aborted DNS
batches lost their prior successes.** Approach promises that a rollback failure "reports
which records were already deleted", but `_partial_dns_failure` builds its 502 from only the
line it was handed and knows nothing of the batch, so propagating it unchanged could not
satisfy that. Step 5 now catches it and re-raises with the batch's already-deleted records
named in the detail. (c) **A reachable selection exceeded the request cap.** `DNSRow.instances`
holds an unbounded `DNSRecord[]` per instance and the table pages at 25 rows, so 25 hostnames
with nine addresses each expand to 225 refs against `DNSRecordBulkInput`'s `max_length=200`
and would fail validation outright. Step 11 now splits each instance's expanded list into
sequential chunks of at most 200; Scope and the acceptance criteria were updated so "one
request at a time" still holds across chunks. Four regression tests added. Codex also noted
that its preliminary concern about Step 5's sibling-row tracking did **not** reproduce, so
the Revision 1 mechanism stands unchanged. No scope was added or removed.
Revision 3: 2026-09-07 — Codex confirmed Revision 2 cleared both earlier blockers, then
stopped again at the plan-review gate on a third correctness conflict in Step 3 that Step 6
inherits. Verified against the source and fixed here. **Literal dedupe against canonical
lookup.** Step 3 deduped the submitted addresses with `dict.fromkeys` (literal strings) but
looked every one of them up by `_adlist_key(address)`. `_adlist_key`
(`app/routes/api.py:121`) strips any trailing slash and lowercases scheme and host, whereas
the `screen_adlist_url(..., resolve=False)` validator on the input only strips a bare root
`/` — so two spellings of one adlist survive dedupe as separate entries and resolve to a
single stored record. The prescribed loop reads `item.enabled` from the `current` snapshot
and never updates it, so a bulk disable would write that record twice and report two
`changed: True` results, and a bulk delete would fail on the second pass and report a false
error for a record it had just successfully deleted — contradicting the "records already in
the requested state come back changed: false and are not written" criterion. The alias case
is already assumed elsewhere in this plan (`test_adlist_bulk_delete_uses_stored_address`
turns on a trailing-slash difference), so this was reachable, not theoretical. Domains have
the identical shape for a different reason: `DomainRef` applies no normalizing validator at
all, so case and trailing-dot spellings all collapse into the one
`(type, kind, domain.lower().rstrip("."))` key. Both steps now dedupe on the same canonical
key they look up by, keyed on the full tuple for domains so that one domain string under
different `type`/`kind` values stays two records; results are reported under the first
literal the caller submitted for each key. Four regression tests added. No in-batch mutation
of the `current` snapshot is needed once dedupe is canonical, since each stored record is
then visited at most once — which is why this differs from Step 5, where one `dns.hosts`
line can carry several distinct hostnames that are each a legitimate separate batch entry.
No scope was added or removed.
Revision 2: 2026-09-07 — Codex stopped at the plan-review gate on first handoff, before
editing anything, with two correctness conflicts. Both verified against the source and fixed
here. (a) **Two sessions, not one.** Step 5 told the DNS bulk route to read the tree via
`_settings_tree(instance)` and then open a client session, but `_settings_tree`
(`app/routes/api.py:1043`) opens its own `_client(instance)` context — so the route would
have built two Pi-hole sessions per request and failed its own "exactly one Pi-hole session
regardless of batch size" acceptance criterion, which the plan also says is asserted by a
test. Step 5 now opens the session first and calls `client.get_config()` inside it, keeping
`_settings_tree`'s error wrapping. Steps 3 and 6 were already correct — they read via
`client.get_adlists()` / `client.get_domains()` inside the session — so only the DNS route
changed. (b) **Page-bounded selection was assumed, not implemented.** Scope claimed AntD's
default bounds selection to the current page. It does not: all three tables hand AntD the
full row array as `dataSource` and paginate client-side, so every row remains in `dataSource`
on every page and `selectedRowKeys` survives page changes (`preserveSelectedRowKeys` only
governs keys *absent* from `dataSource`). Left as written, a user could select on page 1,
page forward, and fire a bulk action over rows no longer on screen. Scope now states the real
behavior, steps 9 and 10 convert `AdlistsPage` / `DomainsPage` to controlled pagination that
clears the selection on page change, step 11 extends `DNSHostsTab`'s existing `onChange`
handler instead of adding new state, and a matching acceptance criterion was added. No scope
was added or removed by this revision — both changes are corrections to steps already in it.
Revision 1: 2026-09-06 — Re-reviewed after plan 0008 shipped (five revisions there, all
caught by Codex's plan-review gate before any code was written, turned up a real Pi-hole
behavior — a `dns.cnameRecords` write briefly restarts something in FTL — that the original
0008 plan had assumed away by analogy to `dns.hosts`). Applying the same scrutiny here found
one genuine correctness gap in Step 5, fixed below before handoff. (a) `_delete_dns_host_record`
(Step 4) was specified to return `None`, but Step 5's in-batch tracking depends on knowing the
*exact* replacement raw line a deletion produced when the deleted hostname shared a
`dns.hosts` line with a sibling also in the batch — without that, a second hostname on the
same original line would be looked up by a raw value that no longer exists once the first
deletion's rewrite has happened, and the delete would incorrectly report "no longer exists."
`_delete_dns_host_record` now returns `str | None` (the replacement line, or `None` if no
survivors), and Step 5 spells out exactly how the batch's local list is updated from it. (b)
Step 5 was silent on what happens when `_delete_dns_host_record` raises a *non-HTTPException*
after a successful same-line-restore rollback (as opposed to the already-specified
`_partial_dns_failure` case, which does raise an `HTTPException` and correctly aborts the
batch) — clarified to match the per-record catch-and-continue pattern the adlist/domain bulk
routes already use, since a successful rollback leaves the instance's data intact and there is
no reason to abandon the rest of the batch over it. Sequencing note in Step 1 also simplified:
0008 has now merged, so the "if plan 0008 has not merged" branch is moot and removed. No
FTL-restart-style resilience work was needed for adlists, domains, or `dns.hosts` themselves —
see the note added to Approach explaining why, and what the live-verification step should
still watch for.
Branch: feature/0010-bulk-multi-select-actions
Author: Claude (architect)
Date: 2026-09-06

## Problem

Every list-shaped screen in this app acts one record at a time. Disabling five adlists
across two instances is ten confirm dialogs and ten round-trips, each of which opens and
closes its own authenticated Pi-hole session; the same is true for cleaning up a batch of
stale domain overrides or DNS records. The work is mechanical and the per-record confirm
step stops being a safety feature and becomes noise the user clicks through. What is
missing is a way to select a set of rows, see one diff for the whole set, and apply it —
without weakening the diff-before-write or one-instance-at-a-time rules that the
per-record path enforces.

## Scope

In:
- Row selection (AntD `rowSelection`) on the Block Lists, Domains, and Local DNS A/AAAA
  tables, with a bulk action bar that appears only when rows are selected.
- Bulk **enable / disable / remove** on Block Lists and Domains.
- Bulk **remove** on Local DNS. No bulk add or edit anywhere.
- Three backend bulk endpoints — one per resource, each scoped to exactly one instance —
  that apply a list of changes sequentially inside a single Pi-hole session and return a
  per-record result array.
- A single confirm dialog per bulk action, itemizing every record and instance that will
  change before anything fires.
- Applying a bulk action across several instances by issuing one per-instance request at a
  time, strictly sequentially, from the browser. **(Revision 4)** A single instance's Local
  DNS batch may additionally be split into consecutive chunks of at most 200 records (see
  step 11); chunks are issued sequentially too, so at most one request is ever in flight.
- Gravity marked pending once per instance whose adlists actually changed.

Out:
- Bulk actions on Groups and Clients (excluded by the answered scope question).
- Bulk actions on the CNAME tab. Plan 0008 introduces that tab; extending selection to it
  is a follow-up, not this plan.
- Bulk *add* / *copy to instance*. "Copy here" stays per-record.
- Bulk group reassignment ("set groups on these five adlists"). Reasonable later; not here.
- Any change to the existing single-record endpoints or their semantics. The per-record
  buttons stay exactly as they are.
- Select-across-pages. Selection is bounded by the current table page — but
  **(Revision 2) AntD does not give us this for free.** All three tables pass the full row
  array as `dataSource` and let AntD paginate client-side, so every row stays in
  `dataSource` on every page and AntD retains `selectedRowKeys` across page changes
  (`preserveSelectedRowKeys` governs only keys absent from `dataSource`, which is not the
  case here). Steps 9-11 therefore clear the selection explicitly on page change; see the
  per-page instructions there.

## Approach

**Backend bulk endpoints, not a browser loop.** The backlog note framed this as "a UI
change over the existing endpoints, not a new backend capability." That is true in the
narrow sense — a sequential `for` loop of `await api.updateAdlist(...)` in the browser
would work and would not violate any invariant. It is rejected anyway, for four reasons:

1. **Session churn.** Each existing single-record call constructs a fresh `PiholeClient`,
   which means a `POST /api/auth` and a `DELETE /api/auth` per record. Twenty records is
   forty extra authenticated round-trips against a device that caps concurrent sessions.
   For Local DNS it is worse: each single-record call re-reads the entire `GET /api/config`
   tree via `_settings_tree` before `_parse_dns_hosts` parses it, so a twenty-record bulk
   delete would pull the full config twenty times.
   One endpoint, one session, one config read.
2. **The invariant lives in the backend.** "One instance mutated at a time" is enforceable
   in a route that resolves exactly one `Instance`; in a browser loop it is a convention
   that a future refactor to `Promise.all` silently breaks. The recorded failure mode here
   is real — two simultaneous authenticated calls to one Pi-hole can race into a 401.
3. **Audit quality.** One bulk operation should read as one intent in `audit.log`, with the
   full before/after set, rather than as N unrelated single-record lines interleaved with
   whatever else happened.
4. **Uniform partial-failure reporting.** The `{"results": [{name, success, error}]}`
   convention already used by the multi-instance add endpoints extends naturally; a browser
   loop would have to reinvent it per page.

**Each bulk endpoint is per-instance by construction.** The instance is a path parameter,
the route resolves one `Instance`, opens one client, and iterates in a plain `for` loop.
Multi-instance bulk work is the browser's job, and it does it by awaiting one instance's
request fully before starting the next — the same pattern `blocking_update` uses
server-side, and the same pattern the confirm dialog describes to the user.

**Diff before write is preserved per record, not skipped for speed.** Each endpoint re-reads
current state once at the top of the session (`get_adlists()` / `get_domains()` /
`get_config()`), matches each requested record against it, and skips-with-a-reason any
record that no longer exists or is already in the requested state. A record that is already
disabled is reported `success: true, changed: false` rather than being written again — that
is what keeps the gravity-pending flag honest.

**Local DNS bulk delete reuses the single-record logic, including its rollback.** The
sibling-hostname rewrite (`_rewrite_without`) and the `_partial_dns_failure` path are the
delicate part of `dns_record_delete`; the bulk route factors that body into a helper that
both routes call, rather than reimplementing it. A partial failure mid-batch stops the
batch and reports which records were already deleted — silently continuing after a rollback
failure would compound the damage.

**On the FTL-restart behavior plan 0008 found (Revision 1 note):** live testing during 0008
established that a `dns.cnameRecords` write briefly restarts something in FTL, causing the
very next request to that instance to intermittently fail at the transport level — and that
`dns.hosts` writes, despite using the same generic config-item mechanism, do *not* show this.
Adlists and domains are unaffected by either behavior for a different reason: they go through
their own dedicated REST resources (`/api/lists`, `/api/domains`), not the generic
`/api/config/dns/...` item-write path that `dns.hosts` and `dns.cnameRecords` share, and
sequential adlist/domain writes within one session have already been exercised live (plan
0007's group-targeting verification) without incident. No retry or verification machinery
like 0008's `_cname_settings_tree` / `_cname_write_add` is warranted here as a result. What
*is* worth carrying forward: this plan's existing per-record `except PiholeAPIError` /
`except Exception` handling already does the conservative thing on an unexpected transient
failure — report that one record as failed and move on — rather than guessing at success, so
no code change is needed even if live testing surfaces an occasional hiccup on a large batch.
The live-verification acceptance criterion below asks for a realistically-sized batch
specifically to give that a chance to show up if it exists.

Rejected alternatives:

- **One generic `/api/bulk` endpoint with a resource discriminator.** Rejected: three
  different record identities, three different validators, three different gravity
  semantics. A union payload would be narrowed by every caller anyway.
- **Server-side multi-instance bulk (an `instances: [...]` field).** Rejected: it puts a
  loop over instances inside one request handler, which is exactly the shape invariant #1
  exists to forbid, even if implemented sequentially. Keeping one request = one instance
  makes the invariant checkable by reading the route signature.
- **Optimistic UI that applies then reconciles.** Rejected: diff-before-write means the user
  sees the change before it happens, not after.
- **(Revision 5) Pi-hole's own batch endpoints (`POST /api/lists:batchDelete`,
  `POST /api/domains:batchDelete`).** Verified 2026-09-07 against lab-pihole's served spec
  (`/api/docs/specs/main.yaml`, `lists.yaml`, `domains.yaml`): both exist, take an array of
  `{item, type}` (lists) / `{item, type, kind}` (domains), and respond `204 Items deleted` with
  no body, or `404 Item not found` / `400` for the **whole request** with a body containing
  only `took`. The only `batch*` operations in the entire spec are the four `batchDelete`s
  (domains, groups, clients, lists). Rejected for this plan: (a) they cover *delete only* —
  there is no batch enable/disable anywhere in the Pi-hole API, so two of the three actions
  are per-record regardless; (b) a 204 with no per-item result cannot produce the
  `results[]` array every caller here depends on, and cannot distinguish "already gone" from
  "deleted"; (c) worse, a single record that vanished between page load and apply fails the
  **entire** batch with a bare 404 — the precise case this plan's acceptance criteria require
  to be reported per-record without failing the rest; (d) `PiholeClient` does not wrap them
  and adding a new client method plus its tests is outside this plan's scope. The per-record
  loop inside one session already removes the session churn that motivated this plan. A
  follow-up may adopt `batchDelete` for the delete action if round-trip count ever matters;
  not here.

## Files

- `app/routes/api.py` — `AdlistBulkInput`, `DNSRecordRef`, `DNSRecordBulkInput`, the two
  bulk routes, and `_delete_dns_host_record(client, owner, hostname, instance)` factored out
  of `dns_record_delete` (signature as in step 4 — **(Revision 5)** an earlier draft of this
  line listed a different parameter list; step 4 is authoritative).
- `app/routes/domains.py` — `DomainBulkInput` and the domains bulk route.
- `frontend/src/api.ts` — `BulkAction`, `BulkResult`, `DomainRef`, `DNSRecordRef` types and
  `bulkAdlists`, `bulkDomains`, `bulkDNSRecords`.
- `frontend/src/pages/BulkActionBar.tsx` — new; the shared selection toolbar
  (count, action buttons, Clear selection), used by all three tables.
- `frontend/src/pages/AdlistsPage.tsx` — `rowSelection`, the bar, and the bulk apply flow.
- `frontend/src/pages/DomainsPage.tsx` — same.
- `frontend/src/pages/DNSHostsTab.tsx` — same, delete only. (If plan 0008 has not merged,
  this is `DNSPage.tsx`; see Steps.)
- `frontend/src/styles.css` — `.bulk-bar` layout.
- `tests/test_api.py` — adlist and DNS bulk tests.
- `tests/test_domains.py` — domain bulk tests.
- `docs/design.md` — §5.3, §5.5 and §5.2 gain a bulk-action bullet; §8's "every mutating
  button opens a confirm step" note extended to cover bulk.
- `docs/potential-features.md` — move the bulk/multi-select item from §1 to the shipped list.
- `app/static/ui/` — rebuilt frontend bundle (invariant #8).

## Steps

1. **Sequencing note for the implementer (Revision 1: resolved — 0008 has merged).** Plan
   0008 split `DNSPage.tsx` into `DNSPage.tsx` + `DNSHostsTab.tsx` + `DNSCnamesTab.tsx`, and
   that split is now on `main`. Branch `feature/0010-bulk-multi-select-actions` from `main`
   and put the Local DNS selection UI in `DNSHostsTab.tsx` (the A/AAAA table) — not
   `DNSCnamesTab.tsx`, which is explicitly Out of Scope.

2. **`app/routes/api.py` — adlist bulk model.**

   ```python
   class AdlistBulkInput(BaseModel):
       action: Literal["enable", "disable", "delete"]
       addresses: list[str] = Field(min_length=1, max_length=200)

       @field_validator("addresses")
       @classmethod
       def validate_addresses(cls, value: list[str]) -> list[str]:
           if any(len(item) > 2048 for item in value):
               raise ValueError("Address too long")
           return [screen_adlist_url(item, resolve=False) for item in value]
   ```

   `resolve=False` matches `AdlistUpdateInput` / `AdlistDeleteInput`: these are identifiers
   for records Pi-hole already holds, not URLs this app is about to fetch.

3. **`POST /api/adlists/{instance_name}/bulk`.** Resolve the instance. In one
   `with _client(instance) as client:` session:

   - `current` — one read for the whole batch, the diff basis, keyed by
     `_adlist_key(item.address)`. **(Revision 4) Build it first-match-wins, not with a plain
     dict comprehension.** `{_adlist_key(item.address): item for item in client.get_adlists()}`
     lets a *later* duplicate silently overwrite an earlier one, whereas the single-record
     path (`app/routes/api.py:679`) uses `next(item for item in client.get_adlists() if ...)`
     and so resolves to the **first** match. If an instance holds two adlists with the same
     canonical key, the bulk route must act on the same record the per-record buttons act on.
     Use an explicit loop with `current.setdefault(key, item)`.
   - **(Revision 3) Dedupe by canonical key, not by literal string.** Build the iteration
     order as: walk `payload.addresses` in order, compute `key = _adlist_key(address)` for
     each, and keep only the first address seen for a given `key` (e.g. a `dict[str, str]`
     of `key -> first literal address`, which preserves insertion order). Iterate that.
     `dict.fromkeys(payload.addresses)` is **wrong here**: it dedupes the literal strings,
     but every lookup below is by `_adlist_key(address)`, and `_adlist_key`
     (`app/routes/api.py:121`) collapses trailing slashes and host/scheme case that
     `screen_adlist_url(..., resolve=False)` deliberately preserves — it only strips a bare
     root `/`. So `https://lists.example/block.txt` and `https://lists.example/block.txt/`
     survive literal dedupe as two entries, resolve to the *same* stored adlist, and the
     loop below would write it twice: on a bulk disable the second pass still sees the
     stale `item.enabled` from `current` and reports a second bogus `changed: True`, and on
     a bulk delete the second `delete_adlist` fails for a record that was in fact deleted,
     reporting a false error. This is not hypothetical — the plan's own
     `test_adlist_bulk_delete_uses_stored_address` is built on exactly that trailing-slash
     alias. Canonical dedupe fixes it at the source: each stored record is then visited at
     most once per batch, so no in-batch mutation of `current` is needed (unlike step 5,
     where one `dns.hosts` line can legitimately carry several *distinct* hostnames that
     are each their own batch entry). Report each result under the first literal the caller
     submitted for that key, so the response still echoes the caller's own strings.
   - Iterate that deduped mapping in a plain `for` loop. For each:
     - not in `current` → `{"id": address, "success": False, "changed": False, "error": "No longer present on this instance"}`.
     - `action == "delete"` → `client.delete_adlist(item.address)` (the *stored* address,
       matching `adlist_delete`), audit `adlist_delete`, `changed: True`.
     - `action in ("enable", "disable")` and `item.enabled` already equals the target →
       `{"success": True, "changed": False, "error": None}`, no write, no audit.
     - otherwise `client.update_adlist(item, enabled=target, groups=list(item.groups))` —
       groups passed through unchanged, so a bulk enable never rewrites group membership;
       audit `adlist_update`.
     - `except PiholeAPIError as exc` → record `error=str(exc)`; `except Exception` →
       record `error="Unable to update Pi-hole block lists"`. Continue to the next record.
   - Audit each touched record with the existing `adlist_update` / `adlist_delete` action
     names plus `{"bulk": payload.action}` in the `after` dict, so a bulk run is
     distinguishable in `audit.log`.
   - After the loop, `mark_gravity_pending(instance_name)` iff any result has
     `changed: True`.
   - Return `{"instance": instance_name, "action": payload.action, "results": results,
     "gravity_pending": instance_name in load_pending_gravity()}`.

4. **`app/routes/api.py` — factor out single-record DNS deletion.** Extract the body of
   `dns_record_delete` between "find the owner" and "audit" into:

   ```python
   def _delete_dns_host_record(client: PiholeClient, owner: dict[str, Any], hostname: str, instance: str) -> str | None:
   ```

   containing the existing `delete_dns_host(owner["raw"])` → `_rewrite_without` →
   `add_dns_host(replacement)` → rollback → `_partial_dns_failure(...)` sequence verbatim,
   with one change (Revision 1): **return the `replacement` value** (the exact new raw line,
   or `None` if no hostnames survived on it) on the success path, instead of returning
   nothing. `dns_record_delete` calls it and ignores the return value — no behaviour change
   there, and the existing DNS tests must still pass untouched. The bulk route in Step 5
   needs this value to keep its own row tracking correct across a batch; without it, a
   second hostname sharing the same original `dns.hosts` line would be looked up by a raw
   string that no longer exists once the first hostname's deletion has rewritten the line,
   and would be wrongly reported as gone.

5. **`app/routes/api.py` — DNS bulk model and route.**

   ```python
   class DNSRecordRef(BaseModel):
       hostname: str = Field(min_length=1, max_length=253)
       address: str = Field(min_length=1, max_length=45)


   class DNSRecordBulkInput(BaseModel):
       instance: str = Field(min_length=1, max_length=100)
       action: Literal["delete"]
       records: list[DNSRecordRef] = Field(min_length=1, max_length=200)
   ```

   `POST /api/dns/records/bulk`: resolve the instance, then open **one** client session and
   read the config tree **inside it** — `with _client(instance) as client:` first, then
   `tree = client.get_config()`, then seed a local `rows = _parse_dns_hosts(tree)` list.
   **(Revision 2) Do not call `_settings_tree(instance)` here.** That helper opens its own
   `_client(instance)` context (`app/routes/api.py:1043`), so `_settings_tree` followed by a
   mutation session would construct *two* Pi-hole sessions per bulk request and fail the
   "exactly one Pi-hole session regardless of batch size" acceptance criterion below. The
   single-record `dns_record_delete` does spend two sessions this way; the bulk route
   deliberately does not, which is the whole point of item 1 in Approach ("One endpoint, one
   session, one config read"). Wrap the session body in the same
   `except HTTPException: raise` / `except Exception as exc: raise _api_error(exc) from exc`
   pair `_settings_tree` uses, so a failure to read the config still surfaces identically.

   **(Revision 5) Normalize and dedupe the submitted records the way the single-record route
   does, before the loop.** For each `DNSRecordRef`, compute
   `hostname = ref.hostname.strip().lower().rstrip(".")` and
   `entry_ip = _canonical_addr(ref.address)` — exactly the two transforms `dns_record_delete`
   applies (`app/routes/api.py:809-810`). These are required for matching, not optional
   hygiene: `_host_entry` (`app/routes/api.py:302`) stores `entry_hosts` lowercased and
   dot-stripped and `entry_ip` canonicalized, so an un-normalized `Server.LAN` or `::1` spelled
   long-form would silently fail `hostname in entry_hosts` / `entry_ip ==` and be reported as
   "no longer exists". Dedupe on the `(entry_ip, hostname)` pair, first-seen wins, and report
   each result under the first `ref` submitted for that pair with `id` =
   `f"{ref.address} {ref.hostname}"` (the caller's own strings). Unlike the single-record
   route, do **not** call `_dns_record(...)` to validate the ref: a malformed hostname or
   address cannot match any parsed row and is reported per-record as "no longer exists",
   which is the correct bulk-shaped outcome; a 422 for the whole batch is not.

   For each deduped record:

   - Structurally match an owner row in the **current** `rows` list (by `entry_ip` +
     `hostname in entry_hosts`, same as the single-record route). Not found →
     `{"success": False, "changed": False, "error": "Record no longer exists on this instance"}`,
     continue to the next record.
   - Call `replacement = _delete_dns_host_record(client, owner, hostname, instance)`.
   - **(Revision 1) Update `rows` to reflect the write that just happened**, before moving to
     the next record: remove `owner` itself from `rows`, and for every *other* row whose
     `raw` equals `owner["raw"]` (i.e. a sibling hostname that shared the same original
     line) — if `replacement is not None`, set that row's `raw` to `replacement` and drop
     `hostname` from its `entry_hosts`; if `replacement is None`, remove that row from
     `rows` too (no hostnames survived on the line, so nothing is left to look up). This is
     what makes `test_dns_bulk_delete_handles_two_hostnames_on_one_line` (Tests, below) pass:
     without it, the second hostname's lookup in the same batch would use the stale raw
     value and 404 for a record Pi-hole no longer has under that string.
   - `except HTTPException as exc:` — this only happens via `_partial_dns_failure` (a failed
     rollback leaving the instance in a state no further automated write should touch) —
     abort the batch. **(Revision 4) Do not propagate it unchanged.** `_partial_dns_failure`
     (`app/routes/api.py:415`) builds its 502 detail from only the line it was handed; it
     knows nothing about this batch, so propagating it verbatim would lose every record the
     batch had *already* deleted successfully — which the Approach section explicitly
     promises to report ("stops the batch and reports which records were already deleted"),
     and which the user needs in order to know what state the instance is now in. Catch it,
     and re-raise `HTTPException(status_code=exc.status_code, detail=...)` where the detail
     is the original `exc.detail` followed by a sentence naming the records this batch
     deleted before the failure — e.g. `" Already deleted in this batch: 'a.example → 10.0.0.1',
     'b.example → 10.0.0.2'."` — or, when the failure hit the first record, a sentence
     saying nothing else in the batch was applied. Do not re-run `record(...)`;
     `_partial_dns_failure` has already written the audit entry for the lost line, and the
     per-record deletions have already written their own.
   - `except (PiholeAPIError, Exception):` — any other failure, including the case where
     `_delete_dns_host_record`'s rewrite failed but its rollback successfully restored the
     original line — record `{"success": False, "changed": False, "error": str(exc)}` (or
     the generic message for a bare `Exception`) and **continue the batch**, matching the
     adlist/domain bulk routes' per-record catch-and-continue (Step 3/6): the instance's
     data is intact in this case, so there's no reason to abandon the rest of the batch over
     one record's hiccup.
   - On success, audit as `dns_record_delete` with `{"bulk": "delete"}` in `after` and report
     `{"success": True, "changed": True, "error": None}`.

   Return `{"instance": ..., "action": "delete", "results": [...]}`.

   `action` is a one-member `Literal` on purpose: it keeps the wire shape identical to the
   other two bulk endpoints and leaves room for `enable`/`disable` if `dns.hosts` ever gains
   a disabled state, without inventing one now.

6. **`app/routes/domains.py` — bulk model and route.**

   ```python
   class DomainRef(BaseModel):
       domain: str = Field(min_length=1, max_length=1000)
       type: Literal["allow", "deny"]
       kind: Literal["exact", "regex"]


   class DomainBulkInput(BaseModel):
       action: Literal["enable", "disable", "delete"]
       domains: list[DomainRef] = Field(min_length=1, max_length=200)
   ```

   `POST /api/domains/{instance_name}/bulk` mirrors step 3 exactly — **(Revision 3)
   including its canonical-dedupe rule**, which matters here for the same reason: `DomainRef`
   applies no normalizing validator, so `Example.com`, `example.com` and `example.com.` reach
   the route as three distinct literals that all collapse to one key, and literal dedupe
   would write that record three times. Dedupe on the full `(type, kind,
   domain.lower().rstrip("."))` tuple — not on `domain` alone, since the same domain string
   legitimately appears as separate records under different `type`/`kind` combinations — and
   report each result under the first literal submitted for that tuple.

   **(Revision 4) The key must depend on `kind`, and must not normalize regex patterns.**
   Use `(type, kind, domain.lower().rstrip("."))` **only when `kind == "exact"`**; when
   `kind == "regex"` use `(type, kind, domain)` with the pattern **verbatim** — no
   `.lower()`, no `.rstrip(".")`. A regex pattern is not a hostname: `ads` and `ads.` are
   different patterns (`.` matches any character), and Pi-hole stores patterns verbatim as
   distinct records with distinct `id`s regardless of case (whatever FTL's lowercase query
   matching later does with them). Normalizing them would collapse two genuinely distinct
   stored records into one key, so selecting `ads` could mutate `ads.` instead — a silent
   wrong-record write, the worst failure mode available to this feature.

   `_find_domain` (`app/routes/domains.py:132`) *does* apply `.lower().rstrip(".")`
   unconditionally, so it carries this flaw for regex records today. **Do not change
   `_find_domain`** — the single-record endpoints are out of scope for this plan. The bulk
   route is deliberately stricter: for a regex record whose literal pattern is not present,
   it reports `success: False, changed: False, error: "No longer present on this instance"`
   rather than guessing at a normalized neighbour. Record this divergence in Contracts. A
   follow-up plan should fix `_find_domain` itself; that is not this change.

   Build the map **first-match-wins** (`setdefault`), for the same reason as step 3:
   `_find_domain` uses `next(...)`, so a plain dict comprehension would flip first-match to
   last-match semantics. Uses `client.get_domains()` as the diff basis keyed by that tuple, `delete_domain(item.domain, item.type, item.kind)`
   using the stored values, and `update_domain(item, enabled=target, groups=list(item.groups))`.
   Result `id` is `f"{type}:{kind}:{domain}"`, matching the table's `rowKey`. **No gravity
   marking** — domain overrides take effect without a gravity run (design.md §5.5, verified
   by plan 0007). Response omits `gravity_pending` entirely.

7. **`frontend/src/api.ts`.**

   ```ts
   export type BulkAction = 'enable' | 'disable' | 'delete'

   export interface BulkResult {
     id: string
     success: boolean
     changed: boolean
     error: string | null
   }

   export interface DomainRef {
     domain: string
     type: 'allow' | 'deny'
     kind: 'exact' | 'regex'
   }

   export interface DNSRecordRef {
     hostname: string
     address: string
   }
   ```

   Methods:

   ```ts
   bulkAdlists: (instance: string, action: BulkAction, addresses: string[]) =>
     request<{ instance: string; action: BulkAction; results: BulkResult[]; gravity_pending: boolean }>(
       `/api/adlists/${encodeURIComponent(instance)}/bulk`,
       { method: 'POST', body: JSON.stringify({ action, addresses }) },
     ),
   bulkDomains: (instance: string, action: BulkAction, domains: DomainRef[]) =>
     request<{ instance: string; action: BulkAction; results: BulkResult[] }>(
       `/api/domains/${encodeURIComponent(instance)}/bulk`,
       { method: 'POST', body: JSON.stringify({ action, domains }) },
     ),
   bulkDNSRecords: (instance: string, records: DNSRecordRef[]) =>
     request<{ instance: string; action: 'delete'; results: BulkResult[] }>('/api/dns/records/bulk', {
       method: 'POST', body: JSON.stringify({ instance, action: 'delete', records }),
     }),
   ```

8. **`frontend/src/pages/BulkActionBar.tsx`.** A small presentational component:

   ```ts
   interface BulkActionBarProps {
     selectedCount: number
     instanceNames: string[]
     selectedInstances: string[]
     onInstancesChange: (instances: string[]) => void
     actions: Array<{ key: BulkAction; label: string; danger?: boolean }>
     onAction: (action: BulkAction) => void
     onClear: () => void
     disabled: boolean
   }
   ```

   Renders nothing when `selectedCount === 0`. Otherwise an AntD `Space` in a
   `.bulk-bar` div: `"{n} selected"`, a multi-`Select` of target instances (the bulk action
   applies only to the instances chosen here, defaulting to all instance names), one
   `Button` per action, and a text `Button` "Clear selection". No `api` calls of its own —
   AGENTS.md keeps server calls in `api.ts` and the page owns the flow. **(Revision 5)** It
   lives in `pages/` even though it is not a screen: there is no `components/` directory, and
   plan 0008 already placed the non-screen `DNSHostsTab.tsx` / `DNSCnamesTab.tsx` there under
   AGENTS.md's "group related components together" rule. Follow that precedent; do not create
   a new directory for one file.

9. **`AdlistsPage.tsx` — selection and apply.** Add `selectedKeys: string[]`,
   `bulkInstances: string[]`, and **(Revision 2)** `currentPage: number` (default `1`) state;
   pass `rowSelection={{ selectedRowKeys: selectedKeys, onChange: setSelectedKeys }}` to the
   `Table`. **(Revision 2)** This table currently uses uncontrolled pagination
   (`pagination={{ pageSize: 25, hideOnSinglePage: true }}`, `AdlistsPage.tsx:252`); make it
   controlled and clear the selection whenever the page changes, so selection stays bounded
   by the visible page as Scope requires:
   `pagination={{ current: currentPage, pageSize: 25, hideOnSinglePage: true, onChange: (page) => { setCurrentPage(page); setSelectedKeys([]); } }}`.
   **(Revision 5)** Also `setCurrentPage(1)` inside `load()` — exactly as `DNSHostsTab.load`
   already does (`DNSHostsTab.tsx:50`) — because once pagination is controlled, a bulk delete
   that empties the last page would otherwise leave `currentPage` pointing past the end and
   render an empty table. (This page has no search or filter state, so there is nothing else
   to clear on; an earlier draft said otherwise.) Render `<BulkActionBar ... actions={[{key:'enable',label:'Enable'},{key:'disable',label:'Disable'},{key:'delete',label:'Remove',danger:true}]} />`
   above it. `applyBulk(action)`:

   - Build `targets: Array<{ instance: string; addresses: string[] }>` — for each selected
     instance, the selected rows that actually exist on it, sending the **stored** per-instance
     value `row.instances[instance].address` (not the merged `row.address`, which may differ
     in trailing slash or case), skipping instances with nothing to do.
   - `modal.confirm` listing, per instance, the count and the addresses (a `List` in
     `content`), with a warning line for `delete` and, for adlists, "Gravity will not run
     automatically."
   - **Sequential loop, `await` per instance** — `for (const target of targets) { await api.bulkAdlists(...) }`.
     Never `Promise.all`; a comment at the loop says why (invariant #1, and the concurrent-
     login 401 race). Collect results.
   - **(Revision 5) A thrown request must not abandon the remaining instances silently.**
     `request()` in `api.ts` throws an `Error` on any non-2xx response (`api.ts:229-248`) — a
     404/409, a network failure, or the DNS route's rollback 502 — so an unguarded `await`
     inside the loop would skip every later instance with no report. Wrap each instance's
     `await` in `try/catch`; on a throw, push an instance-level failure
     `{ instance, error: message }`, and **continue to the next instance** — the failure is
     scoped to one Pi-hole, and the server-side `blocking_update` loop makes the same choice
     (`api.py:578-597`). Include instance-level failures in the summary alongside per-record
     failures. Set `working` true for the whole loop and pass `disabled={working}` to
     `BulkActionBar` so a second bulk action cannot start while one is in flight.
   - Summarize with `message.success` / `message.warning`, naming failed ids and, when
     nothing changed, saying so ("3 selected, 0 changed — already in that state").
   - `setSelectedKeys([])`, then `await load()`.

10. **`DomainsPage.tsx`.** Same as step 9 with `rowKey` `${type}:${kind}:${domain}` (already
    the table's key), `api.bulkDomains`, and no gravity wording. **(Revision 2)** This table
    is also uncontrolled today (`DomainsPage.tsx:228`); apply the same controlled-pagination
    + `setSelectedKeys([])`-on-page-change treatment and the same `setCurrentPage(1)` in
    `load()`. **(Revision 5)** This page also has no search or filter state; nothing else to
    clear on. Build each `DomainRef` from `row.domain`, `row.type`, `row.kind`: unlike
    `AdlistRow`, the per-instance `row.instances[instance]` is a `Domain` that carries no
    `domain` field of its own (`api.ts:145`), so the row-level value is the only one available.
    That is safe: the backend's exact-kind normalization absorbs any case / trailing-dot
    difference from an instance's stored spelling, and regex rows carry the pattern verbatim.

11. **`DNSHostsTab.tsx`.** Same shape, `actions={[{key:'delete',label:'Remove',danger:true}]}`
    only. The selected row is a hostname that may have several records on one instance;
    expand each selected `DNSRow` into one `DNSRecordRef` per `{hostname, address}` present
    on that instance, so a hostname with two addresses deletes both and the confirm dialog
    shows both. `api.bulkDNSRecords` per instance, sequentially.

    **(Revision 4) Chunk the expanded records to respect Step 5's 200-record cap.** A
    `DNSRow` carries `instances: Record<string, DNSRecord[]>` with no bound on the array
    (`frontend/src/api.ts:101`), and the page shows 25 rows, so a page-full selection of
    hostnames that each hold nine addresses on one instance expands to 225 `DNSRecordRef`s
    and would be rejected wholesale by `DNSRecordBulkInput`'s `max_length=200` — a valid
    selection the user can reach from the UI, failing with a validation error that names no
    fixable cause. Split each instance's expanded record list into consecutive chunks of at
    most 200 and `await` them one after another, in order, inside the existing per-instance
    sequential loop. Never overlap chunks or instances — the concurrency rule is unchanged
    and applies to chunks exactly as it applies to instances. Merge the chunks' `results`
    arrays into one per-instance result set before summarizing, so the confirm-summary
    wording and the failure reporting are identical whether or not chunking occurred. The
    confirm dialog still itemizes the whole selection once, before the first request.
    **(Revision 5)** If a chunk throws (step 9's `try/catch`), **stop the remaining chunks
    for that instance** — a rollback-failure 502 means that Pi-hole's `dns.hosts` is in a
    state no further automated write should touch — record the instance-level failure, and
    continue to the next instance. This tab already has
    controlled pagination with `onChange: setCurrentPage` (`DNSHostsTab.tsx:193`), so
    **(Revision 2)** only extend that existing handler to also `setSelectedKeys([])` rather
    than adding new page state. Clear `selectedKeys` when `search` changes too, so a
    filtered-away row can't be acted on invisibly.

12. **`frontend/src/styles.css`.** `.bulk-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; padding: 8px 12px; border-radius: 8px; background: rgba(22, 119, 255, 0.08); }`
    plus a `flex-direction: column; align-items: flex-start;` rule inside the existing
    mobile media query, next to `.dns-table-tools`.

13. **Rebuild the bundle.** `cd frontend && npm run build && npm run lint`, commit
    `app/static/ui/`.

14. **Docs.** design.md: §5.2/§5.3/§5.5 each gain a bullet noting multi-select with a single
    confirm, applied sequentially one instance at a time; §8's closing note extended to
    "…including bulk actions, which show every affected record and instance before firing."
    Move the bulk item in `docs/potential-features.md` from §1 to the shipped list, citing
    plan 0010.

## Contracts

### `POST /api/adlists/{instance_name}/bulk` (new)

Request (`AdlistBulkInput`):

```json
{ "action": "enable" | "disable" | "delete", "addresses": ["https://lists.example/block.txt"] }
```

Response:

```json
{
  "instance": "pihole1",
  "action": "disable",
  "results": [{ "id": "https://lists.example/block.txt", "success": true, "changed": true, "error": null }],
  "gravity_pending": true
}
```

`changed: false` with `success: true` means the record was already in the requested state
and was not written. Errors: 404 unknown instance; 409 no stored credential; 422 more than
200 addresses or an address failing `screen_adlist_url`.

### `POST /api/domains/{instance_name}/bulk` (new)

Request (`DomainBulkInput`):

```json
{ "action": "delete", "domains": [{ "domain": "ads.example.com", "type": "deny", "kind": "exact" }] }
```

Response: same shape as adlists **without** `gravity_pending`; `id` is `"deny:exact:ads.example.com"`.

**(Revision 4) Deliberate divergence from `_find_domain`.** For `kind == "regex"` this route
matches the stored `domain` **verbatim** — no `.lower()`, no `.rstrip(".")` — whereas
`_find_domain` (`app/routes/domains.py:132`), and therefore the single-record endpoints,
normalize unconditionally. A regex pattern is not a hostname, so normalizing it can resolve
to a different record; the bulk route refuses to guess and reports the record as no longer
present instead. The single-record behavior is unchanged by this plan. A follow-up should
correct `_find_domain`.

### `POST /api/dns/records/bulk` (new)

Request (`DNSRecordBulkInput`):

```json
{ "instance": "pihole1", "action": "delete", "records": [{ "hostname": "server.lan", "address": "192.168.1.10" }] }
```

Response: `{ "instance": ..., "action": "delete", "results": [...] }`; `id` is
`"<address> <hostname>"`. A rollback failure inside the batch aborts the remainder and
raises a 502 whose detail is `_partial_dns_failure`'s message **plus** the records this
batch had already deleted (Revision 4, step 5). Callers may send more than 200 records only
by issuing consecutive requests; the browser does this by chunking (step 11).

### `api.ts`

`bulkAdlists`, `bulkDomains`, `bulkDNSRecords` as written in step 7. No existing method
signature changes.

### Config keys / DB

N/A — no new config key. `mark_gravity_pending` / `load_pending_gravity` are existing and
already imported by `app/routes/api.py`.

## Tests

### `tests/test_api.py`

- `test_adlist_bulk_disable_writes_only_changed_records` — three adlists, two enabled;
  asserts only the enabled two were written, that the already-disabled one comes back
  `success: true, changed: false`, and that its `update_adlist` was never called.
- `test_adlist_bulk_preserves_group_membership` — an adlist in groups `(0, 3)`; asserts the
  bulk enable passed `groups=[0, 3]` through unchanged.
- `test_adlist_bulk_delete_uses_stored_address` — stored address differs in trailing slash
  from the submitted one; asserts the stored value was deleted.
- **(Revision 3)** `test_adlist_bulk_dedupes_canonical_aliases` — one stored, enabled adlist;
  the request submits `https://lists.example/block.txt` and
  `https://lists.example/block.txt/` (plus an upper-case-host variant) in a single
  `action="disable"` batch. Asserts `update_adlist` was called **exactly once**, that the
  response carries **one** result rather than two or three, and that it is
  `changed: True`. The pre-Revision-3 loop fails this by writing the record once per alias
  and reporting a second `changed: True` off the stale `current` entry.
- **(Revision 3)** `test_adlist_bulk_delete_dedupes_canonical_aliases` — same alias pair
  under `action="delete"`; asserts `delete_adlist` was called exactly once and that no
  result reports a spurious failure for the second alias.
- `test_adlist_bulk_marks_gravity_pending_only_when_something_changed` — parametrized: a
  batch that changes nothing leaves the instance unmarked; one that changes a record marks
  it.
- `test_adlist_bulk_continues_after_one_failure` — injected `PiholeAPIError` on the second
  of three; asserts all three have results, the third was still attempted, and the failing
  one carries an error string.
- `test_adlist_bulk_reports_missing_records` — an address not on the instance comes back
  `success: false` with "No longer present", and the request does not 404.
- `test_adlist_bulk_rejects_oversized_batch` — 201 addresses raises `ValidationError`.
- `test_adlist_bulk_is_sequential_single_instance` — a fake client that records
  construction; asserts exactly one `PiholeClient` was constructed for the whole batch.
- `test_dns_bulk_delete_removes_each_record` — two records on separate lines; asserts both
  raw lines were deleted.
- `test_dns_bulk_delete_handles_two_hostnames_on_one_line` — `"192.168.1.1 a.lan b.lan"`,
  both selected, `a.lan` processed first; asserts `a.lan`'s deletion rewrites the line to
  `"192.168.1.1 b.lan"`, and that `b.lan`'s subsequent lookup in the *same batch* uses that
  updated raw value (Revision 1's row-tracking fix) rather than the stale original, so both
  are reported as deleted with no orphaned sibling and no spurious "no longer exists" error.
- `test_dns_bulk_delete_aborts_on_rollback_failure` — a client whose replacement
  `add_dns_host` **and** its rollback (re-adding the original line) both fail; asserts a 502
  propagates and later records in the batch were not attempted.
- `test_dns_bulk_delete_continues_after_successful_rollback` (Revision 1) — a client whose
  replacement `add_dns_host` fails but the rollback re-add of the *original* line succeeds
  (data left intact, just this one edit didn't happen); asserts no exception propagates, this
  record's result is `success: False`, and later records in the batch are still attempted —
  contrasting with the double-failure case above, which does abort.
- The existing single-record DNS tests (`test_dns_delete_matches_structurally`,
  `test_dns_delete_preserves_sibling_hostnames`, `test_dns_delete_audits_when_trim_and_restore_both_fail`,
  `test_dns_delete_restore_success_surfaces_original_error_without_orphan`,
  `tests/test_api.py:477-582`) must pass **untouched**; together they already guard every
  branch the step 4 refactor moves, so no new single-record test is needed. **(Revision 5)**
  An earlier draft asked for a new assertion that `test_dns_delete_preserves_sibling_hostnames`
  already makes.
- **(Revision 4, refiled in Revision 5)** `test_adlist_bulk_uses_first_match_on_duplicate_keys`
  — an instance returning two adlists with the same canonical key; asserts the bulk route
  acts on the **first**, matching `next(...)` in `_find_adlist` (`app/routes/api.py:679`).
- **(Revision 4, refiled in Revision 5)** `test_dns_bulk_rollback_error_names_prior_deletions`
  — a three-record batch where the third triggers `_partial_dns_failure`; asserts the raised
  502 detail contains both the unrecoverable line *and* the two records already deleted in
  the batch.
- **(Revision 5)** `test_dns_bulk_normalizes_hostname_and_address` — stored line
  `"192.168.1.1 server.lan"`; the batch submits `{"hostname": "Server.LAN.", "address": "192.168.1.1"}`
  and asserts the record is deleted rather than reported missing. Include an IPv6 case:
  stored `"fd00::1 box.lan"`, submitted address `"fd00:0:0:0:0:0:0:1"`, must match via
  `_canonical_addr`.
- **(Revision 5)** `test_dns_bulk_dedupes_same_record_spelled_twice` — `server.lan` and
  `SERVER.LAN.` with the same address in one batch; asserts one delete and one result.
- **(Revision 5)** `test_dns_bulk_is_sequential_single_instance` — as the adlist equivalent:
  exactly one `PiholeClient` constructed for a multi-record DNS batch (this is the test that
  would have caught Revision 2's two-session defect).

### `tests/test_domains.py`

- `test_domain_bulk_enable_writes_only_changed_records` — mirror of the adlist case.
- `test_domain_bulk_delete_matches_case_insensitively` — stored `Ads.Example.COM`, submitted
  lowercase; asserts the stored value was passed to `delete_domain`.
- `test_domain_bulk_does_not_mark_gravity` — asserts no gravity call and no
  `gravity_pending` key in the response.
- `test_domain_bulk_preserves_group_membership` — as above.
- `test_domain_bulk_continues_after_one_failure`.
- `test_domain_bulk_rejects_unknown_action` — `action="purge"` raises `ValidationError`.
- **(Revision 3)** `test_domain_bulk_dedupes_case_and_trailing_dot_aliases` — one stored
  enabled deny/exact record `ads.example.com`; the batch submits `Ads.Example.COM`,
  `ads.example.com.` and `ads.example.com` together under `action="disable"`. Asserts
  `update_domain` was called exactly once and exactly one result is returned.
- **(Revision 3)** `test_domain_bulk_does_not_dedupe_across_type_or_kind` — the *same* domain
  string submitted as `deny/exact` and as `allow/exact` in one batch stays two separate
  entries and is written twice, one per record. Guards against over-collapsing the dedupe
  key to `domain` alone.
- **(Revision 4)** `test_domain_bulk_regex_patterns_are_not_normalized` — an instance holding
  two regex deny records, `ads` and `ads.`; a batch disabling `ads` asserts `update_domain`
  was called for the record whose `domain` is exactly `ads` and never for `ads.`. Also
  asserts a regex pattern differing only in case is treated as a distinct record.
- **(Revision 4, refiled in Revision 5)** `test_domain_bulk_uses_first_match_on_duplicate_keys`
  — an instance returning two domains with the same canonical key; asserts the bulk route
  acts on the **first**, matching `next(...)` in `_find_domain` (`app/routes/domains.py:132`).
  (The adlist and DNS tests previously listed here belong in `tests/test_api.py`, where the
  adlist and DNS routes live; they are listed there now.)

## Invariants at risk

1. **One instance mutated at a time.** The invariant this plan is closest to, and the
   reason the design is shaped the way it is. Each bulk endpoint takes exactly one instance
   (path parameter for adlists/domains, a single `instance` field for DNS), resolves one
   `Instance`, constructs one `PiholeClient`, and iterates with a plain `for` loop —
   no `_fanout`, no `ThreadPoolExecutor`, no second instance reachable from the handler.
   Multi-instance application happens in the browser as an awaited sequential loop with an
   explanatory comment; `Promise.all` is forbidden on these paths, and the recorded
   concurrent-login 401 race is the concrete reason. **The Reviewer should grep the diff for
   `_fanout` and `Promise.all` on every new path.**
2. **Diff before write.** Each endpoint reads current state once at the top of the session
   and matches every requested record against it. Records that vanished are reported, not
   blindly written. Records already in the requested state are skipped, not rewritten. The
   confirm dialog itemizes every record and instance before the first request goes out — the
   per-record confirm is replaced by a per-batch confirm that shows strictly more
   information, not less.
3. **Gravity gated behind real changes.** `mark_gravity_pending` fires once per instance and
   only when at least one adlist result has `changed: true` — a bulk enable over
   already-enabled lists marks nothing. Domain and DNS bulk operations never mark gravity,
   matching their single-record equivalents. Nothing here calls `update_gravity`; the user's
   explicit button remains the only trigger.
4. **No background scheduler.** All request-scoped, initiated by a button.
5. **FastAPI owns credentials.** All three routes go through `_client(instance)` /
   `CredentialStore`. The new `api.ts` types carry instance names, addresses, domains, and
   hostnames only.
6. **Outbound requests via `url_policy`.** `AdlistBulkInput` runs every address through
   `screen_adlist_url(..., resolve=False)`, matching `AdlistUpdateInput` /
   `AdlistDeleteInput` — these identify records Pi-hole already holds, and this app never
   fetches them, so the non-resolving form is correct and deliberate. No new outbound
   destination is introduced; every request still goes to an instance base URL already
   validated by `resolve_instance_destination`.
7. **Every route behind auth middleware.** All three routes are registered on existing
   `APIRouter(prefix="/api")` instances in `app/routes/api.py` and `app/routes/domains.py`;
   no exemptions.
8. **Compiled frontend committed.** Step 13.
9. **`main` is production.** Work lands on `feature/0010-bulk-multi-select-actions`, after
   0008 merges (step 1).

Additional hardening note: batches are capped at 200 records per request via
`Field(max_length=200)` so a bulk body cannot be used to hold one Pi-hole session open
indefinitely, and the existing request-body size limit in `http_hardening.py` applies
unchanged.

## Acceptance criteria

- [ ] Selecting rows on Block Lists, Domains, and Local DNS reveals a bulk bar showing the
      selection count, a target-instance picker, and the relevant actions; clearing the
      selection hides it.
- [ ] A bulk action opens exactly one confirm dialog listing every affected record and
      instance before anything is written.
- [ ] A bulk action across two instances issues one request per instance, strictly one
      after the other — verifiable in the browser network panel as non-overlapping
      requests.
- [ ] **(Revision 4)** A Local DNS selection expanding to more than 200 records succeeds,
      split into sequential chunks of at most 200, rather than failing validation.
- [ ] Each bulk request constructs exactly one Pi-hole session regardless of batch size
      (asserted by test, and visible as one `POST /api/auth` per instance).
- [ ] Records already in the requested state come back `success: true, changed: false` and
      are not written; the UI says so rather than claiming a change.
- [ ] **(Revision 3)** Two spellings of the same record in one batch (adlist trailing
      slash or host case; domain case or trailing dot) are written once and produce one
      result, not one per spelling.
- [ ] A record that disappeared between page load and apply is reported per-record without
      failing the whole batch.
- [ ] One record's failure does not abandon the rest of the batch (except the DNS
      rollback-failure case, which aborts deliberately and surfaces a 502 naming the
      affected lines).
- [ ] **(Revision 2)** Changing the table page clears the row selection on all three
      tables, so a bulk action can never touch a record the user cannot currently see.
- [ ] Bulk enable/disable never alters group membership.
- [ ] Gravity is marked pending exactly once for an instance whose adlists actually
      changed, and not at all for domain or DNS bulk actions.
- [ ] Bulk-deleting two hostnames that share one `dns.hosts` line leaves no orphaned
      sibling and no stray line.
- [ ] Every existing single-record action on all three pages behaves exactly as before.
- [ ] **Live verification against a real instance (Revision 1; instance corrected 2026-09-07)**:
      on **lab-pihole** — the only configured instance, and the one designated for testing and
      validation; an earlier draft said `pihole1`, which does not exist here — run each
      of the three bulk endpoints through the actual UI with a realistically-sized batch
      (10-20 records, not the 200-record ceiling — that cap is a defensive bound, not a
      target size). Bulk-disable then bulk-enable a handful of adlists and confirm the
      Block Lists page's gravity-pending prompt appears once, correctly, and not on repeats
      of an already-applied state. Bulk-delete a handful of domain overrides and DNS
      records, including at least one pair of hostnames sharing a single `dns.hosts` line,
      and confirm no sibling is orphaned. Watch for any transport-level hiccup analogous to
      plan 0008's CNAME-write restart discovery — none is expected here (adlists, domains,
      and `dns.hosts` all differ from `dns.cnameRecords` in ways Approach explains), but a
      batch this plan has not exercised live before is exactly the kind of assumption 0008
      showed is worth checking rather than trusting by analogy. Record the result in this
      plan file before merge.
- [ ] `./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
- [ ] design.md §5.2/§5.3/§5.5/§8 and docs/potential-features.md updated.

## Implementation verification — 2026-09-07

- Implemented on the existing `feature/0010-bulk-multi-select-actions` branch, with no
  implementation scope changes. The unrelated user edit to `CLAUDE.md` was left untouched.
- `./.venv/bin/pytest`: **326 passed**, including 33 new bulk regression cases. Existing
  single-record DNS tests remain unchanged.
- `cd frontend && npm run build && npm run lint`: both passed. Lint retains the existing
  `App.tsx:33` hook-dependency warning; Vite retains its bundle-size and empty React-chunk
  warnings. Regenerated `app/static/ui/` is saved in the working tree, ready to commit.
- A temporary Node/TypeScript harness exercised the actual page handlers with mocked React
  hooks and API responses: page/search selection clearing, one confirmation, stored adlist
  addresses, unchanged-state summaries, sequential instance requests, continued processing
  after an instance error, DNS 225-record chunking, and stopping the remaining chunks on a
  failed 425-record instance while continuing the next instance. All five scenarios passed.
  This verifies handler behavior; it is not a browser or live Pi-hole test.
- **Live acceptance was pending at implementation time** because the criterion named a
  `pihole1` entry that does not exist in this deployment. Codex attempted no live Pi-hole
  mutations. The criterion has since been corrected to name `lab-pihole`, and the user has run
  the first pass against it — see "Live verification — 2026-09-07" below.
- **Commit blocked by the execution environment.** `git add` failed with
  `Unable to create .git/index.lock: Read-only file system`. Nothing was staged or committed;
  the feature branch remains checked out and all implementation changes are in the working
  tree. Commit only the implementation files and this verification note, excluding `CLAUDE.md`.
  (Resolved: the architect staged and committed the implementation as `00f0c43`, excluding
  `CLAUDE.md`, after independently confirming `326 passed` and that `app/static/ui/index.html`
  references exactly the two rebuilt asset files on disk.)

## Live verification — 2026-09-07 (lab-pihole)

Run by the user through the real UI against `lab-pihole`.

**Passed:**
- Selecting multiple rows reveals the bulk action bar with its instance selector.
- Bulk **delete**, **disable** and **enable** all behave as expected.

**Second pass — 2026-09-07, all remaining checks passed:**
- Bulk-deleting a pair of hostnames sharing a single `dns.hosts` line leaves no orphaned
  sibling and no stray line — the case Revision 1's row-tracking fix exists for, and the
  only path in this feature that could corrupt DNS data. This was the highest-value
  outstanding check; unit tests covered it only against a fake client.
- The gravity-pending prompt appears exactly once after a bulk adlist change, and **not**
  on a repeat of an already-applied state.
- No transport-level hiccup analogous to plan 0008's CNAME-write restart discovery — as
  Approach predicted, since adlists and domains use their own REST resources and `dns.hosts`
  does not exhibit the `dns.cnameRecords` restart behavior.

**The live-verification acceptance criterion is met.**

**Still unexercised (not required by the criterion, recorded for honesty):** batches large
enough to trigger step 11's 200-record DNS chunking. The criterion deliberately asks for
10-20 records rather than the ceiling, so this is a gap against the *chunking* path
specifically. With antd's select-all scoped to the current page (25 rows), reaching 200
requires hostnames fanning out to many addresses each. Backlogged under the frontend
test-runner note in `docs/potential-features.md` §5.
