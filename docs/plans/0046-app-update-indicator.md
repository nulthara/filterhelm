# 0046 — App update indicator

Status: in-progress        <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0046-app-update-indicator
Author: Kyrnveil
Date: 2026-10-25

## Problem

FilterHelm shows its own version in the header but never learns that a newer one exists.
Operators on a pinned image tag find out about releases only by visiting GitHub. A small
up-arrow beside the header version, shown only when a newer GitHub Release exists, closes
that gap without changing how anyone deploys.

## Scope

In:
- A backend check of `GET https://api.github.com/repos/nulthara/filterhelm/releases/latest`.
- The check runs lazily when the browser asks for it, and the backend caches the result in memory.
- `GET /api/update`, behind the auth middleware.
- An up-arrow icon beside the header version link. It shows only when a newer release exists
  and links to that release page.
- `FILTERHELM_UPDATE_CHECK=off` disables the check. The check is on by default.
- A URL-policy function that resolves and screens the fixed GitHub API destination.
- Docs for the outbound call and the opt-out in `README.md`, `docs/deploy.md` and the site
  install guide.

Out:
- Git tags or GHCR tags as the source. Only published, non-draft, non-prerelease GitHub
  Releases count.
- Any background, periodic, or startup check. The first `/api/update` request after the
  cache expires triggers the fetch.
- Self-update, pull, or redeploy actions.
- HTTP proxy support for the outbound call. The client sets `trust_env=False`, matching the
  pinned-transport model.
- A GitHub token or any authenticated GitHub call.
- Release notes or changelog text in the UI.
- Pi-hole update detection, which already exists on the Instances page.

## Approach

The backend makes the call, and the browser never contacts GitHub. The CSP has
`connect-src 'self'`, and invariant 5 keeps all outbound access in FastAPI. A backend call
also sends one request per server instead of one per browser.

The check reads `/releases/latest`, per the user's decision. That endpoint already excludes
drafts and prereleases, and the parser rejects either flag anyway. Today it returns
`v1.0.0` while tags reach `v1.0.2`. The indicator therefore tracks hand-published Releases,
which is intended.

The destination is a constant, but invariant 6 still applies. `url_policy` gains
`resolve_update_check_destination()`. It resolves `api.github.com`, then rejects any answer
that is prohibited (loopback, link-local, metadata, multicast, unspecified, reserved) or
private. A GitHub API answer that resolves to a LAN address points to DNS tampering, so the
check refuses it. The allowlist does not apply here. The client connects through the
existing `_PinnedTransport` from `app/pihole_client.py`, so the checked address is the one
dialed, and TLS still verifies against `api.github.com`.

The cache lives in `app/update_check.py` as a module-level value behind a `threading.Lock`.
A success stays fresh for 6 hours and a failure for 15 minutes. Only one request refreshes
at a time, and no other request waits for it. A caller acquires the lock without blocking.
If another request holds the lock, the caller returns the last cached value at once, or
"no update known" on a cold cache. The cost is that a page load during a refresh can miss
the arrow until the next load.

The `httpx` timeout does not cover DNS, because `socket.getaddrinfo` takes no timeout.
`update_check` therefore passes `resolve_update_check_destination()` a bounded resolver.
That resolver runs `getaddrinfo` on a one-worker executor and waits at most
`TIMEOUT_SECONDS`. A lookup that runs past the limit raises `OSError`, which the policy
function turns into `URLPolicyError`, so the failure gets cached. The timed-out lookup
thread runs on until the OS resolver gives up. The one-worker executor and the 15-minute
failure cache keep that to one stray thread at a time. That thread only serves a read the
user started, so it is not a scheduler.

Revision 1 (review of the first pass): The first version held the lock across the whole
fetch, including an unbounded DNS lookup. A DNS outage made every concurrent `/api/update`
call wait on the lock while it occupied a FastAPI worker thread. Enough of them would stall
unrelated routes.

Every failure degrades to "no update known". Failures include DNS, policy rejection,
timeout, non-200, bad JSON, and a malformed `tag_name`. The route never errors, and the
frontend hides the icon. The server logs a fixed warning naming the exception class, never
the response body.

Versions compare as `(major, minor, patch)` integer tuples parsed by
`^v?(\d+)\.(\d+)\.(\d+)$`. If either side fails to parse, the result is "no update".
FilterHelm releases are plain `X.Y.Z`, so the plan adds no semver library.

