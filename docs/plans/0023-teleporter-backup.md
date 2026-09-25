# 0023 — Per-instance Teleporter backup download

Status: in-progress         <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0023-teleporter-backup
Author: Claude (architect)
Date: 2026-09-25

## Problem

Nothing in the app can export a Pi-hole's configuration. If an instance's SD card dies, or a
bad change lands through the Pi-hole CLI or another tool, the only recovery path is Pi-hole's
own web UI. Pi-hole FTL v6 already builds a full config archive at `GET /api/teleporter`. The
archive holds `pihole.toml`, the gravity config tables (groups, adlists, domains, clients),
and related files. This app holds the credentials for every instance, so it can offer a
one-click download per instance without the user opening each Pi-hole's own UI.

## Scope

In:
- `PiholeClient.get_teleporter()` fetches the archive with a size cap and a zip check.
- `POST /api/instances/{name}/backup` returns the archive as a `application/zip` attachment
  with a generated filename.
- Each successful download writes one audit entry, action `backup_download`, recording the
  archive size and SHA-256 (user decision).
- A "Backup" button in each Instances table row saves the zip through the browser.
- `frontend/src/api.ts` gains a binary download helper that shares `request`'s error and
  session-expiry handling.
- Docs updated in `docs/design.md` §5.1, `README.md` (audit paragraph), and
  `docs/potential-features.md`.

Out:
- Restore or import (`POST /api/teleporter`). That call overwrites the instance's config, so
  it needs its own plan with diff-before-write thinking.
- Keeping a copy of the archive on the server (user decision). The bytes pass through memory
  only.
- A "back up all instances" button, scheduled backups, and retention.
- Inspecting or diffing archive contents.
- Frontend test tooling.

## Approach

**Proxy the archive through the backend.** The browser never talks to a Pi-hole (invariant
5), so the backend authenticates, fetches the zip, and returns the bytes. One `PiholeClient`
session per download, the same as Test.

**`POST`, not `GET`.** The download writes an audit entry, so it has a side effect.
`SameOriginMiddleware` checks only unsafe methods. The local session cookie is
`SameSite=Strict`, but in proxy mode the identity comes from the reverse proxy's own cookie,
which may be `Lax`, so a cross-site `GET` could trigger an export and an audit line. `POST`
puts the route behind the existing same-origin check. Rejected alternative: a plain
`<a href download>` link to a `GET` route. It needs no blob handling, but it loses the
same-origin check, and an error response would download as a file instead of showing a toast.

**Buffer with a cap, then check the zip.** `get_teleporter()` streams the response and stops
at 64 MiB with a `PiholeAPIError`. The import options in Pi-hole's spec list config and
table-level entries (groups, adlists, domains, clients) rather than the gravity domain table,
so real archives should be far below the cap. The live check in Acceptance records the
actual size. After reading, `zipfile.is_zipfile` must accept the bytes, so an
HTML error page or truncated body never reaches the user as a "backup". Streaming the response
straight to the browser was rejected, because the zip check and the audit hash both need the
whole body before anything is sent, and 64 MiB of memory is an acceptable ceiling for a
single-user tool.

**Audit fails closed.** `record()` runs after the archive is fetched and checked, and before
the response is returned. If `record()` raises, the route returns 503 and the archive is not
delivered. This is the opposite of `_audit_landed_write`, which swallows failures because the
write already happened. Here nothing irreversible has happened yet, so refusing costs one retry.

**The filename comes from the backend.** The route sets
`Content-Disposition: attachment; filename="pihole-<slug>-<UTC timestamp>.zip"` and ignores
whatever filename Pi-hole sends. `<slug>` is the instance name with every character outside
`[A-Za-z0-9._-]` replaced by `-`, then leading and trailing `-` and `.` stripped, with
`instance` as the fallback when nothing is left. `Cache-Control: no-store` keeps the archive
out of any cache.

**Frontend saves via an object URL.** `api.downloadBackup(name)` returns `{ blob, filename }`.
The page creates an object URL, clicks a temporary `<a download>`, and revokes the URL after
a second. The CSP needs no change, because a download is not a fetch or a subresource load.

## Files

- `app/pihole_client.py` — add `get_teleporter()`, `TELEPORTER_MAX_BYTES`, and the docstring
  endpoint entry.
- `app/routes/api.py` — add `POST /instances/{name}/backup` and `_backup_filename()`.
- `frontend/src/api.ts` — split error handling out of `request`, add `download()`,
  `Download`, and `api.downloadBackup`.
