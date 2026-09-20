# 0018 — Health detail row opens only when unhealthy

Status: in-progress          <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0018-health-row-auto-expand
Author: Claude (Architect)
Date: 2026-09-19

## Problem

Plan 0017 made every instance's health detail row open by default. Live, that is the wrong
default — the table is as tall as it was before the dot existed, and the twelve fields are
on screen permanently whether or not anything is wrong with the host. The information is
only worth the vertical space when a signal has tripped. This plan inverts the default so a
healthy instance shows just the dot, a warning or critical instance opens its own detail
row, and an explicit click by the user wins over both.

## Scope

In:
- `InstancesPage.tsx` expansion state changes from `collapsed: Set<string>` to
  `expandOverrides: Map<string, boolean>` holding only the rows the user has clicked.
- The per-row default becomes `health !== null && health.status !== 'healthy'`, derived
  during render. An override, once set, wins for the rest of the session.
- `docs/design.md` §5.1 and the `docs/potential-features.md` shipped list updated to the
  new default; the §6 papercut bullet that asked for this is removed.
- Rebuilt `app/static/ui/`.

Out:
- Any backend change. `_health_status`, the thresholds, the `health` object, and
  `GET /api/instances?health=true` are untouched.
- Any change to `HealthBadge`, `HealthDetails`, the twelve fields, the reasons `Alert`,
  the badge tooltip, `Table.EXPAND_COLUMN`'s position, or `rowExpandable`.
- Persisting expansion across a page reload (localStorage, a config key, a server field).
  The state stays in-memory React state, as it is today.
- Re-deriving the default when an instance's status changes under an existing override.
  See Approach.
- Suppressing the badge tooltip on an expanded row, even though its reasons then duplicate
  the `Alert`.
- Adding a frontend test runner (`docs/potential-features.md` §5 tracks that separately).
- The pre-existing `test_repo_hygiene` failure over the `LICENSE` copyright line. See
  Tests — it is the baseline, not a regression, and fixing it is a separate decision.
- `README.md` — it describes the status dot and the thresholds but never the detail row,
  so nothing there goes stale.

## Approach

**One map of explicit choices, default derived per row.** The page keeps
`expandOverrides: Map<string, boolean>` keyed by instance name. `onExpand` writes the
clicked row's new state into the map; nothing else writes to it. During render, a row is
expanded when

```ts
item.health !== null && (expandOverrides.get(item.name) ?? item.health.status !== 'healthy')
```

so the map is consulted first and the status supplies the default when the user has not
touched that row. This gives all four behaviours in one expression: healthy rows start
collapsed, unhealthy rows start open, a collapse of an unhealthy row survives Refresh /
Test / save / remove, and an expand of a healthy row does too.

`Map<string, boolean>` rather than the two sets the papercut note sketched. Two sets
(`expanded` and `collapsed`) would need every write to touch both to stay consistent, and a
row present in neither, both, or the wrong one is a state the type does not rule out. One
map with a three-way read — `true`, `false`, absent — has no such states.

**An override outlives a status change, deliberately.** If a row the user collapsed later
goes from warning to critical, it stays collapsed. Keying the override by
`(name, status)` so a transition resets it was considered and rejected: a row would reopen
by itself whenever a host oscillated across a threshold boundary, which on lab-pihole's
temperature band is a realistic Refresh-to-Refresh event, and a default that quietly
overrules an explicit click is worse than one that goes stale. Nothing is hidden by the
staleness either — the dot still turns red and the badge tooltip still lists the reasons on
hover.

**No pruning of removed instances.** `expandOverrides` can hold names that are no longer in
`instances`; `expandedRowKeys` is derived by filtering `instances`, so a stale entry has no
effect. Re-adding an instance under the same name inherits the earlier choice, which is the
behaviour a user would expect.

