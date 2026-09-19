# 0017 — Health status dot and detail row

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0017-health-status-row
Author: Claude (Architect)
Date: 2026-09-13

## Problem

Plan 0015 put four lines of numbers in a Health column on the Instances page. Live, it
crowds the table: the row height triples, the numbers compete with the Status and
Versions cells, and nothing tells the operator at a glance whether any of it is a
problem. The user wants the opposite shape: a single status dot with a one-word label in
the row, and the numbers moved out of the row into a detail line beneath each instance
that is open by default but can be collapsed. A dot needs a definition of unhealthy, which
0015 deliberately left out; this plan adds one with fixed thresholds computed server-side
so the rule is tested in pytest and the frontend only renders it.

## Scope

In:
- `health.status` (`healthy` | `warning` | `critical`) and `health.reasons` (list of
  strings) added to the health object in `GET /api/instances?health=true`, computed by a
  new `_health_status` in `app/routes/api.py` from temperature, RAM, swap, and 1-minute
  load per core, with thresholds as module constants.
- Instances page: the Health column becomes an AntD `Badge` dot with the label Healthy /
  Warning / Critical / Unavailable and a tooltip listing the reasons. The full number set
  moves into an AntD `Table` expandable row rendered by a new `HealthDetails` component,
  open by default for every instance that has health data, collapsible per row, with the
  collapsed state surviving Refresh. Rows without health data have no expand control.
- Tests for the threshold function (boundaries, precedence, skipped signals) and for the
  new keys in the route payload. Docs: design.md §5.1, README compatibility paragraph,
  potential-features shipped list.
- Rebuilt `app/static/ui/`.

Out:
- Configurable thresholds (settings UI, config keys, per-instance overrides). Constants
  only; changing them is a code change.
- Thresholds on CPU percent, FTL process CPU or memory, database size, or query count.
  No disk-free figure is available from the four endpoints, so database size stays
  informational.
- Any change to the four `PiholeClient` readers, the reads themselves, the `health` query
  flag, `POST /api/instances/{name}/test`, or the Settings page.
- Health status anywhere other than the Instances page. Any timer or background poll.
- Any mutation, audit entry, or new route.

## Approach

**Backend computes the verdict.** `_health_status(system: SystemInfo, sensors:
SensorsInfo | None) -> tuple[Literal["healthy", "warning", "critical"], list[str]]` walks
four signals in a fixed order (temperature, RAM, swap, load) and appends one
self-describing reason string per tripped signal. The status is `critical` if any signal
is critical, else `warning` if any is warning, else `healthy`. Thresholds live next to the
function as module constants:

```python
HEALTH_TEMPERATURE_WARNING_MARGIN = 5.0   # degrees below hot_limit, in Pi-hole's unit
HEALTH_RAM_WARNING_PERCENT = 80.0
HEALTH_RAM_CRITICAL_PERCENT = 95.0
HEALTH_SWAP_WARNING_PERCENT = 50.0
HEALTH_LOAD_WARNING_PER_CORE = 1.0
HEALTH_LOAD_CRITICAL_PER_CORE = 2.0
```

Signal rules, all comparisons `>=`:
- Temperature: only evaluated when `sensors` is present, `cpu_temp` is not `None`, and
  `hot_limit` is not `None`. `value >= limit` → critical; `value >= limit - margin` →
  warning. Pi-hole's own `hot_limit` stays the anchor, so a host configured for Fahrenheit
  is judged in Fahrenheit with a 5-degree margin; that is acceptable for a warning band.
- RAM: `ram_percent >= 95` → critical; `>= 80` → warning.
- Swap: `swap_percent >= 50` → warning. Never critical on its own; a host with no swap
  reports 0 and is never flagged.
- Load: `load[0] / cores` when `cores > 0`; `>= 2.0` → critical; `>= 1.0` → warning.
  Skipped when `cores` is 0.

Reason strings are the contract the frontend shows verbatim, so they name the threshold
that fired:

