# 0050 — Security refresh, FTL 6.9 compatibility, release 1.0.5

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0050-security-refresh-and-ftl-6-9-compat
Author: Claude (architect)
Date: 2026-11-13

## Problem

A security sweep on 2026-11-13 found one new advisory and some patch drift. Pi-hole also
shipped FTL v6.9 and Core v6.6 on 2026-11-11, and one FTL behaviour change degrades the
Instances health panel.

- **Astro GHSA-r3h5-4585-7x3w** (moderate) affects `astro` 5.14.3–7.4.6. It reaches only the
  `@astrojs/node` image endpoint. The site is static and has no `@astrojs/node`, so it is not
  exploitable, but `npm audit` in `site/` fails until the bump lands.
- **FTL 6.9 `/api/info/system`** now returns an error instead of zeros when load averages
  cannot be read. `_connection` treats any system-info failure as fatal for the whole health
  block, so the FTL, database and temperature rows disappear with it.
- **Patch drift.** The base image digest, antd, vite, typescript-eslint and three transitive
  Python packages have newer releases that pass the 24-hour gate.

Audit results:

| Layer | Result |
| --- | --- |
| `pip-audit` runtime + dev locks | No known vulnerabilities |
| Base image `python:3.14-slim-trixie` | Tag moved `cd89c30e…` → `9aaa4740…` (built 2026-11-05). Same CPython 3.14.10. Diff is apt/libapt-pkg 3.0.3 → 3.0.4 and tzdata 2026e → 2026f |
| `site/` `npm audit` | 1 moderate, astro GHSA-r3h5-4585-7x3w |
| `frontend/` `npm audit` | 7 moderate, all minimatch GHSA-g3hv-75wg-m249 via typescript-eslint (accepted in 0049) |
| GitHub Actions | No advisories on any pinned action |

### FTL v6.9 / Core v6.6 compatibility review

Release notes from `pi-hole/FTL` v6.9 and `pi-hole/pi-hole` v6.6, checked against
`app/pihole_client.py` and its callers.

