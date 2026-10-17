# 0036 — Close mutation-safety test gaps and guard settings apply against drift

Status: in-review            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0036-mutation-safety-tests
Author: Kyrnveil
Date: 2026-10-17

## Problem

`docs/design.md` lists mutation tests as open work. They should cover partial failures
and rollback, concurrent drift, multi-host `dns.hosts` lines, IPv6, read-only settings,
and partly reachable instance sets. An audit on 2026-10-17 found most of that already
covered for DNS hosts, CNAMEs and the bulk paths, including multi-host lines. Six gaps
remain. One of them is a real defect. The Settings page sends every managed field to
`POST /api/settings/{name}/apply`, and apply recomputes the diff against the live
config. If any value changes on the instance after the user reviewed the diff, apply
writes a different change set from the one the user approved. That silently reverts the
other writer's change, which breaks diff-before-write.

## Scope

In:
- **Settings drift guard.** Apply carries the `before` values the user reviewed, and it
  returns 409 without writing when the instance no longer matches them.
- **Settings failure tests.** A rejected PATCH, such as Pi-hole refusing an env-forced
  read-only key, returns 502. A transport failure returns 503. Neither writes an audit
  entry.
- **IPv6 single-record tests** for DNS add, delete and update with non-canonical
  spellings. Also a fix so an update that only respells the address returns
  `changed: false` instead of a misleading 409.
- **DNS update stale-original test.** The existing 409 path has no test.
- **Partial-reachability list tests** for `dns_records_list`, `adlists_list`,
  `domains_list`, `groups_list` and `clients_list`.
- **Multi-instance add transport-failure tests** for domains, groups and clients. A
  transport error must report the sanitized per-instance message and keep going.
- **Single-instance write failure tests** for `group_update`, `client_update_groups`,
  `client_delete`, `domain_update` and `domain_delete`. They cover 502 and 503 with no
  audit entry.
- **Docs.** Update `docs/design.md` §5.4 and the "Mutation tests" open item.

Out:
- More multi-host `dns.hosts` tests. Delete, update and bulk paths already cover sibling
  hostnames (`test_dns_delete_preserves_sibling_hostnames`,
  `test_dns_bulk_delete_handles_two_hostnames_on_one_line` and others).
- More DNS and CNAME rollback tests. More than 15 exist.
- Sending only edited fields from the Settings page. The drift guard makes a stale field
  fail loudly, and an unexpected revert still shows in the review diff.
- Detecting env-forced settings ahead of time through `GET /api/config?detailed=true`
  and marking fields read-only in the UI. The tests pin today's fail-closed behavior, and
  no claim is made about how real FTL handles a multi-key PATCH with one forced key.
- Audit-after-write in single-instance group, client and domain routes. `record()` runs
  inside the `try`, so an audit failure after a landed write surfaces as a 503. This is a
  real follow-up, but this plan does not change it.
- The known gaps already listed in `docs/design.md` (mid-loop timeouts, whole-entry
  write-back for lists and domains).

## Approach

**Drift guard.** A new `SettingsApplyInput` extends `SettingsInput` with a required
`expected: dict[str, Any]`. It maps each key in the reviewed diff to the `before` value
the diff showed. `settings_apply` already reads the config and recomputes `changes`
inside one client session. It now builds `current = {change["key"]: change["before"] for
change in changes}`, and it raises 409 before patching when `current` differs from
`expected`. The guard runs before the existing "no changes" early return. So when
someone else lands the same change first, apply returns 409 instead of reporting zero
changes for a diff the user approved.

The comparison uses `json.dumps(..., sort_keys=True)` on both sides, not `==`. Python
treats `0 == False` and `1 == True` as equal. A strict comparison keeps a privacy level
of `0` from matching a reviewed `false`.

The guard catches both kinds of drift:
- A value the user edited changed again. Its `before` differs.
- A value the user did not touch changed. The page sends every field, so that field
  becomes a new key in `changes`, and the key sets differ.

