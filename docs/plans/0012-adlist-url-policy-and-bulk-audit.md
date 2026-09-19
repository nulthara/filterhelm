# 0012 — Adlist URL policy, bulk validator relaxation, and bulk audit ordering

Status: shipped              <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0012-adlist-url-policy-and-bulk-audit
Author: Claude (Architect)
Date: 2026-09-08

> **Revision 1 (2026-09-08)** — Codex stopped at the handoff gate with two correct
> objections against revision 0, both verified before this rewrite:
> (a) settling the result before `record()` does not stop a raising `record()` from hitting
> each bulk route's outer `except Exception` handler, which re-raises through `_api_error`
> and discards every per-item result — so revision 0's own acceptance criterion was
> unreachable, and `dns_cnames_bulk` from plan 0011 has the same hole;
> (b) guarding only `parsed.port` in `_adlist_key()` is not enough, because `urlsplit`
> itself raises on `"http://[broken/list"`, and mapping an out-of-range port to `None`
> makes `http://lists.example:99999/l` collide with `http://lists.example/l` and match the
> wrong record. Steps 3–6 and the audit-failure containment in Approach are rewritten
> accordingly; the URL-policy half of the plan is unchanged.
>
> **Revision 2 (2026-09-08)** — Codex reviewed the whole of revision 1 and returned three
> test-contract defects, all verified: (a) `record_best_effort` living in `app/audit.py`
> would call `app.audit.record`, so the 21 existing tests that patch `<module>.record` stop
> intercepting — including `test_domain_bulk_enable_writes_only_changed_records`, which
> revision 1 explicitly promised would pass unmodified; (b)
> `test_cname_bulk_delete_does_not_mislabel_when_audit_fails` (`tests/test_api.py:1811`)
> asserts the HTTP 503 this plan deliberately removes, so it must be rewritten, not
> preserved; (c) `test_adlist_bulk_marks_gravity_pending_only_when_something_changed`
> (`tests/test_api.py:1534`) asserts exactly one `mark_gravity_pending` call for a
> two-item batch, which revision 1's per-item marking would have broken. Fixes: the
> containment helper becomes a module-local `_audit_landed_write` in each route module so
> the existing patch seam still works, gravity is marked once per batch behind a flag, and
> the one test whose behaviour genuinely changes is named as a rewrite. Codex reported no
> further implementation-design defects.
>
> **Revision 3 (2026-09-08)** — Codex's third full-plan review found three internal
> inconsistencies in revision 2, all in the plan's own bookkeeping rather than its design:
> (a) the Tests section rewrites five `test_url_policy.py` adlist tests and replaces two
> `test_api.py` tests, then claims "exactly one existing test is rewritten" — a flat
> contradiction, since the single-argument `screen_adlist_url` signature makes the old
> `allowlist`/`resolve`/`resolver` call sites impossible to keep; (b) the logging test was
> specified against the `app.audit` logger, but step 3b logs through `app.routes.api` and
> `app.routes.domains`, which do not propagate to `app.audit`; (c) the docs step counted the
> `potential-features.md` §6 entries instead of just naming the section. Revision 3 replaces
> the count with a complete, explicit list of every existing test that changes, retargets the
> logging test, and tells step 9 to clear §6 entirely. Codex again reported no
> implementation-design defects, and confirmed the module-local audit helpers and the
> once-per-batch gravity flag are consistent with the existing test seams.
>
> On (c) Codex said §6 holds "four entries"; it holds three top-level bullets, the first of
> which carries a nested "Amplified by plan 0010's bulk route" note. The ambiguity it points
> at is real even though the count is not, and naming the section removes it either way.

## Problem

`AdlistAddInput.validate_address` runs `screen_adlist_url()`, which resolves the block-list
host and screens every answer through `_screen_addresses()` — the same screen used for
Pi-hole *instance* URLs, which requires public addresses to appear in
`PIHOLE_MANAGER_PUBLIC_ALLOWLIST`. That allowlist exists to stop this app from being
pointed at an arbitrary public host, but **this app never fetches an adlist URL**: only the
target Pi-hole does, on its own gravity run. The result is that no ordinary public block
list can be added at all — `https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/tif.txt`
is refused with `Public instance address … is not allowlisted`, which is the app's core
feature failing closed for no security benefit. Plan 0010's bulk route amplified it: because
`AdlistBulkInput.validate_addresses` screens *every* submitted address, one already-stored
list the screen dislikes (a `file://` entry, an embedded-credentials URL, a fragment) now
422s the **entire batch** — even though those routes never fetch the submitted string and
always write `item.address` read back from the instance. And the user never sees why,
because `api.ts` `request()` stringifies FastAPI's array-shaped 422 `detail` to
`[object Object]`. Separately, all three remaining bulk loops call `record()` between the
Pi-hole write and `result["changed"] = True` inside the same `try`, so a failing audit write
reports a landed mutation as **failed** — and for adlists also leaves gravity unflagged,
so the user is never prompted to run it.

## Scope

In:
- Rewrite `screen_adlist_url()` in `app/url_policy.py` as a syntax-and-canonicalization
  screen that never performs DNS resolution and never consults the public allowlist. A
  literal-IP host is still rejected when it is loopback / link-local / metadata / multicast /
  unspecified / reserved.
