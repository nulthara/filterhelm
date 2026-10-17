# 0035 — Run backend and frontend checks in GitHub CI

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0035-github-ci
Author: Kyrnveil
Date: 2026-10-16

## Problem

The GitHub mirror (`nulthara/filterhelm`) is about to go public, and nothing on GitHub
runs the test suite. A contributor who opens a pull request gets no signal, and the
maintainer must check out every external change to learn whether it passes. Nothing
checks that the committed `app/static/ui/` matches the frontend source either, even
though invariant 8 depends on it. The backend test tool is also installed without
version pins or hashes (`pip install -e '.[dev]'` resolves `pytest>=8.0`), unlike every
other dependency in the repo.

## Scope

In:
- `.github/workflows/ci.yml`, which runs on pull requests to `main` and on pushes to `main`
- A backend job that installs from hashed locks and runs pytest on Python 3.14
- A frontend job that runs `npm ci`, lint, Vitest and the build on Node 26, then fails if
  the build changed `app/static/ui/`
- `requirements-dev.lock`, a hashed lock for pytest and its dependencies
- An optional lock-path argument for `scripts/lock_hashes.py`, so it can refresh either lock
- Pinning the `dev` extra in `pyproject.toml` to the locked pytest version
- Tests that pin the workflow's actions, triggers, permissions and steps, and that hash
  and version-check the dev lock
- Updating the dev-setup instructions in `README.md`, `CONTRIBUTING.md` and `AGENTS.md`

Out:
- Expanding the mutation tests listed in `before_public.md` (failure and rollback,
  concurrent drift, multi-host `dns.hosts`, IPv6, read-only settings, partial
  reachability). That needs its own plan.
- Running the private denylist tests in CI. `.private-denylist.toml` is gitignored, so
  those three tests skip on GitHub. The local `FILTERHELM_REQUIRE_DENYLIST=1` run before
  every mirror push stays the guard.
- A Python version matrix. The image runs 3.14 and the lock targets it.
- Enabling private vulnerability reporting and branch protection. Those are repo settings
  the maintainer changes by hand.
- Build attestation and SBOM, which stay deferred until the repo is public (plan 0034).
- CI on the forge. The forge runner is the deploy host and runs only `deploy.yml`.
- Dependency caching in CI. The install is small, and a shared cache adds a poisoning path.

## Approach

**One workflow, two parallel jobs.** `backend` and `frontend` share nothing, so they run
side by side and fail independently. Both trigger on `pull_request` (branches `main`) and
`push` (branches `main`). The workflow sets `permissions: contents: read` at the top level
and references no secrets, so a fork's pull request cannot write anything or read
credentials. It never uses `pull_request_target`. A `concurrency` group keyed on the ref
cancels stale runs when a branch gets new commits.

**Hashed test tools.** CI installs `requirements.lock` and the new
`requirements-dev.lock` in one `pip install --require-hashes` call, then installs the
app itself with `--no-deps --no-build-isolation -e .`. This matches the README's runtime
install and keeps every package in CI hash-pinned. The dev lock holds only pytest and
its four dependencies. PyYAML, which the supply-chain tests import, is already in the
runtime lock. Every pin was resolved on 2026-10-16 with `lock_hashes.select_hashes`, so
every file in it is older than 24 hours. A fresh venv installed both locks with
`--require-hashes`, `pip check` passed, and pytest ran 653 passed. OSV lists no known
vulnerabilities for any of the five pins.

Rejected alternatives:
- `pip install pytest==9.1.1` without hashes. It is the one unhashed install left in the
  repo, and the supply-chain tests exist to stop that.
- Adding pytest to `requirements.lock`. That lock feeds the production image, and test
  tools do not belong there.

`scripts/lock_hashes.py` gains an optional positional lock path so either lock can be
refreshed. It keeps `requirements.lock` as the default, so the AGENTS.md command does not
change.

