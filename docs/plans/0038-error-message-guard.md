# 0038 — Guard Pi-hole error messages against dynamic content

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0038-error-message-guard
Author: Kyrnveil
Date: 2026-10-17

## Problem

The first GitHub CodeQL scan raised 11 medium `py/stack-trace-exposure` alerts. Each one
flags a route that copies `str(exc)` from a `PiholeAPIError` into a JSON response. The
alerts are false positives today, because every `PiholeAPIError` message in
`app/pihole_client.py` is a fixed string, a fixed string with `resp.status_code`, or a local
`error` constant. Nothing enforces that. A future change such as
`PiholeAPIError(f"... {resp.text}")` would leak upstream content through about 41 route
sites without any test failing. The alerts are being dismissed on the strength of that
convention, so the convention needs a test.

## Scope

In:
- An AST-based pytest that scans `app/**/*.py` and fails when `PiholeAPIError`,
  `PiholeActionUnconfirmedError`, or any subclass defined under `app/` is built with a
  message that is not statically safe
- A self-test of the scanner, so the guard cannot rot into a no-op
- A short note in `AGENTS.md` stating the convention

Out:
- Changing any route, `str(exc)` site, or error message
- A `public_message()` helper (option 2 from the review; rejected below)
- Dismissing the 11 CodeQL alerts. The user does that after merge, citing this test.
- Guarding other exception types or the `HTTPException(detail=...)` sites

## Approach

**Static check over the constructor, not the call sites.** The risk sits where the message
is built, so the test inspects every constructor call once. It parses each file under
`app/` with `ast` and visits every `Call` whose callee name is `PiholeAPIError`, or the
name of a subclass found under `app/`. The subclass set comes from the same scan:
iterate `ClassDef` nodes whose bases include a name already in the set, until stable.

**Allowed first argument (the message).** Exactly one of these shapes:

1. A `str` `Constant`.
2. An f-string (`JoinedStr`) whose every `FormattedValue` is `resp.status_code` or the
   bare name `path`. `path` is assigned in `run_action` from the fixed `_FTL_ACTION_PATHS`
   table and never from request input.
3. A bare `Name` that is only ever assigned a `str` `Constant` within the enclosing
   function (the existing `error = "..."` pattern). Every `Assign` to that name in the
   function must be a constant, and at least one must exist.

Anything else fails, including `str(exc)`, `exc.args`, `resp.text`, `resp.json()`,
`.content`, `%` or `.format()` formatting, string concatenation, and a missing first
argument. Keyword arguments other than `status_code` are not used today and also fail,
so the shape stays closed. `status_code=` must be an `int` `Constant` or the name
`resp.status_code`.

**Failure output.** Each violation reports `path:lineno` and the unparsed offending
expression, so the contributor sees what to change.

**Alternatives rejected.**
- *Option 1, dismiss only.* Leaves the convention unenforced.
- *Option 2, `public_message(exc)` helper.* Touches 41 sites under the full loop, and
  CodeQL cannot see that the helper sanitizes, so the alerts would likely persist.
- *Regex over source text.* Misses multi-line raises and cannot tell a constant from a
  variable.
- *Runtime test that triggers each error.* Needs a fake for every endpoint, and it still
  would not catch an unexercised branch.

## Files

- `tests/test_error_messages.py` — new. The scanner, the guard test, and the scanner self-tests.
- `AGENTS.md` — one bullet under Backend stating the message convention and naming the test.

## Steps

1. Create `tests/test_error_messages.py` with `from __future__ import annotations` and a
   module docstring saying it guards `PiholeAPIError` messages.
2. Add `_pihole_error_names(tree_map)` that returns `{"PiholeAPIError"}` plus every class
   defined under `app/` that derives from a name in the set, iterated to a fixed point.
3. Add `_violations(source: str, filename: str, names: set[str]) -> list[str]` that walks the
   tree and applies the three allowed shapes. Track the enclosing `FunctionDef` for shape 3
   (a module-level call with a bare name fails).
4. Add `test_pihole_error_messages_are_static()` that runs the scan over `ROOT / "app"`
   (`rglob("*.py")`, skipping `app/static/`) and asserts the violation list is empty, with
   the list in the assertion message.
5. Add scanner self-tests that call `_violations` on inline source snippets (see Tests).
6. Add the `AGENTS.md` bullet under "Backend": `PiholeAPIError` messages must be fixed
   strings, optionally with `resp.status_code`, because routes return them to the browser.
   Name `tests/test_error_messages.py` as the guard.
7. Run the full suite and confirm the real tree reports zero violations.

## Contracts

N/A — test and docs only. No API, config, or DB change, and no `frontend/src/api.ts` change.

## Tests

- `tests/test_error_messages.py::test_pihole_error_messages_are_static` — the real `app/`
  tree has no violations.
- `::test_scanner_accepts_safe_shapes` — a constant, `f"... {resp.status_code}"`, an
  f-string with `path`, and `error = "x"` then `PiholeAPIError(error)` all pass.
- `::test_scanner_rejects_dynamic_messages` — each of these yields one violation:
  `str(exc)`, `f"{exc}"`, `f"{resp.text}"`, `resp.json()["detail"]`, `"a" + x`,
  `"%s" % x`, `"{}".format(x)`, and no arguments.
- `::test_scanner_rejects_reassigned_error_variable` — `error = "x"` followed by
  `error = str(exc)` fails.
- `::test_scanner_rejects_unbound_name` — a bare name never assigned in the function fails.
- `::test_scanner_follows_subclasses` — `class Foo(PiholeAPIError)` then
  `Foo(str(exc))` fails, and `PiholeActionUnconfirmedError(...)` is covered.
- `::test_scanner_rejects_dynamic_status_code_keyword` — `status_code=int(x)` fails and
  `status_code=403` passes.

## Invariants at risk

None. The change adds a test and one docs bullet. Invariant 5 (credentials and Pi-hole
access stay in the backend) is reinforced, because the test blocks upstream content from
reaching responses through error messages.

## Acceptance criteria

- [ ] `tests/test_error_messages.py` exists and the guard test passes on the current tree
- [ ] Adding `raise PiholeAPIError(str(exc))` to `app/pihole_client.py` makes the guard
      fail, naming the file and line (the reviewer checks this by hand and reverts)
- [ ] The diff touches only `tests/test_error_messages.py`, `AGENTS.md`, and this plan file
- [ ] `./.venv/bin/pytest` green, with the baseline count plus the new tests
- [ ] No frontend change, so no rebuild of `app/static/ui/`
