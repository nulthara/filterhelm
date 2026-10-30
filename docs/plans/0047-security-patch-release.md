# 0047 — Security patch release 1.0.3

Status: in-review            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0047-security-patch-release
Author: Claude (architect)
Date: 2026-10-29

## Problem

Two layers of the shipped image carry known security fixes we have not picked up.

`pip-audit -r requirements.lock` (run 2026-10-29) reports two advisories.

- **certifi 2026.7.22 — PYSEC-2026-4320.** The CA bundle still trusts `emSign ECC Root CA - G3`.
  Eight eMudhra intermediates under that root have compromised keys and are revoked. httpx
  verifies TLS against certifi, so the update check (`app/update_check.py`, `verify=True`) and
  any Pi-hole with `verify_ssl` on (`app/pihole_client.py`) would accept a certificate forged
  with one of those keys. An on-path attacker could impersonate GitHub or an HTTPS Pi-hole.
- **setuptools 84.0.0 — GHSA-xmj3-9gf7-h52w.** The legacy wheel-install path on Windows can
  create an `__init__.py` outside its target directory. FilterHelm builds and runs on Linux, so
  the risk to us is negligible. The bump is cheap and clears the audit.

The Dockerfile pins `python:3.14-slim-trixie` by digest to an image built 2026-09-19. A rebuild
never picks up newer bases, and nothing has refreshed the pin since plan 0025. The current tag
(built 2026-10-28) differs from it as shown below. This was checked by running `dpkg-query` in both images.

| Component | Pinned | Current |
| --- | --- | --- |
| CPython | 3.14.7 | 3.14.10 |
| libssl3t64, openssl, openssl-provider-legacy | 3.5.7-1~deb13u2 | 3.5.7-1~deb13u3 |
| ca-certificates | 20250419 | 20250419+deb13u1 |
| liblzma5 | 5.8.1-1+deb13u1 | 5.8.1-1+deb13u2 |
| libpcre2-8-0 | 10.46-1~deb13u2 | 10.46-1~deb13u3 |
| tzdata | 2026c-0+deb13u1 | 2026e-0+deb13u1 |
| pip | 26.2.1 | 26.2.2 |

Python's `ssl` module links the image's libssl, so the OpenSSL fix sits on the same TLS path as
the certifi fix. The GHCR image rebuilds only on a `v*` tag, so self-hosters get any of this only
once a release ships. That is why this plan also cuts 1.0.3.

## Scope

In:
- certifi `2026.7.22` → `2026.10.23` in `requirements.lock`.
- setuptools `84.0.0` → `84.2.1` in `requirements.lock` and in `pyproject.toml` `[build-system] requires`.
- Regenerate `requirements.lock` hashes with `scripts/lock_hashes.py`.
- Extend `test_pyproject_pins_match_lock` so it also checks `[build-system] requires` against the lock.
- Dockerfile base image digest → `sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8`
  (same tag, `python:3.14-slim-trixie`).
- Bump the app version to 1.0.3 with `scripts/bump_version.py patch`.

Out:
- Every other outdated Python pin, including cryptography 50.0.2, pycparser 3.12, more-itertools 11.2.1,
  and the dev-lock tools (pytest 9.2.0, pluggy 1.7.0, iniconfig 2.3.1). None has an advisory.
- FastAPI 0.162 / Starlette 1.10 / Pydantic 2.14. These need their own plan.
- `requirements-dev.lock`. It stays byte-identical.
- Changing the base image tag, for example to a newer Debian or Python minor version.
- An `apt-get upgrade` layer in the Dockerfile. It would make builds non-reproducible and bypass the digest pin.
- Automated staleness checks (Renovate, a CI digest check). A later plan can cover them.
- npm trees and pip-audit's own venv dependencies.
- The compose files. `compose.yaml` builds from the Dockerfile, and `compose.ghcr.yaml` tracks `:latest`.
- Tagging `v1.0.3` and pushing to the GitHub mirror. The user does both after merge.
- Frontend rebuild. The bundle does not embed the version (checked: `app/static/ui/` has no
  `1.0.2` string, and `vite.config` defines no version), so `app/static/ui/` does not change.

## Approach

Follow the AGENTS.md "Dependency updates" procedure for Python, and apply the same 24-hour rule to
the base image. Every target passes the gate.

| Target | First published | Notes |
| --- | --- | --- |
| certifi 2026.10.23 | 2026-10-23 | latest release, no PyPI advisory |
| setuptools 84.2.1 | 2026-10-20 | latest release, no PyPI advisory |
| base image `cd89c30e…` | 2026-10-28 08:11 UTC | multi-arch index digest of `3.14-slim-trixie`, the tag's current value |

Edit only the two `name==version` lines in the lock, then run `scripts/lock_hashes.py` and let it
rewrite every hash. `parse_lock` ignores `--hash` lines, so the stale hashes under the edited pins
do not need hand-removal. The script refetches hashes for all 30 pins. Any hash change outside
the certifi and setuptools blocks means an upstream file set changed, and that is a stop condition.

Pin the base image by its multi-arch index digest, as plan 0025 did, so the GHCR publish still
builds `linux/amd64` and `linux/arm64` from one `FROM` line. Keep the existing comment above `FROM`.

The guard test closes a gap. `test_pyproject_pins_match_lock` checks `[project] dependencies`
but not `[build-system] requires`, so the setuptools pin could drift between the two files without
a failure.

The rejected alternatives are listed below.
- Pinning the minimum fixed versions (certifi 2026.10.18, setuptools 84.1.0). Both latest releases
  are old enough for the age gate, so the older ones buy nothing.
- Folding the patch refresh in. The user chose the flagged pins only.
- Using the tag without a digest. That drops reproducibility and the age gate.