```
Temperature 61 °C is at or above the 60 °C limit
Temperature 57 °C is within 5 °C of the 60 °C limit
RAM 96.0% used, at or above 95%
RAM 85.0% used, at or above 80%
Swap 62.0% used, at or above 50%
Load 8.40 is 2.1 per core, at or above 2.0
Load 4.80 is 1.2 per core, at or above 1.0
```

Placing this in the route rather than the frontend keeps the rule under pytest and keeps
`InstancesPage.tsx` a renderer, matching the split the project already uses for
`compatibility` / `compatibility_detail` in `_version_info`. A `reasons` list of
`{level, message}` objects was considered and rejected: the overall `status` already
colours the row, and naming the threshold inside each string tells the reader which tier
fired without a second field.

**Frontend: Badge in the row, Descriptions in the detail row.** `HealthBadge` renders
`<Badge status=… text=…>` with a `Tooltip` of the reasons (none for healthy). The table
gains `expandable` with `expandedRowRender` → `HealthDetails`, which renders an optional
`Alert` (the reasons, `warning` or `error` type) above a `Descriptions size="small"` grid
of the twelve fields, wrapped in `Flex vertical gap="middle"` so no CSS changes are needed.

Expansion is **controlled**, not `defaultExpandAllRows`: AntD evaluates
`defaultExpandAllRows` against the dataSource present at mount, and this page mounts with
`[]` and fills in asynchronously, so nothing would expand. Instead the page keeps a
`collapsed: Set<string>` of instance names the user has closed and derives
`expandedRowKeys` during render as every instance with health whose name is not in the
set. New instances therefore arrive open, a collapse survives Refresh / Test / save /
remove, and no effect is needed. `rowExpandable: (item) => item.health !== null` removes
the chevron for rows with nothing to show. `Table.EXPAND_COLUMN` is placed in `columns`
directly before the Health column so the chevron sits beside the dot rather than at the far
left of the row.

**Rejected alternatives.**
- Frontend-computed status: no pytest coverage of the rule, and the same thresholds would
  have to be duplicated if any other page ever shows the dot.
- Always-visible second row (no collapse): the user chose collapsible.
- Dot in the row with details only in the tooltip: the user wants the numbers visible
  without hovering.
- `defaultExpandAllRows`: see above; it silently does nothing here.

## Files

- `app/routes/api.py` — six `HEALTH_*` constants, `_health_status`, and `_health_info`
  adding `status` / `reasons`.
- `tests/test_api.py` — updated expected payload in
  `test_instance_list_with_health_reads_inside_one_session_in_order`; new
  `_health_status` unit tests and one route-level status test.
- `frontend/src/api.ts` — `HealthStatus` type; `status` and `reasons` on `InstanceHealth`.
- `frontend/src/pages/InstancesPage.tsx` — `HealthBadge`, `HealthDetails`, controlled
  expandable table, `collapsed` state; `HealthCell` removed.
- `docs/design.md` — §5.1 health bullet gains the status rule and the detail row.
- `README.md` — "Pi-hole compatibility" paragraph gains one sentence on the status dot.
- `docs/potential-features.md` — shipped bullet for 0015 gains the 0017 follow-up.
- `app/static/ui/` — rebuilt bundle.

## Steps

1. **Constants and `_health_status`** in `app/routes/api.py`, placed directly above
   `_health_info`:

   ```python
   def _health_status(system: SystemInfo, sensors: SensorsInfo | None) -> tuple[
       Literal["healthy", "warning", "critical"], list[str],
   ]:
   ```

   Body: `reasons: list[str] = []`, `critical = False`, `warning = False`; evaluate the
   four signals in the order and with the exact strings given in Approach; return
   `("critical" if critical else "warning" if warning else "healthy", reasons)`.
   Number formats: temperature values, limit, and margin `:.0f` followed by ` °{unit}`;
   RAM and swap measured percent `:.1f`, threshold `:.0f` (`95%`, `80%`, `50%`); load
   raw value `:.2f`, per-core ratio `:.1f`, threshold `:.1f` (`2.0`, `1.0`). Thresholds
   are always interpolated from the constants, never typed as literals.

