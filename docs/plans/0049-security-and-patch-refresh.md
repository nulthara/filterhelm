# 0049 — Security and patch refresh, release 1.0.4

Status: in-progress          <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0049-security-and-patch-refresh
Author: Claude (architect)
Date: 2026-11-04

## Problem

A security sweep on 2026-11-04 across all four layers found no open advisory against
anything that ships. Three things still need action.

- **Site Node floor.** Astro 7.4.2 raised its minimum Node to 22.23.4 because of the
  October 2026 Node.js security releases. The site pins Astro 7.4.0 and declares
  `"node": ">=22.22.0"`, so a build on an unpatched Node 22 still passes.
- **Patch drift.** Patch releases have accumulated in the frontend and Python locks. Each
  passes the 24-hour gate. Taking them now keeps the next security bump small.
- **minimatch GHSA-g3hv-75wg-m249** (moderate DoS). `npm audit` in `frontend/` reports it
  12 times, once per dependent package. It needs a documented risk acceptance, because
  no upstream fix path exists today.

Audit results:

| Layer | Result |
| --- | --- |
| `pip-audit` runtime + dev locks | No known vulnerabilities |
| Base image `python:3.14-slim-trixie` | Tag still resolves to the pinned digest `cd89c30e…`. CPython 3.14.10, OpenSSL 3.5.7. Only `tzdata` 2026f is pending, from stable-updates, which isn't a security channel |
| `site/` `npm audit` | 0 vulnerabilities |
| `frontend/` `npm audit` | 12 moderate findings, all from minimatch GHSA-g3hv-75wg-m249 |
| GitHub Actions | No advisories on any pinned action |

## Scope

In:
- Site: `astro` 7.4.0 → 7.4.5, `@astrojs/starlight` 0.42.6 → 0.42.8. Raise `engines.node`
  to `>=22.23.4`. Update the Node note in `docs/site.md`.
- Frontend lockfile: `antd` 6.6.7 → 6.6.8, `vitest` 5.0.5 → 5.1.0, `typescript-eslint`
  8.72.0 → 8.72.1 (all within existing `package.json` ranges). Rebuild `app/static/ui/`.
- Runtime lock: `cryptography` 50.0.1 → 50.0.2, `anyio` 4.15.1 → 4.16.0.
- Dev lock: `pytest` 9.1.1 → 9.2.0, `pluggy` 1.6.0 → 1.7.0, `iniconfig` 2.3.0 → 2.3.1.
- Regenerate hashes for both locks with `scripts/lock_hashes.py`.
- Bump the app version to 1.0.4 with `scripts/bump_version.py patch`.
- Record the minimatch risk acceptance in this plan (see Approach).

Out:
- Pydantic 2.14 / pydantic_core 2.50, FastAPI 0.162, Starlette 1.10. Each is a minor with
  behaviour changes and needs its own plan.
- `more-itertools` 11.2.1 and `pycparser` 3.12. No advisory, and nothing to gain now.
- Major frontend tooling: ESLint 10, `@eslint/js` 10, Vite 8, `@vitejs/plugin-react` 6,
  TypeScript 7, `globals` 17, `eslint-plugin-react-refresh` 0.5. Majors need their own plan
  (AGENTS.md).
- An npm `overrides` entry for minimatch.
- Dockerfile base digest. It is already current.
- An `apt-get upgrade` layer for `tzdata`. Plan 0047 rejected that pattern.
- GitHub Actions pins. No advisories, and the newer releases are maintenance only.
- Cloudflare's `NODE_VERSION=22` build variable. It is a dashboard setting outside the repo
  and already floats to the newest 22.x.
- The maintainer's local Node. It is v26.13.0 as of 2026-11-04, which includes the 26.12.1
  security release.
- Tagging `v1.0.4` and pushing to the GitHub mirror. The user does both after merge.

## Approach

Follow the AGENTS.md "Dependency updates" procedure for every layer.

