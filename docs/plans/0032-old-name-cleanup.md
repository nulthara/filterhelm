# 0032 — Remove the old name from the docs

Status: in-progress           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0032-old-name-cleanup
Author: Claude (architect)
Date: 2026-10-13

## Problem

Plan 0030 renamed the product to FilterHelm but left the old name in three places. README
keeps an `## Upgrading from pihole-manager` section. `docs/deploy.md` §8 holds the host
cutover runbook. Plans 0001–0029 keep the old name as historical records. The host cutover
has run and 0030 shipped, so the README section and the runbook no longer serve anyone.
The user wants the old name gone from the docs before the public mirror moves. Plan 0030
stays as the one record of the rename.

## Scope

In:
- Delete README's `## Upgrading from pihole-manager` section.
- Delete `docs/deploy.md` §8, the cutover runbook, and renumber §9 and §10.
- Delete the stale "To abandon containers" paragraph in `docs/deploy.md` §9 (Rollback).
  It describes undoing the 0025 systemd → container cutover, whose runbook 0030 removed.
- Replace the old name in `docs/plans/0001`–`0029` with fixed substitution rules.
- Tighten `tests/test_branding.py` so the old name may appear only in plan 0030, this
  plan, and the guard test itself.

Out:
- Plan 0030. It describes the rename and keeps the old name.
- Plan 0031 and every code file. Neither contains the old name today.
- The `github` remote URL, the local clone directory, and the forge repo. Those are user
  actions outside git.
- Rewriting git history. Old commits keep the old name.
- Any other edit to the historical plans. Only the name changes.

## Approach

**Fixed substitutions, applied in order.** The old name takes these forms in plans
0001–0029 (counted with `git grep -ohiE`). Each rule is case-sensitive.

| # | Old | New |
| --- | --- | --- |
| 1 | `X-Pihole-Manager-Poll` | `X-FilterHelm-Poll` |
| 2 | `Pi-hole Manager` | `FilterHelm` |
| 3 | `PIHOLE_MANAGER` | `FILTERHELM` |
| 4 | `pihole-manager` | `filterhelm` |
| 5 | `pihole_manager` | `filterhelm` |

Rule 1 runs before rule 4 so the mixed-case header keeps the `FilterHelm` casing that
`frontend/src/api.ts` uses. Rule 4 covers paths, unit names, image tags, the container
name, `pihole-manager.example.com`, and `pihole-manager-deploy`. Rule 5 covers
`pihole_manager_session`. These five rules cover every form found, so a case-insensitive
grep over 0001–0029 must print nothing afterwards.

Some rewritten commands, mostly in plan 0025, will name `filterhelm` host paths that did
not exist when the plan ran. The user accepts this.

**Guard exemptions by exact path.** The guard currently exempts all of `docs/plans/` and
checks README and deploy.md per section. After this change it exempts exactly three
files: `tests/test_branding.py` (holds the regex), `docs/plans/0030-rebrand-filterhelm.md`
(the rename record), and this plan (it lists the substitution rules). A new plan that
names the old name fails the guard, which is the point.

**Rejected:**
- Rewriting plan 0030 too. The user chose to keep it as the precise record of the rename.
- Leaving plans 0001–0029 alone. The user wants the old name gone from the docs.
- Writing this plan's rules with regex escapes so it needs no exemption. That makes the
  rules harder to read and to apply.

## Files

- `README.md` — delete the `## Upgrading from pihole-manager` section.
- `docs/deploy.md` — delete §8, renumber §9 → §8 and §10 → §9, delete the "To abandon
  containers" paragraph.
- `docs/plans/0001-security-fixes.md` — substitutions.
- `docs/plans/0002-bug-sweep-and-fanout-perf.md` — substitutions.
- `docs/plans/0003-domain-allow-deny-management.md` — substitutions.
- `docs/plans/0004-auth-session-expiry-redirect.md` — substitutions.
- `docs/plans/0005-idle-timeout-excludes-background-polling.md` — substitutions.
- `docs/plans/0008-cname-records.md` — substitutions.
- `docs/plans/0012-adlist-url-policy-and-bulk-audit.md` — substitutions.
- `docs/plans/0016-privacy-scrub.md` — substitutions.
- `docs/plans/0019-verify-posts-pr-review.md` — substitutions.
- `docs/plans/0020-drift-dashboard.md` — substitutions.
- `docs/plans/0024-dependency-refresh-age-gate.md` — substitutions.
- `docs/plans/0025-container-deploy.md` — substitutions.
- `tests/test_branding.py` — exact-path exemptions, drop the migration-section test.