2. **`_health_info`**: compute `status, reasons = _health_status(system, sensors)` and
   add `"status": status, "reasons": reasons` as the first two keys of the returned dict.
   Signature and every existing key unchanged.

3. **Route tests** in `tests/test_api.py` (Tests section). The existing fixture
   (`SensorsInfo(57.0, 60.0, "C")`, RAM 2.58%, swap 35.97%, load 0.61 on 4 cores) sits in
   the temperature warning band, so the expected payload in the ordering test becomes
   `status: "warning"` with the single temperature reason. Leave the fixture as is; it
   mirrors the live shape captured in 0015.

4. **`frontend/src/api.ts`**: add `export type HealthStatus = 'healthy' | 'warning' |
   'critical'`; add `status: HealthStatus` and `reasons: string[]` to `InstanceHealth`
   (Contracts).

5. **`frontend/src/pages/InstancesPage.tsx`**:
   - Imports: add `Badge`, `Descriptions`, `Flex` to the `antd` import (`Alert` is
     already there) and `type DescriptionsProps` to the `antd` type import; add
     `type HealthStatus` to the `../api` import. `Tooltip`, `Space`, `Typography` stay in
     use elsewhere on the page.
   - Module constants:

     ```ts
     const healthBadge: Record<HealthStatus, 'success' | 'warning' | 'error'> =
       { healthy: 'success', warning: 'warning', critical: 'error' }
     const healthLabel: Record<HealthStatus, string> =
       { healthy: 'Healthy', warning: 'Warning', critical: 'Critical' }
     ```

   - Delete `HealthCell` and `HealthCellProps`. Keep `formatDuration` and `formatBytes`.
   - `interface HealthBadgeProps { health: InstanceHealth | null }`;
     `function HealthBadge({ health })`: when `null` →
     `<Badge status="default" text="Unavailable" />`; otherwise
     `<Tooltip title={health.reasons.length ? health.reasons.join('; ') : undefined}>
     <Badge status={healthBadge[health.status]} text={healthLabel[health.status]} />
     </Tooltip>`.
   - `interface HealthDetailsProps { health: InstanceHealth }`;
     `function HealthDetails({ health })`: destructure as `HealthCell` did; compute
     `temperatureDisplay` as `"57 °C (limit 60 °C)"`, `"57 °C (no limit configured)"`,
     or `"Unavailable"`; render

     ```tsx
     <Flex vertical gap="middle">
       {health.reasons.length > 0 && (
         <Alert type={health.status === 'critical' ? 'error' : 'warning'} showIcon
           message={health.reasons.join('; ')} />
       )}
       <Descriptions size="small" column={{ xs: 1, sm: 2, xl: 4 }} items={items} />
     </Flex>
     ```

     where `items` is a `DescriptionsProps['items']` array of twelve entries with
     `key`/`label`/`children`, in this order: Host uptime, FTL uptime, Load
     (`"0.61 / 1.33 / 1.23 (4 cores)"`), CPU (`"7.0%"`), RAM (`"2.6% (52.9 MiB / 2.0
     GiB)"`), RAM available, Swap (`"36.0% (2.9 GiB / 8.0 GiB)"`), Temperature, FTL CPU,
     FTL memory, Database (`"438.8 MiB · 2,636,298 queries"`), SQLite. Import
     `type DescriptionsProps` from `antd`.
   - State: `const [collapsed, setCollapsed] = useState<Set<string>>(new Set())`.
     Derived in render:
     `const expandedRowKeys = instances.filter((item) => item.health !== null &&
     !collapsed.has(item.name)).map((item) => item.name)`.
   - Column list: replace the Health entry with two entries in this position:
     `Table.EXPAND_COLUMN,` then
     `{ title: 'Health', key: 'health', render: (_, item) => <HealthBadge health={item.health} /> }`.
   - `<Table … expandable={{ expandedRowKeys, rowExpandable: (item) => item.health !==
     null, expandedRowRender: (item) => item.health ? <HealthDetails health={item.health}
     /> : null, onExpand: (expanded, item) => setCollapsed((current) => { const next = new
     Set(current); if (expanded) next.delete(item.name); else next.add(item.name); return
     next }) }} />`.

