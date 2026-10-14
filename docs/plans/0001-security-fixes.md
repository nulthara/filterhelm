# 0001 — Security fixes: file-permission races, latent authz, same-origin ports, outbound pinning

Status: shipped
Branch: feature/0001-security-fixes
Author: Kyrnveil
Date: 2026-09-03

## Problem

A full read of the security-relevant code (`app/security.py`, `app/auth.py`,
`app/auth_middleware.py`, `app/url_policy.py`, `app/http_hardening.py`, `app/config.py`,
`app/pihole_client.py`, `app/routes/`, `app/audit.py`), the tests, and the README /
`docs/design.md` security sections found five issues worth resolving now. The app is a
credential-holding proxy that stores each Pi-hole's plaintext app password and is the
only client of the Pi-hole API, and `main` auto-deploys to production — so credential
exposure at rest, corruption of the config files every request reads, and latent
auth/same-origin gaps all matter.

Several other weaknesses (process-local rate limiter, proxy-IP rate-key collapse) are
already documented in the README as accepted trade-offs and are deliberately left as-is.

## Scope

In:
- **Fix 1** — atomic, private-from-creation writes for `secrets.yml`, `instances.yml`,
  `pending-gravity.yml`; private-from-creation `auth.db`; a write lock around instance +
  credential mutation.
- **Fix 2** — `AuthenticationMiddleware` public-path check no longer matches `/apple`,
  `/app-config`, etc.; mirror the fix in the rate limiter's static-asset exemption.
- **Fix 3** — `SameOriginMiddleware` normalises a redundant default port before comparing
  authorities.
- **Fix 4** — the Pi-hole HTTP client connects to the address validated by
  `app/url_policy.py` and keeps the hostname for TLS/SNI + `Host` (closes the documented
  DNS-rebinding / TOCTOU gap for the API client).
- **Fix 5** — instance `base_url` runs the full destination policy (DNS resolution +
  SSRF checks) at save time, not only at connect time.

Out:
- Trusted-proxy `X-Forwarded-For` client-IP resolution (README recommends proxy-level
  login limits for that topology — unchanged).
- Adlist-URL SSRF guard, `audit.log` rotation, rate-limiter/auth middleware ordering.

## Approach

Each fix is local and independent. The one shared refactor is in `app/url_policy.py`:
`validate_instance_destination` currently returns only the normalised URL string; Fix 4
and Fix 5 both need the validated resolved addresses, so add
`resolve_instance_destination(...) -> ValidatedDestination` and keep
`validate_instance_destination` as a thin wrapper returning `.url` (so
`tests/test_url_policy.py` and any other callers are unchanged).

Outbound pinning uses a `httpx.HTTPTransport` subclass that rewrites the request URL to
the validated IP, sets the `Host` header to the original authority, and sets
`request.extensions["sni_hostname"]` to the original hostname — on the pinned
httpx 0.28.1 / httpcore 1.0.9 that drives both TLS SNI and certificate verification.
No fallback to unvalidated resolution: a connect failure surfaces as `PiholeAPIError`
like any other transport error.

## Files

- `app/config.py` — add `_atomic_write` helper + `_instances_lock` / `instances_mutation()`
  context manager; route `save_instances`, `_save_pending_gravity`,
  `CredentialStore._save_fallback` through it; drop their post-hoc `os.chmod`.
- `app/auth.py` — `initialize()` creates `auth.db` with `os.open(..., O_CREAT|O_EXCL, 0o600)`
  before `sqlite3.connect`; comment on the `-wal`/`-journal` sidecars vs the `0700`
  parent-dir backstop.
- `app/auth_middleware.py` — tighten the `public` path test.
- `app/http_hardening.py` — `RateLimitMiddleware` static-asset exemption: `path == "/app"
  or path.startswith("/app/")`.
- `app/security.py` — `_authority` takes a known `scheme`; strip the default port
  (`80`/`443`) before comparing; pass `scope["scheme"]` for the Host side.
- `app/url_policy.py` — `ValidatedDestination` dataclass + `resolve_instance_destination`;
  `validate_instance_destination` becomes a wrapper.
- `app/pihole_client.py` — `_PinnedTransport`; `PiholeClient.__init__` resolves once and
  builds the client with that transport.
- `app/routes/api.py` — `instance_save` / `instance_update` call
  `resolve_instance_destination` and map `URLPolicyError` → `HTTPException(422)`.