`expected` is required, not optional. The frontend and backend ship in one commit, and
an optional field would let any caller skip the check.

Rejected alternatives:
- An ETag or config hash. Pi-hole exposes no config version, so the server would hash
  the whole tree, and any unrelated config change would then block apply.
- Sending only edited fields. That narrows the window but does not close it. A field the
  user edited can still change between review and apply.

**Frontend.** `SettingsPage` keeps the reviewed `before` map in state next to
`pendingValues`. A small helper `reviewedBefore(changes)` in a new
`frontend/src/settings.ts` builds that map, so Vitest can cover it. On any apply
failure, the page shows the error detail and closes the review modal. It keeps the
form's values, so the user can review again without retyping. Today the modal stays
open on failure, and a retry would only resend the same stale request.

**IPv6 respelling.** `dns_record_update` returns `changed: false` when
`old_record == new_record`, comparing the strings as submitted. A change from
`fd00::1` to `fd00:0:0:0:0:0:0:1` gets past that check and then hits "The replacement
record already exists". The no-op check moves to a structural comparison, with equal
canonical addresses and equal normalized hostnames. It is the same idea as the existing
`_canonical_addr` matching.

**Tests for existing behavior.** Every other item adds tests without changing code. The
fakes gain per-instance failure knobs, following the existing `fail_add` pattern.

## Files

- `app/routes/api.py` — add `SettingsApplyInput`, add the drift guard to
  `settings_apply`, make the no-op check in `dns_record_update` structural
- `frontend/src/api.ts` — `applySettings(name, values, expected)` sends `expected`
- `frontend/src/settings.ts` — new; `reviewedBefore(changes)`
- `frontend/src/settings.test.ts` — new; Vitest for `reviewedBefore`
- `frontend/src/pages/SettingsPage.tsx` — keep the reviewed map, send it, close the
  modal on failure
- `app/static/ui/` — rebuilt bundle
- `tests/test_api.py` — settings, IPv6, DNS stale-original, and DNS/adlist reachability
  tests, plus updates to five existing settings tests
- `tests/test_domains.py` — reachability, add transport failure, update/delete failure
- `tests/test_groups.py` — reachability, add transport failure, update failure
- `tests/test_clients.py` — reachability, add transport failure, update/delete failure
- `docs/design.md` — §5.4 drift rule; replace the "Mutation tests" open item

## Steps

1. **Backend contract.** In `app/routes/api.py`, add after `SettingsInput`:

   ```python
   class SettingsApplyInput(SettingsInput):
       expected: dict[str, Any]
   ```

   Change the `settings_apply` signature to `payload: SettingsApplyInput`.
   `settings_diff` keeps `SettingsInput`.

2. **Drift guard.** In `settings_apply`, right after
   `changes = _changes(tree, payload.values)` and before `if not changes:`, add:

   ```python
   current = {change["key"]: change["before"] for change in changes}
   if json.dumps(current, sort_keys=True) != json.dumps(payload.expected, sort_keys=True):
       raise HTTPException(
           status_code=409,
           detail=f"Settings on {name} changed since the review. Review the changes again.",
       )
   ```

   Add `import json` to the stdlib imports, since the module does not import it today.
   The existing `except HTTPException: raise`
   passes the 409 through. Nothing is patched or audited on this path.

3. **IPv6 no-op.** In `dns_record_update`, replace `if old_record == new_record:` with a
   structural check. After both `_dns_record(...)` calls (which validate), compare
   `_canonical_addr(payload.old_address) == _canonical_addr(payload.address)` and
   `old_hostname == new_hostname`, where both hostnames are normalized with
   `.strip().lower().rstrip(".")`. Move the two hostname assignments above the check.
   The return value stays `{"changed": False, "record": new_record}`.

4. **API client.** In `frontend/src/api.ts`:

   ```ts
   applySettings: (name: string, values: SettingsValues, expected: SettingsValues) =>
     request<{ applied: number; changes: Change[] }>(`/api/settings/${encodeURIComponent(name)}/apply`, {
       method: 'POST', body: JSON.stringify({ values, expected }),
     }),
   ```

