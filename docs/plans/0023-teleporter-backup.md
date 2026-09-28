# 0023 — Per-instance Teleporter backup download

Status: in-progress         <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0023-teleporter-backup
Author: Claude (architect)
Date: 2026-09-25

Revision 1 (2026-09-25): archive validation strengthened after an adversarial review.
`zipfile.is_zipfile` accepts an archive with a corrupt member, so a damaged backup would be
audited and delivered as good. `get_teleporter()` now checks the central directory, entry
and size bounds, every member's CRC, the required files, and that `pihole.toml` parses.
Changed sections: Scope, Approach, Steps 1 and 6, Tests, Acceptance criteria.

Revision 2 (2026-09-25): Codex's plan-review gate found four defects in Revision 1, and
each was reproduced before this revision.
1. `PiholeAPIError` subclasses `RuntimeError`, so the catch-all would have replaced every
   specific message with "corrupt". An explicit `except PiholeAPIError: raise` now comes
   first.
2. `ZipFile()` can raise more than `BadZipFile`. The open step now catches everything.
3. `testzip()` opens members by name, so a corrupt entry hides behind a later duplicate.
   Duplicate names are now rejected, and CRCs are checked per `ZipInfo`.
4. Only deflate bounds its decompressor's output. LZMA and bzip2 are now rejected, and so
   are encrypted members.
Changed sections: Approach, Step 1, Tests.

## Problem

Nothing in the app can export a Pi-hole's configuration. If an instance's SD card dies, or a
bad change lands through the Pi-hole CLI or another tool, the only recovery path is Pi-hole's
own web UI. Pi-hole FTL v6 already builds a full config archive at `GET /api/teleporter`. The
archive holds `pihole.toml`, the gravity config tables (groups, adlists, domains, clients),
and related files. This app holds the credentials for every instance, so it can offer a
one-click download per instance without the user opening each Pi-hole's own UI.

## Scope

In:
- `PiholeClient.get_teleporter()` fetches the archive with a size cap and verifies it is
  intact and complete before returning it.
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

**Buffer with a cap, then verify the archive.** `get_teleporter()` streams the response and
stops at 64 MiB with a `PiholeAPIError`. The live lab-pihole archive is about 27 KB. A
backup exists for disaster recovery, so a damaged or incomplete one must fail loudly now
rather than at restore time. After reading, the backend checks the archive the way a restore
would need it (Revision 1):
- the central directory opens, which rejects HTML error pages and truncated bodies;
- at most 100 entries, no duplicate names, only stored or deflated members, no encrypted
  members, and at most 256 MiB of declared uncompressed data, all checked from the central
  directory before anything is decompressed, so a zip bomb cannot tie up the server;
- every entry's CRC matches, verified by reading each `ZipInfo` in turn (Revision 2);
- `etc/pihole/pihole.toml` and `etc/pihole/gravity.db` are present, since they hold the
  settings and the groups, adlists, domains, and clients;
- `pihole.toml` decodes as UTF-8 and parses as TOML.