`/api/update` stays separate from `/api/version`. `/api/version` stays offline and instant,
and a slow GitHub cannot delay the header version.

Rejected alternatives:
- The browser fetching GitHub directly. That breaks CSP and invariant 5, and it exposes every
  viewer's IP.
- A startup or periodic background check. That runs against the spirit of invariant 4 and
  adds a scheduler for one optional fetch.
- Git tags as the source. The user chose Releases.
- Folding the result into `/api/version`. That couples a network call to a value the header
  needs immediately.

## Files

- `app/url_policy.py` — add `UPDATE_CHECK_ORIGIN` and `resolve_update_check_destination()`.
- `app/update_check.py` — new. Env toggle, fetch, parse, compare, cache.
- `app/routes/api.py` — `UpdateInfo` model and `GET /api/update`.
- `frontend/src/api.ts` — `UpdateInfo` interface and `api.update()`.
- `frontend/src/App.tsx` — fetch update info and render the arrow beside the version.
- `frontend/src/styles.css` — `.app-update` style.
- `app/static/ui/` — rebuilt bundle.
- `README.md` — document the outbound update check and `FILTERHELM_UPDATE_CHECK`.
- `docs/deploy.md` — commented `FILTERHELM_UPDATE_CHECK=off` line in the env sample.
- `site/src/content/docs/guides/install-options.mdx` — one sentence on the check and the opt-out.
- `tests/test_url_policy.py` — tests for the new resolver.
- `tests/test_update_check.py` — new. Module behaviour.
- `tests/test_api.py` — route shape.
- `tests/test_auth.py` — route requires auth.

## Steps

1. **URL policy.** In `app/url_policy.py` add:
   ```python
   UPDATE_CHECK_ORIGIN = "https://api.github.com"

   def resolve_update_check_destination(*, resolver=socket.getaddrinfo) -> ValidatedDestination:
   ```
   - Normalize `UPDATE_CHECK_ORIGIN` with `normalize_instance_url`.
   - Resolve with `resolver(host, 443, type=socket.SOCK_STREAM)`. On `OSError`, raise
     `URLPolicyError("Update check host could not be resolved")`. On an empty answer, raise
     `URLPolicyError("Update check host did not resolve to an address")`.
   - For each address, after `_effective_address`, raise
     `URLPolicyError(f"Update check host resolves to non-public address {address}")` if
     `_prohibited(address) or _default_private(address)`.
   - Return `ValidatedDestination(normalized, host, None, tuple(sorted(addresses, key=str)))`.
   - Do not consult `PublicAllowlist`.

2. **Update module.** Create `app/update_check.py`:
   - Constants:
     - `UPDATE_CHECK_ENV = "FILTERHELM_UPDATE_CHECK"`
     - `RELEASES_LATEST_PATH = "/repos/nulthara/filterhelm/releases/latest"`
     - `SUCCESS_TTL_SECONDS = 6 * 3600`
     - `FAILURE_TTL_SECONDS = 15 * 60`
     - `TIMEOUT_SECONDS = 5.0`
   - `def update_check_enabled() -> bool` — False when the env value, stripped and
     lower-cased, is one of `off`, `0`, `false`, `no`. Otherwise True, including when unset.
     Read the env on every call so tests can monkeypatch it.
   - `def parse_version(value: object) -> tuple[int, int, int] | None` — regex
     `^v?(\d+)\.(\d+)\.(\d+)$` on a `str`, else None.
   - `_RESOLVER = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="filterhelm-update-dns")`.
   - `def _bounded_resolve(host, port, *, type)` — submits
     `socket.getaddrinfo(host, port, type=type)` to `_RESOLVER` and returns
     `future.result(timeout=TIMEOUT_SECONDS)`. On `concurrent.futures.TimeoutError`, call
     `future.cancel()` and raise `OSError("update check DNS lookup timed out")`.
   - `def _open_client() -> httpx.Client` — calls
     `resolve_update_check_destination(resolver=_bounded_resolve)` and returns
     `httpx.Client(base_url=destination.url, transport=_PinnedTransport(destination, verify=True), timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False, headers={"Accept": "application/vnd.github+json", "User-Agent": f"FilterHelm/{__version__}"})`.
     Import `_PinnedTransport` from `app.pihole_client`.
   - `def fetch_latest_release() -> str | None` — opens the client in a `with` block and
     GETs `RELEASES_LATEST_PATH`. It returns `"X.Y.Z"` (no `v`) only when all of these hold:
     - status is 200
     - the body is a JSON object
     - `draft` and `prerelease` are both falsy
     - `parse_version(body["tag_name"])` succeeds

     Return None on anything else. Exceptions (`httpx.HTTPError`, `URLPolicyError`,
     `ValueError`) propagate to the caller.
   - `@dataclass(frozen=True) class UpdateStatus: enabled: bool; current: str; latest: str | None; update_available: bool`.
   - A module cache `_cache: tuple[float, str | None] | None` (expires-at monotonic time,
     latest) and `_lock = threading.Lock()`.
   - `def check_for_update(*, clock: Callable[[], float] = time.monotonic) -> UpdateStatus`:
     1. If disabled, return `UpdateStatus(False, __version__, None, False)` with no fetch.
     2. Try `_lock.acquire(blocking=False)`.
        - If it fails, another request is refreshing. Take `latest` from `_cache` if set,
          else None. Skip to step 5 without fetching.
        - If it succeeds, do steps 3–4 and release the lock in a `finally`.
     3. Reuse the cache while `clock() < expires_at`.
     4. Otherwise call `fetch_latest_release()`.
        - On a str, cache it for `SUCCESS_TTL_SECONDS`.
        - On None or any exception, cache None for `FAILURE_TTL_SECONDS`. Log
          `logger.warning("update check failed: %s", type(exc).__name__)` for exceptions.
     5. Set `update_available` to True only when both versions parse and latest > current.
   - Read `_cache` into a local once per call. Assigning the tuple is atomic, so the
     lock-free read in step 2 sees either the old or the new tuple.
   - `def reset_cache() -> None` — clears `_cache`. Tests use it.

