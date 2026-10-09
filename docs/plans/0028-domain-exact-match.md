# 0028 — Domain matching: exact value first, regex verbatim, refuse ambiguity

Status: in-progress         <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0028-domain-exact-match
Author: Claude (architect)
Date: 2026-10-08

## Problem

`_find_domain` (`app/routes/domains.py:174`) lowercases and strips trailing dots from both
sides before it compares, for every kind. That is wrong for regex records. The patterns
`ads` and `ads.` are different regexes, and Pi-hole stores `Ads` and `ads` as separate
records. With regex `ads` and `ads.` both stored, a PATCH or DELETE for `ads.` hits `ads`
when it comes first, and the app reports success under the submitted pattern. Plan 0010
found this, fixed only the bulk route, and deferred `_find_domain`. The exact-kind path has
the same flaw plan 0027 fixed for block lists. `_find_domain` and `domain_bulk` both take
the first record whose normalized key matches. So if `Ads.example` and `ads.example` are
both stored, a request for the second can write the first. Bulk also folds two spellings
of one record into a single row and drops the second without a word. This app's add path
lowercases exact domains, but the Pi-hole UI, CLI, or a Teleporter import can store case
variants. Both flaws break invariant #2, because the app writes a record the user did not
pick.

## Scope

In:
- Match a submitted domain against the stored value exactly before any normalization.
- Never normalize regex patterns. A regex matches its stored pattern exactly or not at all.
- For exact kind, keep the lowercase and trailing-dot fallback but refuse it when it hits
  more than one record. Single-record routes return 409. Bulk returns an error row.
- In bulk, when two different submitted spellings resolve to one stored record, act once
  and give each later spelling an error row instead of dropping it.
- Update the two existing bulk tests that pin first-match or silent alias dedupe, and add
  regression tests.
- Record the fix in `docs/potential-features.md`.

Out:
- Frontend changes. `DomainsPage.tsx` sends `row.domain`, which is the raw stored string
  from `GET /api/domains`, for single-record and bulk actions. It already shows 409
  details and bulk error rows through existing handlers.
- `domains_list`. It groups rows by the raw `(type, kind, domain)` and never normalizes.
- Add-path validation. `_validate_exact_domain` and `_validate_regex_domain` stay as they
  are.
- Adlist, DNS host, and CNAME matching.

## Approach

Mirror plan 0027. One helper, `_match_domain`, resolves a submitted `(domain, type, kind)`
against a list of stored records in two steps.

1. **Identity.** Filter to records with the same `type` and `kind`. A record whose
   `domain` equals the submitted string selects that record. Pi-hole's `domainlist` table
   is unique on `(domain, type)`, and its integer `type` encodes both allow/deny and
   exact/regex, so at most one record matches.
2. **Exact-kind fallback.** For `kind == "regex"`, stop and return `None`. For
   `kind == "exact"`, compare `domain.lower().rstrip(".")` on both sides. One hit selects
   that record. Two or more is ambiguous and the app refuses to write.

`_find_domain` wraps the helper for PATCH and DELETE. `domain_bulk` calls it per submitted
ref against one `get_domains()` read. Bulk tracks which stored records it has already
acted on, so a second spelling of the same record gets an error row and no second write.
Literal duplicate refs in one request still collapse silently, since they are the same
selection.

This keeps the case-insensitive lookup that `test_domain_delete_uses_stored_form_not_submitted_case`
and `test_domain_bulk_delete_matches_case_insensitively` guard. A request for
`ads.example.com.` still finds a stored `Ads.Example.com` when only that record exists.

Rejected alternatives:
- **Regex-only fix.** It closes the regex hole but leaves exact-kind first-match and the
  silent bulk dedupe. Those are the plan-0027 bug in a different module.
- **Exact match only, no fallback.** The UI never needs the fallback. But external
  callers and stale pages that submit a different case would start getting 404, and two
  existing tests pin the fallback as intended behaviour.
- **Return `success: true, changed: false` for a collapsed bulk alias.** That reads as
  "already in that state" in the page's summary. An error row says the selection was folded
  into another.

## Files

- `app/routes/domains.py` — add `DomainAmbiguousError` and `_match_domain`, rewrite
  `_find_domain` on top of it, remove `_bulk_domain_key`, rework the `domain_bulk`
  resolution loop, and update the comment in `domain_delete`.
- `tests/test_domains.py` — change two bulk tests and add the tests listed below.
- `docs/potential-features.md` — add a "Shipped" bullet.

