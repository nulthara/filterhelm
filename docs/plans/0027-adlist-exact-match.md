# 0027 — Block-list matching: exact address first, refuse ambiguity

Status: in-progress           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0027-adlist-exact-match
Author: Claude (architect)
Date: 2026-10-03

**Revision 2.** Codex stopped on the first handoff because two ambiguity tests contradicted
Step 1. Keeping userinfo in the key means a bare `https://example.com/list` matches neither
credentialed record, so it returns 404, not 409. The 404 is correct, and a new test now
asserts it. The ambiguity tests now use stored `…/l` and `…/l/` with the submitted spelling
`HTTPS://EXAMPLE.COM/l`. Steps and design are unchanged.

**Revision 3.** Codex stopped again with the implementation in place and 635 of 636 tests
passing. The one failure, `test_adlist_bulk_uses_first_match_on_duplicate_keys`, asserts the
old first-match behaviour against an exact submission. Tests → Changed now covers that test.

## Problem

`_adlist_key` (`app/routes/api.py:149`) is not injective. It reads `parsed.hostname`, which
drops userinfo, and it blanks the fragment. Two stored block lists that differ only in
credentials or fragment get the same key. `_find_adlist` takes the first `next(...)` match
and `adlist_bulk` builds `current` with `setdefault`. So with
`https://alice:one@example.com/list` and `https://bob:two@example.com/list` both stored,
deleting Bob's list deletes Alice's and reports success under Bob's address. Selecting both
in a bulk action collapses to one write and one result row, and the dropped selection
reports nothing. This app's add path cannot create such records, but the Pi-hole CLI, a
Teleporter import, or another tool can. The bug breaks invariant #2 because the app writes
a record the user did not see or pick.

## Scope

In:
- Match a submitted address against the stored address exactly before trying a canonical key.
- Keep userinfo and fragment in `_adlist_key`, so the fallback key separates records that
  differ in them.
- Refuse a fallback match that hits more than one stored record. The single-record routes
  return 409. The bulk route returns an error row for that address.
- In bulk, when two different submitted addresses resolve to the same stored record, act
  once and give the later address an error row instead of dropping it.
- Update the three existing bulk tests that assert first-match or silent-dedupe behaviour,
  and add regression tests.

Out:
- Frontend changes. `AdlistsPage.tsx` already sends `row.instances[instance].address`, the
  exact stored string, and shows error rows and 409 details through existing handlers.
- `adlists_list` and `app/routes/drift.py`. Both group by the raw `item.address` and never
  call `_adlist_key`.
- Add-path validation. `AdlistAddInput` keeps the full `screen_adlist_url`.
- Matching for domains, DNS hosts, and CNAMEs.

## Approach

