# 0002 — Bug sweep + concurrent fan-out: issues #1, #2, #4, #6, #7, #8, #9, #10, #12

Status: shipped          <!-- draft | approved | in-progress | in-review | shipped -->
Revision: 2026-09-04 — (a) issue #1 approach corrected (removed contradictory per-IP gate; pure per-username lockout) after Codex flagged it pre-implementation. (b) issue #4 narrowed after review 1: only `POST /api/adlists` resolves+screens the host; `PATCH`/`DELETE` canonicalise only, so a list with a dead domain stays manageable. `_normalize_hostname` no longer applied to adlist hosts. (c) after the adversarial review: every DNS-rewrite compensating call is wrapped by `_partial_dns_failure` so a rollback failure on `dns_record_delete` / `dns_record_update` is reported with the exact lost `dns.hosts` line(s) + audit, never masked as a generic 5xx (was a high-severity no-ship: sibling hostnames could be silently lost).
Branch: feature/0002-bug-sweep-and-fanout-perf
Author: Kyrnveil
Date: 2026-09-04

## Problem

The Gitea tracker has one performance issue (#10) and a cluster of functional bugs
(#2, #6, #7, #8, #9) plus small hardening items (#1, #4, #12) that survived the plan-0001
security pass. The worst of them make whole features unusable:

- **#2** — a single non-canonical `base_url` in `instances.yml` makes `load_instances()`
  raise `URLPolicyError`, and nearly every route calls it, so the entire API 500s with
  no UI path to view or delete the offending instance.
- **#6 / #7** — DNS host records whose stored form isn't canonical (mixed-case hostname,
  non-compressed IPv6, or multiple hostnames on one `dns.hosts` line) can never be
  edited or deleted through the app: the reconstructed `"<ip> <host>"` string never
  matches Pi-hole's raw text, so every delete/update 404s or 409s.
- **#8** — the same string-equality trap for adlists: once Pi-hole normalises a list
  URL, `enable/disable/delete` 404 even though the list is present.
- **#9** — folding `get_versions()` into the connectivity check means a transient
  `/api/info/version` 503/429 (or a reverse-proxied path quirk) flips a fully healthy,
  authenticated instance to `error` and drops all version data.
- **#10** — `GET /api/instances`, `/api/status`, `/api/adlists`, `/api/dns/records` walk
  every instance **sequentially**, each leg up to a 15 s timeout, and every
  `PiholeClient()` re-parses `FILTERHELM_PUBLIC_ALLOWLIST` from the environment and
  runs a blocking `getaddrinfo`. With the #9 change this roughly doubled per-instance
  latency.

#1 (per-IP login lockout locks out *all* admins behind a shared reverse-proxy IP) and
#12's small items round out the sweep. #11 (unlocked `save_instances`) and #3/#5
(rebinding TOCTOU, proxy-auth bypass) are already fixed on the plan-0001 branch.

## Scope

**Prerequisite:** plan 0001 is merged to `main` first (per its own review loop). This
branch is cut from `main` *after* that merge and builds on 0001's
`resolve_instance_destination` / `PublicAllowlist` / `instances_mutation` / atomic-write
work.

In — each item tagged with the issue it closes:

- **#10a** — parse the public allowlist **once** (`get_public_allowlist()`, cached) and
  make it the default for `resolve_instance_destination`, so no construction re-reads the
  environment.
- **#10b** — run the per-instance work of the four **read-only** list endpoints
  (`instances_list`, `status_list`, `adlists_list`, `dns_records_list`) **concurrently**
  on a bounded, request-scoped thread pool.
- **#2** — `Instance.__post_init__` tolerates a non-canonical stored `base_url` instead
  of raising; the bad entry surfaces as a per-instance `error` in the list, and delete
  still works.
- **#6** — DNS record existence / duplicate / rollback checks match **structurally**
  (compressed IP + lower-cased host), not by reconstructed string.
- **#7** — add/delete/update operate on the **whole** `dns.hosts` entry: a multi-hostname
  line is rewritten with the target hostname removed or changed, with rollback.
- **#8** — adlist match + submit use a **canonical key** (lower-cased scheme + host,
  normalised trailing slash), so a Pi-hole-normalised URL still resolves.
- **#4** — adlist URLs are screened through `app/url_policy.py` (same prohibited-range /
  metadata / allowlist checks as instance URLs) at add time. Best-effort (Pi-hole still
  resolves independently when it fetches); documented as such.
- **#9** — version retrieval is best-effort: connectivity status comes from
  `test_connection()` alone; `get_versions()` failure yields `versions = None` and a soft
  note, not `error`.
- **#1** — failed-login lockout is gated **only** on the per-username failed count; the
  client IP is still recorded (audit) but no longer gates, so a shared reverse-proxy IP
  can't lock out every account.
