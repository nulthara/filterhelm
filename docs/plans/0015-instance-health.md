# 0015 — Instance health widgets

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0015-instance-health
Author: Claude (Architect)
Date: 2026-09-13

## Problem

The Instances page shows reachability and component versions and nothing about the box
behind them. When an instance is slow, running out of memory, or has a query database
that has grown past what its disk can take, the operator has to log into the Pi-hole
admin UI or shell to see it. Pi-hole FTL already exposes host uptime, memory, load, CPU,
temperature, FTL process usage, and database size over four read-only `/api/info/*`
endpoints. Surfacing that set next to the version cell puts the health of every instance
on one screen, with the same one-session-per-instance read the page already makes.

## Scope

In:
- Four new read-only `PiholeClient` methods with validated dataclasses: `get_system_info`
  (`/api/info/system`), `get_ftl_info` (`/api/info/ftl`), `get_database_info`
  (`/api/info/database`), `get_sensors` (`/api/info/sensors`).
- `GET /api/instances?health=true` adds a `health` object per instance, read inside the
  existing connection-check session after versions. Without the flag the reads are
  skipped and `health` is `null`.
- Health read failure never flips a reachable instance to error. Sensors are optional:
  a failed sensors read leaves `temperature` null while the rest of `health` stands.
- FTL uptime is normalised from milliseconds to seconds server-side so both uptimes
  share one unit.
- Instances page: a Health column beside Versions with four compact lines and a tooltip
  holding the full set, a Refresh button in the page heading, and `health: true` on the
  page's two list calls.
- Tests for parsing, strictness, ordering, the flag, failure isolation, and credential
  hygiene. Docs: design.md §5.1, README compatibility paragraph, potential-features.
- Rebuilt `app/static/ui/`.

Out:
- FTL database counts from `/api/info/ftl` (gravity, lists, groups, domains, regex,
  clients, privacy level). Other pages already show what matters from that set.
- `/api/info/host`, `/api/info/messages`, `/api/info/metrics`, per-core or per-sensor
  temperature lists. Only the `cpu_temp` roll-up is used.
- Any timer or background poll. The page loads on open, Refresh, Test, save, and remove.
- Health in `POST /api/instances/{name}/test` or in the Settings page's list call.
- Thresholds, alerts, or colouring beyond the temperature-over-limit mark.
- Any mutation, audit entry, or new route.

## Approach

**Health rides in the instance list, opt-in.** `_connection` already opens one
`PiholeClient` per instance, checks the config read, and reads versions inside that
session. Two simultaneous authenticated calls to one Pi-hole can race at login and 401,
so a separate health route that the Instances page would call alongside the list was
rejected. Putting the reads inside the same session keeps one login per instance and
one request per page load. `SettingsPage` also calls `api.listInstances()` and only
needs names, so the reads are gated by a `health` query parameter that only the Instances
page sets. FastAPI parses `health=true` / `health=1` and rejects `health=abc` with 422 on
its own; no model is needed. The route keeps its untyped dict return. In this codebase
read-only fan-out GETs (`instances_list`, `status_list`, `lookup`, `query_log`) return
plain dicts and `response_model` is reserved for bulk mutation results and
`group_orphans`. Do not add one here.

**Failure isolation follows issue #9.** `_connection` already keeps a reachable instance
`success` when `/api/info/version` fails and appends `; version details unavailable`.
Health does the same: any `PiholeAPIError` from system, ftl, or database leaves
`health: null` and appends `; health details unavailable`. Sensors are treated as
optional because containers and VMs commonly expose no thermal zone; a sensors failure
sets `temperature` to `null` and does not touch the detail string. Transport errors are
not caught inside the session, so an instance that drops mid-read still reports `error`,
exactly as before.

**Live-confirmed on lab-pihole, 2026-09-13 (read-only GETs, nothing written).** The
first connection attempt was refused and the retry succeeded, the same transient seen
during 0014.

- `GET /api/info/system` returns `{"system": {"uptime": 1296249, "memory": {"ram":
  {"total": 2097152, "free", "used": 54137, "available": 2042507, "%used": 2.58},
  "swap": {"total": 8388604, "free", "used": 3017108, "%used": 35.97}}, "procs": 2465,
  "cpu": {"nprocs": 4, "%cpu": 6.97, "load": {"raw": [0.61, 1.33, 1.23], "percent":
  [15.2, 33.2, 30.9]}}, "ftl": {"%mem": 1.43, "%cpu": 0.025}}, "took"}`. `uptime` is
  host seconds. Memory values are kilobytes as `/proc/meminfo` reports them
  (`2097152` is exactly 2 GiB). `load.raw` is the 1/5/15-minute triple.