## Steps

1. **README.** Delete from the line `## Upgrading from pihole-manager` (line 277) to end of
   file. The file then ends with the `## TODO` list and a single trailing newline.

2. **docs/deploy.md.**
   - Delete from the line `## 8. Cutover from pihole-manager` up to, not including,
     `## 9. Rollback`.
   - Rename `## 9. Rollback` to `## 8. Rollback` and `## 10. First deploy` to
     `## 9. First deploy`. No file outside `docs/deploy.md` cites these numbers. Its own
     `§3` and `§6` references keep their numbers.
   - In the Rollback section, delete the last paragraph, which starts "To abandon
     containers". Keep the two paragraphs before it.
   - The sudoers rule line still appears exactly once, in §6.

3. **Historical plans.** Apply rules 1–5 in order to each plan file in Files, and to no
   other file. One way:
   ```bash
   sed -i -e 's/X-Pihole-Manager-Poll/X-FilterHelm-Poll/g' \
          -e 's/Pi-hole Manager/FilterHelm/g' \
          -e 's/PIHOLE_MANAGER/FILTERHELM/g' \
          -e 's/pihole-manager/filterhelm/g' \
          -e 's/pihole_manager/filterhelm/g' <files>
   ```
   Do not reflow lines or realign tables. Then confirm
   `git grep -niE 'pihole[-_ ]?manager|pi-hole manager' -- 'docs/plans/00[0-2]*'` prints
   nothing. If it prints a form the rules miss, stop and report it.

4. **Guard test.** In `tests/test_branding.py`:
   - Replace `MIGRATION_SECTIONS` with
     ```python
     OLD_NAME_EXEMPT = {
         "tests/test_branding.py",
         "docs/plans/0030-rebrand-filterhelm.md",
         "docs/plans/0032-old-name-cleanup.md",
     }
     ```
   - In `test_tracked_files_do_not_use_old_name`, skip only `name in OLD_NAME_EXEMPT`.
     The `docs/plans/` prefix check goes away.
   - Delete `test_old_name_only_in_migration_sections`.
   - Keep the `ROOT` import, because the brand-kit tests still use it.

## Contracts

N/A — documentation and one test file. No API, config key, env var, or DB change.

## Tests

- `tests/test_branding.py::test_tracked_files_do_not_use_old_name` — scans every tracked
  text file and fails on any old-name match outside the three exempt paths. It now covers
  README, `docs/deploy.md`, and every plan except 0030 and 0032.
- `tests/test_branding.py::test_old_name_only_in_migration_sections` — deleted, because
  no migration sections remain.
- `tests/test_container.py::test_sudoers_rule_matches_workflow` — unchanged. It must stay
  green after the §8 deletion, which confirms the rule line still appears once.

## Invariants at risk

1. **One instance mutated at a time** — untouched. No code changes.
2. **Diff before write** — untouched.
3. **Gravity gating** — untouched.
4. **No background scheduler** — untouched.
5. **Credentials never reach the browser** — untouched.
6. **Outbound requests via `url_policy`** — untouched.
7. **Auth middleware on every route** — untouched.
8. **Compiled frontend committed** — `frontend/` does not change, so no rebuild.
9. **`main` is production** — merging deploys, but the deploy builds the same image
   because no code, `deploy/`, or workflow file changes. The deleted §8 steps already ran
   on the host. The remaining runbook sections (§4–§7) still match the installed
   `filterhelm` units.

## Acceptance criteria

- [ ] `git grep -niE 'pihole[-_ ]?manager|pi-hole manager' -- ':!tests/test_branding.py' ':!docs/plans/0030-rebrand-filterhelm.md' ':!docs/plans/0032-old-name-cleanup.md'` prints nothing.
- [ ] `git diff main...HEAD --stat` lists only the files in Files.
- [ ] In plans 0001–0029, `git diff main...HEAD --word-diff` shows only the five rule substitutions.
- [ ] `grep -n '^## ' docs/deploy.md` runs §0 through §9 with no gaps, ending `## 8. Rollback` and `## 9. First deploy`.
- [ ] `docs/deploy.md` no longer contains "To abandon containers".
- [ ] README ends with the `## TODO` section.
- [ ] `tests/test_branding.py` has no `MIGRATION_SECTIONS` and no `test_old_name_only_in_migration_sections`.
- [ ] `./.venv/bin/pytest` green.