**No `useMemo`, no effect.** `expandedRowKeys` is computed during render from `instances`
and `expandOverrides`, per `AGENTS.md` ("Compute derived values during render, not in an
effect"). There is no new poll, timer, or fetch.

## Files

- `frontend/src/pages/InstancesPage.tsx` — `collapsed` replaced by `expandOverrides`;
  new `isExpanded` helper; `onExpand` rewritten.
- `docs/design.md` — §5.1 health bullet, last sentence.
- `docs/potential-features.md` — shipped bullet extended; §6 detail-row bullet removed.
- `app/static/ui/` — rebuilt bundle.

## Steps

1. **State** — `frontend/src/pages/InstancesPage.tsx:89`. Replace

   ```ts
   const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
   ```

   with

   ```ts
   const [expandOverrides, setExpandOverrides] = useState<Map<string, boolean>>(new Map())
   ```

   Position in the `useState` block, imports, and every other piece of state unchanged.

2. **Derived keys** — `frontend/src/pages/InstancesPage.tsx:191-192`. Replace the two-line
   `expandedRowKeys` with a named helper and the filter:

   ```ts
   const isExpanded = (item: Instance) => item.health !== null &&
     (expandOverrides.get(item.name) ?? item.health.status !== 'healthy')
   const expandedRowKeys = instances.filter(isExpanded).map((item) => item.name)
   ```

   Both sit where `expandedRowKeys` sits now, directly above `const columns`. `item.health`
   narrows to non-null across the `&&`, so no non-null assertion is needed.

3. **`onExpand`** — `frontend/src/pages/InstancesPage.tsx:278-283`. Replace the
   `setCollapsed` body with:

   ```ts
   onExpand: (expanded, item) => setExpandOverrides((current) => {
     const next = new Map(current)
     next.set(item.name, expanded)
     return next
   }),
   ```

   `expandedRowKeys`, `rowExpandable`, and `expandedRowRender` on lines 275-277 are
   unchanged.

4. **`docs/design.md`** §5.1, lines 112-113. Replace the sentence "The numbers sit in a
   collapsible detail row under each instance, open by default." — it wraps across those
   two lines, so match it as two lines, not one — with:

   > The numbers sit in a collapsible detail row under each instance. A warning or
   > critical instance opens its detail row automatically; a healthy one starts
   > collapsed. Expanding or collapsing a row overrides that default for the rest of the
   > session.

5. **`docs/potential-features.md`**:
   - Extend the 0015/0017 shipped bullet with "; the detail row now opens only for a
     warning or critical instance — plan 0018".
   - Delete the §6 bullet beginning "**The Instances health detail row should start
     collapsed, not expanded.**", including its paragraph. The `_adlist_key` bullet stays.

6. **Build** — `cd frontend && npm run build && npm run lint`; commit `app/static/ui/`.

## Contracts

N/A — no API request or response shape, config key, or DB access changes. `InstanceHealth`
and `HealthStatus` in `frontend/src/api.ts` are read but not modified, and no route is
touched.

## Tests

N/A — no backend behaviour changes, and `frontend/` has no test runner (no vitest, no jest,
no `npm test`; tracked in `docs/potential-features.md` §5 and explicitly out of scope
here). `./.venv/bin/pytest` must come back unchanged as a regression check — no test
asserts on expansion state, so any *new* failure means something outside this plan's Files
list moved. The behaviour is covered by the manual checks in Acceptance criteria.

**Known pre-existing false positive, do not fix here.** On `main` today,
`tests/test_repo_hygiene.py::test_tracked_files_carry_no_known_private_tokens` reports one
`personal domain` finding against `LICENSE:3`. That line is the repo owner's public GitHub
handle in a GitHub-generated MIT copyright notice, not a private domain reference — the
`personal domain` pattern in the local, gitignored `.private-denylist.toml` matches the
bare handle instead of requiring a domain suffix. The baseline is **519 passed, 1 failed**
with that single `LICENSE` finding. Codex must reproduce exactly that and no other finding,
and must not touch `LICENSE`, `.private-denylist.toml`, or `tests/test_repo_hygiene.py`;
narrowing the pattern is a local config change outside this plan.

This paragraph deliberately does not spell the handle out. The hygiene test scans every
tracked file, so a plan that quotes the token becomes a second finding once committed and
masks the baseline it is trying to describe.

## Invariants at risk

1. **One instance mutated at a time** — N/A. Nothing is written to any Pi-hole.
2. **Diff before write** — N/A, read-only change.
3. **Gravity updates gated** — N/A.
4. **No background scheduler** — the change adds no timer, poll, interval, or effect.
   `expandOverrides` is written only by a click on the expand chevron, and
   `expandedRowKeys` is computed during render. Reads still happen only on open, Refresh,
   Test, save, and remove. Intact.
5. **FastAPI owns credentials and Pi-hole access** — no backend file changes and no new
   field reaches the browser. The page reads `health.status`, which 0017 already sends.
   Intact.
6. **All outbound through `url_policy`** — no request of any kind is added. Intact.
7. **Every route behind auth middleware** — no route added or changed. Intact.
8. **Compiled frontend committed** — this is the live one. `frontend/src/` changes, so
   Step 6 rebuilds and commits `app/static/ui/`; the branch is incomplete without it.
9. **`main` is production** — work on `feature/0018-health-row-auto-expand`, merge only
   after an Accept verdict.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` returns 519 passed, 1 failed — the same pre-existing
      `test_tracked_files_carry_no_known_private_tokens` failure described in Tests, and
      nothing else.
- [ ] `cd frontend && npm run build && npm run lint` clean (the pre-existing `App.tsx` hook
      warning may remain); `app/static/ui/` committed.
- [ ] On first load, a healthy instance shows the dot with its row collapsed and the
      chevron closed; a warning or critical instance shows its detail row already open.
- [ ] Collapsing an open warning row and pressing Refresh leaves it collapsed.
- [ ] Expanding a healthy row and pressing Refresh leaves it expanded.
- [ ] An instance with no health data still has no chevron and no detail row.
- [ ] `grep -n "collapsed" frontend/src/pages/InstancesPage.tsx` returns nothing — the old
      state name is gone, not shadowed.
- [ ] `HealthBadge`, `HealthDetails`, the twelve `Descriptions` fields, the reasons
      `Alert`, and the column order are byte-identical in the diff.
- [ ] Audit log mtime unchanged after a page load, Refresh, and expand/collapse.
- [ ] Diff limited to the Files list.
