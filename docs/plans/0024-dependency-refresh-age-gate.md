# 0024 — Dependency refresh and 24-hour age gate

Status: in-progress           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0024-dependency-refresh-age-gate
Author: Claude (architect)
Date: 2026-09-28

## Problem

The Python lock and the npm tree have fallen a few releases behind. Neither
`pip-audit -r requirements.lock` nor `npm audit` reports a known vulnerability (checked
2026-09-27), so this is routine hygiene, not an emergency. The larger gap is policy. The
project wants to accept only releases that have been public for at least 24 hours, so a
hijacked upstream has time to be caught before its release reaches this repo. Nothing
enforces that today. `requirements.lock` pins versions but not file hashes. So the deploy's
`pip install -r requirements.lock` would accept a wheel file that an attacker adds to an
already-pinned release.

## Scope

In:
- Bump Python pins inside current constraints: anyio, idna, starlette, uvicorn.
- Bump npm packages inside the existing `package.json` ranges (`npm update`).
- Add `scripts/lock_hashes.py`. It rewrites `requirements.lock` with sha256 hashes and
  refuses any release or file younger than 24 hours.
- Hash-pin `requirements.lock`.
- Deploy installs with `--require-hashes --uploaded-prior-to P1D`.
- Add `frontend/.npmrc` with `min-release-age=1`.
- Document the refresh procedure in `AGENTS.md`.
- Add tests that guard the gate configuration and the script's selection logic.

Out:
- Major upgrades: vite 8, eslint / @eslint/js 10, TypeScript 7, @vitejs/plugin-react 6,
  globals 17, eslint-plugin-react-refresh 0.5. Each gets its own plan.
- pydantic_core 2.49.0. pydantic 2.13.5 pins `pydantic-core==2.46.5`, and there is no newer
  pydantic.
- Changing `package.json` ranges.
- Tools outside the app's own dependency trees: pip-audit and its deps in the dev venv, the
  gitea-runner binary, the host Python.
- CI-side auditing (a pip-audit or npm audit step in `deploy.yml`).

## Approach

The age rule applies where a version gets chosen, and the hash rule applies where a file
gets installed.

- **npm.** npm 12 supports `min-release-age` (a number of days). Setting it to `1` in
  `frontend/.npmrc` makes every `npm install` / `npm update` in `frontend/` ignore versions
  younger than 24 hours. `package-lock.json` already carries `integrity` hashes, and the
  deploy host never runs npm, so npm needs nothing more.
- **pip resolution.** pip 26.2 supports `--uploaded-prior-to P1D`. The documented refresh
  procedure uses it when trying new pins.
- **pip file pinning.** `scripts/lock_hashes.py` reads each `name==version` pin in
  `requirements.lock`. It fetches that release's file list from the PyPI JSON API and
  writes every eligible file's sha256 as `--hash` lines. A release is eligible only if its
  earliest file upload is at least 24 hours old. A file is eligible only if its own upload
  is at least 24 hours old, which drops a late-added wheel. The script hashes every
  platform's files, not only the ones this machine would install. That matters because the
  lock is generated on a Python 3.14 dev box and deployed to a different host.
- **Deploy.** `deploy.yml` adds `--require-hashes --uploaded-prior-to P1D` to the lock
  install. Hash mode rejects any file the lock didn't record. The age flag is a second
  filter that costs nothing.

Rejected:
- **pip-tools `pip-compile --generate-hashes`.** It adds a new dev dependency, which is
  itself supply-chain surface. It has no per-file age filter, and its compatibility with
  pip 26.2 is unverified.
- **`pip lock` / `pylock.toml`.** It would change the deploy's `pip install -r` flow and
  the lock format together. That is a bigger change than this one needs.
- **Age flags only, no hashes.** This leaves the late-added-wheel gap open.
- **Using `httpx` in the script.** The script uses stdlib `urllib.request`, so it runs from
  any Python 3.11+ without the venv.

## Files

- `pyproject.toml` — starlette `==1.7.0`, uvicorn `==0.54.0` in `dependencies`.
- `requirements.lock` — bumped pins, then rewritten with `--hash` lines by the script.
- `scripts/lock_hashes.py` — new; hash-pins the lock under the 24-hour rule.
- `.gitea/workflows/deploy.yml` — hash-checked, age-gated lock install.
- `docs/deploy.md` — the bootstrap `pip install -r requirements.lock` (around line 180) gets
  the same flags.
- `frontend/.npmrc` — new; `min-release-age=1`.
- `frontend/package-lock.json` — in-range updates.
- `app/static/ui/` — rebuilt bundle after the npm updates.
- `AGENTS.md` — new "Dependency updates" section.
- `tests/test_repo_hygiene.py` — add `"pypi.org"` to `ALLOWED_DOMAINS`, because the script
  contains that URL.