5. **Helper.** Create `frontend/src/settings.ts`:

   ```ts
   import type { Change, SettingsValues } from './api'

   export const reviewedBefore = (changes: Change[]): SettingsValues =>
     Object.fromEntries(changes.map((change) => [change.key, change.before]))
   ```

6. **Settings page.** In `frontend/src/pages/SettingsPage.tsx`:
   - Add `const [reviewed, setReviewed] = useState<SettingsValues>()`.
   - In `review`, after a non-empty diff, call `setReviewed(reviewedBefore(result.changes))`
     alongside `setPendingValues(values)`.
   - In `apply`, return early unless `selected`, `pendingValues` and `reviewed` are all
     set. Call `api.applySettings(selected, pendingValues, reviewed)`.
   - In the `catch` of `apply`, after `message.error(...)`, call `setChanges([])`,
     `setPendingValues(undefined)` and `setReviewed(undefined)`. Do not reload settings,
     so the form keeps the user's edits.
   - Clear `reviewed` wherever `pendingValues` is cleared today, meaning the success path
     and the modal `onCancel`.

7. **Rebuild.** `cd frontend && npm run build && npm run lint && npm test`, then commit
   `app/static/ui/`.

8. **Test fakes.** Add failure knobs and reset them in each module's setup function:
   - `tests/test_domains.py` `FakeDomainsPiholeClient`: `unreachable: set[str]` (in
     `get_domains`, raise `httpx.ConnectError("boom")` when `self.name` is in it),
     `transport_fail_add: set[str]` (same in `add_domain`), and
     `fail_write: dict[str, Exception]` (raise the mapped exception in `update_domain`
     and `delete_domain`).
   - `tests/test_groups.py` `FakeGroupsPiholeClient`: `unreachable`,
     `transport_fail_add`, and `fail_write` (raise in `update_group`).
   - `tests/test_clients.py` `FakeClientsPiholeClient`: `unreachable`,
     `transport_fail_add`, and `fail_write` (raise in `update_client` and `delete_client`).
   - Each setup function must also capture audits in a list, replacing the
     `lambda *_args: None` stub, where a test asserts no audit. `test_groups.py` already
     captures them in `FakeGroupsPiholeClient.audits`.

9. **Tests.** Add the tests in the Tests section, and update the five existing settings
   tests that call `settings_apply`.

10. **Docs.** In `docs/design.md`:
    - §5.4: after "Every change shows a diff before it is applied." add a bullet saying
      apply refuses with 409 when the instance's values no longer match the reviewed diff.
    - "Safety and testing": replace the "Mutation tests" bullet with one naming what
      remains, meaning env-forced settings detection and audit-after-write in
      single-instance group, client and domain routes.

## Contracts

`POST /api/settings/{name}/apply` request body:

```python
# backend — app/routes/api.py
class SettingsInput(BaseModel):
    values: dict[str, Any]

class SettingsApplyInput(SettingsInput):
    expected: dict[str, Any]   # key → `before` from the reviewed diff; {} when the diff was empty
```

```ts
// frontend — src/api.ts
applySettings: (name: string, values: SettingsValues, expected: SettingsValues) => ...
// body: { values, expected }
```

New response: `409 {"detail": "Settings on <name> changed since the review. Review the changes again."}`.
The 200 response shape (`{applied, changes}`) and `POST /api/settings/{name}/diff` do not
change. `PATCH /api/dns/records` keeps its shape. A respelled-address update now returns
200 `{"changed": false, ...}` instead of 409.

## Tests

`tests/test_api.py`, settings:
- Update `test_settings_apply_writes_pihole_revservers_array`,
  `test_settings_apply_disables_conditional_forwarding_with_empty_array`,
  `test_settings_apply_writes_only_changed_managed_fields`,
  `test_settings_rejects_unknown_fields` and `test_settings_rejects_invalid_privacy_level`
  to build `api.SettingsApplyInput(values=..., expected=...)`. The `expected` value is the
  `before` map their diff would show. The two 422 tests pass `expected={}`, and their
  assertions do not change.