`is_zipfile` alone was rejected because it reads only the end-of-archive record and accepts a
zip whose members are corrupt. Opening `gravity.db` as SQLite was also rejected. It would
need a temp file, and the CRC check already proves its bytes match what Pi-hole wrote. The
required-file check has a cost. If a future Pi-hole renames those files, backups fail with a
clear 502 until this app is updated. Failing loudly is the right direction for DR. Streaming
the response straight to the browser was rejected, because verification and the audit hash
both need the whole body before anything is sent, and 64 MiB of memory is an acceptable
ceiling for a single-user tool.

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
     length passes `TELEPORTER_MAX_BYTES`. After the loop, it calls
     `_check_teleporter_archive(bytes(data))` and returns `bytes(data)`.
   - Add module constants `TELEPORTER_MAX_ENTRIES = 100`,
     `TELEPORTER_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024`,
     `TELEPORTER_REQUIRED_MEMBERS = ("etc/pihole/pihole.toml", "etc/pihole/gravity.db")`, and
     `TELEPORTER_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})`.
     Add `import tomllib`.
   - Add module-level `_check_teleporter_archive(data: bytes) -> None`. It runs these checks
     in order, and each failure raises `PiholeAPIError` with the exact message shown.
     Steps 2 to 5 read only the central directory and decompress nothing.
     1. **Open.** `archive = zipfile.ZipFile(io.BytesIO(data))` in its own `try` with
        `except Exception` → "Teleporter response was not a zip archive". Opening can raise
        `BadZipFile`, `UnicodeDecodeError`, `NotImplementedError`, and others, and every
        one of them means the bytes are not a usable archive.
     2. **Entry count.** `len(infolist()) > TELEPORTER_MAX_ENTRIES` →
        "Teleporter archive has too many entries".
     3. **Unique names.** `len(namelist()) != len(set(namelist()))` →
        "Teleporter archive has duplicate entries". Name lookups resolve to the last
        duplicate, so an earlier entry could hide from the checks below and a restore could
        read either copy.
     4. **Member format.** Any `info.compress_type not in TELEPORTER_COMPRESSION` →
        "Teleporter archive uses unsupported compression". Any `info.flag_bits & 0x1` →
        "Teleporter archive is encrypted". Only deflate passes a maximum output length to
        its decompressor in `ZipExtFile._read1`, so LZMA and bzip2 members could expand
        without bound before truncation. lab-pihole's archive uses only stored and deflated
        members (confirmed 2026-09-25).
     5. **Declared size.** `sum(info.file_size for info in infolist()) >
        TELEPORTER_MAX_UNCOMPRESSED_BYTES` → "Teleporter archive is too large to verify".
        With step 4 in force, `ZipExtFile` stops each member's output at its `file_size`,
        so this sum bounds step 6.
     6. **CRCs.** For every `info` in `infolist()`, not by name, open `archive.open(info)`
        and read it in 1 MiB chunks to EOF. `ZipExtFile` raises `BadZipFile` on a CRC
        mismatch at EOF. Keep a running byte count across all members and raise "Teleporter
        archive is too large to verify" if it passes the cap. This replaces `testzip()`,
        which opens members by filename.
     7. **Required files.** Any name in `TELEPORTER_REQUIRED_MEMBERS` missing from
        `namelist()` → "Teleporter archive is missing a required file". The message stays
        fixed so no archive-supplied text reaches the response.
     8. **Config parses.** `tomllib.loads(archive.read("etc/pihole/pihole.toml").decode("utf-8"))`.
        Its own inner `try` turns `UnicodeDecodeError` or `tomllib.TOMLDecodeError` into
        "Teleporter archive has an unreadable pihole.toml".

     Steps 2 to 8 sit in one `try` with two handlers, in this order:
     ```python
     except PiholeAPIError:
         raise
     except Exception as exc:
         raise PiholeAPIError("Teleporter archive is corrupt") from exc
     ```
     The first handler matters, because `PiholeAPIError` subclasses `RuntimeError` and the
     catch-all would otherwise replace every specific message above with "corrupt". The
     catch-all is deliberately broad. This function's job is to turn any failure into a
     rejection, and a list of exception types proved incomplete in Revision 1. A malformed
     archive can therefore only surface as `PiholeAPIError`, never as a 500. The route
     needs no change, because `_api_error` already maps `PiholeAPIError` to 502 and the
     check runs before `record()`.
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
     memory only, capped at 64 MiB. Before delivery the backend verifies every member's CRC
     and confirms that `pihole.toml` and `gravity.db` are present and that `pihole.toml`
     parses, so a damaged or incomplete archive returns 502. Each download is audited as
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
  502  {"detail": "<PiholeAPIError message>"}     # non-200, oversize, not a zip, corrupt or incomplete archive, auth failure
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
Add a test helper
`teleporter_zip(members: dict[str, bytes] | None = None, compression: int = zipfile.ZIP_DEFLATED) -> bytes`.
It builds an archive with that compression, with members defaulting to `etc/pihole/pihole.toml` holding
`b'[dns]\nupstreams = ["1.1.1.1"]\n'` and `etc/pihole/gravity.db` holding `b"sqlite"`.
- `test_get_teleporter_returns_zip_bytes` — a 200 response carrying `teleporter_zip()`
  comes back byte-identical; the request is `GET /api/teleporter` with `X-FTL-SID`.