The key currently does two jobs. It identifies a record, and it absorbs Pi-hole's
normalization (issue #8, plan 0002). This plan splits those jobs into two steps of one
helper.

1. **Identity.** A submitted address equal to a stored address selects that record.
   `get_adlists` fetches `type=block` only, and Pi-hole's `adlist` table is unique on
   `(address, type)`, so at most one record matches exactly.
2. **Normalization fallback.** With no exact match, compare `_adlist_key` values. The key
   still lowercases scheme and host, renders the port, and strips trailing `/` from the
   path. It now keeps userinfo verbatim and keeps the fragment. One fallback match selects
   that record. Two or more is ambiguous and the app refuses to write.

This keeps issue #8 behaviour. A request for `https://example.com/ads/` still finds a
stored `https://example.com/ads` when only that record exists.

In bulk, literal duplicate strings in the request still collapse silently, because they
are the same selection. When two different strings resolve to one stored record, the first
string acts and later strings each get an error row naming the first. This replaces the
silent canonical dedupe from plan 0010 rev 3. That revision exists so one record is never
written twice, and this plan keeps that guarantee. It now reports each extra selection
instead of dropping it.

Rejected alternatives:
- **Exact match only, no fallback.** The UI never needs the fallback, but a stale page,
  where Pi-hole renormalized a list after load, would start returning 404. That reopens
  issue #8.
- **Keep userinfo/fragment in the key but skip the exact-first step.** That fixes the
  credential case but still lets `.../l` and `.../l/`, if both stored, collide. Exact-first
  picks the right one, and the ambiguity check covers the rest.
- **Return `success: true, changed: false` for a collapsed bulk alias.** That reads as "no
  change needed" when the row did nothing on its own behalf. An error row tells the user
  the selection was folded into another.

## Files

- `app/routes/api.py` — change `_adlist_key`, add `_match_adlist` and `AdlistAmbiguousError`,
  rewrite `_find_adlist` on top of `_match_adlist`, and rework the resolution loop in `adlist_bulk`.
- `tests/test_api.py` — update two bulk dedupe tests and add the tests listed below.
- `docs/potential-features.md` — move the §6 item to "Shipped" and update "Suggested next pick".

## Steps

1. **`_adlist_key`** (`app/routes/api.py:149`). Keep the `try/except ValueError` fallback to
   the raw string. Build the netloc from the raw userinfo plus the canonical host and port:
   - `userinfo, sep, _ = parsed.netloc.rpartition("@")`. Prefix `f"{userinfo}@"` when `sep`
     is non-empty. Leave userinfo case and percent-encoding exactly as given.
   - Host and port rendering stay as they are now.
   - Keep `parsed.fragment` and drop the `fragment=""` override.
   - Rewrite the comment block for the current behaviour. The key normalizes scheme/host
     case, port, and trailing slash, and it keeps userinfo, query, and fragment. It is a
     fallback that `_match_adlist` uses only when no address matches exactly.

2. **`AdlistAmbiguousError(Exception)`** and
   **`_match_adlist(items: Sequence[Adlist], address: str) -> Adlist | None`**, placed next to
   `_find_adlist`:
   - Return the first `item` with `item.address == address`.
   - Otherwise collect every `item` with `_adlist_key(item.address) == _adlist_key(address)`.
     Raise `AdlistAmbiguousError` for two or more, return the one item, or return `None`.
   - Import `Sequence` from `collections.abc` if it is not imported already, and import
     `Adlist` from `app.pihole_client` for the hint.

3. **`_find_adlist`**. Call `_match_adlist(client.get_adlists(), address)`.
   - Map `AdlistAmbiguousError` to `HTTPException(409, "Address matches more than one block
     list on this instance; use the exact stored address")`.
   - Map `None` to the existing 404 unchanged.
   - Replace the `# issue #8` comment with one line that says exact match comes first and
     the canonical key is the fallback.
   - `adlist_update` and `adlist_delete` need no change, because their `except HTTPException:
     raise` already passes the 409 through.

4. **`adlist_bulk`** resolution loop. Replace the `current` dict and the `requested` dict with:
   - `current = client.get_adlists()` as a list.
   - `claimed: dict[str, str] = {}`, mapping stored address to the first submitted address
     that resolved to it.
   - Iterate `dict.fromkeys(payload.addresses)` so literal duplicates collapse silently.
   - For each `address`, start `result` with `id=address` as now, then:
     - `AdlistAmbiguousError` gives the error "Matches more than one block list on this
       instance; use the exact stored address".
     - `None` gives the existing "No longer present on this instance".
     - `item.address in claimed` gives the error `f"Same block list as {claimed[item.address]}"`
       with no write.
     - Otherwise set `claimed[item.address] = address` and run the existing write, audit,
       and gravity logic unchanged.
   - Result order follows first appearance in `payload.addresses`, as now.

5. **`docs/potential-features.md`**. Remove the §6 `_adlist_key` entry and add a "Shipped"
   bullet that cites plan 0027. Write "Nothing is outstanding in this section." under §6.
   Replace "Suggested next pick" with one sentence saying no item is queued.

## Contracts

N/A. No request or response shape changes and no config or DB change. `BulkResult` keeps
`{id, success, changed, error}`. A single-record PATCH or DELETE can now return HTTP 409,
and the frontend's existing `request()` error path shows `detail`, as it does for the
other 409s in `api.py`. `frontend/src/api.ts` is untouched.

## Tests

All in `tests/test_api.py`.

Changed:
- `test_adlist_bulk_dedupes_canonical_aliases`. Still exactly one update. `results` now has
  three rows in submission order. The first is `success: True, changed: True`. The second
  and third are `success: False, changed: False, error: "Same block list as
  https://lists.example/block.txt"`.
- `test_adlist_bulk_delete_dedupes_canonical_aliases`. `deletes` is still
  `["https://lists.example/block.txt"]`. `results` has two rows, the first a success and
  the second the "Same block list as …" error.
- `test_adlist_bulk_uses_first_match_on_duplicate_keys`, renamed to
  `test_adlist_bulk_prefers_exact_match_on_duplicate_keys`. Same fixture, with `first` at
  `…/block.txt/` and `second` at `…/block.txt`, submitting `second.address`. The assertion
  becomes `bulk_client.updates == [(second, False, [4])]`. The old assertion pinned the bug
  this plan fixes, where an exact submission wrote whichever sibling came first.

New:
- `test_adlist_key_keeps_userinfo_and_fragment`. `_adlist_key` differs for
  `https://alice:one@example.com/list` vs `https://bob:two@example.com/list`, and for `…/l#a`
  vs `…/l#b`. It is equal for `HTTPS://user@Example.COM/l/` vs `https://user@example.com/l`.
- `test_adlist_delete_targets_the_exact_credentialed_record`. Store Alice's and Bob's lists,
  delete Bob's through `adlist_delete`, and assert only Bob's list is gone and the audit
  `before` carries Bob's address.
- `test_adlist_delete_does_not_match_a_bare_address_to_a_credentialed_record`. Store Alice's
  and Bob's lists, call `adlist_delete` with `https://example.com/list`, and assert
  `HTTPException` 404, no delete, and no audit record. The bare key differs from both
  credentialed keys, so this request matches nothing.
- `test_adlist_delete_rejects_an_ambiguous_canonical_match`. Store `https://example.com/l`
  and `https://example.com/l/`, call `adlist_delete` with `HTTPS://EXAMPLE.COM/l`, and
  assert `HTTPException` 409, no delete, and no audit record. That spelling matches neither
  stored address exactly, and both share its key.
- `test_adlist_exact_match_beats_a_canonical_sibling`. Store `https://example.com/l` and
  `https://example.com/l/`. `adlist_update` on each address updates that record only.
- `test_adlist_bulk_keeps_credentialed_records_distinct`. Store Alice's and Bob's lists, bulk
  delete both, and assert two deletes with the exact stored addresses and two success rows.
- `test_adlist_bulk_reports_an_ambiguous_address`. Store `https://example.com/l` and
  `https://example.com/l/`, bulk disable `HTTPS://EXAMPLE.COM/l`, and assert one error row
  with the ambiguity message and no update.
- `test_adlist_bulk_collapses_literal_duplicates_silently`. Submit the same string twice and
  assert one write and one result row.

Unchanged and still green, as regression guards:
`test_adlist_match_ignores_a_normalized_trailing_slash`,
`test_adlist_bulk_delete_uses_stored_address`, `test_adlist_key_is_total_for_unparseable_addresses`,
`test_adlist_bulk_handles_a_stored_address_that_will_not_parse`, `test_adlist_bulk_reports_missing_records`.

## Invariants at risk

- **#1 One instance mutated at a time.** Every touched route takes one `instance_name`. The
  plan changes record resolution only.
- **#2 Diff before write.** This is the invariant the plan repairs. Every write now targets
  the record the user submitted, or the app refuses. Nothing is written blind.
- **#3 Gravity gated behind real changes.** `mark_gravity_pending` still runs only after a
  landed write. Ambiguous, missing, and collapsed rows write nothing and do not mark gravity.
- **#5 Credentials never reach the browser.** Block-list userinfo already reaches the browser
  as part of `adlist.address` in `GET /api/adlists`, and this plan does not widen that. The
  new error strings echo only addresses the client submitted. This is list-URL userinfo,
  not a Pi-hole or app credential.
- **#6 url_policy.** No new outbound URL. The add path still screens.
- **#8 Committed frontend.** `frontend/` is untouched, so no rebuild is needed.

## Acceptance criteria

- [ ] With Alice's and Bob's lists stored, `DELETE /api/adlists/{name}` for Bob's address
      removes only Bob's list, as the new test shows.
- [ ] A bare `https://example.com/list` with only credentialed variants stored returns 404
      and deletes nothing.
- [ ] A spelling that matches no stored address exactly but shares a key with two stored
      records, such as `HTTPS://EXAMPLE.COM/l` against `…/l` and `…/l/`, returns 409 from
      PATCH and DELETE and an error row from bulk, and no write happens.
- [ ] A bulk request with two spellings of one stored list writes once and returns one row
      per distinct submitted string. Later rows carry `Same block list as <first>`.
- [ ] `test_adlist_match_ignores_a_normalized_trailing_slash` passes unchanged, so issue #8
      still holds.
- [ ] Diff limited to the Files list. `frontend/` and `app/static/ui/` are untouched.
- [ ] `./.venv/bin/pytest` green. The baseline is 628 passed, so expect 628 plus the 8 new tests.