- `test_settings_apply_input_requires_expected`. `api.SettingsApplyInput(values={})`
  raises `ValidationError`, and `api.SettingsInput(values={})` still validates. This
  backs the 422 acceptance item.
- `test_settings_apply_rejects_value_changed_since_review`. `values` sets
  `dns.queryLogging=False` and `misc.privacylevel=0`. `expected` is
  `{"dns.queryLogging": True}`. Before calling, set `FakePiholeClient.tree["misc"]["privacylevel"] = 2`
  and restore it in `finally`. Asserts a 409 with the exact detail, `patches == []`, and
  no audit call.
- `test_settings_apply_rejects_change_that_already_landed`. The tree has
  `dns.queryLogging=False`. `expected` is `{"dns.queryLogging": True}`, and `values` sets
  it to `False`. Asserts a 409, and that nothing is patched or audited.
- `test_settings_apply_drift_check_is_type_strict`. The tree has `misc.privacylevel=0`.
  `values` sets it to `1`, and `expected` is `{"misc.privacylevel": False}`. Asserts a 409.
- `test_settings_apply_proceeds_when_expected_matches`. `expected` is
  `{"dns.queryLogging": True}`, and `values` sets it to `False`. Asserts
  `applied == 1`, one patch, and one audit call with the before and after values.
- `test_settings_apply_rejected_patch_is_502_and_not_audited`. `patch_config` raises
  `PiholeAPIError("Config item dns.queryLogging is read-only", status_code=400)`.
  Monkeypatch the method on `FakePiholeClient`. Asserts a 502 whose detail contains the
  message, and no audit call. This is the read-only (env-forced) case.
- `test_settings_apply_transport_failure_is_503_and_not_audited`. `patch_config` raises
  `httpx.ConnectError("boom")`. Asserts a 503 with detail
  `"Unable to reach Pi-hole instance"`, no audit call, and no `"boom"` in the detail.

`FakePiholeClient.tree` is a class-level dict that the tests share. Every test that
edits it restores it in `finally`, following
`test_settings_apply_disables_conditional_forwarding_with_empty_array`.

`tests/test_api.py`, DNS:
- `test_dns_add_rejects_ipv6_duplicate_spelled_differently`. The tree holds
  `"fd00::1 box.lan"`. Adding `fd00:0:0:0:0:0:0:1` / `box.lan` returns 409, and
  `added_hosts == []`.
- `test_dns_delete_matches_ipv6_spelled_differently`. The tree holds
  `"fd00::1 box.lan nas.lan"`. Deleting `fd00:0::1` / `box.lan` deletes the stored raw line
  and re-adds `"fd00::1 nas.lan"`.
- `test_dns_update_ipv6_respelling_is_a_noop`. The tree holds `"fd00::1 box.lan"`.
  Updating from `fd00::1` to `fd00:0:0:0:0:0:0:1` with the same hostname returns
  `{"changed": False, ...}`, with no adds and no deletes.
- `test_dns_update_moves_record_to_ipv6`. The tree holds `"192.168.1.10 server.lan"`.
  Updating to `fd00::10` adds `"fd00::10 server.lan"` and deletes the old line, in the
  same order `test_dns_update_adds_before_removing_old_record` asserts.
- `test_dns_update_rejects_stale_original`. `old_address` and `old_hostname` match
  nothing stored. Asserts a 409 with "The original record changed; refresh and try
  again", and that nothing is written.

`tests/test_api.py`, reachability:
- `test_dns_records_list_reports_unreachable_instance`. Same shape as
  `test_cname_list_reports_unreachable_instance`, monkeypatching `api._settings_tree` so
  `pihole2` raises a 503. Asserts `errors == {"pihole2": "Unable to reach Pi-hole instance"}`,
  that the `pihole1` records are present, and that their state is `"consistent"`, not
  `"missing"`.