- Drop the `screen_adlist_url` validators from `AdlistUpdateInput`, `AdlistDeleteInput`, and
  `AdlistBulkInput` — those three identify records Pi-hole already holds, they are not
  fetch targets.
- Harden `_adlist_key()` so it is total for any string Pi-hole may return (it loses the
  validator that previously guaranteed the address parsed).
- Add a module-local `_audit_landed_write()` to `app/routes/api.py` and
  `app/routes/domains.py` and use it in all **four** bulk loops
  (`adlist_bulk`, `dns_records_bulk`, `dns_cnames_bulk`, `domain_bulk`): settle
  `success`/`changed` (and, for adlists, `mark_gravity_pending`) from the write's own
  outcome, then audit through a call that cannot raise. `dns_cnames_bulk` is included
  because plan 0011's fix addressed the ordering but not the propagation.
- Fix `[object Object]`: make `api.ts` `request()` render FastAPI's array-shaped `detail`.
- Update `README.md`, `docs/design.md`, and `docs/potential-features.md` to match the new
  policy, clearing `potential-features.md` §6 entirely into its shipped list.

Out:
- Any change to instance-URL policy. `normalize_instance_url`, `resolve_instance_destination`,
  `validate_instance_destination`, `_screen_addresses`, and the
  `PIHOLE_MANAGER_PUBLIC_ALLOWLIST` behaviour for instances are untouched.
- Any new env knob to restore strict adlist screening. If it is ever wanted, it gets its
  own plan.
- Any UI change beyond the `request()` helper — no new components, props, or screens.
- Surfacing an audit-write failure in the UI. That needs a new field on `BulkResponse` plus
  matching changes in `api.ts` and all four pages, and the pages only render `error` for
  rows where `success` is false (`AdlistsPage.tsx:221`), so a per-item string would be
  dropped silently. A failed `audit.log` write is an ops condition (permissions, disk); it
  goes to the server log. If it should reach the UI, that is its own plan.
- The single-record routes (`adlist_add`, `adlist_update`, `adlist_delete`, the domain and
  DNS equivalents). They share the raising-`record()` shape, but the reported defect and its
  gravity consequence are specific to the bulk loops; converting them changes error
  semantics on paths this bug report never covered.
- The remaining `docs/potential-features.md` backlog items (stats dashboard, etc.).

## Approach

**Split the two policies instead of loosening one.** An instance URL is a destination *this
process connects to*, so it keeps the full resolve-and-allowlist screen. A block-list URL is
a *string handed to Pi-hole to store and fetch itself*; the manager never opens it. Treating
them as the same kind of destination is the root error. So `screen_adlist_url` keeps its
syntax rules (http/https only, no embedded credentials, no control characters or backslash,
no fragment, no surrounding whitespace) and its canonicalization — which `adlist_add` depends
on, since the canonical string is what gets written to Pi-hole — and loses the DNS lookup and
the allowlist consult entirely.

**No DNS resolution, rather than resolution-minus-the-allowlist.** A resolve-time check here
is TOCTOU-meaningless (Pi-hole re-resolves independently at gravity time, from a different
host) *and* is a live failure mode: a list whose host is momentarily unresolvable from the
manager cannot be added even though Pi-hole would fetch it fine. What survives is the cheap,
honest part: if the URL names a literal IP, reject the obviously-wrong ones. A hostname is
accepted on syntax alone. Rejected alternative: keep resolution and drop only the allowlist
rule — it preserves a defence that cannot actually defend (the manager never connects) while
keeping the offline-host papercut.

**RFC1918 block lists stay legal.** `http://lists.lan/hosts` is a supported case today
(`test_adlist_url_allows_rfc1918_address`) and a self-hosted list on the LAN is a reasonable
thing to want. The new screen keeps it working, and now without a lookup.

**The three record-identifying inputs lose their validator outright rather than getting a
lenient one.** For update/delete/bulk the write always targets `item.address` read back from
the instance, and matching goes through `_adlist_key()`, which canonicalizes both sides
itself. A validator on those paths can only reject; it can never improve correctness. Making
it *lenient* would still leave a string Pi-hole holds that the app refuses to act on, which
is precisely the reported bulk failure. Removing it means `_adlist_key()` must not raise on a
weird stored address, so its `parsed.port` access gets a guard. Field-level length bounds
(`max_length=2048`, the `len(item) > 2048` check in `AdlistBulkInput`) stay — those are
request-size hygiene, not policy.

**Audit ordering, and containment.** Ordering alone is not enough. Plan 0011 established
the right sequence in `dns_cnames_bulk` — the per-item `try` wraps only the Pi-hole write,
then the result is settled and appended, then `record()` — but every bulk route wraps its
whole loop in `try: … except Exception as exc: raise _api_error(exc)`, so a raising
`record()` still aborts the response and throws away the per-item results that were just
settled correctly. The user gets a 500 for a batch that largely succeeded. So the ordering
fix is paired with containment: each route module gains `_audit_landed_write()`, which logs
and swallows instead of raising, and all four loops call it. Ordering keeps the *result* honest;
containment keeps the *response* alive. Rejected alternative: catching around each `record()`
call site — four copies of the same swallow, and no single place to change the policy later.