- `tests/test_lock_hashes.py` — new; unit tests for the script's pure functions.
- `tests/test_supply_chain.py` — new; guards the gate configuration across files.

## Steps

1. **`frontend/.npmrc`.** Create it with the single line `min-release-age=1`.
2. **npm in-range updates.** In `frontend/`, run `npm update`, then `npm audit`, which must
   report 0 vulnerabilities. Expected resolutions as of 2026-09-27: antd 6.6.5,
   @ant-design/icons 6.3.4, react / react-dom 19.3.0, @types/react / @types/react-dom
   19.3.0, typescript-eslint 8.70.1. `package.json` does not change. Run
   `npm run build && npm run lint`, then stage `app/static/ui/`.
3. **Python pins.** In `requirements.lock`, set `anyio==4.15.1`, `idna==3.20`,
   `starlette==1.7.0`, `uvicorn==0.54.0`. In `pyproject.toml`, set `starlette==1.7.0` and
   `uvicorn==0.54.0`. No other pins change. A dry run on 2026-09-27 confirmed there are no
   new transitive packages.
4. **Install and check.**
   `./.venv/bin/pip install --uploaded-prior-to P1D -r requirements.lock`, then
   `./.venv/bin/pip check` and `./.venv/bin/pip-audit -r requirements.lock --no-deps --disable-pip`.
   Both checks must be clean.