- `frontend/src/pages/InstancesPage.tsx` — add the Backup button, `backingUp` state, and
  `saveDownload()`.
- `app/static/ui/` — rebuilt frontend output.
- `tests/test_pihole_client.py` — client tests for `get_teleporter()`.
- `tests/test_api.py` — route tests, plus `get_teleporter` on `FakePiholeClient`.
- `docs/design.md` — §5.1 bullet for backups.
- `README.md` — audit paragraph names backup downloads.
- `docs/potential-features.md` — move the item to "Shipped", update "Suggested next pick".

## Steps

1. **Client method.** In `app/pihole_client.py`:
   - Add `import zipfile` and `import io`.
   - Add a module constant `TELEPORTER_MAX_BYTES = 64 * 1024 * 1024`.
   - Add `PiholeClient.get_teleporter(self) -> bytes`. It opens
     `self._client.stream("GET", "/api/teleporter", headers=self._auth_headers(), timeout=60.0)`.
     A status other than 200 raises
     `PiholeAPIError(f"GET /api/teleporter failed with HTTP {resp.status_code}", status_code=resp.status_code)`.
     It reads `resp.iter_bytes()` into a `bytearray` and raises
     `PiholeAPIError("Teleporter archive exceeds the size limit")` as soon as the running
     length passes `TELEPORTER_MAX_BYTES`. After the loop, it raises
     `PiholeAPIError("Teleporter response was not a zip archive")` unless
     `zipfile.is_zipfile(io.BytesIO(data))` is true. It returns `bytes(data)`.
   - Add a docstring block after "Instance health":
     ```
     Config backup (spec confirmed 2026-09-25 against lab-pihole's /api/docs; archive
     contents not yet round-tripped from this codebase):
       GET /api/teleporter        application/zip archive of the instance's configuration
     ```

2. **Route.** In `app/routes/api.py`:
   - Add `import hashlib` and `from datetime import datetime, timezone`, and add `Response`
     to the `fastapi` import.
   - Add `_backup_filename(name: str, now: datetime) -> str`. It returns
     `f"pihole-{slug}-{now.strftime('%Y%m%d-%H%M%SZ')}.zip"`, with `slug` built as the
     Approach describes (`re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "instance"`).
   - Add the route directly after `instance_test`:
     ```python
     @router.post("/instances/{name}/backup")
     def instance_backup(name: str) -> Response:
         instance = _instance(name)
         try:
             with _client(instance) as client:
                 archive = client.get_teleporter()
         except HTTPException:
             raise
         except Exception as exc:  # noqa: BLE001
             raise _api_error(exc) from exc
         try:
             record(name, "backup_download", {},
                    {"bytes": len(archive), "sha256": hashlib.sha256(archive).hexdigest()})
         except Exception as exc:  # noqa: BLE001 - no audit line, no archive
             logger.exception("audit write failed for backup_download on %s", name)
             raise HTTPException(503, detail="Backup not delivered: the audit log could not be written") from exc
         filename = _backup_filename(name, datetime.now(timezone.utc))
         return Response(archive, media_type="application/zip", headers={
             "Content-Disposition": f'attachment; filename="{filename}"',
             "Cache-Control": "no-store",
         })
     ```
     `except HTTPException: raise` must come first, so `_client`'s 409 "No credential stored"
     is not turned into a 503 by the catch-all. The audit call uses the module-level `record`
     so `monkeypatch.setattr(api, "record", ...)` works in tests.

3. **`api.ts` download helper.** In `frontend/src/api.ts`:
   - Extract the `!response.ok` branch of `request` into
     `async function failure(path: string, response: Response): Promise<Error>`. It keeps the
     JSON `detail` parsing, the status-text fallback, and the 401 session-expired dispatch,
     and returns the `Error`. `request` becomes `if (!response.ok) throw await failure(path, response)`.
     Behaviour of every existing call is unchanged.
   - Add the exported interface `Download { blob: Blob; filename: string }`.
   - Add `async function download(path: string, fallback: string, init?: RequestInit): Promise<Download>`.
     It calls `fetch(path, init)`, throws `await failure(path, response)` when not ok, and
     reads the filename with
     `/filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? fallback`.
     It returns `{ blob: await response.blob(), filename }`.
   - Add to `api`, next to `testInstance`:
     `downloadBackup: (name: string) => download(`/api/instances/${encodeURIComponent(name)}/backup`, `pihole-${name}-backup.zip`, { method: 'POST' })`.