6. **Docs**:
   - `docs/design.md` §5.1: extend the health bullet: the row shows a status dot
     (healthy / warning / critical / unavailable) computed server-side from temperature
     against Pi-hole's `hot_limit`, RAM percent, swap percent, and 1-minute load per core
     with fixed thresholds; the numbers sit in a collapsible detail row under each
     instance, open by default.
   - `README.md` "Pi-hole compatibility": after the Health sentence, add: "A status dot
     summarises the result: warning within 5 degrees of Pi-hole's temperature limit, at
     80% RAM, 50% swap, or a 1-minute load of one per core; critical at the temperature
     limit, 95% RAM, or two per core."
   - `docs/potential-features.md`: append to the 0015 shipped bullet "; status dot with
     fixed thresholds and a collapsible detail row — plan 0017".

7. **Build**: `cd frontend && npm run build && npm run lint`; commit `app/static/ui/`.

## Contracts

### `GET /api/instances?health=true` (backend, `health` object only; other keys unchanged)

```json
"health": {
  "status": "warning",
  "reasons": ["Temperature 57 °C is within 5 °C of the 60 °C limit"],
  "host_uptime": 1296249,
  "ftl_uptime": 432620,
  "memory": { "total": 2097152, "used": 54137, "available": 2042507, "percent": 2.581453323364258 },
  "swap": { "total": 8388604, "used": 3017108, "percent": 35.9667472680794 },
  "cpu": { "cores": 4, "percent": 6.974999904632568, "load": [0.6064453125, 1.32763671875, 1.234375] },
  "temperature": { "value": 57.0, "limit": 60.0, "unit": "C" },
  "ftl": { "cpu_percent": 0.02500000037252903, "mem_percent": 1.4299392700195312 },
  "database": { "size": 460140544, "queries": 2636298, "sqlite_version": "3.53.1" }
}
```

`status` is one of `healthy`, `warning`, `critical`. `reasons` is `[]` when `healthy`
and otherwise one string per tripped signal in the order temperature, RAM, swap, load.
`health` is still `null` in every case it was `null` before; the unflagged list and
`POST /api/instances/{name}/test` are unchanged.

### `frontend/src/api.ts`

```ts
export type HealthStatus = 'healthy' | 'warning' | 'critical'

export interface InstanceHealth {
  status: HealthStatus
  reasons: string[]
  host_uptime: number
  ftl_uptime: number
  memory: HealthMemory
  swap: HealthSwap
  cpu: HealthCpu
  temperature: HealthTemperature | null
  ftl: HealthFtl
  database: HealthDatabase
}
```

Config and DB: N/A — no config keys or persisted data change.

## Tests

All in `tests/test_api.py`. The `_health_status` tests call the function directly with
`SystemInfo` / `SensorsInfo` built from the fixture values via `dataclasses.replace`, so
they need no monkeypatching. A helper `healthy_system()` returns the fixture `SystemInfo`
and `quiet_sensors()` returns `SensorsInfo(40.0, 60.0, "C")`.

- `test_instance_list_with_health_reads_inside_one_session_in_order` — existing; the
  expected dict gains `'status': 'warning'` and `'reasons': ['Temperature 57 °C is
  within 5 °C of the 60 °C limit']`. Every other assertion unchanged.
- `test_health_status_healthy_when_nothing_trips` — fixture system + quiet sensors →
  `("healthy", [])`.