- `GET /api/info/ftl` returns `{"ftl": {"database": {...counts...}, "privacy_level",
  "query_frequency", "clients": {"total", "active"}, "pid": 318, "uptime":
  432620297.203082, "%mem": 1.43, "%cpu": 0.025, "allow_destructive", "dnsmasq":
  {...}}, "took"}`. `uptime` is FTL process uptime in **milliseconds** with a
  fractional part; the value above is five days while the host reports fifteen. Read as
  seconds it would be thirteen years, so the unit is settled by elimination as well as by
  FTL's `timer_elapsed_msec` source. The route converts to whole seconds.
- `GET /api/info/database` returns `{"size": 460140544, "type", "mode", "atime",
  "mtime", "ctime", "owner": {...}, "queries": 163, "earliest_timestamp",
  "queries_disk": 2636298, "earliest_timestamp_disk", "sqlite_version": "3.53.1",
  "took"}` at the top level, with no wrapper key. `size` is bytes. `queries` is the
  in-memory count and `queries_disk` the on-disk count; only the on-disk count is used.
- `GET /api/info/sensors` returns `{"sensors": {"list": [...per-chip temps...],
  "cpu_temp": 57, "hot_limit": 60, "unit": "C"}, "took"}`. `unit` follows Pi-hole's
  `webserver.api.temp.unit` and `hot_limit` its `webserver.api.temp.limit`. On hosts with
  no thermal source `cpu_temp` is absent, `null`, or FTL's `-1` sentinel; the client maps
  all three to `None`.

**Validation strictness matches the 0014 readers.** Identity and counter fields that the
page renders are required and type-checked with `_is_int` / `_is_number`; a missing or
mistyped one raises `PiholeAPIError("Pi-hole <name> endpoint returned an invalid
response")`. `load.raw` must be a list of at least three numbers and is truncated to
three. Descriptive fields (`unit`, `hot_limit`) fall back rather than fail. Percentages
pass through unrounded; the frontend formats them.

**Health column, not cards or expandable rows.** The user chose a compact column. It
mirrors the Versions column's shape: a vertical `Space` of short `Typography.Text` lines
wrapped in a `Tooltip` that lists every field. Cards were rejected because they grow
vertically with the fleet; expandable rows hide the data behind a click. The Refresh
button exists because health numbers go stale and the page had no way to reload except
Test, which also runs a config read.

**Rejected alternatives.**
- A dedicated `GET /api/instances/health` route: doubles logins per instance and needs
  the frontend to sequence two requests to avoid the login race.
- Reading health inside `POST /api/instances/{name}/test`: the page reloads the list
  right after Test anyway.
- Converting memory to bytes server-side: the frontend already needs a bytes formatter
  for database size, so it multiplies kilobytes by 1024 at render time and the contract
  keeps FTL's units.

## Files

- `app/pihole_client.py` — `SystemInfo`, `FtlInfo`, `DatabaseInfo`, `SensorsInfo`
  dataclasses; `get_system_info`, `get_ftl_info`, `get_database_info`, `get_sensors`;
  docstring inventory line for the four `/api/info/*` paths.
- `app/routes/api.py` — `_health_info`, `_connection(instance, *, health=False)` returning
  a 4-tuple, `instances_list(health: bool = False)`, `instance_test` destructuring update.
- `tests/test_pihole_client.py` — parsing and strictness tests for the four readers.
- `tests/test_api.py` — `FakePiholeClient` health fixtures and call log; flag, ordering,
  failure-isolation, sensors-optional, test-route, and credential tests; updated expected
  payload in `test_instance_api_never_returns_credentials`.
- `frontend/src/api.ts` — `InstanceHealth` and nested interfaces; `health` on `Instance`;
  `listInstances(options?)`.
- `frontend/src/pages/InstancesPage.tsx` — `formatDuration`, `formatBytes`, `HealthCell`,
  Health column, Refresh button, `health: true` on both list calls.
- `docs/design.md` — §5.1 bullet for the health column.
- `README.md` — "Pi-hole compatibility" paragraph for the Instances page.
- `docs/potential-features.md` — move the bullet to shipped, drop it from §2, point
  "Suggested next pick" at Teleporter backup.