**Bundle freshness.** After `npm run build`, the frontend job runs
`git status --porcelain --untracked-files=all -- app/static/ui` and fails if the output
is not empty. That catches modified, added and deleted bundle files. Vite names chunks by
content hash, and a local rebuild on 2026-10-16 left `app/static/ui/` byte-identical, so
a clean checkout reproduces the committed bundle. Node is pinned to major `26`, which
matches the local toolchain (26.11.1). `npm ci` installs exactly what
`package-lock.json` resolves.

Rejected alternative: skipping the freshness check and trusting reviewers. Invariant 8
breaks quietly, because a stale bundle still passes every other check and then ships.

**Full history in the backend job.** `test_commit_history_has_no_forge_review_trailers`
scans `git log HEAD`. A shallow checkout would scan one commit, so the backend checkout
sets `fetch-depth: 0`. Both checkouts set `persist-credentials: false`, because neither
job pushes.

**Pinned actions.** Every action is pinned to a full commit SHA with a version comment,
following plan 0034. These releases were public for more than 24 hours on 2026-10-16:

| Action | Version | Commit SHA |
| --- | --- | --- |
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/setup-node` | v7.1.0 | `949feb2413d6458794dcd2491c4babbbce0c15c1` |

Use these SHAs verbatim. The implementer has no network, so it must not try to resolve
or update them, and it must write `requirements-dev.lock` exactly as given in Step 1.

The forge ignores the new file. Gitea reads only `.gitea/workflows/` while that directory
exists.

## Files

- `.github/workflows/ci.yml` — new; the CI workflow
- `requirements-dev.lock` — new; hashed lock for pytest and its dependencies
- `scripts/lock_hashes.py` — accept an optional lock path
- `pyproject.toml` — pin the `dev` extra to `pytest==9.1.1`
- `tests/test_supply_chain.py` — check the dev lock and the CI workflow
- `tests/test_lock_hashes.py` — test the lock-path argument
- `README.md` — hashed backend-test install
- `CONTRIBUTING.md` — hashed dev setup, and a note that CI runs the same checks
- `AGENTS.md` — name the dev lock in "Dependency updates"

## Steps

1. **Dev lock.** Create `requirements-dev.lock` with exactly this content:

   ```text
   # Test-tool lock for CI and local development, generated on Linux.
   # Install together with requirements.lock; pip then requires hashes for both.
   # Hash-pinned by scripts/lock_hashes.py; files younger than 24 hours are excluded.
   iniconfig==2.3.0 \
       --hash=sha256:c76315c77db068650d49c5b56314774a7804df16fee4402c1f19d6d15d8c4730 \
       --hash=sha256:f631c04d2c48c52b84d0d0549c99ff3859c98df65b3101406327ecc7d53fbf12
   packaging==26.3 \
       --hash=sha256:94edc256424af38762eb31306eed28beb9f0efc50a8837492c9d6fd6004aed79 \
       --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c
   pluggy==1.6.0 \
       --hash=sha256:7dcc130b76258d33b90f61b658791dede3486c3e6bfb003ee5c9bfb396dd22f3 \
       --hash=sha256:e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746
   Pygments==2.21.0 \
       --hash=sha256:2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9 \
       --hash=sha256:610ca751c9bc2492b38eb9a38a7fbc93edbbb2d7182edaf34e66ae493dee5c8c
   pytest==9.1.1 \
       --hash=sha256:1088fbde8f2b49d95a549a195707afa7a76a3ce9bcadc26b6d71f0ffda5fe313 \
       --hash=sha256:37a86b45efb9a47a61a36449063e8e18d0cab3161329fc099eb21783169c4f0c
   ```

2. **Lock script path.** In `scripts/lock_hashes.py`, change `main()` to
   `main(argv: list[str] | None = None) -> int`. Read `argv` from `sys.argv[1:]` when it
   is `None`. With no arguments, use `LOCK_PATH`. With one argument, use `Path(argv[0])`.
   With more than one, print `usage: lock_hashes.py [LOCK]` to stderr and return 2.
   Replace every `LOCK_PATH` use inside `main` with the chosen path. Update the module
   docstring to say it hash-pins a lock file, `requirements.lock` by default. Nothing
   else in the script changes.

3. **pyproject extra.** In `pyproject.toml`, change
   `dev = ["pytest>=8.0"]` to `dev = ["pytest==9.1.1"]`.

4. **Workflow.** Create `.github/workflows/ci.yml`:

   ```yaml
   name: CI

   on:
     pull_request:
       branches: [main]
     push:
       branches: [main]

   permissions:
     contents: read

   concurrency:
     group: ci-${{ github.ref }}
     cancel-in-progress: true

   jobs:
     backend:
       runs-on: ubuntu-latest
       timeout-minutes: 15
       steps:
         - name: Checkout
           uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
           with:
             fetch-depth: 0
             persist-credentials: false
         - name: Set up Python
           uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
           with:
             python-version: "3.14"
         - name: Install locked dependencies
           run: |
             python -m pip install --require-hashes --only-binary=:all: \
               -r requirements.lock -r requirements-dev.lock
             python -m pip install --no-deps --no-build-isolation -e .
             python -m pip check
         - name: Run tests
           run: python -m pytest -q

     frontend:
       runs-on: ubuntu-latest
       timeout-minutes: 15
       defaults:
         run:
           working-directory: frontend
       steps:
         - name: Checkout
           uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
           with:
             persist-credentials: false
         - name: Set up Node
           uses: actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1 # v7.1.0
           with:
             node-version: "26"
         - name: Install dependencies
           run: npm ci
         - name: Lint
           run: npm run lint
         - name: Test
           run: npm test
         - name: Build
           run: npm run build
         - name: Check committed bundle is current
           run: |
             changes=$(git status --porcelain --untracked-files=all -- ../app/static/ui)
             if [ -n "$changes" ]; then
               echo "app/static/ui is stale. Run npm run build and commit the output." >&2
               echo "$changes" >&2
               exit 1
             fi
   ```

   `--only-binary=:all:` matches the Dockerfile's install, so CI never builds an sdist.
   Every pin in both locks ships a wheel for CPython 3.14 on Linux x86_64. A binary-only
   `pip install --dry-run --require-hashes` of both locks succeeded on 2026-10-16.

5. **Supply-chain tests.** In `tests/test_supply_chain.py`, add the tests in the Tests
   section. Turn the body of `test_requirements_lock_is_fully_hashed` into a helper
   `_assert_fully_hashed(path: Path) -> None` and call it for both locks, so the two
   checks cannot drift.

6. **Lock-script test.** Add the test in the Tests section to `tests/test_lock_hashes.py`.

7. **Docs.**
   - `README.md` "Backend tests" block: replace `./.venv/bin/pip install -e '.[dev]'`
     with `./.venv/bin/pip install --require-hashes -r requirements.lock -r requirements-dev.lock`
     followed by `./.venv/bin/pip install --no-deps --no-build-isolation -e .`.
   - `CONTRIBUTING.md` "Development setup": replace the two pip lines with the same two
     commands. Under "Checks", add one sentence saying GitHub CI runs the same backend
     and frontend checks on every pull request, and fails if `app/static/ui/` does not
     match a fresh build.
   - `AGENTS.md` "Dependency updates", Python bullet: say test tools live in
     `requirements-dev.lock`, refreshed with `python3 scripts/lock_hashes.py requirements-dev.lock`.

## Contracts

- New file contract: `requirements-dev.lock` uses the `requirements.lock` format
  (`name==version` lines, each followed by `--hash=sha256:` lines, `#` header comments).