- `test_get_teleporter_rejects_non_200` — a 401 raises `PiholeAPIError` with `status_code == 401`.
Every rejection test asserts the exact message, with
`str(caught.value) == "<message>"`, not a substring match. Revision 1's handler order would
have turned each specific message into "corrupt", and exact assertions catch that. Tests
marked (direct) call `pihole_client._check_teleporter_archive(data)` without HTTP. The rest
go through `get_teleporter()` over MockTransport.
- `test_get_teleporter_rejects_non_zip` — a 200 HTML body →
  "Teleporter response was not a zip archive".
- `test_get_teleporter_rejects_truncated_zip` (direct) — `teleporter_zip()[:-10]` loses the
  end-of-central-directory record → "Teleporter response was not a zip archive".
- `test_check_teleporter_archive_maps_open_errors` (direct) — parametrized over
  `UnicodeDecodeError("utf-8", b"", 0, 1, "bad")` and `NotImplementedError()`. Build the
  data first, then `monkeypatch.setattr(pihole_client.zipfile, "ZipFile", <raiser>)` →
  "Teleporter response was not a zip archive".
- `test_get_teleporter_enforces_size_cap` — with `TELEPORTER_MAX_BYTES` monkeypatched to
  16, a 200 body of 32 bytes → "Teleporter archive exceeds the size limit".
- `test_get_teleporter_rejects_corrupt_member` — take
  `teleporter_zip(compression=zipfile.ZIP_STORED)`, find the `pihole.toml` payload with
  `bytes.find`, flip one byte → "Teleporter archive is corrupt". This is the adversarial
  review's reproduction.
- `test_check_teleporter_archive_rejects_duplicate_names` (direct) — a `ZIP_STORED` archive
  holding both required members plus `etc/pihole/pihole.toml` a second time. The first copy
  holds a marker payload with one byte flipped, and the second copy is valid. Write it under
  `pytest.warns(UserWarning)`, because `zipfile` warns on a duplicate name →
  "Teleporter archive has duplicate entries". `testzip()` returns `None` for this archive
  (confirmed 2026-09-25), so this test fails against a `testzip`-based implementation.
- `test_check_teleporter_archive_rejects_unsupported_compression` (direct) — parametrized
  over `ZIP_LZMA` and `ZIP_BZIP2` via `teleporter_zip(compression=...)` →
  "Teleporter archive uses unsupported compression".
- `test_check_teleporter_archive_rejects_encrypted_member` (direct) — `writestr` clears the
  encryption flag, so patch the bytes. In `teleporter_zip()`, OR `0x1` into the byte at
  `data.find(b"PK\x01\x02") + 8` (central directory flags) and at
  `data.find(b"PK\x03\x04") + 6` (local header flags) → "Teleporter archive is encrypted".
  Opening such a member raises `RuntimeError` (confirmed 2026-09-25), so the pre-check has to
  fire first.
- `test_get_teleporter_rejects_too_many_entries` (direct) — with `TELEPORTER_MAX_ENTRIES`
  monkeypatched to 2, an archive with the two required members plus one more →
  "Teleporter archive has too many entries".
- `test_get_teleporter_rejects_oversize_uncompressed` (direct) — with
  `TELEPORTER_MAX_UNCOMPRESSED_BYTES` monkeypatched to 16, the default archive (about 37
  uncompressed bytes) → "Teleporter archive is too large to verify".
- `test_get_teleporter_requires_members` (direct) — parametrized over each name in
  `TELEPORTER_REQUIRED_MEMBERS`. An archive without that member →
  "Teleporter archive is missing a required file".
- `test_get_teleporter_rejects_unparseable_toml` (direct) — parametrized over `pihole.toml`
  holding `b"[dns\n"` and holding `b"\xff\xfe"` →
  "Teleporter archive has an unreadable pihole.toml".

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
- [ ] Live: `get_teleporter()` against `lab-pihole` still returns the archive after
      Revision 1's checks. Its known contents are `pihole.toml`, `hosts`, `dhcp.leases`,
      `gravity.db`, and `pihole-FTL.db`, about 27 KB.
- [ ] No path in `_check_teleporter_archive` can raise anything but `PiholeAPIError`, as
      each Revision 1 test shows.
- [ ] Live: with the app's stored password for `lab-pihole` temporarily wrong, Backup shows
      an error toast and no file is saved.
- [ ] `docs/design.md` §5.1, `README.md`, and `docs/potential-features.md` updated as in Step 6.