- `app/static/ui/` — rebuilt bundle.

## Steps

1. **Client dataclasses** in `app/pihole_client.py`, frozen, next to `StatsSummary`:

   ```python
   @dataclass(frozen=True)
   class SystemInfo:
       uptime: float                 # host seconds
       ram_total: int                # kilobytes
       ram_used: int
       ram_available: int
       ram_percent: float
       swap_total: int
       swap_used: int
       swap_percent: float
       cores: int
       cpu_percent: float
       load: tuple[float, float, float]

   @dataclass(frozen=True)
   class FtlInfo:
       uptime_ms: float
       cpu_percent: float
       mem_percent: float

   @dataclass(frozen=True)
   class DatabaseInfo:
       size: int                     # bytes
       queries_disk: int
       sqlite_version: str

   @dataclass(frozen=True)
   class SensorsInfo:
       cpu_temp: float | None
       hot_limit: float | None
       unit: str
   ```

2. **Client readers**, each `GET` with `self._auth_headers()`, non-200 →
   `PiholeAPIError(f"GET /api/info/<name> failed with HTTP {status}")`, invalid body →
   `PiholeAPIError("Pi-hole <name> endpoint returned an invalid response")` from
   `(ValueError, KeyError, TypeError, IndexError)`, following `get_stats_summary`'s shape:
   - `get_system_info() -> SystemInfo`: `body["system"]`; `uptime` `_is_number`; ram
     `total`/`used`/`available` `_is_int`, `%used` `_is_number`; swap `total`/`used`
     `_is_int`, `%used` `_is_number`; `cpu.nprocs` `_is_int`, `cpu["%cpu"]` `_is_number`;
     `cpu.load.raw` a list whose first three entries are `_is_number`, stored as a
     3-tuple of floats.
   - `get_ftl_info() -> FtlInfo`: `body["ftl"]`; `uptime`, `%cpu`, `%mem` all `_is_number`.
   - `get_database_info() -> DatabaseInfo`: top-level body; `size` and `queries_disk`
     `_is_int`; `sqlite_version` `str`.
   - `get_sensors() -> SensorsInfo`: `body["sensors"]` must be a dict, else invalid.
     `cpu_temp` → float when `_is_number` and `>= 0`, else `None`. `hot_limit` → float
     when `_is_number`, else `None`. `unit` → the value when a non-empty `str`, else `"C"`.
   - Docstring inventory: add under the query-log block

     ```
     Instance health (confirmed 2026-09-13 against lab-pihole: shapes, kilobyte memory,
     millisecond FTL uptime, and sensors roll-up verified read-only):
       GET /api/info/system
       GET /api/info/ftl
       GET /api/info/database
       GET /api/info/sensors
     ```

3. **Client tests** in `tests/test_pihole_client.py`, reusing the `search_client`
   harness (Tests section).

4. **Route** in `app/routes/api.py`:
   - `_health_info(system: SystemInfo, ftl: FtlInfo, database: DatabaseInfo,
     sensors: SensorsInfo | None) -> dict[str, Any]` builds the Contracts shape.
     `ftl_uptime = int(ftl.uptime_ms // 1000)`. `temperature` is `None` when `sensors` is
     `None` or `sensors.cpu_temp` is `None`.
   - `_connection(instance: Instance, *, health: bool = False) -> tuple[Literal[...],
     str, dict[str, Any] | None, dict[str, Any] | None]`. Inside the existing `with`
     block, after the versions read:

     ```python
     health_info = None
     if health:
         try:
             system = client.get_system_info()
             ftl = client.get_ftl_info()
             database = client.get_database_info()
             try:
                 sensors = client.get_sensors()
             except PiholeAPIError:
                 sensors = None
             health_info = _health_info(system, ftl, database, sensors)
         except PiholeAPIError:
             health_info = None
     ```

     Detail assembly: `"Reachable"`, then `"; version details unavailable"` when versions
     is `None`, then `"; health details unavailable"` when `health` was requested and
     `health_info` is `None`. The incompatible-FTL early return gains the fourth element.
     The `"No credential stored"` and exception returns gain a trailing `None`.
   - `instances_list(health: bool = False)`: worker calls
     `_connection(instance, health=health)` and adds `"health": health_info` to the row.
   - `instance_test`: `status, detail, versions, _health = _connection(instance)`; the
     response keys stay `status`, `detail`, `versions`.