3. **Route.** In `app/routes/api.py`, next to `VersionInfo`:
   ```python
   class UpdateInfo(BaseModel):
       enabled: bool
       current: str
       latest: str | None
       update_available: bool

   @router.get("/update", response_model=UpdateInfo)
   def update() -> UpdateInfo:
       status = check_for_update()
       return UpdateInfo(enabled=status.enabled, current=status.current,
                         latest=status.latest, update_available=status.update_available)
   ```
   Keep it a sync `def` so FastAPI runs it in the threadpool. Do not touch
   `auth_middleware.py`, so the route stays authenticated.

4. **API client.** In `frontend/src/api.ts` add the `UpdateInfo` interface (Contracts) and
   `update: (signal?: AbortSignal) => request<UpdateInfo>('/api/update', { signal })` beside
   `version`.

5. **Header.** In `frontend/src/App.tsx`:
   - Add `const [update, setUpdate] = useState<UpdateInfo>()`.
   - Add a separate `useEffect` keyed on `auth?.authenticated`, shaped like the version
     effect. It uses an `AbortController`, calls `api.update(controller.signal)`, ignores
     errors, and aborts on cleanup. Keep it separate so a slow check cannot delay the version.
   - Import `ArrowUpOutlined` from `@ant-design/icons`.
   - Directly after the version link, render this when
     `update?.update_available && update.latest`:
     ```tsx
     <Tooltip title={`FilterHelm ${update.latest} is available`}>
       <Typography.Link className="app-update" href={releaseUrl(update.latest)}
         target="_blank" rel="noopener noreferrer"
         aria-label={`FilterHelm ${update.latest} is available`}>
         <ArrowUpOutlined />
       </Typography.Link>
     </Tooltip>
     ```
     Bind the JSX to a named variable (`updateIndicator`) per AGENTS.md.

6. **Style.** In `frontend/src/styles.css`, after `.app-version`, add:
   `.app-update.ant-typography { font-size: 14px; color: var(--fh-cyan); }`.
   Cyan on the navy header clears WCAG AA for a non-text icon. Do not add a theme-dependent
   override, because the header is navy in both modes.

7. **Docs.**
   - `README.md`: after § "Outbound instance policy", add a "### Update check" subsection.
     State three things:
     - On each new cache window, FilterHelm asks GitHub's API for the latest published
       release. It sends no instance data, and the User-Agent carries the running version.
     - The result is cached for 6 hours, or 15 minutes after a failure.
     - `FILTERHELM_UPDATE_CHECK=off` disables the call.
   - `docs/deploy.md`: in the `/etc/filterhelm/env` sample, add
     `# Optional. Set to off to stop the GitHub release check.` and
     `# FILTERHELM_UPDATE_CHECK=off`.
   - `site/src/content/docs/guides/install-options.mdx`: add one sentence near the
     environment-variable guidance. It says the header shows an arrow when a newer release
     exists, and that `FILTERHELM_UPDATE_CHECK=off` disables the check.