| Target | Published (UTC) | Notes |
| --- | --- | --- |
| astro 7.4.5 | 2026-11-02 14:26 | 7.4.2 raised the Node floor for the Oct 2026 Node security releases |
| @astrojs/starlight 0.42.8 | 2026-10-30 08:29 | patch |
| antd 6.6.8 | 2026-10-26 02:48 | patch, ships in the bundle |
| vitest 5.1.0 | 2026-11-02 16:12 | minor within `^5.0.3`, dev only |
| typescript-eslint 8.72.1 | 2026-10-26 17:09 | patch, dev only |
| cryptography 50.0.2 | 2026-09-30 14:43 | needs `cffi>=2.0.0`; lock has 2.1.1 |
| anyio 4.16.0 | 2026-11-01 11:20 | needs `idna>=2.8`, `typing_extensions>=4.16.0`; lock has 3.20 / 4.16.0 |
| pytest 9.2.0 | 2026-10-27 21:30 | needs `pluggy<2,>=1.6`, `packaging>=24` |
| pluggy 1.7.0 | 2026-10-15 09:50 | |
| iniconfig 2.3.1 | 2026-10-06 22:48 | |

The frontend bumps go through `npm update <names>`. That keeps the caret ranges in
`frontend/package.json` and changes only `package-lock.json`. The site pins exact
versions, so its `package.json` lines change by hand before `npm install`.

The Python bumps edit only the `name==version` lines, then let `scripts/lock_hashes.py`
rewrite hashes. Any hash change outside the edited blocks is a stop condition, as in 0047.

The release ships because cryptography, anyio and antd change what the image runs. GHCR
self-hosters get them only through a `v*` tag.

### minimatch risk acceptance

GHSA-g3hv-75wg-m249 is a DoS through glob-pattern amplification, fixed only in
minimatch 11.0.1. The frontend reaches minimatch only through ESLint
(minimatch 3.1.5) and `@typescript-eslint/typescript-estree` (minimatch 10.2.6).

- No release of ESLint, including 10.x, accepts minimatch 11. Current ranges are `^3`,
  `^10.2.4` and `^10.2.6`.
- minimatch published no backport to 3.x or 10.x.
- An `overrides` entry would hand ESLint 9 a minimatch with a different module shape.
- The patterns minimatch evaluates come from our own lint config, at lint time. Nothing
  reaches the production bundle or the server.

The risk is accepted as dev-only. Revisit when ESLint or typescript-eslint accepts
minimatch 11. After this plan, `npm audit` in `frontend/` still reports these findings.

Rejected alternatives:
- Taking every outdated package. The majors and the Pydantic/FastAPI/Starlette minors carry
  real migration risk and need their own plans.
- A site-only change with no release. It would leave the bundle's antd and the image's
  cryptography/anyio drifting with no reason to wait.

## Files

- `site/package.json` — `astro`, `@astrojs/starlight` pins; `engines.node`.
- `site/package-lock.json` — regenerated.
- `docs/site.md` — Node floor note 22.22.0 → 22.23.4.
- `frontend/package-lock.json` — antd, vitest, typescript-eslint trees.
- `requirements.lock` — cryptography, anyio; regenerated hashes.
- `requirements-dev.lock` — pytest, pluggy, iniconfig; regenerated hashes.
- `pyproject.toml` — `[project.optional-dependencies] dev` pytest pin (Revision 1).
- Version files touched by `scripts/bump_version.py patch` (as in 0047).
- `app/static/ui/` — rebuilt bundle.

## Steps

0. **Network.** `npm` and `scripts/lock_hashes.py` need `registry.npmjs.org` and `pypi.org`.
   The Codex sandbox has network access for this plan. If a network step fails anyway,
   stop and report.
1. **Site.** In `site/package.json`, set `"astro": "7.4.5"`, `"@astrojs/starlight": "0.42.8"`
   and `"node": ">=22.23.4"`. Run `cd site && npm install && npm run build && npm audit`.
   The build must pass and `npm audit` must report 0 vulnerabilities.
2. **Site doc.** In `docs/site.md` line 10, change `22.22.0 or later` to `22.23.4 or later`.
   Leave `site/.node-version` as `22`.
3. **Frontend.** `cd frontend && npm update antd vitest typescript-eslint`.
   `frontend/package.json` must not change except for Step 6's `version` field.
   `npm ls antd vitest typescript-eslint` must show 6.6.8, 5.1.0 and 8.72.1.