5. **Route tests** in `tests/test_api.py` (Tests section). Extend `FakePiholeClient` with
   class attributes `system_info`, `ftl_info`, `database_info`, `sensors_info` holding
   the dataclasses from the live shape above, and `info_calls: list[str]`; methods
   `get_system_info` / `get_ftl_info` / `get_database_info` / `get_sensors` append
   `"system"` / `"ftl"` / `"database"` / `"sensors"` and return the attribute. Reset
   `info_calls = []` in `setup_api`.

6. **`frontend/src/api.ts`**: interfaces from Contracts; `health: InstanceHealth | null`
   on `Instance`; `listInstances: (options?: { health?: boolean }) =>
   request<{ instances: Instance[] }>(options?.health ? '/api/instances?health=true' :
   '/api/instances')`.

7. **`frontend/src/pages/InstancesPage.tsx`**:
   - Module-level helpers:

     ```ts
     function formatDuration(seconds: number): string   // "15d 0h", "3h 12m", "12m"
     function formatBytes(bytes: number): string        // 1024-based, "439.0 MiB", "2.0 GiB", "512 B"
     ```

     `formatDuration` floors to days/hours/minutes and drops the leading unit when it is
     zero. `formatBytes` uses `['B', 'KiB', 'MiB', 'GiB', 'TiB']`, one decimal above bytes.
   - Import `type InstanceHealth` alongside `Instance` from `../api`.
   - `interface HealthCellProps { health: InstanceHealth | null }` and
     `function HealthCell({ health }: HealthCellProps)`. When `health` is `null` render
     `<Typography.Text type="secondary">Unavailable</Typography.Text>`. Otherwise a
     `Tooltip` around `<Space direction="vertical" size={2}>` with four lines:
     1. `Up {formatDuration(host_uptime)} · FTL {formatDuration(ftl_uptime)}`
     2. `Load {load[0].toFixed(2)} / {load[1].toFixed(2)} / {load[2].toFixed(2)} ({cores} cores)`
     3. `RAM {memory.percent.toFixed(1)}%` and, when `temperature` is non-null,
        ` · {value.toFixed(0)} °{unit}` rendered with `type="danger"` when `limit` is
        non-null and `value >= limit`.
     4. `DB {formatBytes(database.size)} · {database.queries.toLocaleString()} queries`

     Tooltip body is a vertical `Space` of one line per field: RAM used/total
     (`formatBytes(kb * 1024)`), RAM available, swap percent with used/total, CPU
     percent, FTL CPU and memory percent, temperature with limit, SQLite version.
   - Column `{ title: 'Health', key: 'health', render: (_, item) => <HealthCell
     health={item.health} /> }` inserted directly after the Versions column, no
     `responsive` restriction, matching Versions.
   - Page heading: `<Space>` holding `<Button icon={<ReloadOutlined />} loading={loading}
     onClick={() => void load()}>Refresh</Button>` before the existing Add instance button.
   - Both list calls (`load` and the mount effect) pass `{ health: true }`.