**A failed audit write is logged, not returned.** The wrapper logs through
`logging.getLogger(__name__).exception(...)`, which uvicorn already surfaces. `app/` has no
logger today, so this introduces the stdlib pattern in these two functions. The mutation is
still reported as it actually happened, which is the defect being fixed; what is lost is one
audit line, and that loss is now visible in the server log instead of silently inverting a
result.

**The wrapper is module-local, not shared, and that is deliberate.** Revision 1 put
`record_best_effort` in `app/audit.py`; it would then call `app.audit.record`, and the
21 existing tests that do `monkeypatch.setattr(api, "record", …)` /
`monkeypatch.setattr(domains, "record", …)` would stop intercepting the bulk paths —
silently, since a patch that no longer fires does not fail loudly, it just captures nothing.
`test_domain_bulk_enable_writes_only_changed_records` (`tests/test_domains.py:349`) asserts
`len(audits) == 2` and would break. Defining `_audit_landed_write` in each route module means
it resolves `record` from that module's globals at call time, so the established patch seam
keeps working and no existing audit assertion has to be rewritten to accommodate a refactor.
The cost is one five-line function duplicated between `api.py` and `domains.py`. Rejected
alternative: keep the shared helper and repoint ~21 tests at `app.audit.record` — more churn,
on tests that are not what this plan is about, for no behavioural gain.

**For `adlist_bulk`, `mark_gravity_pending(instance_name)` fires once, on the first write
that lands**, behind a local flag, replacing the post-loop `any(result["changed"])` gate.
Marking on the *first* change rather than after the loop means a mid-batch abort cannot leave
a landed delete with gravity unflagged; keeping it to *one* call per batch preserves
`test_adlist_bulk_marks_gravity_pending_only_when_something_changed`
(`tests/test_api.py:1534`), which asserts `calls == ["pihole1"]` for a two-item batch.
Invariant #3 holds either way — the call is still only reachable from a branch where a write
actually changed something.

**`_adlist_key()` degrades to exact match rather than to a lossy key.** Dropping the input
validators means the function must accept anything Pi-hole may hold, and two things bite:
`urlsplit` itself raises on an unmatched bracket (`"http://[broken/list"`), so the guard has
to wrap the `urlsplit` call, not just the `parsed.port` access; and mapping an out-of-range
port to `None` would make `http://lists.example:99999/l` and `http://lists.example/l`
produce the same key, so a bulk action could hit the wrong record. Returning the raw string
on any parse failure fixes both: an unparseable address matches only itself.

## Files

- `app/url_policy.py` — rewrite `screen_adlist_url()`; add `_prohibited_literal_host()` helper.
- `app/routes/api.py` — remove three validators; make `_adlist_key()` total; add a
  module-local `_audit_landed_write()` and `logger`; reorder and contain the audit calls in
  `adlist_bulk`, `dns_records_bulk`, and `dns_cnames_bulk`, including `adlist_bulk`'s
  gravity sequencing.
- `app/routes/domains.py` — add its own `_audit_landed_write()` and `logger`; reorder and
  contain `domain_bulk`'s audit call.
- `app/audit.py` — **unchanged.** Revision 1 put the helper here; revision 2 does not,
  for the patch-seam reason in Approach.