## Files

- `pyproject.toml` — setuptools build-system pin `84.0.0` → `84.2.1`.
- `requirements.lock` — certifi and setuptools pins, plus regenerated hashes for both.
- `tests/test_supply_chain.py` — `test_pyproject_pins_match_lock` also covers `[build-system] requires`.
- `Dockerfile` — base image digest in the `FROM` line.
- `app/__init__.py` — `__version__` `1.0.2` → `1.0.3` (via `scripts/bump_version.py`).
- `frontend/package.json` — `version` → `1.0.3` (via the script).
- `frontend/package-lock.json` — both root `version` fields → `1.0.3` (via the script).

## Steps

1. In `pyproject.toml`, change `requires = ["setuptools==84.0.0"]` to `requires = ["setuptools==84.2.1"]`.
2. In `requirements.lock`, change the line `certifi==2026.7.22 \` to `certifi==2026.10.23 \` and
   `setuptools==84.0.0 \` to `setuptools==84.2.1 \`. Do not touch `--hash` lines by hand.
3. Run `python3 scripts/lock_hashes.py`. It needs network access to `pypi.org`.
   - Check `git diff requirements.lock`. Only the certifi and setuptools blocks may change.
   - If the script fails, or any other block's hashes change, stop and report the output. Do not
     hand-edit or revert hashes.
4. Refresh the venv from the locks:
   `./.venv/bin/pip install --uploaded-prior-to P1D --require-hashes -r requirements.lock -r requirements-dev.lock`.
   Then run `./.venv/bin/pip check`.
5. In `tests/test_supply_chain.py::test_pyproject_pins_match_lock`, after the `project["project"]["dependencies"]`
   loop, add a loop over `project["build-system"]["requires"]` with the same body. It should
   partition on `==`, assert a separator with the message `f"Unpinned build requirement: {dependency}"`,
   and assert that `lock[normalize(name)] == version`.
6. In `Dockerfile` line 2, replace the digest so that the line reads exactly
   `FROM docker.io/library/python:3.14-slim-trixie@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8`.
   Change nothing else in the Dockerfile.
7. Run `./.venv/bin/python scripts/bump_version.py patch`. It must apply `1.0.3`, and the only
   edits are the three version files.
8. Run the full test suite and both audits (see Acceptance criteria). The image build is a
   reviewer step, because the Codex sandbox cannot run a container engine.

## Contracts

N/A. No API, config key, or DB change. `GET /api/update` reports `current: "1.0.3"` after the
bump, and its shape stays the same.

## Tests

- `tests/test_supply_chain.py::test_pyproject_pins_match_lock` now also asserts that every
  `[build-system] requires` pin matches its `requirements.lock` version. It passes with setuptools 84.2.1
  in both files and would fail if either drifted.
- The existing `test_dockerfile_base_images_pinned_by_digest` must pass unchanged. It covers the new digest form.
- The existing `test_requirements_lock_is_fully_hashed`, `test_dev_lock_does_not_overlap_runtime_lock`
  and `test_versions_agree` must pass unchanged. They cover the regenerated hashes and the 1.0.3 bump.
- `tests/test_update_check.py` must pass unchanged. It is the certifi-backed caller, so it catches any
  test that hard-codes the current version.

## Invariants at risk

- **6. All outbound requests go through `url_policy`.** Not touched. The certifi and OpenSSL updates
  change which roots TLS trusts and the library that does the handshake. They do not change how
  outbound URLs get checked or pinned.
- **5. FastAPI owns all credentials.** Not touched.
- **8. Compiled frontend is committed.** `frontend/package.json` changes, but only its `version`
  field, and the bundle does not read it. `app/static/ui/` must stay unchanged. If the build
  hygiene tests disagree, stop and report.
- **9. `main` is production.** The work lands on `feature/0047-security-patch-release`. The merge
  auto-deploys 1.0.3 to the lab host, which rebuilds on the new base image. The reviewer builds and
  smoke-tests the image first. The GHCR tag is a separate manual step for the user.
- 1–4 and 7 are nowhere near this change.

## Acceptance criteria

- [ ] `git diff main --stat` lists only the seven files in Files.
- [ ] `requirements.lock` pins `certifi==2026.10.23` and `setuptools==84.2.1`. No other pin or hash block changes.
- [ ] `requirements-dev.lock` is byte-identical to `main`.
- [ ] `pyproject.toml` `[build-system] requires` is `["setuptools==84.2.1"]`.
- [ ] The Dockerfile `FROM` line carries digest `sha256:cd89c30e…cec8`, and the rest of the file is unchanged.
- [ ] `./.venv/bin/pip-audit -r requirements.lock --disable-pip --no-deps` reports no known vulnerabilities.
- [ ] `./.venv/bin/pip-audit -r requirements-dev.lock --disable-pip --no-deps` reports no known vulnerabilities.
- [ ] `./.venv/bin/pip check` is clean.
- [ ] `./.venv/bin/python -c "import app; print(app.__version__)"` prints `1.0.3`.
- [ ] `./.venv/bin/pytest` green.
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green (the mirror push after merge needs it).
- [ ] `app/static/ui/` unchanged.
- [ ] Reviewer only: `podman build -t filterhelm:0047 .` succeeds. In the image,
      `python -c "import sys, ssl, certifi; print(sys.version, ssl.OPENSSL_VERSION, certifi.__version__)"`
      reports 3.14.10, OpenSSL 3.5.7, and `2026.10.23`. `dpkg-query -W libssl3t64` reports `3.5.7-1~deb13u3`.
- [ ] Reviewer only: the container starts, and `GET /api/auth/status` answers 200.