- `README.md` — update the "Outbound instance policy" paragraph (TOCTOU caveat closed for
  the API client).
- `tests/` — see Tests.

## Steps

1. `app/config.py`: add `import tempfile`, `import contextlib`. Add `_atomic_write(path,
   render, *, mode=0o600)` — `tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
   suffix=".tmp")`, `os.fchmod`, write via `os.fdopen`, `fh.flush()` + `os.fsync`,
   `os.replace(tmp, path)`, and on any `BaseException` unlink the temp and re-raise.
2. `app/config.py`: rewrite `save_instances`, `_save_pending_gravity`,
   `CredentialStore._save_fallback` to call `_atomic_write(path, lambda fh:
   yaml.safe_dump(data, fh, sort_keys=False))`; remove their now-redundant `os.chmod`
   blocks. `_ensure_data_dir()` is unchanged.
3. `app/config.py`: add `_instances_lock = threading.Lock()` and
   `@contextlib.contextmanager def instances_mutation()` that holds it. (Keep the existing
   `_pending_gravity_lock` as-is.)
4. `app/routes/api.py`: wrap the load → modify → save body of `instance_save`,
   `instance_delete`, `instance_update` in `with instances_mutation():`, including the
   `credentials.set` / `credentials.delete` / `clear_gravity_pending` calls.
5. `app/auth.py`: in `initialize()`, before the `sqlite3.connect` in the `with` block,
   `with contextlib.suppress(FileExistsError, OSError): os.close(os.open(self.database,
   os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600))`. Keep the trailing best-effort
   `os.chmod` for pre-existing DBs. Add `import contextlib`. Comment: sidecar files rely
   on the `0700` data dir.
6. `app/auth_middleware.py`: `public = path in {"/", "/app", "/api/auth/login",
   "/api/auth/status"} or path.startswith("/app/")`.
7. `app/http_hardening.py`: `RateLimitMiddleware.__call__` — replace
   `path.startswith("/app")` with `path == "/app" or path.startswith("/app/")`.
8. `app/security.py`: `_DEFAULT_PORTS = {"http": 80, "https": 443}`. `_authority(value,
   *, origin, scheme=None)`: for the origin side use `parsed.scheme`; for the host side
   use the passed `scheme`. After resolving `port`, set `port = None` when `port in
   (80, 443)` or `port == _DEFAULT_PORTS.get(scheme)`. In `__call__`, call
   `_authority(host_values[0], origin=False, scheme=scope.get("scheme"))` and
   `_authority(source, origin=True)`.
9. `app/url_policy.py`: add `@dataclass(frozen=True) class ValidatedDestination` with
   `url: str`, `host: str`, `port: int | None`, `addresses:
   tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]`. Add
   `resolve_instance_destination(value, *, allowlist=None, resolver=socket.getaddrinfo)`
   containing the current body of `validate_instance_destination`, returning the
   dataclass (addresses = the set it already computes). Re-implement
   `validate_instance_destination(...)` as `return resolve_instance_destination(value,
   allowlist=allowlist, resolver=resolver).url`.
10. `app/pihole_client.py`: add `class _PinnedTransport(httpx.HTTPTransport)` taking the
    `ValidatedDestination` **and `*, verify: bool`**; call
    `super().__init__(verify=verify)` (httpx ignores the `verify=` passed to
    `httpx.Client` when a custom `transport=` is supplied, so the TLS-verification
    choice must be applied here or `verify_ssl=False` instances silently keep verifying).
    Override `handle_request(request)` to set
    `request.url = request.url.copy_with(host=<ip literal>, port=<dest.port or request.url.port>)`,
    `request.headers["Host"] = <original host[:port]>`,
    `request.extensions = {**request.extensions, "sni_hostname": dest.host}`, then
    `return super().handle_request(request)`. Bracket IPv6 literals as httpx expects.
11. `app/pihole_client.py`: `PiholeClient.__init__` — `dest =
    resolve_instance_destination(base_url)`; `self.base_url = dest.url`;
    `self._client = httpx.Client(base_url=dest.url,
    transport=_PinnedTransport(dest, verify=verify_ssl),
    timeout=timeout, follow_redirects=False)`.