- `test_health_status_temperature_band` — parametrised over `(cpu_temp, hot_limit,
  expected_status)`: `(54.9, 60, "healthy")`, `(55, 60, "warning")`, `(59.9, 60,
  "warning")`, `(60, 60, "critical")`, `(75, 60, "critical")`; asserts status and the
  exact reason string for the warning and critical cases.
- `test_health_status_skips_temperature_without_limit_or_sensors` — `SensorsInfo(90.0,
  None, "C")`, `SensorsInfo(None, 60.0, "C")`, and `None` each yield `("healthy", [])`.
- `test_health_status_ram_band` — `ram_percent` 79.9 → healthy; 80 → warning with
  `"RAM 80.0% used, at or above 80%"`; 95 → critical with `"RAM 95.0% used, at or above
  95%"`.
- `test_health_status_swap_warns_only` — `swap_percent` 49.9 → healthy; 50 → warning
  with `"Swap 50.0% used, at or above 50%"`; 100 → still `warning`.
- `test_health_status_load_per_core` — cores 4: `load[0]` 3.99 → healthy; 4.0 →
  warning with `"Load 4.00 is 1.0 per core, at or above 1.0"`; 8.0 → critical with
  `"Load 8.00 is 2.0 per core, at or above 2.0"`; cores 0 with load 99 → healthy.
- `test_health_status_critical_outranks_warning_and_keeps_order` — RAM 85 (warning),
  swap 60 (warning), load 9.0 on 4 cores (critical), sensors `(61, 60)` (critical) →
  `"critical"` with four reasons in the order temperature, RAM, swap, load.
- `test_health_status_in_route_payload` — monkeypatch `FakePiholeClient.system_info` to
  `ram_percent=96.0` and `sensors_info` to `SensorsInfo(None, None, "C")`:
  `row['health']['status'] == 'critical'`, `row['health']['reasons'] == ['RAM 96.0%
  used, at or above 95%']`, `row['status'] == 'success'`, `row['status_detail'] ==
  'Reachable'`, and `'secret' not in repr(row)`.
- Every other 0015 health test is unchanged and still passes: they assert sub-keys only.

## Invariants at risk

1. **One instance mutated at a time** — nothing is written. N/A.
2. **Diff before write** — N/A, read-only.
3. **Gravity gated** — N/A.
4. **No background scheduler** — no timer or poll. Expansion state is plain React state
   updated by clicks; reads still happen only on open, Refresh, Test, save, and remove.
   Intact.
5. **FastAPI owns credentials and Pi-hole access** — `_health_status` sees only the
   dataclasses; reason strings contain numbers and units. The route-payload test asserts
   `"secret"` is absent. Intact.
6. **All outbound through `url_policy`** — no new request of any kind. Intact.
7. **Every route behind auth middleware** — no new route; no change to `instances_list`'s
   signature. Intact.
8. **Compiled frontend committed** — Step 7. Intact.
9. **`main` is production** — work on `feature/0017-health-status-row`, merge after
   Accept.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including the nine tests above.
- [ ] `cd frontend && npm run build && npm run lint` clean (the pre-existing `App.tsx`
      hook warning may remain); `app/static/ui/` committed and reproducible.
- [ ] `GET /api/instances?health=true` rows carry `health.status` and `health.reasons`;
      applying the thresholds by hand to the returned numbers for lab-pihole gives the
      same status the response reports.
- [ ] `GET /api/instances` (no flag) still returns `health: null`.
- [ ] Instances page: the Health cell is a dot plus one word; hovering it on a warning or
      critical instance shows the reasons. Each instance with health data has a chevron
      beside the dot and opens expanded on first load; collapsing one and pressing Refresh
      leaves it collapsed; an instance with the credential removed shows a grey
      "Unavailable" dot and no chevron.
- [ ] The detail row lists all twelve fields; the reasons `Alert` appears only when the
      status is not healthy and is red for critical, amber for warning.
- [ ] Audit log mtime unchanged after a page load, Refresh, and expand/collapse.
- [ ] Diff limited to the Files list.