## Steps

1. **`DomainAmbiguousError(Exception)`** and
   **`_match_domain(items: Sequence[Domain], domain: str, type: str, kind: str) -> Domain | None`**,
   placed where `_bulk_domain_key` sits now (`app/routes/domains.py:114`).
   - `candidates = [item for item in items if item.type == type and item.kind == kind]`.
   - Return the first candidate with `item.domain == domain`.
   - If `kind != "exact"`, return `None`.
   - Otherwise collect candidates with `item.domain.lower().rstrip(".") == domain.lower().rstrip(".")`.
     Raise `DomainAmbiguousError` for two or more, return the one item, or return `None`.
   - Add `from collections.abc import Sequence`. `Domain` is already imported.
   - Delete `_bulk_domain_key`. Only `domain_bulk` uses it, and no test imports it.

2. **`_find_domain`** (`app/routes/domains.py:174`). Call
   `_match_domain(client.get_domains(), domain, type, kind)`.
   - Map `DomainAmbiguousError` to `HTTPException(409, "Domain matches more than one record
     on this instance; use the exact stored value")`.
   - Map `None` to the existing 404 unchanged.
   - `domain_update` and `domain_delete` need no logic change, because their
     `except HTTPException: raise` passes the 409 through before any write or `record()`.

3. **`domain_delete` comment** (`app/routes/domains.py:361-363`). Replace the three lines
   with one present-state comment. It should say the route deletes the matched record's
   stored value because the exact-kind fallback can match a different spelling.

4. **`domain_bulk` resolution loop** (`app/routes/domains.py:275-287`). Replace the
   `current` and `requested` dicts with:
   - `current = client.get_domains()` as a list.
   - `claimed: dict[tuple[str, str, str], str] = {}`, mapping a stored record's
     `(item.type, item.kind, item.domain)` to the first submitted `domain` string that
     resolved to it.
   - Iterate `dict.fromkeys((ref.type, ref.kind, ref.domain) for ref in payload.domains)`
     so literal duplicates collapse silently. `DomainRef` is a Pydantic model and is not
     hashable, so key on the tuple.
   - For each `(type, kind, domain)`, start `result` with `id=f"{type}:{kind}:{domain}"`
     as now, then:
     - `DomainAmbiguousError` gives the error "Matches more than one record on this
       instance; use the exact stored value".
     - `None` gives the existing "No longer present on this instance".
     - A record whose key is already in `claimed` gives the error
       `f"Same domain as {claimed[key]}"` with no write.
     - Otherwise set `claimed[key] = domain` and run the existing write and audit logic
       unchanged.
   - Result order follows first appearance in `payload.domains`, as now.

5. **`docs/potential-features.md`**. Add a bullet at the top of the "Shipped since these
   notes were first written" list, above the plan 0027 bullet. It should say domain
   actions match the exact stored value first, never normalize regex patterns, refuse an
   ambiguous exact-kind fallback, and report collapsed bulk aliases — plan 0028. §6 and
   "Suggested next pick" stay as they are.

## Contracts

N/A — no request or response shape changes, and no config or DB change.
`DomainBulkResult` keeps `{id, success, changed, error}`. `PATCH /api/domains/{instance}`
and `DELETE /api/domains/{instance}` can now return HTTP 409 with the ambiguity detail.
Both already return 409 for "No credential stored", and `frontend/src/api.ts`'s
`request()` error path shows `detail`. `frontend/src/api.ts` is untouched.

## Tests

All in `tests/test_domains.py`. Baseline is 636 passed.

Changed:
- `test_domain_bulk_dedupes_case_and_trailing_dot_aliases`. Stored `ads.example.com`.
  Submitted `Ads.Example.COM`, `ads.example.com.`, `ads.example.com`. The first falls back
  to the one stored record and acts. The second falls back to the same record. The third
  matches it exactly. Both later refs find it claimed. Still exactly one update.
  `results` now has three rows in submission order. The first is
  `{"id": "deny:exact:Ads.Example.COM", "success": True, "changed": True, "error": None}`.
  The second and third are `success: False, changed: False,
  error: "Same domain as Ads.Example.COM"`, with ids `deny:exact:ads.example.com.` and
  `deny:exact:ads.example.com`.