- `frontend/src/api.ts` — add `errorDetail()` and use it in `request()`.
- `app/static/ui/**` — rebuilt production bundle (invariant #8).
- `tests/test_url_policy.py` — rewrite the adlist screen tests for the new policy.
- `tests/test_api.py` — new add/update/delete/bulk policy tests; audit-ordering tests for
  `adlist_bulk`, `dns_records_bulk`, and `dns_cnames_bulk`.
- `tests/test_domains.py` — audit-ordering test for `domain_bulk`.
- `README.md` — correct the adlist sentence in the outbound-request-policy section.
- `docs/design.md` — correct the "Done — outbound request policy" bullet.
- `docs/potential-features.md` — clear §6 into the shipped list; every bug it records is
  fixed here.

## Steps

1. **`app/url_policy.py` — new adlist screen.** Replace `screen_adlist_url` with:

   ```python
   def screen_adlist_url(value: str) -> str:
   ```

   No `allowlist`, `resolve`, or `resolver` parameters. Behaviour, in order:
   - Reject when `value != value.strip()`, contains `\`, or contains any character with
     `ord(char) < 32 or ord(char) == 127` — message `"Block list URL contains invalid characters"`.
   - `urlsplit(value)` and read `parsed.port` inside a `try`; on `ValueError` raise
     `URLPolicyError("Block list URL is malformed")`.
   - Scheme (lowercased) must be `http` or `https` and `parsed.hostname` must be set —
     `"Block list URL must be a complete HTTP or HTTPS URL"`.
   - `parsed.username`/`parsed.password` must be `None` —
     `"Block list URL must not contain embedded credentials"`.
   - `parsed.fragment` must be empty — `"Block list URL must not contain a fragment"`.
   - Canonicalize exactly as today: lowercase host; if the host parses as an IP, use
     `address.compressed`, bracketed when v6; re-append `:port` when present;
     `path = "" if parsed.path == "/" else parsed.path`; keep `parsed.query`; drop the
     fragment; lowercase the scheme; `urlunsplit`.
   - **New**, replacing the resolve/`_screen_addresses` block: when the host parsed as a
     literal IP, call the new `_prohibited_literal_host(address)`; if it returns `True`
     raise `URLPolicyError(f"Block list URL points at prohibited address {address}")`.
     When the host is a hostname, do nothing further — never call a resolver, never
     consult `get_public_allowlist()`.

   Add the helper next to `_prohibited`:

   ```python
   def _prohibited_literal_host(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
       # A block list URL is fetched by Pi-hole, never by this process, so there is no
       # SSRF surface here — only an obviously-wrong literal destination to refuse.
       return _prohibited(address)
   ```

   Keep the existing `# issue #4` comment but rewrite it to say the screen is
   syntax-and-canonicalization only, and why (plan 0012). Do not touch `_prohibited`,
   `_default_private`, `_screen_addresses`, `normalize_instance_url`,
   `resolve_instance_destination`, or `validate_instance_destination`.

2. **`app/routes/api.py` — drop the three record-identifying validators.**
   - `AdlistUpdateInput`: delete the `validate_address` field validator (keep
     `validate_groups` and `require_change`, and keep `address: str = Field(min_length=1, max_length=2048)`).
   - `AdlistDeleteInput`: delete `validate_address`; the class keeps only the `address` field.
   - `AdlistBulkInput`: delete `validate_addresses` entirely — the `max_length=200` list
     bound and the per-item length check move into a validator that only enforces length:

     ```python
     @field_validator("addresses")
     @classmethod
     def validate_addresses(cls, value: list[str]) -> list[str]:
         if any(len(item) > 2048 for item in value):
             raise ValueError("Address too long")
         return value
     ```

   `AdlistAddInput.validate_address` keeps calling `screen_adlist_url(value)` — now with the
   single-argument signature.

3. **`app/routes/api.py` — make `_adlist_key` total.** The guard must wrap the `urlsplit`
   call itself, not just `parsed.port`, and an unparseable address must degrade to an
   exact-match key rather than a lossy one:

   ```python
   def _adlist_key(url: str) -> str:
       # issue #8: match on a canonical key so a Pi-hole-normalised URL still resolves.
       # Plan 0012: addresses are no longer screened on the way in, so anything Pi-hole
       # may hold has to be accepted here. urlsplit raises on an unmatched bracket, and a
       # port that will not parse must not be dropped — mapping it to None would let
       # "http://h:99999/l" collide with "http://h/l" and match the wrong record. Fall back
       # to the raw string so a value we cannot canonicalize matches only itself.
       try:
           parsed = urlsplit(url)
           port = parsed.port
       except ValueError:
           return url
       host = (parsed.hostname or "").lower()
       rendered_host = f"[{host}]" if ":" in host else host
       netloc = rendered_host if port is None else f"{rendered_host}:{port}"
       path = parsed.path.rstrip("/")
       return parsed._replace(scheme=parsed.scheme.lower(), netloc=netloc, path=path, fragment="").geturl()
   ```

   Both sides of every comparison already go through `_adlist_key`, so the fallback is
   symmetric: a malformed stored address still matches the identical string submitted from
   the UI (which is read from that same instance), and matches nothing else.

3b. **Add a containment wrapper to each route module.** Add `import logging` and a
   module-level `logger = logging.getLogger(__name__)` to **both** `app/routes/api.py` and
   `app/routes/domains.py`, and define this function in each — identical body, one copy per
   module:

   ```python
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
   ```

   Do not add a shared helper to `app/audit.py`, and leave `record()` itself unchanged — the
   single-record routes still call it directly and still surface audit failures as errors.

4. **`app/routes/api.py` — `adlist_bulk` result/gravity/audit ordering.** Restructure the
   per-item body so the `try` wraps only the Pi-hole write:

   ```python
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
   ```

   Initialise `gravity_marked = False` next to `results: list[dict[str, Any]] = []` at the top
   of the route, and delete the post-loop
   `if any(result["changed"] for result in results): mark_gravity_pending(instance_name)`.

   The flag keeps this to **exactly one** `mark_gravity_pending` call per batch, which is what
   `test_adlist_bulk_marks_gravity_pending_only_when_something_changed` asserts
   (`calls == ["pihole1"]` for two changed lists). Marking on the first landed write rather
   than after the loop is what closes the gap: an abort later in the batch can no longer leave
   a completed delete with gravity unflagged. The response's
   `"gravity_pending": instance_name in load_pending_gravity()` is unchanged and still reads
   correctly, because marking happens before the return, and the call is still reachable only
   from a branch where a write actually changed something — invariant #3 holds.

5. **`app/routes/api.py` — `dns_records_bulk` result/audit ordering.** Same shape: the `try`
   wraps only `_delete_dns_host_record(...)`; its `except HTTPException` branch keeps the
   existing "Already deleted in this batch" re-raise verbatim; the `PiholeAPIError` and bare
   `Exception` branches set `result["error"]`, append, and `continue`. After the try, keep the
   `rows.remove(owner)` / sibling-rewrite bookkeeping, then set
   `result["success"] = True; result["changed"] = True`, `results.append(result)`, and only
   then `_audit_landed_write(payload.instance, "dns_record_delete", {"dns.hosts": owner["raw"]}, {"bulk": "delete"})`.
   Remove the now-duplicate trailing `results.append(result)` at the end of the loop body.
   Leave `_delete_dns_host_record`'s internal `_partial_dns_failure` audit call on `record`
   as it is — that path reports an orphaned record and is not a landed-write audit.

6. **`app/routes/domains.py` — `domain_bulk` result/audit ordering.** Same shape as step 4,
   without the gravity call (domain overrides need no gravity run — confirmed by plan 0007):
   the `try` wraps only `client.delete_domain(...)` / `client.update_domain(...)`, the
   already-in-state branch sets `success` and `continue`s, and both audit calls move below
   `results.append(result)` and become `_audit_landed_write(...)` with their existing payloads
   unchanged.

6b. **`app/routes/api.py` — `dns_cnames_bulk` containment.** Plan 0011 already settles and
   appends the result before auditing (`app/routes/api.py:1023-1034`); the only change is
   swapping that `record(...)` call for `_audit_landed_write(...)`, so a failing audit write
   cannot abort a batch whose deletes landed. Leave the surrounding structure and the
   existing "Revision 1" comment in place, extending it to note the containment.

7. **`frontend/src/api.ts` — render array-shaped `detail`.** Add above `request()`:

   ```ts
   function errorDetail(detail: unknown, fallback: string): string {
     if (typeof detail === 'string') return detail
     const message = (item: unknown): string | null => {
       if (typeof item === 'string') return item
       if (item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string') {
         return (item as { msg: string }).msg
       }
       return null
     }
     if (Array.isArray(detail)) {
       const messages = detail.map(message).filter((entry): entry is string => entry !== null)
       if (messages.length) return messages.join('; ')
     }
     return message(detail) ?? fallback
   }
   ```

   In `request()`, replace `detail = body.detail ?? detail` with
   `detail = errorDetail(body?.detail, detail)`. Nothing else in `request()` changes — the
   401 session-expired hook and the `204` path stay as they are. No exported type changes,
   so no page component needs editing.

8. **Rebuild the frontend.** `cd frontend && npm run build && npm run lint`, then commit the
   regenerated `app/static/ui/` (invariant #8).

9. **Docs.**
   - `README.md`: replace the two sentences beginning "Adlist URLs receive the same
     prohibited-range, metadata-address, and public-allowlist screen when submitted…" with a
     statement that block-list URLs are validated for syntax and canonicalized only — scheme,
     embedded credentials, control characters, and fragments are rejected, and a literal-IP
     host in a prohibited range is refused — but they are **not** resolved and **not** subject
     to `PIHOLE_MANAGER_PUBLIC_ALLOWLIST`, because Pi-hole, not this app, fetches them.
   - `docs/design.md`: extend the "Done — outbound request policy" bullet with the same
     instance-vs-block-list distinction.
   - `docs/potential-features.md`: **clear §6 entirely.** Every bug it documents is fixed by
     this plan — do not count entries, remove all of them: "Add-time adlist URL screening
     blocks all public block lists" *including its nested "Amplified by plan 0010's bulk
     route" note*, "A failed audit write misreports a successful bulk mutation (plan 0010)",
     and "`[object Object]` toast on any 422". Add a corresponding entry to the "Shipped since
     these notes were first written" list at the top, crediting plan 0012 and naming all
     three fixes. Leave the `## 6. Bugs / papercuts noticed in use` heading in place with
     "Nothing outstanding in this section." beneath it, matching §1's existing wording. Leave
     every other section untouched — in particular §5's frontend-test-runner note stays, since
     this plan does not add one.

## Contracts

No request or response *shape* changes. Both sides of the affected contracts stay as they
are; only server-side accept/reject behaviour and one client-side error-string derivation
change.

**Backend — validation behaviour only (no field changes):**

| Model | Field | Before | After |
| --- | --- | --- | --- |
| `AdlistAddInput` | `address: str` (1–2048) | `screen_adlist_url(value)` — resolves + allowlist screen | `screen_adlist_url(value)` — syntax + canonicalize, literal-IP screen, no DNS |
| `AdlistUpdateInput` | `address: str` (1–2048) | `screen_adlist_url(value, resolve=False)` | no validator |
| `AdlistDeleteInput` | `address: str` (1–2048) | `screen_adlist_url(value, resolve=False)` | no validator |
| `AdlistBulkInput` | `addresses: list[str]` (1–200) | length check + `screen_adlist_url(item, resolve=False)` per item | length check only |

**`app/url_policy.py` signature change (internal, backend-only):**

```python
# before
def screen_adlist_url(value, *, allowlist=None, resolve=True, resolver=socket.getaddrinfo) -> str
# after
def screen_adlist_url(value: str) -> str
```

**Frontend — `frontend/src/api.ts`:** `request<T>()` keeps its signature and still throws
`Error(detail)`. Only the derivation of `detail` changes, so an array-shaped FastAPI 422 now
yields e.g. `"Value error, Block list URL must not contain embedded credentials"` instead of
`"[object Object]"`. No exported interface is added, removed, or altered.

**New private function, defined once in each of `app/routes/api.py` and
`app/routes/domains.py` (internal, backend-only). `app/audit.py` is unchanged. No response
shape changes — every bulk route keeps its exact `BulkResponse` fields, so `api.ts` needs no
type edit for this half of the plan:**

```python
def _audit_landed_write(instance: str, action: str, before: dict, after: dict) -> None
```

**Config / DB:** N/A — no config key, no schema, no on-disk format changes.
`PIHOLE_MANAGER_PUBLIC_ALLOWLIST` keeps its exact meaning for instance URLs.

## Tests

`tests/test_url_policy.py` — rewrite the adlist block (the five tests spanning lines 130–172; the instance-URL tests above them are untouched):

- `test_adlist_url_allows_a_public_list_without_an_allowlist` — asserts
  `screen_adlist_url("https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/tif.txt")`
  returns the URL unchanged, with `PIHOLE_MANAGER_PUBLIC_ALLOWLIST` unset. This is the
  reported bug, asserted directly.
- `test_adlist_url_is_canonicalized` — `"https://Example.COM/list.txt?format=hosts"` →
  `"https://example.com/list.txt?format=hosts"`; `"https://Example.com/"` → `"https://example.com"`.
- `test_adlist_url_never_resolves` — monkeypatches `socket.getaddrinfo` to raise
  `AssertionError`, then screens both a public hostname and `"http://lists.lan/hosts"`
  successfully. Replaces `test_adlist_url_without_resolution_never_calls_resolver`.
- `test_adlist_url_allows_rfc1918_and_hostnames` — `"http://lists.lan/hosts"` and
  `"http://192.168.1.4/hosts"` both pass unchanged.
- `test_adlist_url_rejects_prohibited_literal_addresses` — parametrized over
  `"http://169.254.169.254/latest"`, `"http://127.0.0.1/list"`, `"http://[::1]/list"`;
  each raises `URLPolicyError` matching `"prohibited"`.
- `test_adlist_url_rejects_unsafe_syntax` — kept, arguments dropped: parametrized over
  `"https://user:pass@example.com/list"`, `"ftp://example.com/list"`,
  `"https://example.com/line\nfeed"`, `"https://example.com/list#frag"`,
  `"https://example.com:99999/list"`, `" https://example.com/list"`.
- `test_instance_url_policy_is_unchanged` — asserts a public instance URL still raises
  `"not allowlisted"` through `validate_instance_destination(..., allowlist=EMPTY,
  resolver=resolver_for("8.8.8.8"))`, proving the split did not loosen invariant #6 for
  instances.

`tests/test_api.py`:

- `test_adlist_add_accepts_a_public_block_list` — `api.AdlistAddInput(instances=["pihole1"],
  address="https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/tif.txt")`
  constructs and `.address` round-trips unchanged, with no monkeypatched screen and no
  allowlist env var.
- `test_adlist_add_rejects_prohibited_literal_address` — replaces
  `test_adlist_input_rejects_metadata_address` and drops its monkeypatch:
  `api.AdlistAddInput(instances=["pihole1"], address="http://169.254.169.254/x")` raises
  `ValidationError`.
- `test_adlist_update_delete_and_bulk_accept_unscreened_addresses` — replaces
  `test_adlist_update_and_delete_do_not_resolve_dead_host`: `AdlistUpdateInput`,
  `AdlistDeleteInput`, and `AdlistBulkInput` all construct with
  `"file:///etc/pihole/local.list"` and `"https://user:pass@example.com/l#frag"` without
  raising, and `AdlistBulkInput` still rejects a 2049-character entry.
- `test_adlist_key_is_total_for_unparseable_addresses` — direct unit test:
  `_adlist_key("http://[broken/list")` returns the string unchanged instead of raising
  `ValueError`, and `_adlist_key("http://lists.example:99999/l") != _adlist_key("http://lists.example/l")`
  — the collision revision 0 would have introduced.
- `test_adlist_bulk_handles_a_stored_address_that_will_not_parse` — with the instance holding
  `"http://lists.example:99999/l"`, a bulk delete naming that exact string matches and
  deletes it, and a bulk delete naming `"http://lists.example/l"` reports
  `"No longer present on this instance"` rather than deleting the wrong record or 500ing.
- `test_adlist_bulk_reports_success_and_marks_gravity_when_audit_fails` — patch
  `api.record` (the existing seam) to raise `OSError`; assert the call
  **returns normally** (does not raise `HTTPException`), that every item result is
  `success=True, changed=True`, that the instance is in `load_pending_gravity()`, and that
  the delete reached the fake client. The "returns normally" assertion is the half revision 0
  could not have satisfied.
- `test_dns_records_bulk_reports_success_when_audit_fails` — same shape for
  `dns_records_bulk`: returns normally, the deleted record's result is
  `success=True, changed=True`, and the host was removed from the fake client.
- `test_dns_cnames_bulk_reports_success_when_audit_fails` — same shape for
  `dns_cnames_bulk`, covering the containment gap plan 0011 left behind.
- `test_bulk_audit_failure_is_logged` — with `record` raising, `caplog` at `ERROR` captures
  one entry naming the action and instance, proving the audit loss is reported rather than
  swallowed silently. Assert against the **owning route module's** logger —
  `app.routes.api` for the three `api.py` loops, `app.routes.domains` for `domain_bulk` —
  not `app.audit`: step 3b logs through `logging.getLogger(__name__)` in each route module,
  and those loggers do not propagate to `app.audit`.
- `test_audit_landed_write_swallows_and_delegates` — direct unit test of the wrapper, once
  per module (`api._audit_landed_write`, `domains._audit_landed_write`): with
  `monkeypatch.setattr(<module>, "record", …)` a successful call writes through, and a
  raising `record` produces no exception. This also pins the module-local seam, so a future
  move of the helper into `app/audit.py` fails loudly instead of silently detaching the
  21 existing `setattr(<module>, "record", …)` patches.

`tests/test_domains.py`:

- `test_domain_bulk_reports_success_when_audit_fails` — `record` raises; the deleted domain's
  result is `success=True, changed=True` and `delete_domain` reached the fake client.

**Every existing test that changes is named here. There are eight, in three groups.**
Anything not on this list must pass unmodified.

*Group 1 — forced by the `screen_adlist_url` signature change (five tests,
`tests/test_url_policy.py`, lines 130–172).* The new signature takes only `value`, so the
existing `allowlist=` / `resolve=` / `resolver=` call sites cannot compile as written. These
are replaced by the five specified above:
`test_adlist_url_is_screened_and_canonicalized`, `test_adlist_url_rejects_prohibited_addresses`,
`test_adlist_url_allows_rfc1918_address`, `test_adlist_url_without_resolution_never_calls_resolver`,
and `test_adlist_url_rejects_unsafe_syntax` (the last keeps its name and parametrize list,
dropping only the `allowlist`/`resolver` arguments).

*Group 2 — replaced because the behaviour they pin is gone (two tests, `tests/test_api.py`).*
`test_adlist_input_rejects_metadata_address` (`:321`) monkeypatches `screen_adlist_url` to
reject; it is replaced by `test_adlist_add_rejects_prohibited_literal_address`, which needs no
monkeypatch. `test_adlist_update_and_delete_do_not_resolve_dead_host` (`:330`) exercises a
`resolve=False` path that no longer exists; it is replaced by
`test_adlist_update_delete_and_bulk_accept_unscreened_addresses`.

*Group 3 — one test whose assertions invert (one test, `tests/test_api.py`).*

- `tests/test_api.py::test_cname_bulk_delete_does_not_mislabel_when_audit_fails`
  (`tests/test_api.py:1811`) currently asserts `HTTPException` with `status_code == 503` and
  `isinstance(caught.value.__cause__, OSError)` when the second `record` call raises. That is
  precisely the behaviour this plan removes. Rewrite it — same name, same `failing_record`
  fixture, same `monkeypatch.setattr(api, "record", failing_record)` — to assert instead that
  `api.dns_cnames_bulk(...)` **returns normally**, that both results are
  `success=True, changed=True`, that both deletes reached the client, and that `audits`
  captured two attempts. For the "deletes landed" assertion use the fake's `deleted_cnames`
  list (`tests/test_api.py:89`) — `FakePiholeClient.delete_dns_cname` appends to it and does
  **not** mutate `FakePiholeClient.tree`, so an assertion against `tree["dns"]["cnameRecords"]`
  would be wrong. Do not delete the test and do not rename it; the git history should show
  the assertion inverting.

**Existing tests that must keep passing unmodified** — each was checked against the revised
steps, and revision 2 exists because two of them were not. None of these is in the eight
above:

- `tests/test_domains.py::test_domain_bulk_enable_writes_only_changed_records` (`:349`) —
  patches `domains.record` and asserts `len(audits) == 2`. Passes because step 3b keeps the
  wrapper module-local, so the patch still intercepts.
- `tests/test_api.py::test_adlist_bulk_marks_gravity_pending_only_when_something_changed`
  (`:1534`) — asserts `calls == ["pihole1"]` for two changed lists and `calls == []` for none.
  Passes because step 4 marks gravity once per batch behind `gravity_marked`.
- `tests/test_api.py::test_cname_bulk_delete_audits_each_removed_record` (`:1793`) — patches
  `api.record` and asserts the exact audit tuples. Passes for the same seam reason.
- `test_adlist_match_ignores_a_normalized_trailing_slash`,
  `test_adlist_bulk_delete_dedupes_canonical_aliases`,
  `test_domain_bulk_delete_matches_case_insensitively`, and every instance-URL test in
  `tests/test_url_policy.py`.

If any existing test outside the eight named above needs changing to make this plan pass,
that is a signal the plan is wrong — stop and report it rather than editing the test.

Frontend: no test runner exists (`docs/potential-features.md` §5), so `errorDetail` is
covered by `npm run lint` plus the manual acceptance check below. Do **not** add a test
framework in this plan.

## Invariants at risk

- **#6 — all outbound requests go through `app/url_policy.py`.** This is the invariant the
  plan deliberately touches, and the plan's position is that it was being applied to the
  wrong thing: a block-list URL is not an outbound request *this app* makes. Every URL this
  process actually connects to still goes through `resolve_instance_destination` with the
  full prohibited-range + allowlist screen, unchanged; `_screen_addresses` and
  `normalize_instance_url` are not edited. `screen_adlist_url` stays in `url_policy.py` so
  the policy remains centralized and reviewable in one file, and a dedicated test asserts the
  instance path did not loosen. This is the explicit, approved policy decision ARCHITECT.md
  requires for a change in reach of an invariant.
- **#3 — gravity updates are gated behind real changes.** Step 4 moves
  `mark_gravity_pending` out of a post-loop `any(changed)` gate and into the branch that runs
  only after a write actually landed, so it is *more* tightly gated than before, not less.
  The "already in the requested state" branch returns `success=True, changed=False` and never
  marks gravity.
- **Auditing (README "Request integrity" — every write reconstructable after the fact).**
  Not one of the nine numbered invariants, but it is the reason `audit.py` exists, and
  `_audit_landed_write` deliberately downgrades an audit failure from fatal to logged. The
  trade accepted here: a mutation that already landed on a Pi-hole is reported to the user
  as it actually happened, and the audit gap is announced in the server log rather than
  being papered over by a misleading "failed" result. Coverage is otherwise unchanged —
  every successful bulk item still writes exactly the same audit entry it does today, and
  the single-record routes keep the strict `record()`.
- **#2 — diff before write.** Unchanged. All three bulk loops still read current state
  (`client.get_adlists()`, `_parse_dns_hosts`, `client.get_domains()`), match it against the
  request, skip absent records, and skip no-op enable/disable. The restructure only moves
  `record()` relative to the result assignment.
- **#1 — one instance mutated at a time.** Unchanged. `adlist_bulk`, `dns_records_bulk`, and
  `domain_bulk` are already single-instance routes; `adlist_add` keeps its sequential
  per-instance loop with the existing comment.
- **#4 — no background scheduler.** Unchanged. Nothing added runs off a request, and removing
  the DNS lookup strictly reduces work done during a request.
- **#5 — FastAPI owns all credentials.** Unchanged. No credential or Pi-hole URL moves toward
  the frontend; `errorDetail` only reshapes text the backend already returns in `detail`, and
  the affected messages are validation errors about the user's own submitted string. Upstream
  errors are still sanitized by `_api_error`.
- **#7 — every route behind auth middleware.** Unchanged. No route is added, removed, or
  re-registered.
- **#8 — compiled frontend committed.** `frontend/src/api.ts` changes, so step 8 rebuilds and
  commits `app/static/ui/`. The branch is incomplete without it.
- **#9 — `main` is production.** Work lands on `feature/0012-adlist-url-policy-and-bulk-audit`
  and merges only on an Accept verdict.

## Acceptance criteria

- [ ] `screen_adlist_url("https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/tif.txt")`
      returns the URL unchanged with `PIHOLE_MANAGER_PUBLIC_ALLOWLIST` unset, and
      and the body of `screen_adlist_url` contains no reference to `getaddrinfo`, a
      `resolver` argument, or `get_public_allowlist`.
- [ ] `screen_adlist_url` rejects, with `URLPolicyError`: `ftp://…`, an embedded-credentials
      URL, a URL with a fragment, a URL with a control character, and
      `http://169.254.169.254/latest`.
- [ ] `http://lists.lan/hosts` and `http://192.168.1.4/hosts` are still accepted.
- [ ] `AdlistUpdateInput`, `AdlistDeleteInput`, and `AdlistBulkInput` construct successfully
      with `file:///etc/pihole/local.list`; `AdlistBulkInput` still rejects an entry longer
      than 2048 characters and a list longer than 200 entries.
- [ ] `validate_instance_destination` still raises `"not allowlisted"` for a public instance
      address with an empty allowlist — instance policy provably unchanged.
- [ ] In all four bulk loops (`adlist_bulk`, `dns_records_bulk`, `dns_cnames_bulk`,
      `domain_bulk`), an audit write that raises leaves the route **returning normally** with
      every landed item reported `success=True, changed=True` — no `HTTPException`, no
      `_api_error` 500, no discarded results; for `adlist_bulk` the instance is also in
      `load_pending_gravity()`, and the failure is logged by the owning route module.
- [ ] `adlist_bulk` calls `mark_gravity_pending` **exactly once** for a batch that changes
      two lists, and **not at all** for a batch that changes nothing.
- [ ] `app/audit.py` is byte-for-byte unchanged, and `record()` still raises on failure.
- [ ] Exactly the eight pre-existing tests named in Tests were changed — five in
      `tests/test_url_policy.py`, three in `tests/test_api.py`. `git diff main...HEAD --
      tests/` shows no other existing test body rewritten, and in particular
      `tests/test_domains.py` gains a test without any existing one changing.
- [ ] The audit-failure log entry is captured on `app.routes.api` / `app.routes.domains`,
      not `app.audit`.
- [ ] `_adlist_key("http://[broken/list")` returns rather than raises, and
      `http://lists.example:99999/l` and `http://lists.example/l` produce different keys.
- [ ] `record()` itself still raises on failure and is still used by the single-record
      routes — the containment is scoped to the bulk loops.
- [ ] Live check against `lab-pihole`: adding
      `https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/tif.txt` from the
      Block Lists page succeeds, the row appears, the gravity-pending prompt shows, and the
      list can then be disabled and deleted.
- [ ] Manual UI check: submitting `ftp://example.com/list` in the add-block-list form shows
      the real rejection message in the toast, not `[object Object]`.
- [ ] `README.md` and `docs/design.md` no longer claim block-list URLs get the
      public-allowlist screen; `docs/potential-features.md` §6 is empty apart from its
      heading and a "Nothing outstanding in this section." line, with no trace of the
      "Amplified by plan 0010's bulk route" note, and the shipped list credits plan 0012.
- [ ] `./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