5. **`scripts/lock_hashes.py`.** Python 3.11+, `from __future__ import annotations`,
   stdlib only, full type hints. Module-level `MIN_AGE = timedelta(hours=24)` and
   `LOCK_PATH = <repo root>/requirements.lock`.
   - `class LockError(Exception)`.
   - `parse_lock(text: str) -> tuple[list[str], list[tuple[str, str]]]` returns the
     leading `#` header lines and the ordered `(name, version)` pins. Continuation lines
     (`--hash=...`, trailing `\`) are ignored, so a rerun on a hashed lock works. A line
     that isn't a comment, blank, continuation, or `name==version` raises `LockError`.
   - `fetch_release_files(name: str, version: str) -> list[dict[str, Any]]` does a GET on
     `https://pypi.org/pypi/{name}/{version}/json` with a 30 s timeout and returns
     `["urls"]`. This is the only network call.
   - `select_hashes(name: str, version: str, files: list[dict[str, Any]], now: datetime) -> list[str]`
     reads each file's `upload_time_iso_8601` and `digests.sha256`. It raises `LockError`
     if `files` is empty or if the earliest upload is newer than `now - MIN_AGE`. It drops
     files newer than `now - MIN_AGE` and prints each dropped filename to stderr. It
     raises `LockError` if no file remains. It returns the sorted unique sha256 hex
     strings.
   - `render_lock(header: list[str], entries: list[tuple[str, str, list[str]]]) -> str`
     writes the header, then per entry `name==version \` followed by
     `    --hash=sha256:<hex> \`, with no backslash after the last hash. It keeps input
     order and ends with a trailing newline.
   - `main() -> int` parses, fetches, and selects with `now = datetime.now(UTC)`. It
     writes the file only after every pin succeeds. On `LockError` or a network error it
     prints the package and reason to stderr, leaves the file untouched, and returns 1.
     The file ends with `if __name__ == "__main__": raise SystemExit(main())`.
   - Header comment for the regenerated lock:
     ```
     # Production runtime and build-tool lock generated on Linux.
     # Install this file before installing the application itself with --no-deps.
     # Hash-pinned by scripts/lock_hashes.py; files younger than 24 hours are excluded.
     ```
     The script writes the header it parsed. Update the header by hand once, before the
     first run.
6. **Hash the lock.** Run `python3 scripts/lock_hashes.py`. Every pin now has at least one
   `--hash`. Then do a clean-venv check:
   `python3 -m venv <scratch> && <scratch>/bin/pip install --require-hashes --uploaded-prior-to P1D -r requirements.lock`
   must succeed.
7. **`deploy.yml`.** In "Install dependencies", change the first command to
   `./.venv/bin/pip install --require-hashes --uploaded-prior-to P1D -r requirements.lock`.
   Leave the `-e .` line unchanged, because a local editable project cannot be
   hash-checked. Add a one-line comment above the command noting that the lock is
   hash-pinned and the age gate backs it up.
8. **`docs/deploy.md`.** Apply the same flags to the bootstrap
   `sudo -u gitea-runner ./.venv/bin/pip install -r requirements.lock` line.
9. **`AGENTS.md`.** Add a `## Dependency updates` section after "Frontend". It holds these
   rules, without URLs:
   - Accept only releases public for at least 24 hours. This mitigates hijacked upstreams.
   - npm: `frontend/.npmrc` enforces `min-release-age=1`. Run `npm audit` after any change.
     Majors need their own plan.
   - Python: edit pins in `requirements.lock` (and `pyproject.toml` for direct deps).
     Install with `--uploaded-prior-to P1D`. Run `pip check` and `pip-audit`. Then run
     `python3 scripts/lock_hashes.py`. Never hand-edit `--hash` lines.
10. **Hygiene allowlist.** Add `"pypi.org"` to `ALLOWED_DOMAINS` in
    `tests/test_repo_hygiene.py`.
11. **Tests** as listed below.

## Contracts

N/A — no API, config key, or DB change. The only file-format change is the
`requirements.lock` hash lines. That format is pip's standard `--hash` syntax, and the
deploy workflow is its only consumer.

## Tests

`tests/test_lock_hashes.py` loads `scripts/lock_hashes.py` through
`importlib.util.spec_from_file_location`, because `scripts/` is not a package. No test
touches the network.

- `test_parse_lock_reads_header_and_pins` — a plain lock yields its header lines and
  ordered pins.
- `test_parse_lock_ignores_existing_hash_lines` — a hashed lock parses to the same pins as
  the plain one.
- `test_parse_lock_rejects_unpinned_line` — `fastapi>=0.1` raises `LockError`.
- `test_select_hashes_keeps_files_older_than_24h` — two files uploaded 48 h before `now`
  yield both sorted hashes.
- `test_select_hashes_drops_late_added_file` — a release with one 48 h file and one 2 h
  file yields only the old file's hash.
- `test_select_hashes_rejects_young_release` — all files 23 h old raises `LockError`.
- `test_select_hashes_rejects_empty_release` — `[]` raises `LockError`.
- `test_render_lock_round_trips` — `parse_lock(render_lock(...))` returns the same header
  and pins. The last hash line has no trailing backslash.

`tests/test_supply_chain.py` reads repo files only.

- `test_npmrc_sets_min_release_age` — `frontend/.npmrc` has `min-release-age` with an
  integer value of at least 1.
- `test_deploy_lock_install_requires_hashes_and_age` — the `deploy.yml` line installing
  `-r requirements.lock` contains `--require-hashes` and `--uploaded-prior-to P1D`.
- `test_requirements_lock_is_fully_hashed` — every pin in `requirements.lock` has an exact
  `==` version and at least one `--hash=sha256:` of 64 hex chars.
- `test_pyproject_pins_match_lock` — every `name==version` in `pyproject.toml`
  `[project].dependencies` appears in the lock with the same version, with names
  normalized per PEP 503.

## Invariants at risk

- **6. Outbound requests go through `url_policy.py`.** `scripts/lock_hashes.py` makes an
  outbound request, but it is a developer tool. The app never imports or runs it, and its
  only URL is PyPI with a pinned name and version. The invariant covers app features that
  fetch URLs, so it does not apply. Nothing in `app/` changes.
- **8. Compiled frontend is committed.** The npm updates change the bundle, so step 2
  rebuilds and stages `app/static/ui/`.
- **9. `main` is production.** `deploy.yml` changes. A malformed lock or a missing hash
  would fail the deploy's install step before the restart, so the running service stays
  up on the old code. Step 6's clean-venv install proves the flags before merge. The
  change lands on `feature/0024-dependency-refresh-age-gate` only.
- **4. No background scheduler.** The script is run by hand. There is no cron job and no
  CI job.
- 1, 2, 3, 5, 7: not touched. No Pi-hole access, mutation, credential, or route changes.

## Acceptance criteria

- [ ] `requirements.lock` pins anyio 4.15.1, idna 3.20, starlette 1.7.0, uvicorn 0.54.0,
      and every other pin is unchanged.
- [ ] Every lock entry carries `--hash=sha256:` lines, and a rerun of
      `python3 scripts/lock_hashes.py` produces no diff.
- [ ] A clean venv installs with
      `pip install --require-hashes --uploaded-prior-to P1D -r requirements.lock`, and
      `pip check` is clean.
- [ ] `pip-audit -r requirements.lock --no-deps --disable-pip` reports no known
      vulnerabilities.
- [ ] `frontend/.npmrc` contains `min-release-age=1`. `npm audit` reports 0
      vulnerabilities. `package.json` is unchanged.
- [ ] `deploy.yml` and `docs/deploy.md` use the hash-checked, age-gated install.
- [ ] `AGENTS.md` has the "Dependency updates" section.
- [ ] `./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8420` starts, and
      `/api/auth/status` answers 200.
- [ ] `./.venv/bin/pytest` green. The count is the baseline plus the 12 new tests.
- [ ] `npm run build` + `npm run lint` clean, `app/static/ui/` committed.