12. `app/routes/api.py`: import `resolve_instance_destination`, `URLPolicyError`. In
    `instance_save` and `instance_update`, before building the `Instance`, `try:
    resolve_instance_destination(payload.base_url) except URLPolicyError as exc: raise
    HTTPException(status_code=422, detail=str(exc))`. Comment the temporarily-unresolvable
    trade-off.
13. `README.md`: revise the "Outbound instance policy" paragraph — the client now
    connects to the validated address and pins the hostname for TLS/`Host`, so the
    DNS-rebinding/TOCTOU limitation no longer applies to the API client (adlist URLs are
    still fetched by Pi-hole itself).
14. Add the tests below; `./.venv/bin/pytest`.

## Contracts

No API request/response shape changes. New env vars: none. New internal API:
`app.url_policy.ValidatedDestination`, `app.url_policy.resolve_instance_destination`,
`app.config.instances_mutation`.

`instance_save` / `instance_update` may now return `422` (was: only `normalize_instance_url`
syntax errors from the Pydantic layer, surfaced as `422` already) when the host resolves
to a prohibited or non-allowlisted address.

## Tests

- `tests/test_config.py`
  - `secrets.yml` and `instances.yml` are mode `0o600` immediately after the first write.
  - An exception raised inside the `render` callback leaves any pre-existing file
    unchanged and removes the temp file (no `.tmp` left in the dir).
  - Two threads calling `save_instances` concurrently never produce a file that fails
    `yaml.safe_load`.
- `tests/test_auth.py`
  - A freshly created `auth.db` is mode `0o600`.
  - `middleware_status(manager, "/apple")` and `.../app-config` → `401`;
    `"/app"`, `"/app/"`, `"/app/assets/x.js"` → `204`.
- `tests/test_security.py`
  - `Host: manager.example:443` + `Origin: https://manager.example` → `404` (allowed).
  - `Host: manager.example:80` + `Origin: http://manager.example` (scheme `http`) → `404`.
  - Genuine cross-origin (`Origin: https://attacker.example`) still → `403`.
- `tests/test_pihole_client.py`
  - With a stub resolver returning a private IP, the client sends requests to that IP,
    with `Host` = original hostname and `extensions["sni_hostname"]` = hostname
    (assert via a mock transport or `httpx.MockTransport` wrapper / captured request).
  - Stub resolver returning `169.254.169.254` → `PiholeClient(...)` raises `URLPolicyError`
    before any HTTP call.
  - Stub resolver returning a public, non-allowlisted IP → `URLPolicyError`.
  - `PiholeClient(..., verify_ssl=False)` yields a transport whose SSL context has
    `verify_mode == ssl.CERT_NONE`; the default (`verify_ssl=True`) yields
    `ssl.CERT_REQUIRED` with `check_hostname` on. (Regression guard for the
    custom-transport `verify=` handling.)
- `tests/test_api.py`
  - `POST /api/instances` with `base_url` whose host resolves (stub) to a prohibited
    address → `422`; a normal RFC1918 URL → `201`.
  - `PATCH /api/instances/{name}` likewise → `422` / `200`.

## Invariants at risk

- ARCHITECT.md #5 (credentials never reach the browser) — not weakened; Fix 1 hardens
  credential storage at rest.
- ARCHITECT.md #6 (all outbound requests go through `app/url_policy.py`) — strengthened:
  Fix 4 + Fix 5 keep a single `resolve_instance_destination` entry point and add pinning.
- #1/#2/#3/#4/#8/#9 (one-instance mutation, diff-before-write, gravity gating, no
  scheduler, committed frontend build, `main` is production) — untouched. No frontend
  change, so no `app/static/ui/` rebuild.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including the new tests.
- [ ] After adding an instance via the running app, `ls -l ~/.config/filterhelm`
      shows `instances.yml`, `secrets.yml`, `auth.db` all `-rw-------`.
- [ ] `curl -si -X POST -H 'Host: 127.0.0.1:80' -H 'Origin: http://127.0.0.1' --data '{}'
      http://127.0.0.1:8420/api/auth/logout` is not a `403` from the same-origin check.
- [ ] `curl -si http://127.0.0.1:8420/app-config` → `401`.
- [ ] Adding an instance with `base_url: http://169.254.169.254` is rejected at save (422).
- [ ] `git diff main...HEAD` is limited to the files in the Files section (+ this plan).
- [ ] No frontend changes; `app/static/ui/` untouched.