4. **Runtime lock.** In `requirements.lock`, change `cryptography==50.0.1 \` to
   `cryptography==50.0.2 \` and `anyio==4.15.1 \` to `anyio==4.16.0 \`. Run
   `python3 scripts/lock_hashes.py`. Only those two blocks may change.
5. **Dev lock.** In `requirements-dev.lock`, change `pytest==9.1.1`, `pluggy==1.6.0`,
   `iniconfig==2.3.0` to `9.2.0`, `1.7.0`, `2.3.1`. Run
   `python3 scripts/lock_hashes.py requirements-dev.lock`. Only those three blocks may change.
   Revision 1: also change `pyproject.toml` line 21 to `dev = ["pytest==9.2.0"]`.
   `tests/test_supply_chain.py::test_dev_extra_matches_dev_lock` enforces this match.
6. **Venv.** `./.venv/bin/pip install --uploaded-prior-to P1D --require-hashes -r requirements.lock -r requirements-dev.lock`,
   then `./.venv/bin/pip check`.
7. **Version.** `./.venv/bin/python scripts/bump_version.py patch` must apply `1.0.4` and
   touch only its version files.
8. **Build and checks.** `cd frontend && npm run build && npm run lint && npm test`;
   `./.venv/bin/pytest`; both `pip-audit` runs. The image build is a reviewer step.

## Contracts

N/A. No API, config key, or DB change. `GET /api/update` reports `current: "1.0.4"` after
the bump, and its shape stays the same.

## Tests

No new tests. These existing tests must pass unchanged:

- `tests/test_supply_chain.py` — lock hashing, the dev/runtime overlap check, and the pyproject/lock pin match.
- `test_versions_agree` — 1.0.4 across version files.
- `tests/test_repo_hygiene.py` — rebuilt bundle staged, no forbidden hosts or tokens.
- Frontend `npm test` (vitest 5.1.0) — all 176 tests.
- `tests/test_update_check.py` — the cryptography/anyio TLS path is unaffected.

## Invariants at risk

- **8. Compiled frontend committed.** antd changes the bundle. Step 8 rebuilds, and the
  new `app/static/ui/` is committed. `site/dist/` stays uncommitted.
- **6. Outbound requests through `url_policy`.** anyio and cryptography sit under httpx's
  transport. The bumps change no call sites, and `url_policy` checks happen before the
  transport.
- **9. `main` is production.** The work lands on the feature branch. The merge redeploys
  1.0.4 to the lab host. The reviewer builds and smoke-tests the image first.
- 1–5 and 7 are nowhere near this change.

## Acceptance criteria

- [ ] `git diff main --stat` lists only files in Files.
- [ ] `site/package.json` pins astro 7.4.5, starlight 0.42.8, `engines.node` `>=22.23.4`.
- [ ] `cd site && npm ci && npm run build` succeeds; `npm audit` reports 0 vulnerabilities.
- [ ] `frontend/package.json` changes only in `version`; `npm ls` shows antd 6.6.8,
      vitest 5.1.0, typescript-eslint 8.72.1.
- [ ] `cd frontend && npm audit` shows only the accepted minimatch GHSA-g3hv-75wg-m249 chain.
- [ ] `requirements.lock` pins cryptography 50.0.2 and anyio 4.16.0; no other block changes.
- [ ] `requirements-dev.lock` pins pytest 9.2.0, pluggy 1.7.0, iniconfig 2.3.1; no other block changes.
- [ ] Both `pip-audit -r … --disable-pip --no-deps` runs report no known vulnerabilities.
- [ ] `./.venv/bin/pip check` clean.
- [ ] `./.venv/bin/python -c "import app; print(app.__version__)"` prints `1.0.4`.
- [ ] `./.venv/bin/pytest` and `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` + `npm test` clean; `app/static/ui/` committed.
- [ ] Reviewer only: `podman build -t filterhelm:0049 .` succeeds. In the image,
      `python -c "import cryptography, anyio; print(cryptography.__version__, anyio.__version__)"`
      prints `50.0.2 4.16.0`. The container starts, and `GET /api/auth/status` answers 200.
- [ ] After merge (user): the next Cloudflare build log shows Node ≥ 22.23.4.