8. **Build.** `cd frontend && npm run build && npm run lint && npm test`. Commit
   `app/static/ui/`. Then `cd site && npm ci && npm run build`.

## Contracts

`GET /api/update`. Auth required. No request body.

Backend (`app/routes/api.py`):
```python
class UpdateInfo(BaseModel):
    enabled: bool
    current: str
    latest: str | None
    update_available: bool
```

Frontend (`frontend/src/api.ts`):
```ts
export interface UpdateInfo {
  enabled: boolean
  current: string
  latest: string | null
  update_available: boolean
}
```

Example responses:
- Newer release: `{"enabled": true, "current": "1.0.2", "latest": "1.1.0", "update_available": true}`.
- Disabled: `{"enabled": false, "current": "1.0.2", "latest": null, "update_available": false}`.
- Check failed: `{"enabled": true, "current": "1.0.2", "latest": null, "update_available": false}`.

Config key: env var `FILTERHELM_UPDATE_CHECK`. Unset or anything else means on. `off`,
`0`, `false`, `no` (case-insensitive) mean off. No DB or YAML change.

## Tests

`tests/test_url_policy.py`:
- `test_update_check_destination_accepts_public_answers` — a resolver returning a public
  IPv4 and IPv6 address yields `url == "https://api.github.com"`, `host == "api.github.com"`,
  and both addresses.
- `test_update_check_destination_rejects_non_public_answers` — parametrized over
  `127.0.0.1`, `169.254.169.254`, `10.0.0.5`, `192.168.1.2`, `fd00::1`, `::ffff:10.0.0.1`.
  Each raises `URLPolicyError`, including when mixed with one public address.
- `test_update_check_destination_ignores_public_allowlist` — with
  `FILTERHELM_PUBLIC_ALLOWLIST=10.0.0.0/8`, a `10.0.0.5` answer is still rejected.
- `test_update_check_destination_resolution_failure` — a resolver raising `OSError` gives
  `URLPolicyError`.

`tests/test_update_check.py`. An autouse fixture calls `reset_cache()` before and after each
test, and tests pin `update_check.__version__` with monkeypatch.
- `test_disabled_values_skip_fetch` — parametrized over `off`, `OFF`, `0`, `false`, `no`.
  `fetch_latest_release` is patched to fail the test if called. The result is
  `enabled=False, latest=None, update_available=False`.
- `test_unset_or_other_value_is_enabled` — unset and `on` both fetch.
- `test_newer_release_flags_update` — current `1.0.2`, fetched `1.1.0`, so
  `update_available` is True.
- `test_same_or_older_release_does_not_flag` — fetched `1.0.2` and `0.9.9` both give False.
- `test_unparseable_current_version_never_flags` — current `1.0.2.dev1`, fetched `9.0.0`,
  so False.
- `test_success_is_cached_for_six_hours` — a fake clock and a counting fetch. Calls at
  t=0 and t=6h−1 fetch once. A call at t=6h fetches again.
- `test_failure_is_cached_for_fifteen_minutes` — the fetch raises `httpx.ConnectError`.
  The result has `latest=None`. Calls at t=0 and t=15m−1 fetch once. A call at t=15m fetches
  again. The warning log contains `ConnectError` and not the exception message.
- `test_fetch_parses_release_and_sends_headers` — patch `_open_client` to return an
  `httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(...))`
  with the module's headers. Assert:
  - the request path is `RELEASES_LATEST_PATH`
  - `Accept` is `application/vnd.github+json`
  - `User-Agent` starts with `FilterHelm/`
  - `{"tag_name": "v1.1.0", "draft": false, "prerelease": false}` returns `"1.1.0"`
- `test_fetch_rejects_bad_payloads` — parametrized. Each case returns None:
  - a 404 response
  - a 302 response with `Location`, which is not followed
  - a JSON list
  - `tag_name` missing
  - `tag_name: "nightly"`
  - `prerelease: true`
  - `draft: true`
