# 0008 — CNAME records on the Local DNS page

Status: shipped             <!-- draft | approved | in-progress | in-review | shipped -->
Revision 1: 2026-09-06 — Codex stopped at the plan-review gate with two correct findings, both fixed here before any code was written. (a) Step 2 named `_validate_exact_domain`, which lives in `app/routes/domains.py`, outside this plan's Files list; the `_HOSTNAME_RE` hoist is now scoped to `api.py` alone and `domains.py` is explicitly left untouched. (b) Step 7 reused `_partial_dns_failure` as-is, but it hardcodes `"dns.hosts"` as its audit key, which would have misfiled a CNAME rollback failure; it now takes a defaulted `setting_key` parameter. Design intent and Scope are unchanged.
Revision 2: 2026-09-06 — Manual live-instance testing against pihole1 (after Codex's implementation of Revision 1 passed all automated checks) surfaced two real behavioral differences between `dns.cnameRecords` and `dns.hosts` that this plan's Approach wrongly assumed away. Both are described in full in Approach and fixed in Steps 2 and 7 below. (a) A `dns.cnameRecords` write triggers a brief FTL restart that `dns.hosts` writes do not — confirmed by scripting the same add/read/delete sequence directly against the Pi-hole client for both record types: `dns.hosts` never failed across repeated runs, `dns.cnameRecords` reliably produced `httpx.RemoteProtocolError` / `httpx.ConnectError` on the request immediately following a write, recovering on its own within a few seconds (single-retry recovery observed in the cleanup run). (b) Pi-hole enforces one target per alias at the FTL write layer for CNAMEs (no such constraint exists for `dns.hosts` lines), which the add-new-before-delete-old update ordering — copied from the hosts pattern — violates on every ordinary edit that keeps the same alias and changes only the target or TTL: the add-new step gets rejected with a live HTTP 400 because the old alias/target pair is still present. Reproduced directly against pihole1; all test artifacts (`cnamereprotest.lan`, `cnamereprotest2.lan`, `cnameupdatetest.lan`, `timingtest.lan`) were cleaned up and pihole1's `dns.cnameRecords` was confirmed empty again afterward. Scope is unchanged; Approach's core decision (item-level FTL endpoints, no full-array rewrite) stands — only the update ordering and read resilience are corrected.
Revision 3: 2026-09-06 — Codex stopped at the plan-review gate on Revision 2 with two more correct findings, both fixed here before any code was written. (a) `_cname_settings_tree`'s retry-exhaustion branch raised a bare `PiholeAPIError` from inside an `except` clause, which the function's own sibling `except Exception: raise _api_error(...)` cannot catch (it's not re-entering the `try`) — so exhaustion would have escaped every caller's `except HTTPException` and crashed the route with a 500 instead of populating `errors`. Fixed: the exhaustion branch now raises `_api_error(exc)` directly, matching `_settings_tree`'s own convention. (b) The update route's write sequence (delete-old, add-new, rollback-add-old) was still three bare, unretried writes chained back-to-back — exactly the shape most exposed to Finding 2(a)'s restart window, since each write is "the next request" right after the previous one. A transport blip on the *second* write there would trigger the scary `_partial_dns_failure` manual-intervention path on what might just be a transient reconnect. Fixed with new helpers `_cname_write_add` / `_cname_write_delete` (Step 2) that retry a write across `httpx.TransportError`, informed by an empirical check run before writing this revision: retrying an identical `add_dns_cname` after it already succeeded gets a clean `HTTP 400`, and retrying `delete_dns_cname` after it already succeeded gets a clean `HTTP 404` — both distinguishable, both confirmed live against pihole1, 2026-09-06. Step 7 now uses these helpers for all three writes. Scope and the Revision 2 design decisions (delete-before-add ordering, CNAME-scoped resilience only) are unchanged.
Revision 4: 2026-09-06 — Codex stopped at the plan-review gate a third time with one more correct finding in Revision 3's write helpers, fixed here before any code was written. `_cname_write_add` / `_cname_write_delete` treated *any* `PiholeAPIError` on a retry as confirmation the earlier attempt had landed — but `add_dns_cname` / `delete_dns_cname` raise that same exception type for every non-2xx status, not only the specific 400/404 the empirical check in Revision 3 actually observed. Concrete failure Codex identified: delete-old succeeds; add-new's first attempt hits a transport error before anything is sent; the retry gets a genuine, unrelated HTTP 500 (e.g. FTL still mid-restart) — the old helper would swallow that 500 as "success," and Step 7 would report a successful edit while the alias was actually left with no CNAME at all. Fixed by making the tolerance precise instead of broad: `PiholeAPIError` gains an optional `status_code` attribute (Step 1), populated by `add_dns_cname` / `delete_dns_cname`, and the write helpers now check for the *exact* confirmed code (400 for add, 404 for delete) on a retry — any other status, including 401/403/500, propagates as a genuine failure exactly as it did before Revision 3. No dual-session verification-read was needed; the fix is a precise status check on an exception type these two methods already raise. Scope, Revision 2's ordering, and Revision 3's transport-retry shape are otherwise unchanged.
Revision 5: 2026-09-06 — Codex stopped at the plan-review gate a fourth time, still inside `_cname_write_add`, with a finding one level deeper than Revision 4's: a `400` on a retry proves the alias is now claimed by *something*, not that it's claimed by *this app's own* intended value. Concrete race Codex constructed, using the plan's own confirmed one-target-per-alias constraint: the update deletes `a.lan,x.lan`; adding `a.lan,y.lan` hits a transport error before reaching Pi-hole; a second writer (another admin, another tab, a second `pihole-manager` instance — this app does not claim exclusive access to a Pi-hole's native admin API) adds `a.lan,z.lan` during the retry delay; the retry then gets a `400` because the alias is taken — by `z.lan`, not `y.lan`. Revision 4's status-code check alone would treat that `400` as success and the route would report and audit `a.lan,y.lan` while the instance actually holds `a.lan,z.lan`. Fixed: `_cname_write_add` now takes the owning `instance` as well as `client`, and on a retry's `400` it verifies the *exact* intended record is what's actually stored — via `_cname_settings_tree` (itself already transport-retry-safe) and a structural `_cname_entry` comparison, not a raw-string or status-code guess — before treating it as success; if verification shows a different value (or the read itself cannot be completed), the `400` propagates as the genuine failure it is, and Step 7's existing rollback path handles it exactly as it would any other add failure. `_cname_write_delete` needs no equivalent change: a `404` deleting an exact, already-known raw string is complete proof on its own that *that specific string* is no longer present, regardless of what caused it to be gone (our own earlier attempt landing, or anyone else's write) — there is no analogous "gone, but replaced by something else" ambiguity for a delete of a fully-specified value the way there is for an add racing against a shared alias key. Scope and every prior revision's other decisions are unchanged.
Branch: feature/0008-cname-records
Author: Claude (architect)
Date: 2026-09-06

## Problem

Pi-hole v6 stores local CNAME aliases in `dns.cnameRecords` alongside the A/AAAA entries in
`dns.hosts`. This app manages `dns.hosts` (design.md §5.2) but ignores `dns.cnameRecords`
entirely, so a CNAME that exists on pihole1 and not pihole2 is invisible to the
cross-instance drift view that is the whole point of the Local DNS page — and adding or
removing one still means logging into a Pi-hole admin UI directly. design.md §5.2 already
claims "local A/CNAME records" as in-scope; only the A/AAAA half was ever built.

## Scope

In:
- Read `dns.cnameRecords` from every instance and present a unified per-alias
  cross-instance table with the same consistent / missing / conflict states as
  `dns.hosts`.
- Add, edit, and delete a single CNAME record on one selected instance, each through
  the existing confirm-before-write step.
- "Copy here" to replicate a CNAME onto an instance that is missing it, matching the
  A/AAAA table's affordance.
- Optional per-record TTL (Pi-hole's third field), including it in the round-trip.
- Restructure `DNSPage` into a two-tab screen ("A/AAAA records", "CNAME records").
- Client-side search on the CNAME tab, matching the A/AAAA tab.

Out:
- Bulk / multi-select actions on either tab — that is plan 0010.
- Any change to `dns.hosts` behaviour, parsing, or its rollback paths. The A/AAAA table
  moves into its own component file unchanged.
- Cross-instance "apply to many instances at once" for CNAMEs. Like `dns.hosts`,
  add/edit/delete targets exactly one instance per operation.
- CNAME target reachability checks ("orphaned target" in §5.2) — still not built for
  A/AAAA either.

## Approach

`dns.cnameRecords` is a config array reached through the same generic FTL item endpoints
this app already uses for `dns.hosts`: `PUT /api/config/dns/cnameRecords/{value}` and
`DELETE /api/config/dns/cnameRecords/{value}`. That means no new outbound-request pattern
and no `patch_config` full-array rewrite — the existing "item-level operation, never
replace the whole array" rule (issue #6/#7 in `api.py`) carries over directly.

One structural simplification versus `dns.hosts`: a `dns.hosts` line can carry several
hostnames after one IP, which is why deletion has to rewrite the surviving line
(`_rewrite_without`) and why the rollback paths in `dns_record_delete` /
`dns_record_update` are as involved as they are. A `dns.cnameRecords` entry is
`<cname>,<target>[,<ttl>]` — exactly one alias per entry, no siblings. So CNAME delete is
a single `DELETE`, and CNAME update is add-new-then-delete-old with a single rollback
step. The plan deliberately does **not** reuse the hosts helpers; a parallel, simpler set
(`_parse_dns_cnames`, `_cname_entry`, `_cname_record`) is clearer than generalizing
`_parse_dns_hosts` over two dissimilar record shapes.

Rejected alternatives:

- **Fold CNAMEs into the existing `/api/dns/records` endpoints with a `kind` discriminator.**
  Rejected: the two record types have different value grammars, different validation
  (target is a hostname, not an IP), and different failure modes. A shared endpoint would
  be a union type that every caller has to narrow, and it would put the delicate hosts
  rollback logic on the same code path as the trivial CNAME one.
- **A separate "CNAMEs" nav page.** Rejected: design.md §5.2 groups A/AAAA and CNAME as one
  concern, and a sixth nav item for one small table splits a view the user reads together.
- **Both tables stacked on one un-tabbed page.** Rejected: two independent search boxes and
  two paginators on one scroll surface; tabs keep each table's tools next to its table.

The Pi-hole endpoint path and the `<cname>,<target>[,<ttl>]` value grammar follow the
documented FTL v6 config-item API but have **not** been round-tripped from this codebase.
Per the standing rule in `pihole_client.py`'s module docstring they are hypotheses until
confirmed; the Acceptance criteria require a live check against a real instance before
merge, the same gate plan 0007 used.

**Revision 2 — two behavioral differences the live check found, both load-bearing.**
The endpoint path and value grammar hypothesis above held up; two other assumptions,
carried over from the `dns.hosts` pattern without re-checking them for CNAMEs, did not:

1. **A `dns.cnameRecords` write briefly restarts something in FTL; a `dns.hosts` write
   does not.** The write itself always completes and returns success, but the very next
   request to that instance — a read or another write, ours or anyone else's — can land in
   a several-second window where FTL's HTTP listener drops the connection
   (`httpx.RemoteProtocolError: Server disconnected without sending a response`) or refuses
   it outright (`httpx.ConnectError: Connection refused`), then recovers on its own. This
   is why the add/delete flow "worked" (the record really was written) but the page's
   immediate refresh showed `pihole1 unavailable` / `Unable to reach Pi-hole instance` —
   the list GET that follows every mutation caught the tail of that window. The fix is a
   CNAME-scoped read retry (Step 2), not a change to `_settings_tree` itself, which
   `dns.hosts` and Settings also use and which never shows this symptom.
2. **A CNAME alias can only have one target at the FTL layer; a `dns.hosts` hostname has
   no such constraint.** The add-new-before-delete-old ordering Step 7 copied from
   `dns_record_update` assumes both the old and new value can exist simultaneously for the
   brief moment between the two writes — true for `dns.hosts` lines, false for
   `dns.cnameRecords`. Editing a CNAME's target or TTL while keeping the same alias (the
   ordinary edit case) tries to add the new alias/target pair while the old one for that
   same alias is still present, and FTL rejects it with a live HTTP 400. The fix is to
   reverse the order for CNAMEs specifically — delete old, then add new — with the rollback
   direction reversed to match (Step 7). This does not affect `dns_record_update`, which
   keeps its existing order because its underlying constraint is different.

Both were found by scripting the same operations plan 0007's live check used, directly
against `PiholeClient`, bypassing the API layer so the real Pi-hole response was visible
without this app's own error-sanitizing in the way. See Revision 2 above for the specific
reproduction evidence.

## Files

- `app/pihole_client.py` — add `add_dns_cname` / `delete_dns_cname`; extend the module
  docstring's endpoint inventory with the cnameRecords paths and their VERIFY status.
  Revision 4 adds an optional `status_code` attribute to `PiholeAPIError` (backward
  compatible — every other raise site is untouched) and has the two new methods populate it.
- `app/routes/api.py` — CNAME parsing/validation helpers, Pydantic models, and the four
  `/api/dns/cnames` routes; plus two contained changes to existing helpers (Revision 1):
  the `_HOSTNAME_RE` hoist out of `_dns_record`, and a defaulted `setting_key` parameter on
  `_partial_dns_failure`. Revision 2 adds `_cname_settings_tree` (a CNAME-scoped, retrying
  read — see Step 2) and reverses the update route's write order (Step 7); both are new
  CNAME-only code, not changes to `dns.hosts` or Settings logic. Revision 3 fixes
  `_cname_settings_tree`'s exhaustion branch (it raised a bare exception its own sibling
  `except` couldn't catch) and adds `_cname_write_add` / `_cname_write_delete`, used only in
  Step 7. Needs `import httpx` and `import time` added to the module's imports.
- `frontend/src/api.ts` — `CNAMERecord`, `CNAMERow`, `CNAMERecordInput`, `CNAMERecordUpdate`
  types and the four client methods.
- `frontend/src/pages/DNSPage.tsx` — reduced to the screen shell: heading plus an AntD
  `Tabs` hosting the two tab components.
- `frontend/src/pages/DNSHostsTab.tsx` — new; the current A/AAAA table, form, and search
  moved out of `DNSPage.tsx` essentially verbatim.
- `frontend/src/pages/DNSCnamesTab.tsx` — new; the CNAME table, add/edit modal, and search.
- `tests/test_api.py` — CNAME parsing, list-shape, add/update/delete, and validation tests;
  extend `FakePiholeClient` with `cnameRecords` state and the two new client methods.
- `tests/test_pihole_client.py` (Revision 4) — one test guarding `PiholeAPIError`'s
  backward-compatible `status_code` default.
- `docs/design.md` — §5.2 gains a CNAME bullet describing the tabbed view and the
  no-sibling deletion difference.
- `docs/potential-features.md` — move "CNAME records" out of §1 into the "Shipped since
  these notes were first written" list.
- `app/static/ui/` — rebuilt frontend bundle (invariant #8).

## Steps

1. **`app/pihole_client.py` — client methods.** Add, next to `add_dns_host` /
   `delete_dns_host`:

   ```python
   def add_dns_cname(self, record: str) -> None:
       """Append one dns.cnameRecords entry without replacing the full array."""
       path = f"/api/config/dns/cnameRecords/{quote(record, safe='')}"
       resp = self._client.put(path, headers=self._auth_headers())
       if resp.status_code not in (200, 201, 204):
           raise PiholeAPIError(
               f"PUT dns.cnameRecords entry failed with HTTP {resp.status_code}",
               status_code=resp.status_code,
           )

   def delete_dns_cname(self, record: str) -> None:
       """Remove one exact dns.cnameRecords entry without replacing the full array."""
       path = f"/api/config/dns/cnameRecords/{quote(record, safe='')}"
       resp = self._client.delete(path, headers=self._auth_headers())
       if resp.status_code not in (200, 204):
           raise PiholeAPIError(
               f"DELETE dns.cnameRecords entry failed with HTTP {resp.status_code}",
               status_code=resp.status_code,
           )
   ```

   Add to the module docstring, under the existing local-DNS notes:
   `PUT/DELETE /api/config/dns/cnameRecords/{value}  # VERIFY — value is "<cname>,<target>[,<ttl>]"`.

   **Revision 4 — `PiholeAPIError` gains an optional `status_code` attribute.** Currently:

   ```python
   class PiholeAPIError(RuntimeError):
       pass
   ```

   Change to:

   ```python
   class PiholeAPIError(RuntimeError):
       def __init__(self, message: str, *, status_code: int | None = None):
           super().__init__(message)
           self.status_code = status_code
   ```

   Every existing call site constructs `PiholeAPIError` with a single positional message and
   no `status_code`, so this is backward-compatible — `status_code` defaults to `None`, and
   no other raise site in `pihole_client.py` needs to change. Only `add_dns_cname` and
   `delete_dns_cname` (both new, both in this plan's Files) pass it, so the write helpers in
   Step 2 can check the *exact* HTTP status instead of trusting the exception type alone.

2. **`app/routes/api.py` — validation helpers.** Add below `_rewrite_without`:

   - `_HOSTNAME_RE` — hoist the hostname pattern currently inlined in `_dns_record` into a
     module-level `_HOSTNAME_RE = re.compile(r"(?=.{1,253}$)(?!-)[a-z0-9_-]+(?:\.(?!-)[a-z0-9_-]+)*", re.IGNORECASE)`
     **in `api.py` only**, and have `_dns_record` and the new `_cname_record` both use it.
     (Local tidy-up, no behaviour change; call it out in the handback.)
     `app/routes/domains.py`'s `_validate_exact_domain` carries a near-identical regex and
     **stays untouched** — it is outside this plan's Files list, and de-duplicating a regex
     across two route modules is a refactor this plan does not authorize. Revision 1: the
     original wording told the implementer to fold both into one constant, which would have
     forced an out-of-scope edit to `domains.py`.
   - `_cname_record(hostname: str, target: str, ttl: int | None) -> str` — lowercases and
     strips a trailing dot from both names, validates both against `_HOSTNAME_RE`, rejects
     `hostname == target` with 422 ("A CNAME cannot point at itself"), rejects a `target`
     that parses as an IP address with 422 ("A CNAME target must be a hostname, not an IP
     address"), and requires `0 <= ttl <= 604800` when `ttl` is not None. Returns
     `f"{hostname},{target}"` or `f"{hostname},{target},{ttl}"`.
   - `_cname_entry(raw: str) -> tuple[str, str, int | None] | None` — splits on `,`, strips
     each field, returns `None` for anything that is not 2 or 3 non-empty fields or whose
     third field is not a non-negative integer. Normalizes both names lowercase,
     trailing-dot-stripped.
   - `_parse_dns_cnames(tree) -> list[dict[str, Any]]` — mirrors `_parse_dns_hosts`: for
     each `get_setting(tree, "dns.cnameRecords") or []`, skip unparseable entries, emit
     `{"hostname", "target", "ttl", "raw"}` where `raw` is the exact stored string.
   - `_cname_settings_tree(instance: Instance) -> dict[str, Any]` (Revision 2) — reads the
     config tree for a CNAME route, retrying up to 3 times (4 attempts total, 1.5s apart)
     when the read itself raises `httpx.TransportError` (`ConnectError`,
     `RemoteProtocolError`, `ReadTimeout`, etc.) — the symptom confirmed live for the brief
     restart a `dns.cnameRecords` write triggers. A non-transport failure (missing
     credential, a real `PiholeAPIError`, an already-built `HTTPException`) is not retried
     and propagates immediately, so a genuinely unreachable instance or bad credential still
     fails fast:

     ```python
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
     ```

     (Revision 3: exhaustion raises `_api_error(exc)` — the same conversion `_settings_tree`
     applies — rather than a bare `PiholeAPIError`. A bare `PiholeAPIError` raised from
     inside the `except httpx.TransportError` clause is not caught by the function's own
     sibling `except Exception`, since raising from within one except clause does not
     re-enter the `try`; it would have escaped as an unhandled exception through every
     caller's `except HTTPException`, crashing the route with a 500 instead of the intended
     `errors: {...}` entry. `_api_error` on a plain `httpx.TransportError` — not a
     `PiholeAPIError` — correctly falls through to its generic branch, producing
     `HTTPException(503, "Unable to reach Pi-hole instance")`.)

     Used by all four CNAME routes in place of `_settings_tree` for their config-tree
     reads. `_settings_tree` itself is untouched — `dns.hosts` and Settings never show this
     restart symptom, and retrying their reads would only add needless latency on a real
     failure. Requires `import httpx` and `import time` added to `api.py`'s imports.

   - `_cname_write_add(instance: Instance, client: PiholeClient, record: str) -> None` and
     `_cname_write_delete(instance: Instance, client: PiholeClient, record: str) -> None`
     (Revision 3, corrected in Revisions 4 and 5) — retry a single CNAME write across the
     same transport-error window, for use where two CNAME writes happen back-to-back in one
     session (Step 7) and each is "the next request" right after the other — the shape most
     exposed to Finding 2(a)'s restart window. Confirmed live against pihole1, 2026-09-06:
     retrying an `add_dns_cname` call whose value already landed gets a clean `HTTP 400`;
     retrying a `delete_dns_cname` call whose value is already gone gets a clean `HTTP 404`.

     **Revision 4 — tolerance narrowed to the exact confirmed status code.** The original
     Revision 3 code treated *any* `PiholeAPIError` on a retry as confirmation — but
     `add_dns_cname` / `delete_dns_cname` raise that same type for every non-2xx status, not
     only 400/404. Codex found the concrete failure: a transport error on attempt 0, then an
     unrelated `500` on the retry (e.g. FTL still mid-restart) — the broad check would have
     swallowed that as "success" and reported a completed edit while the record was actually
     never written. Fixed using the `status_code` attribute from Step 1: only the specific
     code each method is confirmed to return for "this already happened" is tolerated, and
     only on a retry (`attempt > 0`, meaning a transport error already fired for this same
     call) — a matching status on attempt 0 is a real, first-attempt failure (e.g. the
     app-level duplicate-alias check in Step 5 didn't catch a race, or the record already
     vanished) and must still propagate; so must any *other* status at any attempt.

     **Revision 5 — a retry's `400` is verified against real state before being trusted.**
     Even the exact-status check from Revision 4 isn't enough for *add*: a `400` on retry
     proves the alias is now claimed by something, not that it's claimed by the value this
     call intended. Codex's concrete race: this app deletes `a.lan,x.lan`, then a transport
     error hits while adding `a.lan,y.lan`; a second writer (another admin, another browser
     tab, a second instance of this app — nothing gives this app exclusive access to a
     Pi-hole's native admin API) adds `a.lan,z.lan` for that alias during the retry delay;
     the retry then gets a `400` because the alias is taken — by `z.lan`, not `y.lan`.
     Trusting the status code alone would report and audit `a.lan,y.lan` as the new state
     while the instance actually holds `a.lan,z.lan`. Fixed: on a retry's `400`, read the
     current state via `_cname_settings_tree` (itself already transport-retry-safe) and
     compare the *parsed* entries (`_cname_entry`, not raw-string equality, since Pi-hole may
     reformat) — only if the exact intended `(hostname, target, ttl)` is present is this
     treated as success; otherwise the `400` propagates as the real failure it is. `delete`
     needs no equivalent change: a `404` deleting an exact, already-known raw string is
     complete proof on its own that *that specific string* is gone, regardless of what caused
     it — there's no "gone, but replaced by something else" ambiguity for a fully-specified
     delete the way there is for an add racing on a shared alias key:

     ```python
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
     ```

     If `_cname_settings_tree` inside the verification branch itself raises (read-retry
     exhausted), that exception propagates out of `_cname_write_add` in place of the original
     `400` — correct and conservative: an add whose outcome cannot be confirmed is treated as
     unconfirmed, not as success.

     Used only in Step 7 (the update route), replacing the bare
     `client.add_dns_cname(...)` / `client.delete_dns_cname(...)` calls there. `POST` and
     `DELETE` (Steps 5 and 6) each make one CNAME write per request with nothing chained
     immediately after it in the same session, and no transport error has been observed on a
     "cold" write in testing — only ever on the request that follows one — so they are left
     as plain single calls rather than adding unneeded retry surface. An uncaught exception
     from either helper (transport-exhaustion on the final attempt, or a genuine
     first-attempt `PiholeAPIError`) propagates to Step 7's existing try/except exactly as a
     bare client call would have, so the rollback and `_partial_dns_failure` logic there is
     otherwise unchanged.

3. **`app/routes/api.py` — Pydantic models.** Beside `DNSRecordInput` / `DNSRecordUpdate`:

   ```python
   class CNAMERecordInput(BaseModel):
       instance: str = Field(min_length=1, max_length=100)
       hostname: str = Field(min_length=1, max_length=253)
       target: str = Field(min_length=1, max_length=253)
       ttl: int | None = Field(default=None, ge=0, le=604800)


   class CNAMERecordUpdate(CNAMERecordInput):
       old_hostname: str = Field(min_length=1, max_length=253)
       old_target: str = Field(min_length=1, max_length=253)
       old_ttl: int | None = Field(default=None, ge=0, le=604800)
   ```

4. **`GET /api/dns/cnames`.** Same shape and fan-out as `dns_records_list`: `_fanout` over
   `load_instances()`, each worker returning `(name, _parse_dns_cnames(_cname_settings_tree(instance)), None)`
   (Revision 2: `_cname_settings_tree`, not `_settings_tree` — see Step 2) or
   `(name, None, detail)` on `HTTPException`. Group by `hostname` into
   `{hostname: {instance: [record, ...]}}`, where each public record is
   `{"hostname", "target", "ttl", "raw"}`. State per row: `conflict` if the set of
   `(target, ttl)` pairs across instances has more than one member, else `missing` if the
   present instances are not the full reachable set, else `consistent`. Return
   `{"instance_names": [...], "records": [...], "errors": {...}}`.

5. **`POST /api/dns/cnames` (201).** `_instance(payload.instance)`; build
   `cname_record = _cname_record(payload.hostname, payload.target, payload.ttl)`; read via
   `_cname_settings_tree` (Revision 2); 409 "That CNAME already exists on this instance" if any parsed row's
   `hostname` equals the normalized hostname (alias uniqueness is per-name, not per
   name+target — two CNAMEs for one alias is not a valid DNS state); then
   `with _client(instance) as client: client.add_dns_cname(cname_record)` wrapped in the
   existing `except HTTPException: raise / except Exception: raise _api_error(exc)` pair.
   `record(payload.instance, "dns_cname_add", {}, {"dns.cnameRecords": cname_record})`.
   Return `{"instance": ..., "record": cname_record}`.

6. **`DELETE /api/dns/cnames`.** Validate the payload through `_cname_record`, re-read the
   tree via `_cname_settings_tree` (Revision 2), and find the owning row by normalized
   `hostname` **and** normalized `target` (a
   structural match, not string equality against the reconstructed value — same rule as
   issue #6 for hosts, so a stored `Alias.LAN,Server.LAN` still matches). 404 "CNAME no
   longer exists on this instance" if absent. Delete `owner["raw"]` — the stored string,
   not the reconstructed one. No sibling rewrite and therefore no rollback branch.
   `record(instance, "dns_cname_delete", {"dns.cnameRecords": owner["raw"]}, {})`.
   Return `{"deleted": owner["raw"]}`.

7. **`PATCH /api/dns/cnames`.** Build `old_record` and `new_record` through `_cname_record`;
   return `{"changed": False, "record": new_record}` unchanged if equal. Re-read the tree
   via `_cname_settings_tree` (Revision 2); 409 "The original CNAME changed; refresh and try
   again" if the old row is gone; 409 "The replacement CNAME already exists" if a different
   row already owns the new hostname.

   **Revision 2 — write order reversed: delete old, then add new.** Originally specified
   as add-new-then-delete-old, mirroring `dns_record_update`. Confirmed live against
   pihole1 that this order is wrong for CNAMEs: FTL enforces one target per alias, so
   whenever an edit keeps the same alias (changing only the target or TTL — the ordinary
   case), adding the new value while the old value for that alias still exists is rejected
   with a live HTTP 400. `dns.hosts` lines carry no such constraint, which is why
   `dns_record_update` can safely add first.

   **Revision 3 — both writes go through the retrying helpers, not the bare client
   methods.** Two back-to-back writes in one session is exactly the shape Finding 2(a)
   (Revision 2's restart window) hits hardest — each write is "the next request" right
   after the previous one. Using `_cname_write_delete` / `_cname_write_add` (Step 2) here
   means a transient reconnect on the second write no longer trips the rollback/
   `_partial_dns_failure` path on its own; only a genuine failure — transport-retry
   exhaustion, or a `PiholeAPIError` on the very first attempt — does.

   In one client session: `_cname_write_delete(instance, client, old_owner["raw"])`; on
   success `_cname_write_add(instance, client, new_record)`; if that raises, attempt
   `_cname_write_add(instance, client, old_owner["raw"])` to roll the deletion back and
   re-raise the original add failure, and if the rollback add itself raises call
   `_partial_dns_failure(instance, "dns_cname_update_orphaned", removed=[old_owner["raw"]], added=[], setting_key="dns.cnameRecords")`
   so the audit log and the 502 both name the exact lost alias. Note the roles of `removed`
   and `added` are swapped from the hosts version to match: here it is the *old* record
   that ends up lost if rollback fails, not a stray *new* one.
   `record(instance, "dns_cname_update", {"dns.cnameRecords": old_owner["raw"]}, {"dns.cnameRecords": new_record})`.

   **Revision 1 — `_partial_dns_failure` gains a `setting_key` parameter.** The function
   currently hardcodes `"dns.hosts"` in its audit payload
   (`record(instance, event, {"dns.hosts": removed}, {"dns.hosts": added})`), so reusing it
   unchanged would file a CNAME rollback failure under the wrong setting and make the audit
   log lie about what was lost. Add a keyword-only `setting_key: str = "dns.hosts"` and use
   it as the audit dict key. The default keeps all three existing `dns.hosts` call sites
   byte-for-byte identical — this is the only permitted change to existing non-CNAME logic
   besides the `_HOSTNAME_RE` hoist in Step 2, and it must not alter the human-readable
   detail message, which is already worded generically ("re-add it in the Pi-hole Local DNS
   settings") and reads correctly for both record types.

8. **`frontend/src/api.ts` — types and methods.**

   ```ts
   export interface CNAMERecord {
     hostname: string
     target: string
     ttl: number | null
     raw: string
   }

   export interface CNAMERow {
     hostname: string
     instances: Record<string, CNAMERecord[]>
     state: 'consistent' | 'missing' | 'conflict'
   }

   export interface CNAMERecordInput {
     instance: string
     hostname: string
     target: string
     ttl?: number | null
   }
   ```

   plus, on the `api` object:

   ```ts
   listDNSCnames: () =>
     request<{ instance_names: string[]; records: CNAMERow[]; errors: Record<string, string> }>('/api/dns/cnames'),
   addDNSCname: (input: CNAMERecordInput) =>
     request<{ instance: string; record: string }>('/api/dns/cnames', { method: 'POST', body: JSON.stringify(input) }),
   deleteDNSCname: (input: CNAMERecordInput) =>
     request<{ deleted: string }>('/api/dns/cnames', { method: 'DELETE', body: JSON.stringify(input) }),
   updateDNSCname: (input: CNAMERecordInput & { old_hostname: string; old_target: string; old_ttl?: number | null }) =>
     request<{ changed: boolean; record: string }>('/api/dns/cnames', { method: 'PATCH', body: JSON.stringify(input) }),
   ```

9. **`frontend/src/pages/DNSHostsTab.tsx`.** Move the entire current body of `DNSPage.tsx`
   into a `DNSHostsTab` component, unchanged apart from the rename and dropping the
   `page-heading` block (which moves up to the shell). Export the `stateTag` map and
   nothing else new.

10. **`frontend/src/pages/DNSCnamesTab.tsx`.** Same structure as `DNSHostsTab`, adapted:
    columns are `Alias` (fixed left, `Typography.Text strong`), one column per instance
    rendering `<Typography.Text code>{target}</Typography.Text>` plus a TTL secondary label
    when `ttl != null`, with edit and `Popconfirm`-guarded delete buttons and a
    `Copy here` dashed button when the instance has no record and is not in `errors`; and a
    fixed-right `State` column. Add/edit modal fields: `instance` (`Select`, disabled while
    editing), `hostname` (`Input`, placeholder `alias.lan`), `target` (`Input`, placeholder
    `server.lan`), `ttl` (`InputNumber`, `min={0} max={604800}`, optional, helper text
    "Leave blank to use the Pi-hole default"). `save()` opens the same `modal.confirm`
    diff step the hosts tab uses, wording the change as `old.target → new.target`. Client-side
    `filterCNAMERows(rows, search)` matches alias, target, and instance name.

11. **`frontend/src/pages/DNSPage.tsx`.** Reduce to the heading (`Local DNS` title and
    description) plus `<Tabs items={[{ key: 'hosts', label: 'A/AAAA records', children: <DNSHostsTab /> }, { key: 'cnames', label: 'CNAME records', children: <DNSCnamesTab /> }]} />`,
    with `destroyOnHidden` left off so a tab keeps its loaded rows when the user switches
    back. Each tab component owns its own loading, error alerts, refresh button, and add
    button; `App.tsx` is untouched.

12. **Rebuild the bundle.** `cd frontend && npm run build && npm run lint`, commit
    `app/static/ui/`.

13. **Docs.** design.md §5.2: add a bullet — "CNAMEs (`dns.cnameRecords`) share the Local
    DNS screen as a second tab, with the same missing/conflict states. One alias per entry,
    so unlike `dns.hosts` a delete never has to rewrite a surviving line." Move the CNAME
    item in `docs/potential-features.md` from §1 to the shipped list, citing plan 0008.

## Contracts

### `GET /api/dns/cnames`

Response (backend, plain dicts built in the route — matching `dns_records_list`, which is
also untyped):

```json
{
  "instance_names": ["pihole1", "pihole2"],
  "records": [
    {
      "hostname": "alias.lan",
      "instances": {
        "pihole1": [{ "hostname": "alias.lan", "target": "server.lan", "ttl": 300, "raw": "alias.lan,server.lan,300" }]
      },
      "state": "missing"
    }
  ],
  "errors": { "pihole2": "Unable to reach Pi-hole instance" }
}
```

`api.ts` mirror: `listDNSCnames(): Promise<{ instance_names: string[]; records: CNAMERow[]; errors: Record<string, string> }>`.

### `POST /api/dns/cnames` → 201

Request (`CNAMERecordInput`): `{ "instance": str, "hostname": str, "target": str, "ttl": int | null }`
Response: `{ "instance": str, "record": str }` where `record` is the exact stored value.
Errors: 404 unknown instance; 409 alias already present; 422 invalid name / self-reference /
IP target / TTL out of range; 502/503 from `_api_error`.

### `DELETE /api/dns/cnames`

Request (`CNAMERecordInput`, same body). Response: `{ "deleted": str }`.
Errors: 404 record no longer present; 422 invalid input.

### `PATCH /api/dns/cnames`

Request (`CNAMERecordUpdate`): `CNAMERecordInput` plus `{ "old_hostname": str, "old_target": str, "old_ttl": int | null }`.
Response: `{ "changed": bool, "record": str }`.
Errors: 409 original changed / replacement exists; 502 partial-apply via `_partial_dns_failure`.

### Pi-hole client

`PUT /api/config/dns/cnameRecords/{urlquoted value}` → 200/201/204
`DELETE /api/config/dns/cnameRecords/{urlquoted value}` → 200/204
Value grammar: `<cname>,<target>` or `<cname>,<target>,<ttl>`. **Confirmed live against
pihole1 (Revision 2, 2026-09-06)** — path and grammar are correct. Also confirmed live: a
write to this key briefly restarts something in FTL (unlike `dns.hosts`), and FTL enforces
one target per alias — both are handled per Revision 2's changes to Steps 2 and 7.

### Config keys / DB

N/A — no new config key, no new stored state. `dns.cnameRecords` is read out of the
existing `GET /api/config` tree via `get_setting`; nothing is added to `MANAGED_FIELDS`
(these are item-level writes, not settings-form fields).

## Tests

All in `tests/test_api.py` except the one `tests/test_pihole_client.py` addition noted
below (Revision 4), extending `FakePiholeClient` with
`tree["dns"]["cnameRecords"] = ["alias.lan,server.lan"]`, class-level `added_cnames` /
`deleted_cnames` lists, `add_dns_cname` / `delete_dns_cname` recorders, and a reset of both
lists in `setup_api`.

- `test_cname_records_are_returned_in_unified_view` — two instances, one shared alias, one
  alias only on pihole1, one alias with differing targets; asserts the three rows come back
  `consistent` / `missing` / `conflict` respectively and that `instance_names` is in
  configured order.
- `test_cname_list_skips_unparseable_entries` — `dns.cnameRecords` containing `"garbage"`,
  `"a,b,c,d"`, `"alias.lan,server.lan,notanumber"` and one valid entry; asserts only the
  valid entry appears and the route does not raise.
- `test_cname_add_uses_item_level_operation` — asserts `added_cnames == ["alias2.lan,server.lan"]`
  and `patches == []` (never a full-array `patch_config`).
- `test_cname_add_with_ttl_round_trips` — `ttl=300` produces `"alias2.lan,server.lan,300"`.
- `test_cname_add_rejects_duplicate_alias` — adding a second target for an existing alias
  raises `HTTPException` 409.
- `test_cname_add_rejects_self_reference_and_ip_target` — parametrized; `hostname == target`
  and `target="192.168.1.10"` each raise 422.
- `test_cname_delete_matches_structurally` — stored `"Alias.LAN,Server.LAN"`, request
  `hostname="alias.lan", target="server.lan"`; asserts `deleted_cnames == ["Alias.LAN,Server.LAN"]`
  (the stored string, not the reconstructed one).
- `test_cname_delete_missing_returns_404`.
- `test_cname_update_removes_before_adding_new_record` (Revision 2 — order reversed from
  the original plan) — asserts ordering: `deleted_cnames` has the old value and
  `added_cnames` the new one, and that the old value was removed first. This is the case
  that reproduced live as an HTTP 400 under the original add-first ordering: same alias,
  changed target.
- `test_cname_update_same_alias_changes_target_succeeds` (Revision 2) — the concrete
  regression for the live finding: `old=("a.lan","x.lan",None)`, `new=("a.lan","y.lan",None)`
  — same hostname, different target. Must return `{"changed": True}`, not raise.
- `test_cname_update_rolls_back_old_record_when_add_fails` (Revision 2, replaces
  `test_cname_update_rolls_back_new_record_when_delete_fails` — roles reversed with the
  order) — a client subclass whose `add_dns_cname` raises `PiholeAPIError` on its first call
  for the new value (attempt 0, so `_cname_write_add` does not tolerate it — see the
  Revision 3 tests below for the tolerated case); asserts the old record is re-added
  (rollback) and a 502 is still raised (the edit failed even though consistency was
  restored).
- `test_cname_update_reports_partial_failure_when_rollback_fails` (Revision 2 — updated) —
  both the add-new and the rollback add-old raise `PiholeAPIError` on their first calls;
  asserts a 502 whose detail names the stray *old* record as lost (roles swapped from the
  original plan's wording, since delete now happens first).
- `test_cname_write_add_tolerates_duplicate_after_transport_retry` (Revision 3, status check
  narrowed in Revision 4) — a client whose `add_dns_cname` raises `httpx.RemoteProtocolError`
  on attempt 0 and `PiholeAPIError(status_code=400)` on attempt 1, with the fake's
  `dns.cnameRecords` state already containing the exact intended record (Revision 5:
  verification must find a match to tolerate the 400); asserts `_cname_write_add` returns
  normally rather than propagating it.
- `test_cname_write_add_does_not_trust_400_when_a_different_value_is_stored` (Revision 5) —
  Codex's exact race: `httpx.RemoteProtocolError` on attempt 0, `PiholeAPIError(status_code=400)`
  on attempt 1, but the fake's `dns.cnameRecords` holds a *different* target for the same
  alias (simulating a concurrent writer winning the alias in the retry gap). Asserts
  `_cname_write_add` propagates the 400 rather than treating it as success — the whole point
  of this revision.
- `test_cname_write_add_propagates_when_verification_read_fails` (Revision 5) — the retry
  gets a `400`, and the verification's `_cname_settings_tree` call itself raises (transport
  errors on every attempt); asserts the original 400 — or the verification's own exception —
  propagates rather than being swallowed. An add whose outcome cannot be confirmed must never
  resolve to success.
- `test_cname_write_add_first_attempt_duplicate_still_raises` (Revision 3) — a client whose
  `add_dns_cname` raises `PiholeAPIError(status_code=400)` on attempt 0 with no preceding
  transport error; asserts `_cname_write_add` propagates it — a first-attempt failure is
  real, not a retry-confirmation.
- `test_cname_write_add_unrelated_error_after_transport_retry_still_raises` (Revision 4) —
  the concrete regression Codex found: `httpx.RemoteProtocolError` on attempt 0, then
  `PiholeAPIError(status_code=500)` on attempt 1; asserts `_cname_write_add` propagates the
  500 rather than treating it as confirmation — only a matching 400 on a retry is tolerated,
  not any error. Parametrize with 401 and 403 alongside 500 to cover the exact codes Codex
  named.
- `test_cname_write_delete_tolerates_not_found_after_transport_retry` (Revision 3, status
  check narrowed in Revision 4) — mirrors the add case: `httpx.RemoteProtocolError` then
  `PiholeAPIError(status_code=404)`; asserts `_cname_write_delete` returns normally.
- `test_cname_write_delete_unrelated_error_after_transport_retry_still_raises`
  (Revision 4) — mirrors the add case: a retry getting `PiholeAPIError(status_code=500)`
  (not 404) still propagates.
- `test_cname_update_survives_transport_blip_on_second_write` (Revision 3) — the concrete
  regression: a client whose `add_dns_cname` (the second write in the update sequence)
  raises `httpx.RemoteProtocolError` once then succeeds cleanly (a 2xx on the retry, not an
  error of any kind); asserts the update completes with `{"changed": True}` and —
  critically — that `_partial_dns_failure` / `record` was never called with an `_orphaned`
  event. Without Revision 3 this transient blip would have looked identical to a genuine add
  failure and triggered the rollback path.
- `test_pihole_api_error_status_code_defaults_to_none` (Revision 4) —
  `tests/test_pihole_client.py`: constructing `PiholeAPIError("message")` with no
  `status_code` keyword still works and `.status_code is None`, guarding the backward-
  compatible default every other raise site in `pihole_client.py` relies on.
- `test_cname_update_noop_returns_unchanged` — identical old/new returns `{"changed": False}`
  with no client calls.
- `test_partial_failure_audits_the_right_setting_key` (Revision 1) — asserts a CNAME rollback
  failure records under `"dns.cnameRecords"` and a hosts rollback failure still records under
  `"dns.hosts"`, capturing `record` via monkeypatch. Guards the shared-helper change from
  silently mislabelling either record type.
- `test_cname_list_retries_transport_error_and_recovers` (Revision 2) — a client whose
  `get_config` raises `httpx.RemoteProtocolError` once then succeeds; asserts the instance
  comes back in `records`, not `errors`, and that a retry actually happened (call count > 1).
- `test_cname_list_gives_up_after_repeated_transport_error` (Revision 2) — a client whose
  `get_config` always raises `httpx.TransportError`; asserts the instance lands in `errors`
  with a readable message after the retry budget is exhausted, not an unhandled 500.
- `test_cname_add_lookup_retries_transport_error` (Revision 2) — the pre-add duplicate-check
  read tolerates one transient transport error before succeeding; asserts the add still
  completes.
- `test_cname_settings_tree_does_not_retry_non_transport_errors` (Revision 2) — a client
  raising `PiholeAPIError` (a real Pi-hole-reported error, not a dropped connection) is not
  retried; asserts exactly one call and that the original error surfaces immediately.

## Invariants at risk

1. **One instance mutated at a time.** Every CNAME mutation route takes a single
   `instance` in its body, resolves exactly one `Instance`, and opens one `PiholeClient`.
   There is no multi-instance CNAME endpoint in this plan (explicitly Out of scope), and
   the read path is the only fan-out — reads are unaffected by this invariant.
2. **Diff before write.** All three mutations re-read the current stored array (via
   `_cname_settings_tree`, Revision 2) and compute the change against it before writing; the
   PATCH route 409s if the record it was told to change is no longer there. The frontend's
   `modal.confirm` step shows the old → new value before firing, matching the hosts tab.
3. **Gravity gated behind real changes.** Not touched — CNAMEs are local DNS, not adlists.
   No CNAME route calls `mark_gravity_pending` or `update_gravity`.
4. **No background scheduler.** Nothing added runs on a timer; both tabs load on mount and
   on an explicit Refresh click, same as today.
5. **FastAPI owns credentials.** The new routes go through `_client(instance)`, which reads
   from `CredentialStore`. Nothing in the new `api.ts` types or either tab component
   carries a Pi-hole URL, password, or session id — the browser sees only instance
   *names*.
6. **Outbound requests via `url_policy`.** No new outbound URL: `add_dns_cname` /
   `delete_dns_cname` are paths on the already-validated `PiholeClient` base URL, whose
   destination went through `resolve_instance_destination` at construction. No user-supplied
   URL is fetched, so `screen_adlist_url` is not involved.
7. **Every route behind auth middleware.** The four routes are registered on the existing
   `app/routes/api.py` `APIRouter(prefix="/api")`, which is already mounted behind the
   middleware; no route-level exemption is added.
8. **Compiled frontend committed.** Step 12 rebuilds and commits `app/static/ui/`.
9. **`main` is production.** Work lands on `feature/0008-cname-records`.

Additional note on **request integrity / hardening**: `DELETE` and `PATCH` with JSON bodies
are already the established pattern here (`/api/dns/records`), so the same-origin and Fetch
Metadata checks in `http_hardening.py` cover them without change, and the body-size limit
applies unchanged.

## Acceptance criteria

- [x] `GET /api/dns/cnames` returns one row per alias with `consistent` / `missing` /
      `conflict` states, and an unreachable instance appears in `errors` without failing
      the request.
- [x] Adding a CNAME on one instance writes exactly one `PUT /api/config/dns/cnameRecords/...`
      and issues no `PATCH /api/config`.
- [x] Editing a CNAME while keeping the same alias (changing only target or TTL) succeeds —
      the concrete case Revision 2 fixed, previously rejected by Pi-hole with a live 400.
      The route deletes the old value before adding the new one (Revision 2 — reversed from
      the plan's original order), and a failure of the add rolls the old value back.
- [x] Adding, deleting, or editing a CNAME does not surface a false "unavailable" error on
      the page's own subsequent refresh — `_cname_settings_tree`'s retry (Revision 2) rides
      out the brief restart window a `dns.cnameRecords` write triggers.
- [x] Deleting a CNAME removes the exact stored string, including when its stored casing
      differs from the submitted casing.
- [x] A self-referencing CNAME, an IP-address target, a TTL above 604800, and a duplicate
      alias are each rejected with a 4xx and a readable message.
- [x] The Local DNS screen shows two tabs; the A/AAAA tab behaves exactly as before
      (search, add, edit, delete, Copy here, missing/conflict tags all unchanged).
- [x] The CNAME tab supports search, add, edit, delete, and Copy here, and every mutation
      opens the confirm step before firing.
- [x] The update route's writes tolerate a transport blip on the second write (Revision 3)
      without invoking `_partial_dns_failure` — only genuine failures (retry exhaustion, or
      a first-attempt `PiholeAPIError`) reach the rollback path.
- [x] **Live verification against a real instance** (the gate plan 0007 set) — **done,
      2026-09-06.** Endpoint path and value grammar confirmed correct directly against
      pihole1. Live testing surfaced three real behavioral differences from `dns.hosts` —
      the restart-on-write window, the one-target-per-alias constraint, and (via Codex's
      plan-review gate, not live testing) two further gaps in the first two fix attempts —
      all fixed through Revisions 2–5. After Revisions 2–5 were implemented (commit
      `69e21ff`) and the dev server restarted onto that code, re-ran the full UI flow on
      pihole1 through the actual browser: add, edit-same-alias (the case that used to 400),
      and delete, each followed by `dig` confirmation and a check for the false
      "unavailable" error the original bug report described. User confirmed: "all local
      testing works."
- [x] `./.venv/bin/pytest` green — 266 passed.
- [x] `npm run build` + `npm run lint` clean, `app/static/ui/` committed — rebuild after the
      Revision 2–5 changes produced no content diff (backend-only change), lint shows only
      the pre-existing, unrelated dependency warning in untouched `App.tsx`.
- [x] design.md §5.2 and docs/potential-features.md updated (committed in `0ac29ed`).