- `test_domain_bulk_uses_first_match_on_duplicate_keys`, renamed to
  `test_domain_bulk_prefers_exact_match_on_duplicate_keys`. Same fixture, `first` at
  `ADS.example.` and `second` at `ads.example`, submitting `second.domain`. The assertion
  becomes `domain_bulk_client.updated == [("pihole1", second, False, [0])]`. The old
  assertion pinned the bug this plan fixes.

New:
- `test_domain_delete_matches_regex_verbatim`. Store regex `ads` and regex `ads.` (both
  deny). Delete `ads.` with `kind="regex"`. Assert only the `ads.` record is gone. The old
  code deletes `ads` here.
- `test_domain_update_regex_has_no_case_fallback`. Store regex `Ads` only. PATCH regex
  `ads` with `enabled=False`. Assert `HTTPException` 404 and `updated == []`.
- `test_domain_exact_match_beats_a_case_sibling`. Store exact `Ads.example` and exact
  `ads.example`. `domain_update` on each stored value updates that record only, checked
  through `FakeDomainsPiholeClient.updated`.
- `test_domain_rejects_an_ambiguous_exact_match`, parametrized over `update` and
  `delete`. Store exact `Ads.example` and exact `ads.example.`. Submit `ADS.EXAMPLE`,
  which matches neither stored value exactly but shares a key with both. Assert
  `HTTPException` 409, no update or delete, and no `record()` call (monkeypatch `record`
  to append to a list).
- `test_domain_bulk_reports_an_ambiguous_exact_match`. Same two stored records. Bulk
  disable `ADS.EXAMPLE`. Assert one row with
  `error: "Matches more than one record on this instance; use the exact stored value"`
  and `updated == []`.
- `test_domain_bulk_collapses_literal_duplicates_silently`. Store `ads.example`. Submit
  `("ads.example", "deny", "exact")` twice. Assert one update and one result row.

Expect 636 + 7 = 643 passed (the parametrized test adds two).

Unchanged and still green, checked against the new algorithm:
- `test_domain_delete_uses_stored_form_not_submitted_case`. One stored record, fallback
  hit, deletes `Ads.Example.com`.
- `test_domain_bulk_delete_matches_case_insensitively`. Same shape in bulk.
- `test_domain_bulk_regex_patterns_are_not_normalized`. `ads` and `Ads` match exactly.
  `ADS` has no regex fallback and gets "No longer present on this instance".
- `test_domain_bulk_does_not_dedupe_across_type_or_kind`. Each ref matches exactly in its
  own `(type, kind)`.
- `test_domain_not_found_on_instance_returns_404`, `test_domain_bulk_enable_writes_only_changed_records`,
  `test_domain_bulk_continues_after_one_failure`, `test_domain_bulk_preserves_group_membership`,
  `test_domain_bulk_reports_success_when_audit_fails`, `test_domain_bulk_does_not_mark_gravity`.
- `tests/test_api.py` touches `domains` only through `_audit_landed_write`, which this
  plan does not change.

## Invariants at risk

- **#1 One instance mutated at a time.** Every touched route takes one `instance_name`.
  The plan changes record resolution only.
- **#2 Diff before write.** This is the invariant the plan repairs. Every write now
  targets the record the user submitted, or the app refuses.
- **#3 Gravity gated behind real changes.** Domain routes never mark gravity, and
  `test_domain_bulk_does_not_mark_gravity` still guards that.
- **#5 Credentials never reach the browser.** The new error strings echo only domains the
  client submitted.
- **#8 Committed frontend.** `frontend/` is untouched, so no rebuild is needed.

## Acceptance criteria

- [ ] With regex `ads` and `ads.` stored, `DELETE /api/domains/{name}` for `ads.` removes
      only `ads.`.
- [ ] A regex submitted in a different case from the stored pattern returns 404 and
      writes nothing.
- [ ] With exact `Ads.example` and `ads.example.` stored, `ADS.EXAMPLE` returns 409 from
      PATCH and DELETE and an error row from bulk, and nothing is written or audited.
- [ ] A bulk request with two spellings of one stored exact domain writes once and
      returns one row per distinct submitted ref. Later rows carry `Same domain as <first>`.
- [ ] `test_domain_delete_uses_stored_form_not_submitted_case` passes unchanged.
- [ ] `_bulk_domain_key` no longer exists, and `grep -n "lower().rstrip" app/routes/domains.py`
      shows only `_validate_exact_domain` and `_match_domain`.
- [ ] Diff limited to the Files list. `frontend/` and `app/static/ui/` are untouched.
- [ ] `./.venv/bin/pytest` green at 643 passed.
