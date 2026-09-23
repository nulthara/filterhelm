# Potential features — research notes

A backlog of functionality that would fit this app's scope (multi-Pi-hole
management, one instance mutated at a time, diff-before-write, no background
scheduler) but isn't built yet. This is **not** a plan — see
[ARCHITECT.md](../ARCHITECT.md) for that contract. Treat this as raw
material: run `/plan` against whichever item gets picked up next.

Grounded in what `app/pihole_client.py` already wraps (`/api/config`,
`/api/dns/blocking`, `/api/lists`, `/api/action/gravity`,
`/api/config/dns/hosts/*`, `/api/domains`, `/api/groups`, `/api/clients`,
`/api/info/version`) versus the rest of the Pi-hole FTL v6 REST surface,
which is idle. Each item notes which invariant (ARCHITECT.md § "Project
invariants") it brushes up against, if any.

**Shipped since these notes were first written** (moved out of the backlog):

- Unified drift dashboard across Local DNS, CNAMEs, block lists, domains, groups,
  clients, and managed settings, with category counts and drift filters —
  plan 0020 (design.md §5.10).

- Instance health column with uptime, memory, load, CPU, temperature, FTL usage,
  and database size — plan 0015 (design.md §5.1); status dot with
  fixed thresholds and a collapsible detail row — plan 0017; the detail row now
  opens only for a warning or critical instance — plan 0018.

- Cross-instance query log with window, status, domain, and client filters, plus
  24-hour per-instance counters, top domains, top clients, and upstreams —
  plan 0014 (design.md §5.9).

- "Why is this blocked/allowed?" domain lookup, with read-only per-instance matches,
  exact verdicts, and partial search — plan 0013 (design.md §5.8).

- Public block-list adds use syntax-only URL screening; update, delete, and bulk
  inputs accept existing addresses without screening. All four bulk routes preserve
  successful mutation results when auditing fails, log the audit loss, and retain
  the adlist gravity-pending flag. Validation-error toasts show readable messages
  instead of `[object Object]` — plan 0012.

- Bulk / multi-select delete for CNAME records on the Local DNS screen, each delete
  through 0008's `_cname_write_delete` transport-retry helper, with one confirm and
  sequential per-instance requests — plan 0011 (design.md §5.2).

- Bulk / multi-select enable, disable, and remove for Block Lists and Domains, and remove
  for Local DNS A/AAAA, with one confirm and sequential per-instance requests — plan 0010
  (design.md §5.2, §5.3, §5.5).

- Group deletion orphan preview and optional deletion or reassignment to Default, with
  per-entry results and a Default-group confirmation gate — plan 0009 (design.md §5.6).

- CNAME records (`dns.cnameRecords`) on the Local DNS screen, with per-instance
  add/edit/delete, Copy here, and optional TTL — plan 0008 (design.md §5.2).

- Domain allow/deny-list management, exact + regex — plan 0003 (design.md §5.5).
  No gravity run needed for domain overrides, confirmed against a live instance
  by plan 0007.
- Group management + per-client group assignment — plan 0006 (design.md §5.6,
  §5.7).
- Group targeting for adlists and domains ("Edit groups") — plan 0007
  (design.md §5.3, §5.5).

---

## 1. Extensions of existing patterns (lowest risk — same shape as shipped code)

These reuse the diff/confirm/audit machinery already built for local DNS
and adlists; they're "more of the same," not new architecture.

Nothing is outstanding in this section.

## 2. Read-only additions (no new invariant exposure — nothing here mutates)

Lower risk than anything above, since there's no diff-before-write surface
to get right — just new fan-out reads, same shape as Status (§5.1) and the
Instances version check.

- **Config backup via Teleporter.** `/api/teleporter` returns a full
  config export. A "download backup" button per instance is a read/export
  action, not a mutation — useful disaster-recovery insurance independent
  of anything this app manages directly, and doesn't touch the diff-before-
  write path since there's nothing to diff.
- **In-app audit log viewer.** The audit log (README "Request integrity"
  section) is a private file today, readable only by grepping on the host.
  A filterable read view in Settings would surface it without a new write
  path.

## 3. New confirmed mutations (same diff-before-write pattern, new action type)

- **FTL service actions** — restart DNS resolver, flush logs
  (`/api/action/*`). Same shape as the existing blocking pause/resume:
  single-instance, explicitly confirmed, audited.
- **Multi-user local accounts with roles.** Today there's one admin account
  set from the server console (README "Authentication"). A read-only
  viewer role, manageable from the Settings UI instead of the CLI, would
  suit a household or small office where more than one person wants to *look*
  without being able to mutate. Touches `app/auth.py` and the auth
  middleware, not the Pi-hole-facing invariants.
- **Change history with rollback.** The audit log already records what
  changed; storing enough of the *previous* value to offer a "revert this"
  button on settings/DNS/adlist changes would go further than logging.
  Needs care: a rollback must go through the same diff-before-write
  confirmation as a forward change, not bypass it as a special case.

## 4. Larger, already-flagged items (tracked elsewhere — listed for completeness)

- **OPNsense-managed local DNS** — design.md §13. Long-term; open question
  there is whether it's a third "instance" type reusing this app's
  diff/confirm/audit machinery, or a separate integration entirely.
- **Read-only external API key** for a Grafana/Kuma-style dashboard to
  pull status without a full login (design.md §12 mentions this in
  passing). Would need its own scoped, read-only credential type distinct
  from the session-based local/proxy auth this app has now.

## 5. Lower-priority / needs more thought

- **Instance tagging/grouping** for fleets larger than the current two
  (pihole1, pihole2) — filter/act by tag instead of per-instance. Low
  value at the current fleet size; worth deferring until a third instance
  actually shows up.
- **Alerting/notifications** (e.g., "blocking has been paused for over an
  hour", "an instance stopped responding"). Directly in tension with the
  no-background-scheduler invariant — anything that fires on its own
  needs a process independent of this app's request-scoped model. If
  wanted, it belongs in a separate scheduled job outside `app/main.py`,
  not inside it.
- **DHCP config visibility** — Pi-hole can run DHCP, but OPNsense is the
  authoritative DHCP source here and Pi-hole's DHCP is unused (design.md
  §10 confirms zero overlap today). Low value unless that changes.
- **A frontend test runner.** `frontend/` has no test tooling at all — no vitest,
  no jest, no `npm test` script — so every page handler is covered only by manual
  UI exercise. Plan 0010 made that gap concrete: its DNS chunking loop, the
  sequential per-instance `for … await`, the per-instance `try/catch` that stops one
  instance's remaining chunks without abandoning the rest, and the selection-clearing
  rules are all pure logic with no automated coverage, and the chunking path is the
  one part live verification did not reach either (it needs a selection expanding past
  200 records). Codex exercised these with a throwaway Node harness during
  implementation; that harness is not in the repo and nothing reproduces it. Vitest is
  the natural fit next to Vite, and the handlers would need light extraction to be
  callable outside React. Worth doing before the next plan that puts real branching
  logic in a page component — not urgent on its own, since the backend holds the
  mutation safety and is well covered (326 tests).

## 6. Bugs / papercuts noticed in use

- **`_adlist_key` is not injective, so a bulk or single-record action can hit the
  wrong block list.** `_adlist_key` (`app/routes/api.py:141`) reads `parsed.hostname`,
  which drops userinfo, and sets `fragment=""`. Two stored addresses that differ only
  in credentials or fragment collapse to one key. `_find_adlist` takes the first
  `next(...)` match and `adlist_bulk` builds `current` with `setdefault`, so with
  `https://alice:one@example.com/list` and `https://bob:two@example.com/list` both
  stored, deleting the second deletes the first and reports `success: True` under the
  requested address. Selecting both collapses the batch to one delete and one result
  row, and the dropped selection produces no error row. Found by an adversarial review
  of plan 0012; reproduced against `adlist_bulk` with a fake client.

  **Pre-existing, and reachable on `main`.** `_adlist_key` is unchanged by 0012 apart
  from its parse guard, and `main` accepts a clean `https://example.com/list` that keys
  onto a stored credential-bearing record, so `main` deletes the wrong list through that
  path today. What 0012 changed is the reverse direction — dropping the update/delete/bulk
  validators lets a submitted credential- or fragment-bearing string reach the same lossy
  match, where `main` refused it with a 422. Harm needs two stored records sharing a key
  either way. With one such record stored, 0012 matches it correctly and `main` 422s.

  **Bounded by the add path.** `AdlistAddInput` still runs the full `screen_adlist_url`
  and still rejects credentials and fragments, so this app cannot create a colliding
  record. They arrive from the Pi-hole CLI, a teleporter import, or another tool.

  **Why this needs a plan rather than a patch.** The obvious repair — preserve
  credentials and fragment in the key, match exact stored addresses first — runs into two
  behaviours that are deliberate. `test_adlist_match_ignores_a_normalized_trailing_slash`
  (`tests/test_api.py:310`) relies on lossy matching for issue #8's Pi-hole normalization,
  and `test_adlist_bulk_delete_dedupes_canonical_aliases` (`tests/test_api.py:1509`)
  relies on it to collapse `.../block.txt` and `.../block.txt/` into a single delete.
  Separating real identity from normalization is the decision to make. Candidate scope:
  keep path and case normalization, preserve credentials and fragment, refuse an
  ambiguous match across stored records instead of picking the first, and report a
  collapsed bulk selection as an error row rather than dropping it. Touches invariant #2
  (diff before write) — the wrong record is currently written without the user seeing it.

---

## Suggested next pick

The next read-only item is **config backup via Teleporter** using `/api/teleporter`
for a per-instance configuration download. It is a reasonable `/plan` target next.