- New CLI contract: `python3 scripts/lock_hashes.py [LOCK]`. No argument means
  `requirements.lock`. More than one argument exits with status 2.
- `pyproject.toml` `[project.optional-dependencies] dev = ["pytest==9.1.1"]`.
- No API, config key, DB, or `frontend/src/api.ts` change.

## Tests

- `tests/test_supply_chain.py::test_requirements_lock_is_fully_hashed` — refactored onto
  `_assert_fully_hashed`; still asserts `requirements.lock` is fully hashed.
- `tests/test_supply_chain.py::test_dev_lock_is_fully_hashed` — `requirements-dev.lock`
  has at least one entry, and every entry is `name==version` with only sha256 hashes.
- `tests/test_supply_chain.py::test_dev_extra_matches_dev_lock` — every entry in
  `pyproject.toml`'s `dev` extra is pinned with `==` and matches the version in
  `requirements-dev.lock`. Use the same name normalization as
  `test_pyproject_pins_match_lock`.
- `tests/test_supply_chain.py::test_dev_lock_does_not_overlap_runtime_lock` — no
  normalized package name appears in both locks, so pip never sees two pins for one
  package.
- `tests/test_supply_chain.py::test_ci_workflow_pins_actions_by_sha` — every `uses:` line
  in `ci.yml` matches `@<40 hex> # vX.Y.Z`, the same check as the publish workflow.