4. **Instances page.** In `frontend/src/pages/InstancesPage.tsx`:
   - Import `DownloadOutlined` and `type Download`.
   - Add a module-level `saveDownload({ blob, filename }: Download): void`. It creates an
     object URL, creates an `<a>` with `href` and `download` set, calls `click()`, and
     revokes the URL in `setTimeout(..., 1000)`.
   - Add `const [backingUp, setBackingUp] = useState<string>()`, mirroring `testing`.
   - Add `backup(name: string)`. It sets `backingUp`, awaits `api.downloadBackup(name)`,
     calls `saveDownload`, and shows `message.success(`${download.filename} downloaded`)`.
     On error it shows `message.error(error instanceof Error ? error.message : 'Backup failed')`.
     `finally` clears `backingUp`. It does not reload the instance list.
   - In the Actions column, insert before Edit:
     ```tsx
     <Tooltip title="Download a Teleporter archive of this Pi-hole's configuration. It may include password hashes, so store it privately.">
       <Button icon={<DownloadOutlined />} loading={backingUp === item.name} onClick={() => void backup(item.name)}>Backup</Button>
     </Tooltip>
     ```

5. **Build.** `cd frontend && npm run build && npm run lint`, then stage `app/static/ui/`.

6. **Docs.**
   - `docs/design.md` §5.1: add a bullet after the Health bullet. Each Instances row has a
     Backup action. It fetches `GET /api/teleporter` through the backend in one session and
     downloads the zip as `pihole-<name>-<UTC timestamp>.zip`. The archive passes through
     memory only, capped at 64 MiB, and is checked to be a zip. Each download is audited as
     `backup_download` with size and SHA-256, and fails with 503 if the audit line cannot be
     written. Restore is not offered.
   - `README.md`: in the paragraph beginning "Authentication successes, failures,
     throttling", add backup downloads to the list of recorded events.
   - `docs/potential-features.md`: remove the Teleporter bullet from §2, add a "Shipped"
     line (`Per-instance Teleporter config backup download, audited — plan 0023 (design.md
     §5.1).`), write "Nothing is outstanding in this section." under §2, and replace
     "Suggested next pick" with the FTL service actions in §3.

## Contracts

Backend route, new:

```
POST /api/instances/{name}/backup
  request body: none
  200  Content-Type: application/zip
       Content-Disposition: attachment; filename="pihole-<slug>-<YYYYMMDD-HHMMSSZ>.zip"
       Cache-Control: no-store
       body: the Teleporter archive bytes, unchanged
  404  {"detail": "Instance not found"}
  409  {"detail": "No credential stored"}
  502  {"detail": "<PiholeAPIError message>"}     # non-200, oversize, not a zip, auth failure
  503  {"detail": "Unable to reach Pi-hole instance"}
  503  {"detail": "Backup not delivered: the audit log could not be written"}
```

No Pydantic response model, because the body is binary. There is no request model because
there is no body.

Audit entry, new action:

```json
{"ts": "...", "instance": "<name>", "action": "backup_download", "actor": "<user>",
 "before": {}, "after": {"bytes": 48213, "sha256": "<64 hex chars>"}}
```

`frontend/src/api.ts`, new:

```ts
export interface Download {
  blob: Blob
  filename: string
}

api.downloadBackup: (name: string) => Promise<Download>
```