| FTL/Core change | FilterHelm impact |
| --- | --- |
| Version 6.9 | None. `_version_info` checks only `SUPPORTED_FTL_MAJOR = 6` |
| `/api/info/system` errors when load is unreadable | **Health block blanks.** Fixed in this plan |
| `GET /api/auth/sessions`, `DELETE /api/auth/session/{id}` 403 for app sessions without `app_sudo` | None. FilterHelm calls neither; logout uses `DELETE /api/auth` |
| `dns.cnameRecords` rejects invalid names and out-of-range TTLs | None. `_cname_record` already requires `_HOSTNAME_RE` names and TTL 0–604800 |
| Regex: `{,n}` means `{0,n}`; deep approximate nesting and back-references in unbounded repetition rejected | None to sync. `_validate_regex_domain` defers validity to FTL, and diffs compare strings. A newly rejected pattern fails per instance with `POST /api/domains failed with HTTP 400` |
| Partial search returns ABP-style list entries | None. `search_domain` accepts any `str` gravity domain |
| `pihole -g` via API stopped when the client stopped reading (#3335) | None. `update_gravity` drains the stream |
| Concurrent config writes lost one (#3184); `pihole.toml.lock` held up to 300 s | Fixes help. FilterHelm already writes one instance at a time |
| App-password login counted twice against the rate limit (#3334) | Fix helps. FilterHelm authenticates with app passwords |
| `DELETE /api/dhcp/leases/{ip}` 503; `webserver.acl` matching; >64 headers refused | None. Not used |
| Core v6.6 (installer symlink fixes, `gravity.sh` SQL escaping, debug-log redaction) | None. Host-side only |

Self-hosters should upgrade Pi-hole for FTL GHSA-pj9m-9623-2g75 and the Core symlink
advisories. That needs both FTL v6.9 and Core v6.6, and it is their action, not ours.

## Scope

In:
- Site: `astro` 7.4.5 → 7.4.11, `@astrojs/starlight` 0.42.8 → 0.42.11.
- Frontend lockfile: `antd` 6.6.8 → 6.7.0, `vite` 7.3.7 → 7.3.8, `typescript-eslint`
  8.72.1 → 8.72.2 (all within existing `package.json` ranges). Rebuild `app/static/ui/`.
- Runtime lock: `click` 8.5.0 → 8.5.1, `annotated-doc` 0.0.5 → 0.0.6,
  `typing-inspection` 0.4.4 → 0.4.5. Regenerate hashes.
- Dockerfile base digest `cd89c30e…` → `9aaa4740…`.
- Health: a `/api/info/system` failure leaves FTL, database and temperature rows intact,
  nulls the host fields, and raises status to at least `warning` with a reason.
- Move `formatDuration`/`formatBytes` out of `InstancesPage.tsx` into a new
  `frontend/src/health.ts` with a testable row builder.
- Update `docs/design.md` and `README.md` health text.
- Carry forward the minimatch risk acceptance from 0049.
- Bump the app version to 1.0.5.

Out:
- `astro` 7.4.12 (published 2026-11-13 19:02 UTC, inside the 24-hour gate). 7.4.11 already
  carries the fix.
- `argon2-cffi-bindings` 26.2.0 (published 2026-11-13 07:15 UTC, inside the gate). No advisory.
- Pydantic 2.14, FastAPI 0.162, Starlette 1.10, `more-itertools` 11.2, `pycparser` 3.12.
  Same reasoning as 0049. Each minor needs its own plan.
- Major frontend tooling (ESLint 10, Vite 8, TypeScript 7, `@vitejs/plugin-react` 6,
  `globals` 17, `eslint-plugin-react-refresh` 0.5).
- GitHub Actions bumps (`setup-python` 7.1.0, `login-action` 4.8.0). No advisories.
- Isolating `get_ftl_info` and `get_database_info` failures. FTL 6.9 did not change those,
  and they stay fatal for the health block.
- Any change to `HealthStatus` values, the badge, or the auto-expand rule.
- Upgrading `lab-pihole`, tagging `v1.0.5`, and pushing the GitHub mirror. The user does these.

## Approach

### Dependencies

Follow the AGENTS.md "Dependency updates" procedure, as in 0049.

| Target | Published (UTC) | Notes |
| --- | --- | --- |
| astro 7.4.11 | 2026-11-11 17:35 | past the 7.4.6 advisory ceiling; `engines.node` stays `>=22.23.4` |
| @astrojs/starlight 0.42.11 | 2026-11-12 14:04 | peer `astro ^7.2.10` |
| antd 6.7.0 | 2026-11-04 03:59 | minor within `^6`, ships in the bundle |
| vite 7.3.8 | 2026-11-09 11:01 | patch, build tool |
| typescript-eslint 8.72.2 | 2026-11-09 17:08 | patch, dev only; still pulls minimatch `^10.2.2` |
| click 8.5.1 | 2026-11-06 17:36 | transitive via uvicorn |
| annotated-doc 0.0.6 | 2026-11-10 18:17 | transitive via fastapi |
| typing-inspection 0.4.5 | 2026-11-04 14:13 | needs `typing-extensions>=4.15.0`; lock has 4.16.0 |
| base image `9aaa4740…` | built 2026-11-05 07:58 | apt 3.0.4, tzdata 2026f |

The minimatch acceptance from 0049 still holds. The advisory range is now `>=7.0.0 <11.0.1`,
so ESLint's minimatch 3.1.5 is no longer flagged. `npm audit fix` only moves
typescript-eslint to 8.72.2, which still depends on minimatch `^10.2.2`. No release of
`@typescript-eslint/typescript-estree` accepts minimatch 11. The findings stay dev-only.

### Health

Treat host system metrics as optional, as sensors already are.

- `_connection` calls `get_system_info()` in its own `try`. On `PiholeAPIError` it keeps
  `system = None` and continues to `get_ftl_info()`, `get_database_info()` and
  `get_sensors()`. Call order and the single session stay the same.
- `_health_status(system: SystemInfo | None, sensors)` skips the RAM, swap and load checks
  when `system` is `None`. It appends the reason `"Host metrics unavailable"` and sets
  `warning`. The temperature check still runs. A critical temperature still wins.
- `_health_info(system: SystemInfo | None, ...)` emits `host_uptime`, `memory`, `swap` and
  `cpu` as `None` together when `system` is `None`.
- The frontend renders each null host field as `Unavailable`. A row with missing host metrics
  is `warning`, so it auto-expands and the existing `Alert` shows the reason.

`instances_list` returns `dict[str, Any]` through `_fanout` with no `response_model`. That is
the existing fan-out GET precedent, so this plan adds no Pydantic model.

Rejected alternatives:
- Nesting the host fields under one `system` object. That reshapes a stable contract to
  avoid four nullable fields.
- A new `unknown` health status. It touches the badge maps, the expand rule and the docs
  table for a rare case, and `warning` with a reason already says "look here".
- Reporting `healthy` when host metrics are missing. It would claim RAM, swap and load are
  fine when nobody checked.

## Files

- `site/package.json` — astro and starlight pins.
- `site/package-lock.json` — regenerated.
- `frontend/package-lock.json` — antd, vite, typescript-eslint trees.
- `frontend/package.json` — `version` only (Step 9).
- `requirements.lock` — click, annotated-doc, typing-inspection; regenerated hashes.
- `Dockerfile` — base image digest.
- `app/routes/api.py` — `_connection`, `_health_status`, `_health_info`.
- `tests/test_api.py` — update two health tests, add three.
- `frontend/src/api.ts` — nullable host fields on `InstanceHealth`.
- `frontend/src/health.ts` — new. `formatDuration`, `formatBytes`, `healthDetailRows`.
- `frontend/src/health.test.ts` — new. Row builder tests.
- `frontend/src/pages/InstancesPage.tsx` — use `health.ts`; drop the local formatters.
- `docs/design.md` — Health bullets.
- `README.md` — Instances health paragraph.
- Version files touched by `scripts/bump_version.py patch` (`app/__init__.py`,
  `frontend/package.json`, `frontend/package-lock.json`, as in 0049).
- `app/static/ui/` — rebuilt bundle.

## Steps

0. **Network.** `npm` and `scripts/lock_hashes.py` need `registry.npmjs.org` and `pypi.org`.
   If a network step fails, stop and report.
1. **Site.** In `site/package.json`, set `"astro": "7.4.11"` and
   `"@astrojs/starlight": "0.42.11"`. Run `cd site && npm install && npm run build && npm audit`.
   The build must pass and `npm audit` must report 0 vulnerabilities.
2. **Frontend deps.** `cd frontend && npm update antd vite typescript-eslint`.
   `npm ls antd vite typescript-eslint` must show 6.7.0, 7.3.8 and 8.72.2.
   `frontend/package.json` must not change in this step.
3. **Runtime lock.** In `requirements.lock`, change `annotated-doc==0.0.5 \` →
   `annotated-doc==0.0.6 \`, `click==8.5.0 \` → `click==8.5.1 \`, and
   `typing-inspection==0.4.4 \` → `typing-inspection==0.4.5 \`. Run
   `python3 scripts/lock_hashes.py`. Only those three blocks may change.
4. **Dockerfile.** Replace the digest on line 2 with
   `sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f`. Keep the tag.
5. **Backend health.** In `app/routes/api.py`:
   - `_health_status(system: SystemInfo | None, sensors: SensorsInfo | None)`. Run the
     temperature block first, unchanged. If `system is None`, set `warning = True`, append
     `"Host metrics unavailable"`, and skip the RAM, swap and load blocks. Otherwise run them
     unchanged. Reason order is temperature, then host.
   - `_health_info(system: SystemInfo | None, ftl, database, sensors)`. When `system` is
     `None`, emit `"host_uptime": None, "memory": None, "swap": None, "cpu": None`. All
     other keys are unchanged.
   - `_connection`, inside `if health:`:
     ```python
     try:
         system = client.get_system_info()
     except PiholeAPIError:
         system = None
     try:
         ftl = client.get_ftl_info()
         database = client.get_database_info()
         ...sensors block unchanged...
         health_info = _health_info(system, ftl, database, sensors)
     except PiholeAPIError:
         health_info = None
     ```
     Transport errors still propagate to the outer handler as before.
6. **Frontend types.** In `frontend/src/api.ts`, change `InstanceHealth` to
   `host_uptime: number | null`, `memory: HealthMemory | null`, `swap: HealthSwap | null`,
   `cpu: HealthCpu | null`.
7. **Frontend helpers.** Create `frontend/src/health.ts`:
   - Move `formatDuration(seconds: number): string` and `formatBytes(bytes: number): string`
     verbatim from `InstancesPage.tsx` and export them.
   - Export `interface HealthDetailRow { key: string; label: string; value: string }`.
   - Export `healthDetailRows(health: InstanceHealth): HealthDetailRow[]`. It returns the
     same twelve rows, keys, labels, order and formatting as today's `items` array in
     `HealthDetails`. `host_uptime`, `load`, `cpu`, `ram`, `ram_available` and `swap` read
     `Unavailable` when their source field is `null`. `temperature` keeps its existing
     `Unavailable` text.
8. **InstancesPage.** In `HealthDetails`, build `items` as
   `healthDetailRows(health).map(({ key, label, value }) => ({ key, label, children: value }))`.
   Delete the local `formatDuration` and `formatBytes`. Keep `className="health-details"`, the
   `Alert`, and `Descriptions` props unchanged.
9. **Version.** `./.venv/bin/python scripts/bump_version.py patch` must apply `1.0.5` and
   touch only its version files.
10. **Docs.** `docs/design.md` Health bullet "A failed health read leaves the instance
    reachable and marks Health unavailable. Sensors are optional." becomes "A failed FTL or
    database read leaves the instance reachable and marks Health unavailable. Sensors and
    host metrics are optional. Missing host metrics show as unavailable and raise the status
    to warning." In `README.md` § Page behavior, add one sentence after the threshold
    table: "If `/api/info/system` fails, the host rows read Unavailable and the status is at
    least Warning."
11. **Venv and checks.** `./.venv/bin/pip install --uploaded-prior-to P1D --require-hashes -r requirements.lock -r requirements-dev.lock`,
    `./.venv/bin/pip check`, both `pip-audit` runs,
    `cd frontend && npm run build && npm run lint && npm test`, `./.venv/bin/pytest`.
    Commit the rebuilt `app/static/ui/`.

## Contracts

`GET /api/instances?health=true` keeps its shape. Four `health` fields become nullable, and
are null together exactly when `/api/info/system` failed. `status` gains no new value.

Backend (`_health_info` dict, no Pydantic model):

```python
{
    "status": "healthy" | "warning" | "critical",
    "reasons": list[str],               # may include "Host metrics unavailable"
    "host_uptime": float | None,
    "ftl_uptime": int,
    "memory": {"total": int, "used": int, "available": int, "percent": float} | None,
    "swap": {"total": int, "used": int, "percent": float} | None,
    "cpu": {"cores": int, "percent": float, "load": [float, float, float]} | None,
    "temperature": {...} | None,        # unchanged
    "ftl": {...},                       # unchanged
    "database": {...},                  # unchanged
}
```

Frontend (`frontend/src/api.ts`):

```ts
export interface InstanceHealth {
  status: HealthStatus
  reasons: string[]
  host_uptime: number | null
  ftl_uptime: number
  memory: HealthMemory | null
  swap: HealthSwap | null
  cpu: HealthCpu | null
  temperature: HealthTemperature | null
  ftl: HealthFtl
  database: HealthDatabase
}
```

`GET /api/update` reports `current: "1.0.5"` after the bump. No config key or DB change.

## Tests

Backend, `tests/test_api.py`:

- `test_health_failure_does_not_hide_healthy_connection` — rename to
  `test_system_info_failure_keeps_other_health_rows`. With `get_system_info` raising: `status`
  `success`, `status_detail` `Reachable`, `health['host_uptime']`, `memory`, `swap`, `cpu` all
  `None`, `health['ftl_uptime'] == 432620`, `health['database']['size'] == 460140544`,
  temperature present, `info_calls == ['system', 'ftl', 'database', 'sensors']`.
- `test_health_and_version_failures_both_append_detail` — change the failing health reader
  from `get_system_info` to `get_ftl_info`, so the assertions (health `None`, both detail
  suffixes) still hold.
- `test_health_status_without_system_warns` — new. `_health_status(None, None)` returns
  `("warning", ["Host metrics unavailable"])`.
- `test_health_status_without_system_keeps_temperature` — new. `_health_status(None,
  SensorsInfo(61.0, 60.0, 'C'))` returns `critical` with the temperature reason first and
  `"Host metrics unavailable"` second.
- `test_health_info_without_system_nulls_host_fields` — new. `_health_info(None, ftl,
  database, None)` has exactly the existing key set, with the four host keys `None` and the
  others populated.
- `test_required_health_reader_failure_is_isolated` — unchanged. `get_ftl_info` and
  `get_database_info` failures still yield `health is None`.

Frontend, `frontend/src/health.test.ts` (vitest):

- Full health object → twelve rows in today's order. Spot-check `host_uptime` (`15d 0h` for
  1296249), `ram` and `sqlite` values.
- Health with the four host fields `null` → `host_uptime`, `load`, `cpu`, `ram`,
  `ram_available`, `swap` read `Unavailable`. `ftl_uptime`, `ftl_cpu` and `database` stay
  formatted.
- `formatBytes(0)` → `0 B`; `formatBytes(1536)` → `1.5 KiB`. `formatDuration(59)` → `0m`.

Existing tests that must pass unchanged: `tests/test_frontend_tables.py`,
`tests/test_supply_chain.py`, `test_versions_agree`, `tests/test_repo_hygiene.py`,
`tests/test_container.py`.

## Invariants at risk

- **5. FastAPI owns credentials.** The health change adds no field from Pi-hole beyond what
  already flows. No URL or credential enters the payload. The existing
  `'secret' not in repr(response)` assertion stays.
- **7. Auth middleware.** No new route. `/api/instances` keeps its middleware.
- **1–4. Mutation rules.** The health path stays read-only, one session per instance, same
  call order. No scheduler.
- **6. `url_policy`.** No new outbound URL. The dependency bumps sit below httpx and change
  no call sites.
- **8. Compiled frontend committed.** antd, vite and the `InstancesPage` change alter the
  bundle. Step 11 rebuilds and commits `app/static/ui/`. `site/dist/` stays uncommitted.
- **9. `main` is production.** Work stays on the feature branch. The merge redeploys 1.0.5.

## Acceptance criteria

- [ ] `git diff main --stat` lists only files in Files.
- [ ] `site/package.json` pins astro 7.4.11 and starlight 0.42.11.
- [ ] `cd site && npm ci && npm run build` succeeds; `npm audit` reports 0 vulnerabilities.
- [ ] `frontend/package.json` changes only in `version`; `npm ls` shows antd 6.7.0,
      vite 7.3.8, typescript-eslint 8.72.2.
- [ ] `cd frontend && npm audit` shows only the accepted minimatch GHSA-g3hv-75wg-m249 chain.
- [ ] `requirements.lock` pins annotated-doc 0.0.6, click 8.5.1, typing-inspection 0.4.5;
      no other block changes.
- [ ] `Dockerfile` pins `python:3.14-slim-trixie@sha256:9aaa4740…802f`.
- [ ] Both `pip-audit -r … --disable-pip --no-deps` runs report no known vulnerabilities;
      `./.venv/bin/pip check` clean.
- [ ] `./.venv/bin/python -c "import app; print(app.__version__)"` prints `1.0.5`.
- [ ] `./.venv/bin/pytest` and `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` + `npm test` clean; `app/static/ui/` committed.
- [ ] `InstancesPage.tsx` no longer defines `formatDuration` or `formatBytes`.
- [ ] Reviewer only: `podman build -t filterhelm:0050 .` succeeds. In the image,
      `python -c "from importlib.metadata import version as v; print(v('click'), v('typing-inspection'))"`
      prints `8.5.1 0.4.5`. The container starts and `GET /api/auth/status` answers 200.
- [ ] Reviewer only: the Instances page renders lab-pihole health with every row filled and
      a visual check of antd 6.7.0 on Instances, Domains and Settings shows no regressions.
      If lab-pihole runs FTL 6.9 by then, record its version from the Versions column.
- [ ] After merge (user): upgrade Pi-hole instances to FTL v6.9 / Core v6.6 for
      GHSA-pj9m-9623-2g75 and the Core symlink advisories.