- `tests/test_supply_chain.py::test_ci_workflow_shape` — parse `ci.yml` with `yaml.safe_load`
  and assert:
  - `workflow[True] == {"pull_request": {"branches": ["main"]}, "push": {"branches": ["main"]}}`,
    so there is no `schedule`, `pull_request_target` or tag trigger
  - `workflow["permissions"] == {"contents": "read"}`, and no job sets its own `permissions`
  - `set(workflow["jobs"]) == {"backend", "frontend"}`
  - the text contains no `secrets.` reference
  - every checkout step sets `persist-credentials: false`, and the backend checkout sets
    `fetch-depth: 0`
  - the backend's pip install step contains `--require-hashes`, `--only-binary=:all:`,
    `-r requirements.lock` and `-r requirements-dev.lock`, and a later step runs `pytest`
  - the frontend steps run `npm ci`, `npm run lint`, `npm test` and `npm run build`, in
    that order, followed by a step whose `run` contains `git status --porcelain` and
    `app/static/ui`
- `tests/test_lock_hashes.py::test_main_writes_given_lock_path` — write a temp lock with
  `demo==1.0`, monkeypatch `lock_hashes.fetch_release_files` to return one
  `release_file("demo-1.0.whl", 48, "a" * 64)`, call `lock_hashes.main([str(path)])`, and
  assert it returns 0 and the file now has `--hash=sha256:` followed by 64 `a`s. Use
  `release_file`'s age relative to the real clock, or monkeypatch time, so the 24-hour
  check passes regardless of the run date. Also assert `lock_hashes.main(["a", "b"]) == 2`.

## Invariants at risk

- **4. No background scheduler.** The workflow has no `schedule` trigger, and it never
  touches a Pi-hole. `test_ci_workflow_shape` pins the trigger set exactly.
- **5. Credentials never reach the browser.** Unaffected. CI uses no secrets, and the
  workflow has no `secrets.` reference (tested).
- **8. Compiled frontend is committed.** Strengthened. The frontend job fails when a fresh
  build differs from the committed `app/static/ui/`. The plan changes no frontend source,
  so the bundle must not change in this branch.
- **9. `main` is production.** The workflow has read-only permissions and no deploy step.
  The forge reads only `.gitea/workflows/`, so the new file cannot trigger a deploy, and
  `deploy.yml` does not change.
- 1, 2, 3, 6, 7 — not near. No backend code, route, outbound URL or mutation path changes.

## Acceptance criteria

- [ ] `.github/workflows/ci.yml` matches Step 4, with the three SHAs from Approach
- [ ] `requirements-dev.lock` matches Step 1 byte for byte
- [ ] A fresh venv installs `-r requirements.lock -r requirements-dev.lock` with
      `--require-hashes --only-binary=:all:`, `pip check` passes, and pytest runs green there
- [ ] `python3 scripts/lock_hashes.py a b` exits 2 with the usage line, without network
- [ ] `./.venv/bin/pytest` green, including the six new or refactored tests
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] `cd frontend && npm run build` leaves `git status -- app/static/ui` empty
- [ ] Diff is limited to the Files list, and `deploy.yml` and `publish-image.yml` are unchanged
- [ ] After the mirror push, the CI run on `github/main` passes both jobs. This is checked
      after merge, because the forge cannot run the workflow.