- **#12a** — `RequestBodyLimitMiddleware` never emits a second `http.response.start` when
  `_BodyTooLarge` fires after the downstream app already started the response.
- **#12b** — `InstanceInput.app_password` / `InstanceUpdate.app_password` gain a
  `max_length`.
- **#12c** — `dns_record_update` records an audit entry and returns a clear error when
  its compensating delete also fails (orphaned new record).

Out:

- **DNS pre-resolution TTL cache** (#10 suggestion 2). Concurrency removes the wall-clock
  penalty and `get_public_allowlist()` removes the env re-parse; a resolution cache would
  trade a slice of rebinding-protection margin for little gain on a LAN. Revisit only if
  profiling shows `getaddrinfo` still hot.
- **Trusted-proxy `X-Forwarded-For` client-IP resolution** (#1's other half). Still the
  documented trade-off in `README.md`; per-username lockout closes the "locks out
  everyone" impact without it.
- **`audit.log` rotation, shared-state rate limiter, middleware ordering** — unchanged,
  documented trade-offs.
- Any `frontend/` change. All fixes are backend-side; API response shapes are unchanged
  (see Contracts), so `app/static/ui/` is not rebuilt.

## Approach

**#10a — allowlist parsed once.** Add `get_public_allowlist() -> PublicAllowlist` to
`app/url_policy.py`, memoised with `functools.lru_cache(maxsize=1)`, wrapping
`PublicAllowlist.from_environment()`. Change `resolve_instance_destination` /
`validate_instance_destination` to default `allowlist` to `get_public_allowlist()`
instead of calling `.from_environment()` inline. A custom `allowlist=` still overrides
(tests do this). Expose `get_public_allowlist.cache_clear()` and clear it in a new
`tests/conftest.py` autouse fixture so env-mutating tests stay isolated. No signature
change to `PiholeClient` — it picks up the cached default for free.

**#10b — concurrent read-only fan-out.** Add `_fanout(items, worker)` in
`app/routes/api.py`: a `concurrent.futures.ThreadPoolExecutor(max_workers=_FANOUT_WORKERS)`
created inside the helper, `map`-style, results returned in input order, executor
`shutdown()` before returning (request-scoped, no background thread survives the
response). `_FANOUT_WORKERS = max(1, int(os.environ.get("FILTERHELM_FANOUT_WORKERS",
"8")))`. Apply to `instances_list`, `status_list`, `adlists_list`, `dns_records_list`
only. The mutating fan-outs (`blocking_update`, `adlist_add`) stay a sequential `for`
loop — see Invariants. Each worker touches exactly one instance and only reads.

**#2 — tolerant load.** `Instance.__post_init__` wraps `normalize_instance_url` in
`try/except URLPolicyError` and keeps the stripped raw value on failure. Downstream,
`PiholeClient(...)` → `resolve_instance_destination` raises `URLPolicyError` for a truly
broken URL, which `_connection` already catches as `("error", <msg>, None)` and the
mutating endpoints map through `_api_error`. `instance_delete` only needs the name, so a
bad entry is deletable. Net effect: one bad row degrades one instance, never the whole
API.

**#6 / #7 — structural DNS matching + whole-entry rewrite.** New helpers in
`app/routes/api.py`:

- `_canonical_addr(text) -> str` — `ipaddress.ip_address(text).compressed`, or the
  stripped text if it doesn't parse.
- `_host_entry(raw) -> tuple[str, list[str]] | None` — split a `dns.hosts` line into
  `(_canonical_addr(ip), [host.lower().rstrip(".") for host in rest])`.
- `_parse_dns_hosts` keeps returning one row per hostname but each row also carries
  `entry_ip`, `entry_hosts` (the full canonical sibling list), and `raw`.

Existence / duplicate checks compare `(_canonical_addr(address), hostname.lower())`
against the parsed rows structurally. Mutations rewrite the owning line.

**Every compensating (rollback) call is itself wrapped.** The rewrite deletes a whole
`dns.hosts` line before re-adding the trimmed/renamed version, so a failure *during
rollback* can leave the Pi-hole with siblings dropped. That must never surface as a bare
`_api_error`: whenever a compensating `add_dns_host` / `delete_dns_host` throws, the
handler emits a truthful audit event carrying the **exact** lines that were removed and
not restored, and raises `HTTPException(502, ...)` whose `detail` names the line the
operator must re-add by hand. A `_partial_dns_failure(instance, event, removed, added)`
helper centralises this (audit + 502 with recovery text).

- **delete**: `client.delete_dns_host(raw)`; if `entry_hosts` had siblings,
  `client.add_dns_host(<ip> + remaining hosts)`. If that re-add fails → try
  `client.add_dns_host(raw)` (restore original); if **that** also fails →
  `_partial_dns_failure(instance, "dns_record_delete_orphaned", removed=[raw], added=[])`.
- **update**: add the new record, then `delete_dns_host(old_owner["raw"])`.
  - delete-old fails → try `delete_dns_host(new_record)`; if that fails →
    `_partial_dns_failure(..., "dns_record_update_orphaned", removed=[], added=[new_record])`.
  - delete-old succeeds, then re-adding the trimmed old line (`old_replacement`) fails →
    try to restore `old_owner["raw"]` **and** remove `new_record`; if either compensating
    call fails → `_partial_dns_failure(..., "dns_record_update_orphaned",
    removed=[old_owner["raw"]] , added=[new_record])`.
- Successful-path audits use `old_owner["raw"]` (the full line), matching the orphan
  events, not the reconstructed single pair.

Reject nothing new — multi-hostname lines become editable rather than erroring.

**#8 — canonical adlist key.** `_adlist_key(url) -> str`: `urlsplit`, lower-case scheme +
host, keep port/path/query, collapse a bare `"/"` path to `""`. `_find_adlist` matches
`_adlist_key(item.address) == _adlist_key(address)`. Submissions to Pi-hole
(`add_adlist`) send the canonical form so new entries round-trip. Existing non-canonical
entries match via the key.

**#4 — adlist SSRF screen.** Add `screen_adlist_url(value, *, allowlist=None,
resolve=True) -> str` to `app/url_policy.py`. It always runs the current `_adlist_url`
syntactic checks (scheme, creds, control chars, port) — which move into `url_policy` —
and returns the canonicalised URL (`_adlist_key` form with path preserved). When
`resolve=True` it *additionally* resolves the host and runs the existing `_prohibited` /
`_default_private` / allowlist screen, factored out of `resolve_instance_destination`
into `_screen_addresses(addresses, *, hostname, allowlist)`.

Only **`AdlistAddInput.address`** calls it with `resolve=True` — add is the only input
that introduces a new fetch target. **`AdlistUpdateInput.address` /
`AdlistDeleteInput.address` call it with `resolve=False`** (syntax + canonicalise only):
disabling or deleting a list fetches nothing, and a list whose domain has since gone
offline must stay manageable — requiring live DNS there would re-create the exact
"can't manage the list" failure #8 fixes, as a `422` instead of a `404`.

A prohibited/non-allowlisted host now → `422` at **add** time, matching instance URLs.
The screen is check-time only; Pi-hole re-resolves when it fetches, so `README.md`
documents it as best-effort. `_normalize_hostname` is **not** applied to adlist hosts
(it forbids `_` and other things real blocklist hosts use) — canonicalisation is a plain
lower-case of scheme + host.

**#9 — best-effort versions.** In `_connection`:

```
with PiholeClient(...) as client:
    client.test_connection()                 # decides success / warning
    try:
        versions = _version_info(client.get_versions())
    except PiholeAPIError:
        versions = None
```

Status stays `success` / `warning` from `test_connection`; when `versions is None` the
detail string gets a `"; version details unavailable"` suffix. `versions` is already
`PiHoleVersions | null` in `api.ts`, so no contract change.

**#1 — per-username lockout.** `login_attempts` gains a nullable `username` column
(guarded `ALTER TABLE ... ADD COLUMN`, since the table is `CREATE TABLE IF NOT EXISTS`
and its rows are ephemeral — 15-minute window). `login_allowed(remote_addr, username)`
gates **solely on the per-username failed count** in the window (`< 5`).
`authenticate(...)` writes both `username` and `remote_addr` into the row — the latter is
kept for the audit trail / forensics, **not** used for gating — and a successful login
clears the rows for that username. Behaviour behind a shared proxy IP: a brute force
against one account locks only that account.

The pre-existing per-IP lockout is **replaced, not kept alongside**: an `OR` against
`remote_addr` would re-introduce the exact all-accounts lockout #1 exists to fix (5
failures from the shared proxy IP → everyone locked). Non-proxy trade-off: a direct
attacker is no longer IP-throttled at this layer; the app-wide `RateLimitMiddleware`
(120 req / 60 s) and argon2's verify cost still apply, and usernames aren't enumerable.
`routes/auth.py` passes `payload.username` through. `README.md` notes the switch, the
residual targeted single-account lockout, and that this stacks with proxy-level limits.

**#12a — no double response start.** `RequestBodyLimitMiddleware.__call__` wraps `send`
to record whether `http.response.start` has passed. On `_BodyTooLarge`: if the response
already started, re-raise (the ASGI server aborts the stream — the correct outcome);
otherwise `_reject` with `413` as today.

**#12b** — `app_password: str = Field(min_length=1, max_length=512)` on both models.

**#12c** — the `_partial_dns_failure` helper described under "#6 / #7" above. It covers
**both** the delete path (`dns_record_delete_orphaned`) and the update path
(`dns_record_update_orphaned`), on **any** compensating-call failure — not just the
single add-new/delete-old branch. The `502` `detail` names the exact `dns.hosts` line(s)
the operator must re-add, e.g. `"Partially applied — the line '192.168.1.5 alpha beta'
was removed and could not be restored; re-add it in the Pi-hole Local DNS settings."`.
No generic `_api_error` is allowed to mask a rollback failure.

Rejected alternatives:

- *#7: reject multi-hostname lines with an error* (the issue's other option) — worse UX;
  users with existing multi-host lines could never fix them through the app.
- *#10b: make the whole stack async* — a large refactor of `PiholeClient` (sync `httpx`)
  and every route; a bounded thread pool on the four hot endpoints gets the same
  wall-clock win for this low-traffic local tool.
- *#1: gate on per-username `OR` per-IP count* — an `OR` on the client IP, which is the
  shared reverse-proxy's address, keeps the all-accounts lockout #1 exists to fix. A
  much-higher per-IP ceiling alongside per-username was also rejected: still
  attacker-triggerable, more moving parts. Pure per-username it is.
- *#1: also add trusted-proxy `X-Forwarded-For` resolution now* — that's the larger
  change the user deferred; out of scope (see Scope "Out").

## Files

- `app/url_policy.py` — `get_public_allowlist()` (cached) + make it the default for
  `resolve_instance_destination` / `validate_instance_destination`; factor
  `_screen_addresses(...)` out of `resolve_instance_destination`; add
  `screen_adlist_url(...)` and move the adlist syntactic checks here.
- `app/routes/api.py` — `_FANOUT_WORKERS`, `_fanout(...)`; concurrency in
  `instances_list` / `status_list` / `adlists_list` / `dns_records_list`; `_connection`
  best-effort versions (#9); `_canonical_addr` / `_host_entry` / `_rewrite_without` /
  `_partial_dns_failure`, richer `_parse_dns_hosts`, structural matching + whole-entry
  rewrite with wrapped compensation in `dns_record_add` / `dns_record_delete` /
  `dns_record_update` (#6/#7/#12c); `_adlist_key` + `_find_adlist` (#8); `AdlistAddInput`
  validator calls `screen_adlist_url` (resolving), `AdlistUpdate`/`AdlistDelete` call it
  with `resolve=False` (#4); `app_password` `max_length` (#12b).
- `app/config.py` — `Instance.__post_init__` tolerant of a non-canonical stored
  `base_url` (#2).
- `app/http_hardening.py` — `RequestBodyLimitMiddleware` double-`response.start` guard
  (#12a).
- `app/auth.py` — `login_attempts.username` column + guarded migration;
  `login_allowed(remote_addr, username)`; `authenticate` writes/clears by username (#1).
- `app/routes/auth.py` — pass `payload.username` to `login_allowed` / `authenticate` (#1).
- `README.md` — per-username lockout + residual (#1); adlist SSRF screen, best-effort
  (#4); concurrent list endpoints + `FILTERHELM_FANOUT_WORKERS` (#10).
- `tests/conftest.py` — **new**: autouse fixture calling
  `get_public_allowlist.cache_clear()` before/after each test.
- `tests/test_url_policy.py`, `tests/test_api.py`, `tests/test_auth.py`,
  `tests/test_http_hardening.py`, `tests/test_config.py` — see Tests.
- `docs/plans/0002-bug-sweep-and-fanout-perf.md` — this plan.

## Steps

1. **#10a** `app/url_policy.py`: `import functools`. Add
   `@functools.lru_cache(maxsize=1) def get_public_allowlist() -> PublicAllowlist: return
   PublicAllowlist.from_environment()`. In `resolve_instance_destination`, replace
   `allowlist = allowlist or PublicAllowlist.from_environment()` with
   `allowlist = allowlist or get_public_allowlist()`. Inline comment:
   `# issue #10: parse the allowlist once, not on every PiholeClient construction`.
2. `app/url_policy.py`: extract the per-address loop of `resolve_instance_destination`
   into `_screen_addresses(addresses, *, hostname, allowlist) -> None` (raises
   `URLPolicyError`), and call it from `resolve_instance_destination`. No behaviour change
   there.
3. **#4** `app/url_policy.py`: add `screen_adlist_url(value, *, allowlist=None,
   resolve=True, resolver=socket.getaddrinfo) -> str`. Port the body of
   `routes/api.py::_adlist_url` (scheme in `{http,https}`, no embedded creds, no fragment,
   no control/backslash chars, valid port) here; build a canonical URL (lower-case scheme
   + host — **no `_normalize_hostname`** — keep port/path/query, drop a lone `"/"` path).
   When `resolve` is `True`: resolve the host (literal IP → use directly, else `resolver`)
   and `_screen_addresses(addrs, hostname=host, allowlist=allowlist or
   get_public_allowlist())`. When `resolve` is `False`: skip resolution and screening
   entirely — syntax + canonicalise only. Return the canonical URL. Comment:
   `# issue #4: add-time SSRF screen for adlist hosts (best-effort — Pi-hole re-resolves on fetch)`.
4. **#8 + #4** `app/routes/api.py`: replace `_adlist_url` with `_adlist_key(url) -> str`
   (canonical match key: lower scheme+host, keep port/path/query, strip a trailing path
   `/`). Change the validators: `AdlistAddInput.address` → `return screen_adlist_url(value)`
   (the resolving screen); `AdlistUpdateInput.address` and `AdlistDeleteInput.address` →
   `return screen_adlist_url(value, resolve=False)` (syntax + canonicalise only — see the
   #4 Approach note; disabling/deleting fetches nothing and must not depend on live DNS).
   Import `screen_adlist_url` + `URLPolicyError` from `app.url_policy`; a raised
   `URLPolicyError` surfaces as `422` via pydantic. In `_find_adlist`, match on
   `_adlist_key(item.address) == _adlist_key(address)`. In `adlist_add`, submit
   `payload.address` directly (the validator already returned the canonical screened
   form — do not call `screen_adlist_url` again). Comment at `_find_adlist`:
   `# issue #8: match on a canonical key so a Pi-hole-normalised URL still resolves`.
5. **#9** `app/routes/api.py::_connection`: call `client.test_connection()` first and set
   status from it; wrap `_version_info(client.get_versions())` in
   `try/except PiholeAPIError: versions = None`; when `versions is None`, append
   `"; version details unavailable"` to the detail. Comment:
   `# issue #9: a transient /api/info/version error must not flip a healthy instance to "error"`.
6. **#2** `app/config.py::Instance.__post_init__`:
   `try: self.base_url = normalize_instance_url(self.base_url) except URLPolicyError:
   self.base_url = self.base_url.strip()`. Import `URLPolicyError`. Comment:
   `# issue #2: a non-canonical stored base_url must not blow up load_instances() for every route`.
7. **#10b** `app/routes/api.py`: `import concurrent.futures`, `import os`. Add
   `_FANOUT_WORKERS = max(1, int(os.environ.get("FILTERHELM_FANOUT_WORKERS", "8")))`
   and `def _fanout(items, worker): ... ` using a request-scoped
   `ThreadPoolExecutor(max_workers=min(_FANOUT_WORKERS, len(items) or 1))`, preserving
   input order, `shutdown(wait=True)` before returning. Comment:
   `# issue #10: per-instance checks are independent reads — run them concurrently, not in a 15s-each sequence`.
8. `app/routes/api.py`: rewrite `instances_list`, `status_list`, `adlists_list`,
   `dns_records_list` to build their per-instance result via `_fanout(load_instances(),
   worker)`. Keep result assembly (row dicts, `errors`, sort order, `gravity_pending`)
   identical. Leave `blocking_update` and `adlist_add` sequential; add a comment on each:
   `# sequential on purpose: mutation fan-out (ARCHITECT.md invariant #1)`.
9. **#6/#7** `app/routes/api.py`: add `_canonical_addr(text)` and `_host_entry(raw)`;
   extend `_parse_dns_hosts` rows with `entry_ip`, `entry_hosts`, `raw`. Add
   `_rewrite_without(entry_raw, drop_host) -> str | None` (returns the original IP token +
   remaining hosts, or `None` when the line would be empty). Add
   `_partial_dns_failure(instance, event, *, removed, added) -> NoReturn` that calls
   `record(instance, event, {"dns.hosts": removed}, {"dns.hosts": added})` and raises
   `HTTPException(502, ...)` whose detail lists each entry in `removed` verbatim with
   "re-add it in the Pi-hole Local DNS settings". Comment:
   `# issue #12: a rollback failure must be reported with the exact lost lines, never masked as a generic 502/503`.
10. `app/routes/api.py::dns_record_add`: duplicate check becomes
    `any(r["entry_ip"] == _canonical_addr(payload.address) and hostname in r["entry_hosts"]
    for r in rows)`. Comment: `# issue #6: structural match, not reconstructed-string equality`.
11. `app/routes/api.py::dns_record_delete`: find the owning row structurally. `replacement
    = _rewrite_without(owner["raw"], hostname)`. Then:
    `client.delete_dns_host(owner["raw"])`; if `replacement is not None`, `try:
    client.add_dns_host(replacement) except Exception: try: client.add_dns_host(owner["raw"])
    except Exception: _partial_dns_failure(instance, "dns_record_delete_orphaned",
    removed=[owner["raw"]], added=[]); raise` (the bare `raise` re-raises the *first*
    add-replacement failure so a successful restore still surfaces as a normal error).
    Success audit / response use `owner["raw"]`. Comment:
    `# issue #7: rewrite the whole dns.hosts line so sibling hostnames survive`.
12. `app/routes/api.py::dns_record_update`: resolve old/new rows structurally.
    `old_replacement = _rewrite_without(old_owner["raw"], old_hostname)`. Sequence:
    `client.add_dns_host(new_record)`; then
    `try: client.delete_dns_host(old_owner["raw"])
     except Exception:
         try: client.delete_dns_host(new_record)
         except Exception: _partial_dns_failure(instance, "dns_record_update_orphaned",
             removed=[], added=[new_record])
         raise`;
    then `if old_replacement is not None:
        try: client.add_dns_host(old_replacement)
        except Exception:
            try:
                client.add_dns_host(old_owner["raw"]); client.delete_dns_host(new_record)
            except Exception: _partial_dns_failure(instance, "dns_record_update_orphaned",
                removed=[old_owner["raw"]], added=[new_record])
            raise`.
    Success audit uses `old_owner["raw"]` (not `old_record`). Comment:
    `# issue #12: keep the audit log truthful when rollback can't complete`.
13. **#12b** `app/routes/api.py`: `app_password: str = Field(min_length=1,
    max_length=512)` on `InstanceInput`; `app_password: str | None = Field(default=None,
    min_length=1, max_length=512)` on `InstanceUpdate`. Comment: `# issue #12`.
14. **#12a** `app/http_hardening.py::RequestBodyLimitMiddleware.__call__`: wrap `send` in
    a `guarded_send` that sets `nonlocal response_started = True` on
    `http.response.start`; pass `guarded_send` to `self.app`. In the
    `except _BodyTooLarge:` handler, `if response_started: raise` else `await
    self._reject(...)`. Comment: `# issue #12: a second http.response.start is an ASGI protocol error — let the server abort instead`.
15. **#1** `app/auth.py::initialize()`: after the `executescript`, run
    `with contextlib.suppress(sqlite3.OperationalError):
    database.execute("ALTER TABLE login_attempts ADD COLUMN username TEXT")`. Comment:
    `# issue #1: existing DBs predate the username column; login_attempts rows are ephemeral`.
16. `app/auth.py`: `login_allowed(self, remote_addr, username, now=None)` (`username` now
    required, positional before the keyword-ish `now`) — prune `login_attempts` by the
    15-min window, then `count = SELECT COUNT(*) ... WHERE username = ?`; `return count <
    5`. **Do not** also count `remote_addr` — see the #1 Approach note. `authenticate(self,
    username, password, remote_addr)` — call `self.login_allowed(remote_addr, username,
    now)`; the failure `INSERT` writes `(remote_addr, username, now)`; the success cleanup
    runs `DELETE FROM login_attempts WHERE username = ?`. Update the `INSERT` column list
    and the `CREATE TABLE login_attempts` in `initialize()` to include `username TEXT`.
    Comment: `# issue #1: gate on username — the client IP is the shared proxy's, so an IP gate here locks out every account`.
17. `app/routes/auth.py::login`: `if not manager.login_allowed(remote_addr,
    payload.username): ...` (add the `username` arg — this is the only external caller);
    the `manager.authenticate(payload.username, payload.password, remote_addr)` call is
    unchanged.
18. **#10a test isolation** `tests/conftest.py` (new):
    `import pytest; from app.url_policy import get_public_allowlist` +
    `@pytest.fixture(autouse=True) def _clear_allowlist_cache(): get_public_allowlist.cache_clear(); yield; get_public_allowlist.cache_clear()`.
19. `README.md`: update "Outbound instance policy" (adlist URLs now screened, best-effort)
    and the auth / rate-limit paragraphs (per-username lockout + residual targeted
    lockout); add `FILTERHELM_FANOUT_WORKERS` (default 8) near the other env vars and
    note the four list endpoints query instances concurrently.
20. Add the tests below; `./.venv/bin/pytest`. No `frontend/` change → no
    `npm run build`, `app/static/ui/` untouched.

## Contracts

**API response shapes — unchanged.** `instances_list`, `status_list`, `adlists_list`,
`dns_records_list`, `instance_test` return the same JSON; `_connection`'s `versions` was
already `PiHoleVersions | null` in `frontend/src/api.ts` (line 33). No `api.ts` edit.

**New status codes / rejections:**

- `POST /api/adlists` — now `422` when the adlist host resolves to a prohibited or
  non-allowlisted address (was: syntactic checks only). `PATCH` / `DELETE
  /api/adlists/{name}` do **not** resolve — syntax + canonicalise only — so a list with a
  dead domain stays manageable. Same `URLPolicyError` → pydantic `422` path used for
  instance URLs.
- `POST /api/instances`, `PATCH /api/instances/{name}` — `422` when `app_password`
  exceeds 512 chars (was: only the 1 MB body cap).
- `PATCH /api/dns/records` and `DELETE /api/dns/records` — new `502` with the exact lost
  `dns.hosts` line(s) in `detail` on any rollback-compensation failure (was: raw
  transport error / no audit / possible silent sibling loss). Paired with a
  `dns_record_update_orphaned` / `dns_record_delete_orphaned` audit event.

**New env vars:**

- `FILTERHELM_FANOUT_WORKERS` — int, default `8`, clamped `>= 1`. Upper bound on the
  per-request thread pool for the four read-only list endpoints.

**DB change:** `login_attempts` gains `username TEXT` (nullable). Additive; applied by a
guarded `ALTER TABLE` in `AuthManager.initialize()`. Rows are pruned on a 15-minute
window, so no backfill.

**New internal API:** `app.url_policy.get_public_allowlist` (cached; `.cache_clear()` in
tests), `app.url_policy.screen_adlist_url`, `app.url_policy._screen_addresses`
(module-private), `app.routes.api._fanout`.

## Tests

- `tests/test_url_policy.py`
  - `get_public_allowlist` returns the same object twice and reflects a changed env only
    after `cache_clear()`.
  - `screen_adlist_url("https://Example.COM/list.txt")` → canonical
    `"https://example.com/list.txt"` with a stub resolver returning a public allowlisted
    IP; unchanged path/query preserved.
  - `screen_adlist_url` with a stub resolver returning `169.254.169.254` → `URLPolicyError`;
    returning loopback → `URLPolicyError`; returning RFC1918 → allowed.
  - `screen_adlist_url` rejects embedded creds, non-http scheme, control chars (parity
    with the old `_adlist_url`) — both `resolve=True` and `resolve=False`.
  - `screen_adlist_url(..., resolve=False)` returns the canonical URL **without** calling
    the resolver at all (pass a resolver that raises if called) — and does not reject a
    host that would fail the SSRF screen.
- `tests/test_config.py`
  - `load_instances()` with an `instances.yml` entry `base_url: "http://192.168.1.2/admin"`
    (and `" http://pi.hole "`) returns without raising; the entry keeps its raw value.
  - A valid entry alongside a bad one still loads and is usable.
- `tests/test_api.py`
  - `GET /api/instances` with a stubbed slow `PiholeClient` (each call sleeps) completes
    in roughly one leg's time, not N legs' — assert wall-clock `< 2 ×` single-leg with 3
    instances (or assert the fake client saw concurrent entry via a barrier).
  - `_connection`: fake client where `get_versions()` raises `PiholeAPIError` but
    `test_connection()` succeeds → row `status == "success"`, `versions is None`, detail
    ends with "version details unavailable" (#9).
  - DNS: fake config tree with `dns.hosts: ["192.168.1.1 Server.LAN"]` —
    `DELETE /api/dns/records` for `192.168.1.1 / server.lan` → `200`, client saw
    `delete_dns_host("192.168.1.1 Server.LAN")` (#6).
  - DNS: `dns.hosts: ["192.168.1.5 alpha beta"]` — `DELETE` of `alpha` → `200`, client
    saw `delete_dns_host("192.168.1.5 alpha beta")` then
    `add_dns_host("192.168.1.5 beta")`; `beta` row still listed afterwards (#7).
  - DNS: `PATCH /api/dns/records` where delete-old **and** the compensating
    `delete_dns_host(new_record)` both fail → `502` whose detail mentions `new_record`,
    plus a `dns_record_update_orphaned` audit call (#12c).
  - DNS: `DELETE /api/dns/records` on a multi-host line where the trimmed-line re-add
    **and** the restore of the original line both fail → `502` whose detail contains the
    original `"192.168.1.5 alpha beta"`, plus a `dns_record_delete_orphaned` audit call
    (#12c — adversarial-review finding: no generic `_api_error` may mask a rollback loss).
  - DNS: `DELETE` on a multi-host line where the trimmed-line re-add fails once but the
    restore of the original line succeeds → the original error surfaces (normal `5xx`),
    `dns.hosts` is back to `["192.168.1.5 alpha beta"]`, **no** orphan audit.
  - Adlists: fake instance list returns address `"https://example.com/ads"`;
    `PATCH /api/adlists/{name}` with body `"https://example.com/ads/"` (trailing slash) →
    `200` (canonical key match, #8).
  - Adlists: `POST /api/adlists` with an address whose stub host resolves to
    `169.254.169.254` → `422` (#4).
  - Adlists: `PATCH` and `DELETE /api/adlists/{name}` succeed for a list whose host does
    **not** resolve (real `screen_adlist_url`, resolver stubbed to raise `OSError`) — the
    canonical-key match still finds the entry (#8 regression guard for the `resolve=False`
    path).
  - `POST /api/instances` with a 600-char `app_password` → `422` (#12b).
- `tests/test_auth.py`
  - Two usernames from the **same** `remote_addr`: 5 bad `authenticate("alice", "wrong",
    addr)` → `login_allowed(addr, "alice") is False` while `login_allowed(addr, "bob") is
    True` (#1 — a shared proxy IP no longer locks other accounts).
  - **Rewrite** `test_login_attempts_are_throttled` for the new signature + semantics: 5
    bad `authenticate("admin", "wrong", "192.0.2.1")` → `login_allowed("192.0.2.1",
    "admin") is False` (per-username gate; `login_allowed` now requires `username`). The
    old per-IP-only assertion (`login_allowed("192.0.2.1")`) is removed.
  - A pre-existing `auth.db` whose `login_attempts` table lacks the `username` column:
    `AuthManager(...)` initialises and `authenticate(...)` / `login_allowed(...)` work
    (guarded `ALTER TABLE`, #1).
- `tests/test_http_hardening.py`
  - An app that emits `http.response.start` then a body larger than the cap: the
    middleware does **not** send a second start; the connection errors out /
    `_BodyTooLarge` propagates (assert via a capturing ASGI harness) (#12a).
  - The existing "reject before response" `413` path still works.

## Invariants at risk

(ARCHITECT.md § "Project invariants")

1. **One instance mutated at a time.** `_fanout` is applied *only* to the four read-only
   list endpoints. `blocking_update` and `adlist_add` — the endpoints that write to
   Pi-hole — stay sequential `for` loops, with an inline comment saying why. Each fan-out
   worker reads one instance. Kept intact.
2. **Diff before write.** DNS mutations still read `dns.hosts` and match structurally
   before any PUT/DELETE; the whole-line rewrite is computed from the current entry.
   Adlist mutations still `get_adlists()` first and match by canonical key. Kept.
3. **Gravity gated behind real changes.** `mark_gravity_pending` is still called only on
   a successful adlist add/update/delete; canonicalisation changes matching, not the
   gate. Kept.
4. **No background scheduler.** `get_public_allowlist` is a lazy `lru_cache`, no timer.
   `_fanout`'s `ThreadPoolExecutor` is created per request and `shutdown(wait=True)` before
   the response returns — no thread outlives the request, no periodic work. Kept.
5. **FastAPI owns credentials; browser talks only to `/api`.** No new outbound of
   secrets. Per-username lockout keeps usernames server-side (already in the audit log).
   Kept.
6. **All outbound requests go through `app/url_policy.py`.** *Strengthened*: adlist URLs
   are screened by `screen_adlist_url` (same `_screen_addresses` core as instance URLs)
   at **add** time — the point a new fetch target is introduced. Disable/delete only
   canonicalise (they trigger no fetch). The allowlist is still sourced from
   `PublicAllowlist.from_environment()` — just cached.
7. **Every route behind auth middleware.** No new routes; `/api/auth/login` path handling
   unchanged.
8. **Compiled frontend committed.** No `frontend/` change; response shapes unchanged;
   `app/static/ui/` not rebuilt.
9. **`main` is production.** Branch cut from `main` after 0001 merges; one plan → one
   branch → one merge after Accept.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including the new tests.
- [ ] Plan 0001 is merged to `main` and this branch is based on that `main`.
- [ ] `instances.yml` containing `base_url: "http://192.168.1.2/admin"` no longer 500s
      `GET /api/instances`; that instance shows `status: "error"` and can be deleted.
- [ ] With 3 instances and an artificially slow Pi-hole stub, `GET /api/instances`
      returns in ≈ one round-trip, not three.
- [ ] A DNS record stored by Pi-hole as `192.168.1.1 Server.LAN` can be deleted through
      the app; a `192.168.1.5 alpha beta` line can have `alpha` removed with `beta`
      surviving.
- [ ] An adlist that Pi-hole stored with a trailing slash can be enabled/disabled/deleted.
- [ ] An adlist whose domain no longer resolves can still be disabled/deleted (no `422`).
- [ ] `POST /api/adlists` with `address: http://169.254.169.254/x` → `422`.
- [ ] Behind one source IP, 5 bad logins for `alice` do not block `bob` from logging in.
- [ ] `git diff main...HEAD` is limited to the Files list (+ this plan).
- [ ] No `frontend/` changes; `app/static/ui/` untouched.
- [ ] `README.md` reflects per-username lockout, adlist screening, and
      `FILTERHELM_FANOUT_WORKERS`.
- [ ] Issues #1, #2, #4, #6, #7, #8, #9, #10, #12 are referenced by an inline comment at
      each fix site and closed on merge; #11 closed by 0001.