8. **Docs**:
   - `docs/design.md` §5.1, new bullet after the live-status bullet: the Instances page
     reads host and FTL uptime, memory, swap, load, CPU, temperature, FTL process usage,
     and query-database size from `/api/info/{system,ftl,database,sensors}` inside the
     same session as the connection check, on open, Refresh, and after Test, save, and
     remove (the page's existing list reload after those actions); FTL uptime is
     converted from milliseconds; a failed health read leaves the instance reachable with
     the column marked unavailable; sensors are optional.
   - `README.md` "Pi-hole compatibility", extend the Instances paragraph with one
     sentence naming the four endpoints and the Health column.
   - `docs/potential-features.md`: add a shipped bullet "Instance health column with
     uptime, memory, load, CPU, temperature, FTL usage, and database size — plan 0015
     (design.md §5.1)"; remove the §2 bullet; "Suggested next pick" → config backup via
     Teleporter.

9. **Build**: `cd frontend && npm run build && npm run lint`; commit `app/static/ui/`.

## Contracts

### `GET /api/instances?health=true` (backend, one row of `instances`)

```json
{
  "name": "lab-pihole",
  "base_url": "https://192.168.1.2",
  "notes": "",
  "verify_ssl": false,
  "status": "success",
  "status_detail": "Reachable",
  "versions": { "core": {}, "web": {}, "ftl": {}, "docker": {}, "compatibility": "compatible", "compatibility_detail": "Supported FTL 6.x API", "update_available": false },
  "health": {
    "host_uptime": 1296249,
    "ftl_uptime": 432620,
    "memory": { "total": 2097152, "used": 54137, "available": 2042507, "percent": 2.581453323364258 },
    "swap": { "total": 8388604, "used": 3017108, "percent": 35.9667472680794 },
    "cpu": { "cores": 4, "percent": 6.974999904632568, "load": [0.6064453125, 1.32763671875, 1.234375] },
    "temperature": { "value": 57.0, "limit": 60.0, "unit": "C" },
    "ftl": { "cpu_percent": 0.02500000037252903, "mem_percent": 1.4299392700195312 },
    "database": { "size": 460140544, "queries": 2636298, "sqlite_version": "3.53.1" }
  }
}
```

Units: both uptimes in seconds; `memory` and `swap` totals in kilobytes; `database.size`
in bytes; `database.queries` is FTL's `queries_disk`. `temperature` is `null` when the
sensors read failed or reported no CPU temperature; `temperature.limit` may be `null`.
`health` is `null` when the flag is absent, when the credential is missing, when the
instance is unreachable, or when system, ftl, or database failed to read. Without the
flag the row is byte-identical to today's plus `"health": null`.

`POST /api/instances/{name}/test` is unchanged: `{status, detail, versions}`.

### `frontend/src/api.ts`

```ts
export interface HealthMemory {
  total: number
  used: number
  available: number
  percent: number
}

export interface HealthSwap {
  total: number
  used: number
  percent: number
}

export interface HealthCpu {
  cores: number
  percent: number
  load: [number, number, number]
}

export interface HealthTemperature {
  value: number
  limit: number | null
  unit: string
}

export interface HealthFtl {
  cpu_percent: number
  mem_percent: number
}

export interface HealthDatabase {
  size: number
  queries: number
  sqlite_version: string
}

export interface InstanceHealth {
  host_uptime: number
  ftl_uptime: number
  memory: HealthMemory
  swap: HealthSwap
  cpu: HealthCpu
  temperature: HealthTemperature | null
  ftl: HealthFtl
  database: HealthDatabase
}

export interface Instance {
  name: string
  base_url: string
  notes: string
  verify_ssl: boolean
  status: Status
  status_detail: string
  versions: PiHoleVersions | null
  health: InstanceHealth | null
}

listInstances: (options?: { health?: boolean }) =>
  request<{ instances: Instance[] }>(options?.health ? '/api/instances?health=true' : '/api/instances'),
```

Config and DB: N/A — no config keys or persisted data change.

## Tests

Client (`tests/test_pihole_client.py`, via `search_client`; each asserts the request
path and `headers` carry the session, and closes the client in `finally`):

- `test_get_system_info_parses_live_shape` — the lab-pihole body above yields
  `SystemInfo(1296249, 2097152, 54137, 2042507, 2.58..., 8388604, 3017108, 35.96..., 4,
  6.97..., (0.606..., 1.327..., 1.234...))`; a fourth load entry is ignored.
- `test_get_system_info_rejects_invalid_response` — parametrised over `http_error`
  (503), `missing_system`, `missing_memory`, `bool_cores` (`nprocs: True`),
  `string_uptime`, `short_load` (two entries): each raises `PiholeAPIError`.
- `test_get_ftl_info_keeps_milliseconds` — `uptime: 432620297.203082` is stored
  unchanged in `uptime_ms`; `%cpu` and `%mem` map to `cpu_percent` / `mem_percent`.
- `test_get_ftl_info_rejects_invalid_response` — `http_error`, `missing_ftl`,
  `missing_uptime`, `string_cpu`.
- `test_get_database_info_parses_top_level_body` — `size`, `queries_disk`,
  `sqlite_version` map; the in-memory `queries` field is ignored.
- `test_get_database_info_rejects_invalid_response` — `http_error`, `bool_size`,
  `missing_queries_disk`, `int_version`.
- `test_get_sensors_parses_temperature` — `cpu_temp: 57, hot_limit: 60, unit: "C"` →
  `SensorsInfo(57.0, 60.0, "C")`.
- `test_get_sensors_without_temperature_returns_none` — parametrised over `cpu_temp`
  absent, `null`, and `-1`; each yields `cpu_temp=None`; missing `unit` falls back to
  `"C"`, missing `hot_limit` to `None`.
- `test_get_sensors_rejects_http_error_and_bad_body` — 503 and `{"sensors": []}` each
  raise `PiholeAPIError` (the route, not the client, decides sensors are optional).

Route (`tests/test_api.py`):

- `test_instance_api_never_returns_credentials` — existing test; expected row gains
  `"health": None`, and `FakePiholeClient.info_calls == []` afterwards, proving the
  unflagged list makes no health reads.
- `test_instance_list_with_health_reads_inside_one_session_in_order` —
  `api.instances_list(health=True)` returns the Contracts `health` object for the
  fixture; `info_calls == ["system", "ftl", "database", "sensors"]`;
  `health["ftl_uptime"] == 432620`; `status_detail == "Reachable"`; `"secret" not in
  repr(response)`.
- `test_health_failure_does_not_hide_healthy_connection` — subclass whose
  `get_system_info` raises `PiholeAPIError`: `status == "success"`, `versions` present,
  `health is None`, `status_detail == "Reachable; health details unavailable"`;
  `info_calls == ["system"]` so ftl and database were not attempted.
- `test_health_and_version_failures_both_append_detail` — both raise:
  `status_detail == "Reachable; version details unavailable; health details unavailable"`.
- `test_sensors_failure_leaves_temperature_null` — `get_sensors` raises: `health` present
  with `temperature is None`, `status_detail == "Reachable"`.
- `test_health_survives_incompatible_ftl_warning` — `ftl.local.version = "v5.25"` with
  `health=True`: `status == "warning"`, `health` still populated.
- `test_instance_test_route_omits_health` — `api.instance_test("pihole1")` keys are
  exactly `{"status", "detail", "versions"}` and `info_calls == []`.
- `test_instance_list_fans_out_connection_checks` — existing; unchanged, still passes
  with the 4-tuple.

## Invariants at risk

1. **One instance mutated at a time** — nothing is written. N/A.
2. **Diff before write** — N/A, read-only.
3. **Gravity gated** — N/A.
4. **No background scheduler** — no timer or poll is added. The Instances page reads on
   open, Refresh, and after Test, save, and remove, all user-initiated. Intact.
5. **FastAPI owns credentials and Pi-hole access** — the health reads run inside the
   existing server-side session; the response carries no URL beyond the already-exposed
   `base_url`, no password, and no session id. The credential test asserts `"secret"`
   never appears. Intact.
6. **All outbound through `url_policy`** — the four reads use the existing
   `PiholeClient` whose transport is pinned by `resolve_instance_destination`; no new
   host or URL is constructed. Intact.
7. **Every route behind auth middleware** — no new route; `GET /api/instances` is already
   under the `/api` prefix and the middleware. Intact.
8. **Compiled frontend committed** — Step 9. Intact.
9. **`main` is production** — work on `feature/0015-instance-health`, merge after Accept.

Also guarded: the concurrent-login race. Health is read inside the one session
`_connection` already holds, so the Instances page still opens exactly one session per
instance per load.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including every test in the Tests section (nine client,
      seven new or changed route tests).
- [ ] `cd frontend && npm run build && npm run lint` clean; `app/static/ui/` committed and
      reproducible by a fresh build.
- [ ] `GET /api/instances` (no flag) returns rows identical to before plus `"health": null`,
      and the server log shows no `/api/info/system` read for it.
- [ ] `GET /api/instances?health=true` against lab-pihole returns `health` with
      `host_uptime` in days not years, `ftl_uptime` about a third of it (the box has been
      up longer than FTL), `memory.total == 2097152`, `database.size` within a few MiB of
      `ls -l /etc/pihole/pihole-FTL.db` on the instance, and `temperature.value` between
      40 and 70.
- [ ] `GET /api/instances?health=abc` → 422; unauthenticated `GET
      /api/instances?health=true` → 401.
- [ ] Instances page shows the Health column with four lines; the tooltip lists every
      field; Refresh reloads the table; Settings page's network call to `/api/instances`
      carries no `health` parameter.
- [ ] With the credential removed for an instance, the row shows `No credential stored`
      and `Unavailable` in Health; with a reachable instance whose sensors read is broken
      (simulated in tests), the column still renders without a temperature.
- [ ] Audit log mtime unchanged after a full page load and Refresh.
- [ ] Diff limited to the Files list.