Pi-hole API used (from lab-pihole's `teleporter.yaml`, fetched 2026-09-25):
`GET /api/teleporter` returns 200 with `application/zip` (binary) or 401 JSON.

No config key or DB change.

## Tests

`tests/test_pihole_client.py` (MockTransport, session set directly, the pattern at
`test_group_requests_use_name_path_segment`):
- `test_get_teleporter_returns_zip_bytes` — a 200 response carrying a real zip built with
  `zipfile.ZipFile` over `io.BytesIO` comes back byte-identical; the request is
  `GET /api/teleporter` with `X-FTL-SID`.
- `test_get_teleporter_rejects_non_200` — a 401 raises `PiholeAPIError` with `status_code == 401`.
- `test_get_teleporter_rejects_non_zip` — a 200 HTML body raises `PiholeAPIError`.
- `test_get_teleporter_enforces_size_cap` — with `TELEPORTER_MAX_BYTES` monkeypatched to
  16, a 200 body of 32 bytes raises `PiholeAPIError`.

`tests/test_api.py`. These tests call route functions directly after `setup_api(monkeypatch)`,
like the rest of the file, and assert errors with `pytest.raises(HTTPException)`. Add a
`teleporter: bytes` class attribute and `get_teleporter()` to `FakePiholeClient`, and reset
`teleporter` to a small valid zip in `setup_api`. Audit calls are captured with
`monkeypatch.setattr(api, "record", lambda *args: audits.append(args))`.
- `test_instance_backup_returns_archive_and_audits` — `api.instance_backup("pihole1")`
  returns a `Response` whose `body` is the fake's exact bytes, `media_type` is
  `application/zip`, `content-disposition` header matches
  `attachment; filename="pihole-pihole1-\d{8}-\d{6}Z\.zip"`, and `cache-control` header is
  `no-store`. Exactly one audit call is captured, `("pihole1", "backup_download", {},
  {"bytes": len(archive), "sha256": sha256(archive).hexdigest()})`.
- `test_instance_backup_unknown_instance_404` — `api.instance_backup("missing")` raises
  404; no audit call.
- `test_instance_backup_without_credential_409` — after
  `credentials.delete("pihole1")`, the call raises 409 with "No credential stored", not
  503; no audit call.
- `test_instance_backup_pihole_error_502` — a `FakePiholeClient` subclass whose
  `get_teleporter` raises `PiholeAPIError("GET /api/teleporter failed with HTTP 500")`;
  the call raises 502 with that detail; no audit call.
- `test_instance_backup_fails_closed_when_audit_fails` — `api.record` raises `OSError`;
  the call raises 503 with "Backup not delivered: the audit log could not be written", so
  no `Response` carrying the archive is returned.
- `test_instance_backup_route_is_post_only` — among `api.router.routes`, the route with path
  `/api/instances/{name}/backup` has `methods == {"POST"}`, and no other route has that
  path. Same-origin enforcement for `POST` is already covered by
  `tests/test_security.py::test_mutation_rejects_cross_origin`.
- `test_backup_filename_slugs_instance_name` — `_backup_filename("lab pihole/1", now)`
  starts with `pihole-lab-pihole-1-`, `_backup_filename("../", now)` starts with
  `pihole-instance-`, and a fixed `now` of 2026-09-25 14:03:07 UTC gives
  `...-20260925-140307Z.zip`.

## Invariants at risk

1. **One instance mutated at a time** — nothing is mutated. Each download touches one
   instance with one read session.
2. **Diff before write** — no write happens. `POST /api/teleporter` (restore) is out of scope.
3. **Gravity gated** — not touched.
4. **No background scheduler** — the download starts only from a click. No scheduled or
   startup backups.
5. **FastAPI owns credentials** — the browser calls only `/api/instances/{name}/backup`. No
   Pi-hole URL or password enters the bundle, and the app's stored app-password never
   appears in the response. The archive's `pihole.toml` may hold Pi-hole's own password hashes,
   which this feature deliberately delivers to the signed-in user as a file. That is the
   point of a backup. The tooltip says so, and the audit entry records every export.
6. **Outbound requests through `url_policy`** — `get_teleporter()` uses the existing pinned
   `PiholeClient` transport, built from `resolve_instance_destination`. No new URL.
7. **Auth middleware** — `/api/instances/{name}/backup` is not in the public path set, so it
   requires a session. `POST` also puts it behind `SameOriginMiddleware`.
8. **Compiled frontend committed** — Step 5.
9. **`main` is production** — work on `feature/0023-teleporter-backup`.

## Acceptance criteria

- [ ] `POST /api/instances/<name>/backup` exists in `app/routes/api.py`, and no `GET` route
      serves a backup.
- [ ] Every test in the Tests section exists and passes.
- [ ] `./.venv/bin/pytest` green, with no existing test changed except the added
      `FakePiholeClient` attribute and method.
- [ ] `npm run build` and `npm run lint` clean, and `app/static/ui/` committed.
- [ ] Existing `request` callers behave the same. A 401 from any call still fires the
      session-expired handler, and a 422 still shows the joined `msg` text.
- [ ] Live, against `lab-pihole`: clicking Backup saves `pihole-lab-pihole-<timestamp>.zip`.
      `unzip -l` lists a `pihole.toml` entry; record the archive size and entry list in the
      handback report. The Audit log page shows one
      `backup_download` entry for `lab-pihole` whose `sha256` matches `sha256sum` of the
      saved file.
- [ ] Live: with the app's stored password for `lab-pihole` temporarily wrong, Backup shows
      an error toast and no file is saved.
- [ ] `docs/design.md` §5.1, `README.md`, and `docs/potential-features.md` updated as in Step 6.