- `test_open_client_uses_pinned_policy_transport` — patch
  `update_check.resolve_update_check_destination` with a fake that records its `resolver`
  keyword and returns a known destination. Assert the recorded resolver is
  `update_check._bounded_resolve`. Assert the client's transport is a `_PinnedTransport`
  with that destination, `follow_redirects` is False, and `trust_env` is False. The
  first-pass `lambda: destination` patch no longer fits the new call, so replace it.
- `test_concurrent_caller_does_not_wait_for_refresh` (revision 1) — patch
  `fetch_latest_release` to block on a `threading.Event`. Start `check_for_update()` in a
  thread and wait until the fetch is entered. A second `check_for_update()` call on the main
  thread returns within 1 second with `latest=None` and `update_available=False`. Then set
  the event, join the thread, and assert the fetch ran exactly once.
- `test_concurrent_caller_gets_stale_cache_during_refresh` (revision 1) — seed the cache
  with `1.1.0` at t=0. Advance the clock past `SUCCESS_TTL_SECONDS` and block the refresh as
  above. A concurrent call returns `latest="1.1.0"` without waiting.
- `test_bounded_resolve_times_out` (revision 1) — set `update_check.TIMEOUT_SECONDS` to
  0.1. Patch `update_check.socket.getaddrinfo` to block on an event. `_bounded_resolve`
  raises `OSError` within 1 second. Release the event in a `finally` so the worker thread exits.
- `test_bounded_resolve_returns_answers` (revision 1) — `getaddrinfo` is patched to return a
  fixed answer list, and `_bounded_resolve` returns that list unchanged.

`tests/test_api.py`:
- `test_update_route_returns_status` — monkeypatch `api.check_for_update` to return a fixed
  `UpdateStatus`. `api.update().model_dump()` equals the matching dict.

`tests/test_auth.py`:
- `test_update_route_requires_authentication` — `middleware_status(manager, "/api/update") == 401`.

## Invariants at risk

- **4. No background scheduler.** The check runs only inside a user's `/api/update`
  request. It adds no timer, startup hook, or task. The one-worker DNS executor runs only
  lookups that a request submitted, and it never mutates a Pi-hole.
- **5. FastAPI owns credentials and outbound access.** The browser calls only `/api/update`.
  No GitHub URL is fetched from the frontend. The bundle gains a link target, which
  `release.ts` already has, and no new connect target, so the CSP is unchanged.
- **6. Outbound requests go through `url_policy`.** The new destination resolves and screens
  through `resolve_update_check_destination()`. The client dials the checked address via
  `_PinnedTransport`, with `follow_redirects=False` and `trust_env=False`.
- **7. Auth middleware.** `/api/update` is not added to the public path set.
  `test_update_route_requires_authentication` guards this.
- **8. Compiled frontend committed.** Step 8 rebuilds and commits `app/static/ui/`.
  `site/dist/` stays uncommitted.
- **9. `main` is production.** Work lands on `feature/0046-app-update-indicator`.
- Invariants 1–3 are untouched. No Pi-hole client call or mutation path changes.

## Acceptance criteria

- [ ] With the network up and no env override, a signed-in `GET /api/update` returns
      `enabled: true` and `latest: "1.0.2"` (the current latest Release), and
      `update_available: false` while running 1.0.2.
- [ ] While one refresh is stuck in DNS or HTTP, other `/api/update` calls return
      immediately. `test_concurrent_caller_does_not_wait_for_refresh` covers this.
- [ ] A hung DNS lookup ends the refresh within `TIMEOUT_SECONDS` and caches a failure.
- [ ] With `__version__` temporarily set below the latest Release, the header shows a cyan
      up-arrow beside the version. Hovering it shows "FilterHelm X.Y.Z is available", and
      clicking it opens `https://github.com/nulthara/filterhelm/releases/tag/vX.Y.Z` in a
      new tab.
- [ ] With `FILTERHELM_UPDATE_CHECK=off`, `/api/update` returns `enabled: false`, the server
      makes no outbound request, and no arrow renders.
- [ ] With outbound network blocked, `/api/update` returns 200 with `latest: null`, the
      header shows no arrow and no error toast, and the log line names only the exception class.
- [ ] Unauthenticated `GET /api/update` returns 401.
- [ ] `grep -rn "api.github.com" frontend/src app/static/ui` finds nothing.
- [ ] `./.venv/bin/pytest` green.
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green.
- [ ] `cd frontend && npm run build && npm run lint && npm test` clean, `app/static/ui/` committed.
- [ ] `cd site && npm run build` succeeds.
