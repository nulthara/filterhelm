# 0040 — Contain audit failures after a landed write in single-instance routes

Status: in-review             <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0040-audit-after-write-single-routes
Author: Kyrnveil
Date: 2026-10-18

## Problem

Plan 0012 made the bulk routes survive an audit failure after a write lands. The
single-instance and fan-out routes did not get the same treatment. In 21 of them the
`record()` call runs after Pi-hole has accepted the change, and an exception from it
reaches the error handler. A user then sees a failure for a change that landed. In the
fan-out routes the instance is reported as `success: false`, so a retry hits an
"already exists" error. The DNS and settings routes call `record()` outside any `try`,
so the failure surfaces as an unhandled 500. Plan 0036 recorded this as a follow-up in
`docs/design.md`.

## Scope

In:
- Route each post-write `record()` call listed under Files through a module-local
  `_audit_landed_write`, the helper plan 0012 added to `api.py` and `domains.py`
- Add the same helper to `clients.py` and `groups.py`
- Tests that make `record` raise and assert each route still reports its landed write

Out:
- `backup_download` in `api.py`. It is a read, and it fails closed on purpose: no audit
  line, no archive. Its test (`test_instance_backup_fails_closed_when_audit_fails`) stays.
- `auth.py` events (`login`, `logout`, and the failure events). Nothing is written to a Pi-hole.
- Sites that already use `_audit_landed_write` (bulk routes, `run_ftl_action`)
- Merging the four copies of the helper into one function (see Approach)
- Changing what is audited or the audit format

## Approach

**Reuse the 0012 pattern as it stands.** `_audit_landed_write(instance, action, before, after)`
calls `record(...)` inside `try/except Exception` and sends a failure to `logger.exception`.
Its docstring explains why it is module-local: the existing tests patch
`<module>.record`, and a helper that resolves `record` from its own module's globals keeps
that working. This plan keeps that rule, so `clients.py` and `groups.py` each get a copy
and `api.py` and `domains.py` reuse theirs.

**Tradeoff accepted.** An audit failure now leaves a write with no audit line, visible
only in the server log. That is the 0012 decision for bulk routes, extended to the rest.
The alternative, failing the response, misreports a mutation that cannot be undone.

**`_partial_dns_failure` also uses the helper.** Its `record()` runs while the route
builds a 502 that carries the exact lines a failed rollback lost (issue #12). An audit
exception there would replace that detail with an unrelated error.

**Rejected: one shared helper in `app/audit.py`.** It would take the recorder as an
argument or break the `monkeypatch.setattr(<module>, "record", ...)` tests. A merge is a
reasonable later cleanup and gets its own plan.

## Files

- `app/routes/clients.py` — add `logger` and `_audit_landed_write`; use it at the three `record()` sites (`client_add` fan-out, `client_update_groups`, `client_delete`)
- `app/routes/groups.py` — add `logger` if absent and the helper; use it for `group_add`, `group_update`, the two orphan-cascade records and `group_delete` in `group_delete`'s route
- `app/routes/domains.py` — use the existing helper for `domain_add` (fan-out), `domain_update` and `domain_delete` (single routes)
- `app/routes/api.py` — use the existing helper for `blocking_change`, `adlist_add`, `adlist_update`, `adlist_delete`, `gravity_update`, `dns_record_add`, `dns_record_delete`, `dns_record_update`, `dns_cname_add`, `dns_cname_delete`, `dns_cname_update`, `settings_change`, and the record in `_partial_dns_failure`
- `tests/test_clients.py`, `tests/test_groups.py`, `tests/test_domains.py`, `tests/test_api.py` — failing-`record` tests
- `docs/design.md` — in the "Mutation safety follow-ups" bullet, drop the audit-after-write half and keep "Detect env-forced settings"
- `docs/plans/0040-audit-after-write-single-routes.md` — this plan

## Steps

1. In `clients.py` and `groups.py`, add `import logging`, `logger = logging.getLogger(__name__)`
   if missing, and a copy of `_audit_landed_write` with the 0012 docstring trimmed to what
   applies. `record` must be imported at module level, as it is today.
2. Replace each listed `record(...)` call with `_audit_landed_write(...)`, keeping the
   arguments unchanged. In `group_delete`, the per-orphan record and the final
   `group_delete` record both change. The orphan record sits in a `try` that turns any
   exception into a per-item error, so it must no longer be able to flag a landed
   orphan write as failed.
3. Leave the position of each call alone. A call that runs after `mark_gravity_pending` or
   `clear_gravity_pending` stays after it.
4. In `_partial_dns_failure`, call `_audit_landed_write` so the 502 detail survives an audit failure.
5. Add the tests below.
6. In `docs/design.md` (the "Mutation safety follow-ups" bullet), remove only the
   audit-after-write half. The env-forced settings follow-up stays.

## Contracts

N/A — responses keep their shapes. The change affects which status and body a client sees
only when the audit write fails, and then the body is the route's normal success body.

## Tests

For every route in Files, with `record` patched to raise `OSError`, on the module the
route lives in:

- `tests/test_clients.py::test_client_routes_report_success_when_audit_fails` — parametrized
  over add (fan-out), update groups, delete. Asserts a 2xx and `success: true` for add, that
  the fake Pi-hole saw the write, and that `logger.exception` ran (via `caplog`).
- `tests/test_groups.py::test_group_routes_report_success_when_audit_fails` — add, update,
  delete with `orphans: delete` and `orphans: ignore`. For the orphan case, each orphan
  item is `success: true` and the group is deleted.
- `tests/test_domains.py::test_domain_single_routes_report_success_when_audit_fails` — add,
  update, delete.
- `tests/test_api.py::test_blocking_and_adlist_routes_report_success_when_audit_fails` —
  blocking change, adlist add, update, delete, gravity update.
- `tests/test_api.py::test_dns_routes_report_success_when_audit_fails` — record and CNAME
  add, update, delete. Asserts a normal 2xx body, not a 500.
- `tests/test_api.py::test_settings_apply_reports_success_when_audit_fails` — a landed
  settings change returns `applied` with its changes.
- `tests/test_api.py::test_partial_dns_failure_detail_survives_audit_failure` — the 502
  detail still lists the lost lines.
- The existing `test_instance_backup_fails_closed_when_audit_fails` and the bulk audit
  tests must keep passing unchanged.

## Invariants at risk

- Invariant 2 (diff before write) and 1 (one instance at a time) are not touched. Call
  order and fan-out stay the same.
- Audit completeness is the nearby property. This plan trades a possible missing audit
  line, logged server-side, for an honest response. Plan 0012 made the same trade for bulk routes.
- Invariant 4 (no background scheduler) is not affected.

## Acceptance criteria

- [ ] Every site named in Files uses `_audit_landed_write`, and `grep -n "[^_a-z]record(" app/routes/{clients,groups,domains,api}.py` shows only the helpers' internal calls, `backup_download`, and `auth.py` is untouched
- [ ] The new tests fail when a route is reverted to a bare `record(...)` (the reviewer checks one by hand)
- [ ] `test_instance_backup_fails_closed_when_audit_fails` still passes unchanged
- [ ] The diff touches only the Files list
- [ ] `./.venv/bin/pytest` green, and `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] No frontend change, so no rebuild of `app/static/ui/`