- `test_adlists_list_reports_unreachable_instance`. Add `pihole2`. `FakePiholeClient`
  in this file ignores its base URL, so monkeypatch `api._client` with a wrapper that
  raises `httpx.ConnectError("boom")` for `instance.name == "pihole2"` and otherwise
  returns `FakePiholeClient()`. Asserts `errors == {"pihole2": "Unable to reach Pi-hole instance"}`,
  that the `pihole1` lists are still returned, and that `"boom"` appears nowhere in the
  response.

`tests/test_domains.py`, `tests/test_groups.py` and `tests/test_clients.py`, for each module:
- `test_<module>_list_reports_unreachable_instance`. `pihole2` is in `unreachable`.
  Asserts `errors == {"pihole2": "Unable to reach Pi-hole instance"}`, and that `pihole1`
  items are still returned.
- `test_<singular>_add_hides_transport_error_and_continues`. `pihole1` is in
  `transport_fail_add`, and the add targets both instances. Asserts the results are
  `[False, True]`. The `pihole1` error is the module's sanitized string (`"Unable to update
  Pi-hole domains"`, `"... groups"` or `"... clients"`), and `pihole2` was still written.
- `test_<singular>_write_failure_is_not_audited`, parametrized over the module's
  single-instance write routes and two exceptions. The routes are `domain_update` and
  `domain_delete`, `group_update`, or `client_update_groups` and `client_delete`. The
  exceptions are `PiholeAPIError("write failed")`, which gives 502 and the message, and
  `httpx.ConnectError("boom")`, which gives 503 and `"Unable to reach Pi-hole instance"`.
  Each case asserts the status, the detail, and that no audit was recorded.

`frontend/src/settings.test.ts`:
- `reviewedBefore` maps each change's `key` to its `before`, keeping `false`, `0`, `[]`
  and `null` as they are.
- `reviewedBefore([])` returns `{}`.

## Invariants at risk

- **1. One instance mutated at a time.** Untouched. Settings apply stays on one
  instance. The new multi-instance add tests check that the existing sequential loop
  continues past a transport failure. They do not change the loop.
- **2. Diff before write.** Strengthened. Apply now refuses to write anything except the
  change set the user reviewed. Tests cover both kinds of drift (an edited value and an
  untouched one) and a change that already landed.
- **3. Gravity gated behind real changes.** Not touched. No adlist write path changes.
- **4. No background scheduler.** Not touched.
- **5. Credentials stay in the backend.** The new `expected` field carries only managed
  setting values, which the browser already receives from `GET /api/settings/{name}`.
  The transport tests assert that exception text does not leak into responses.
- **6. Outbound URLs through `url_policy`.** Not touched. No new outbound requests.
- **7. Every route behind auth.** No new routes. The settings apply route keeps its path.
- **8. Compiled frontend committed.** Step 7 rebuilds and commits `app/static/ui/`. The
  CI bundle check from plan 0035 enforces it.
- **9. `main` is production.** Normal feature-branch flow.

## Acceptance criteria

- [ ] `POST /api/settings/{name}/apply` without `expected` returns 422
- [ ] Apply returns 409 when the instance drifted since the review, and the Pi-hole is
      not patched (tests cover an edited value, an untouched value, and a change that
      already landed)
- [ ] Apply still succeeds when `expected` matches, and it is audited once
- [ ] A rejected PATCH returns 502, a transport failure returns 503, and neither is audited
- [ ] An IPv6 respelled update returns `changed: false` with no writes
- [ ] Every test named in the Tests section exists, with the assertions described
- [ ] In the browser, review a change, then change a different setting on the instance
      out of band, then apply. The page shows the 409 message, closes the modal, and
      keeps the form edits.
- [ ] `./.venv/bin/pytest` green
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] `npm run build` + `npm run lint` + `npm test` clean, and `app/static/ui/` committed
- [ ] Diff limited to the Files list
