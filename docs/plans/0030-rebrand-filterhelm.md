# 0030 — Rebrand to FilterHelm

Status: shipped                <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0030-rebrand-filterhelm
Author: Claude (architect)
Date: 2026-10-11

## Problem

The product is named "Pi-hole Manager" / `pihole-manager`. Pi-hole's trademark rules
(pi-hole.net/t) forbid "Pi-hole" as the first word of a third-party product name and
forbid marks similar enough to imply affiliation. The header's π mark echoes Pi-hole too.
The repo has a public GitHub mirror, so the name ships with every clone. The new name is
FilterHelm, and an untracked brand kit now sits in `branding/`.

## Scope

In:
- Display name becomes `FilterHelm` everywhere it shows: page title, header, login
  screen, FastAPI title, CLI help, error text, README, `docs/`, `CLAUDE.md`, `ARCHITECT.md`.
- Hard-cut rename of every code identifier, with no fallback to the old names:
  - env vars `PIHOLE_MANAGER_*` → `FILTERHELM_*`
  - session cookie, poll header, keyring service name, default data dir
  - Python and npm package names
- Hard-cut rename of deploy identifiers in the repo: `deploy/` files, Quadlet, redeploy
  script, image tag, container name, host paths, sudoers file name, runner label,
  Dockerfile user/workdir, `compose.yaml` service and volume.
- A host cutover runbook in `docs/deploy.md` that replaces the finished §8 runbook.
- Commit the `branding/` kit as the brand source of truth.
- Replace the π mark with `filterhelm-icon-compact.svg`. Add a favicon.
- README gets the logo, the tagline, and the non-affiliation statement.
- A guard test that fails if the old name reappears in tracked files.

Out:
- Retheming the UI to brand colors, Inter, or dark mode. Ant Design tokens stay
  (`colorPrimary: '#1677ff'`). That is a follow-up plan.
- Renaming the forge repo, the GitHub mirror, the local clone directory, or moving to the
  `nulthara` namespace. Those are user actions outside git.
- Rewriting `docs/plans/0001`–`0029`. They are historical records and keep the old name.
- Any compatibility shim for old env vars, cookie, keyring entries, or data dir.
- Running the host cutover. The user does that before merge.
- `LICENSE` copyright holder.

## Approach

**Hard cut.** Every `PIHOLE_MANAGER_X` becomes `FILTERHELM_X` by plain substitution. No
helper reads both names. The user chose this over a legacy fallback because the only
production install is one host, and the runbook rewrites its env file in one `sed`.

Consequences the user accepts:
- All sessions end once, because the cookie name changes.
- A local dev install loses its keyring credentials and its `~/.config/pihole-manager`
  data dir until moved by hand. README states the one-line move. Production is
  unaffected because the container uses `secrets.yml` under `/data`, not a keyring.
- `docker compose` users get a fresh named volume `filterhelm-data`.

**Deploy rename with a pre-merge cutover.** The merge's deploy run uses
`runs-on: filterhelm-deploy`, `cd /opt/filterhelm`, and
`sudo /usr/local/sbin/filterhelm-redeploy`. None of those exist on the host today. So the
host must be cut over from the feature branch before merge, the same pattern §8 used for
plan 0025. Container UID/GID stays `10001:10001`, so the moved data dir keeps its
ownership.

**Brand mark.** Copy `branding/logos/filterhelm-icon-compact.svg` into
`frontend/src/assets/` and import it. The brand kit specifies the compact icon for
16–32 px. Importing from `../../branding` was rejected because Vite's dev server
restricts files outside the frontend root. `branding/` stays the source of truth. The
favicon goes in `frontend/public/` so Vite copies it unhashed to `app/static/ui/`. CSP
already allows `img-src 'self' data:`, so either an emitted file or an inlined data URI
works.

**Rejected:**
- A legacy env-var fallback, rejected by the user.
- Display-only rename. It leaves the old name in public config docs.
- Deferring the deploy rename. It leaves `pihole-manager` on the host and in `deploy/`.

## Files

Renamed (use `git mv`; the redeploy script keeps mode `100755`):
- `deploy/pihole-manager.container` → `deploy/filterhelm.container` — Quadlet rename and new contents.
- `deploy/pihole-manager-redeploy` → `deploy/filterhelm-redeploy` — script rename and new contents.

Added:
- `branding/**` — commit the existing untracked kit as-is.
- `frontend/src/assets/filterhelm-icon-compact.svg` — byte-identical copy of `branding/logos/filterhelm-icon-compact.svg`.
- `frontend/public/favicon.svg` — byte-identical copy of `branding/logos/filterhelm-icon-compact.svg`.
- `frontend/public/favicon-32.png` — byte-identical copy of `branding/icons/filterhelm-32.png`.
- `frontend/src/vite-env.d.ts` — `/// <reference types="vite/client" />` so `tsc` accepts the SVG import.
- `tests/test_branding.py` — old-name guard.

Modified:
- `app/auth.py` — cookie, poll header, env vars, CLI description.
- `app/config.py` — `FILTERHELM_DATA`, default dir, `CredentialStore.SERVICE_NAME`.
- `app/http_hardening.py` — env var names and error text.
- `app/main.py` — FastAPI title.
- `app/url_policy.py` — `PUBLIC_ALLOWLIST_ENV`.
- `app/routes/api.py` — `FILTERHELM_FANOUT_WORKERS`, `compatibility_detail` text.
- `app/routes/{clients,domains,drift,groups,lookup,query_log}.py` — `FILTERHELM_FANOUT_WORKERS`.
- `frontend/index.html` — title, favicon links.
- `frontend/src/App.tsx` — logo image and title (two places).
- `frontend/src/styles.css` — `.brand-mark` becomes an image rule.
- `frontend/src/api.ts` — poll header name.
- `frontend/package.json`, `frontend/package-lock.json` — package name.
- `app/static/ui/**` — rebuilt bundle.
- `pyproject.toml` — project name.
- `Dockerfile` — env var, workdir, user/group name.
- `compose.yaml` — service, image, env vars, volume.
- `.gitea/workflows/deploy.yml` — runner label, paths, script, message.
- `tests/test_container.py`, `tests/test_auth.py`, `tests/test_api.py`, `tests/test_url_policy.py`, `tests/test_repo_hygiene.py` — new names.
- `README.md` — name, logo, tagline, disclaimer, env vars, commands, dev migration note.
- `docs/deploy.md` — all names, plus the new §8 cutover runbook.
- `docs/design.md` — current-state name references.
- `CLAUDE.md`, `ARCHITECT.md`, `.claude/commands/verify.md` — name and forge env vars.

## Steps

1. **Commit the brand kit.** `git add branding/` unchanged. Run
   `./.venv/bin/pytest tests/test_repo_hygiene.py` with it staged. The architect pre-scanned
   it clean against both hygiene checks.

2. **Backend identifiers.** Make the exact substitutions in the Contracts table. Every
   `os.environ.get("PIHOLE_MANAGER_…")` becomes `"FILTERHELM_…"`, and so does every
   `RuntimeError` message that names one. Other display strings:
   - `app/main.py` — `FastAPI(title="FilterHelm")`
   - `app/auth.py` — `argparse.ArgumentParser(description="Manage FilterHelm authentication")`
   - `app/routes/api.py:321` — `compatibility_detail = "FilterHelm requires the FTL 6.x API"`

3. **Package names.** `pyproject.toml` `name = "filterhelm"`. `frontend/package.json`
   `"name": "filterhelm-ui"`. Edit both `"name"` fields at the top of
   `frontend/package-lock.json` by hand (lines 2 and 8). Do not run `npm install`,
   because the sandbox has no network.

4. **Frontend name and mark.**
   - `frontend/src/vite-env.d.ts` holds `/// <reference types="vite/client" />`.
   - `App.tsx` adds `import brandMark from './assets/filterhelm-icon-compact.svg'`.
   - Both headers (login at line 70, app at line 94) replace `<div className="brand-mark">π</div>`
     with `<img className="brand-mark" src={brandMark} alt="" width={32} height={32} />`.
     The alt is empty because the title beside it names the product.
   - Both `brand-title` texts become `FilterHelm`.
   - `styles.css` `.brand-mark` becomes `{ display: block; width: 32px; height: 32px; flex: none; }`.
     Drop the grid, color, background, radius, and font rules, since the SVG carries its own hexagon.
   - `api.ts:616` header becomes `'X-FilterHelm-Poll': '1'`.
   - `index.html` gets `<title>FilterHelm</title>` and, in `<head>`:
     ```html
     <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
     <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png" />
     ```
     Vite rewrites these to `/app/…` from `base: '/app/'`. Leave `theme-color` as-is.
   - Rebuild with `npm run build`, then commit `app/static/ui/`.

5. **Container and compose.**
   - `Dockerfile`: `FILTERHELM_DATA=/data`, `WORKDIR /opt/filterhelm`, and
     `groupadd`/`useradd` name `filterhelm`. Keep `--gid 10001`/`--uid 10001` and
     `USER 10001:10001`.
   - `compose.yaml`: service `filterhelm`, `image: localhost/filterhelm:latest`, env keys
     `FILTERHELM_*`, volume `filterhelm-data` in both the mount and the top-level `volumes:`.

6. **Deploy files.** `git mv` both files in `deploy/`.
   - In `filterhelm.container`: `Description=FilterHelm`,
     `Image=localhost/filterhelm:latest`, `ContainerName=filterhelm`,
     `Volume=/var/lib/filterhelm:/data`, `EnvironmentFile=/etc/filterhelm/env`,
     `Environment=FILTERHELM_DATA=/data`. All other lines are unchanged.
   - In `filterhelm-redeploy`: `image=localhost/filterhelm`, build context
     `/opt/filterhelm`, `filterhelm.service` in both `systemctl restart` calls, and
     `"filterhelm failed its health probe"`. The logic is unchanged.

7. **Workflow.** In `.gitea/workflows/deploy.yml`:
   - `runs-on: filterhelm-deploy`
   - `cd /opt/filterhelm`
   - the comment names `/etc/sudoers.d/gitea-runner-filterhelm`
   - `run: sudo /usr/local/sbin/filterhelm-redeploy`
   - `echo "filterhelm did not come back up after restart"`

   `sudo ` must still appear exactly once.

8. **Tests.** Update the existing tests to the new names (see Tests). Add
   `tests/test_branding.py`.

9. **README.**
   - Top: a `<picture>` with `branding/logos/filterhelm-horizontal-dark.svg` for
     `(prefers-color-scheme: dark)` and `-light.svg` by default, `alt="FilterHelm"`.
   - Then `# FilterHelm`, the tagline line "Multiple instances. One control plane.", and
     "An independent, third-party management platform for Pi-hole instances. Not
     affiliated with or endorsed by Pi-hole."
   - Replace every other old name per the Contracts table. Descriptive uses of "Pi-hole"
     stay, such as "supports the Pi-hole FTL 6.x REST API".
   - Add a section headed exactly `## Upgrading from pihole-manager`, placed last. It is
     the only part of README where old names may appear. It says:
     - Rename `PIHOLE_MANAGER_*` env vars to `FILTERHELM_*`.
     - Local installs run `mv ~/.config/pihole-manager ~/.config/filterhelm`.
     - Keyring users re-enter instance app passwords.
     - Compose users get a new volume, so copy data from `pihole-manager-data` or start fresh.
     - Everyone signs in again.

10. **docs/deploy.md.**
    - Replace every old identifier in every section except §8 with the new one. This includes the
      runner `--name filterhelm-runner`, `--labels filterhelm-deploy:host`, the
      runner unit `Description=Gitea Actions runner (filterhelm)`, the clone URL path
      `example/filterhelm.git`, and all env keys.
    - Replace §8 (the finished systemd → container cutover) with "§8 Cutover from
      pihole-manager". Its contents are in the Cutover runbook section below.
    - Old names may appear only inside §8. The sudoers rule line
      `gitea-runner ALL=(root) NOPASSWD: /usr/local/sbin/filterhelm-redeploy` must appear
      exactly once in the file, in §6. §8 points to §6 and does not repeat the rule.

11. **Other docs.**
    - `docs/design.md`: replace every old name. Today that is lines 1, 380, 401, 472, 536,
      541–547, 561, and 625. Line 401 sits inside an ASCII box, so pad `FilterHelm` with
      spaces to keep the right border aligned. Line 472 records where the code lives.
      Rewrite it as "decided: its own Gitea repo", dropping the old path. No old name
      remains in this file.
    - `CLAUDE.md` line 3: `FilterHelm — local GUI for managing multiple Pi-hole instances.`
    - `ARCHITECT.md` and `.claude/commands/verify.md`: `FILTERHELM_FORGE_API`,
      `FILTERHELM_FORGE_TOKEN`.

### Cutover runbook (content for docs/deploy.md §8)

Steps 0–7 run on the deploy host before merging `feature/0030-rebrand-filterhelm`, after the
branch is pushed. Use numbered steps with code blocks, in this order:

0. **Rename the forge repo first, if renaming it.** Do this in the forge UI before step 2,
   because step 2 fetches through the new remote URL.
1. **Stop the service.**
   ```bash
   sudo systemctl stop pihole-manager.service
   ```
2. **Move the checkout and fetch the branch.**
   ```bash
   sudo mv /opt/pihole-manager /opt/filterhelm
   ```
   If the repo was renamed, run
   `sudo -u gitea-runner git -C /opt/filterhelm remote set-url origin <new URL>`.
   Then fetch `feature/0030-rebrand-filterhelm` and run `checkout --detach FETCH_HEAD`.
   The 0025 runbook used the same commands.
3. **Move config and rename env keys.**
   ```bash
   sudo mv /etc/pihole-manager /etc/filterhelm
   sudo sed -i 's/^PIHOLE_MANAGER_/FILTERHELM_/' /etc/filterhelm/env
   ```
   Show the file afterwards and check that no `PIHOLE_MANAGER_` remains.
4. **Move data.** `sudo mv /var/lib/pihole-manager /var/lib/filterhelm`. Ownership stays
   `10001:10001`.
5. **Swap the units.** Remove `/etc/containers/systemd/pihole-manager.container`,
   `/usr/local/sbin/pihole-manager-redeploy`, and `/etc/sudoers.d/gitea-runner-pihole-manager`.
   Install the new Quadlet and script with the §5 `install` commands. Create the new
   sudoers file per §6. Then run `sudo systemctl daemon-reload`.
6. **Start and verify.** Run `sudo /usr/local/sbin/filterhelm-redeploy`, then
   `curl -fsS http://127.0.0.1:8420/api/auth/status`. This first run has no `:good`
   image, so a failure does not roll back. Recover by fixing forward or by reversing
   steps 1–5.
7. **Relabel the runner.** Change the runner's label to `filterhelm-deploy:host`. Edit
   the `labels` array in `/home/gitea-runner/.runner`, or re-register per §3, then
   `sudo systemctl restart gitea-runner.service`. Confirm the forge lists the new label.
8. **Merge.** The deploy run resets `/opt/filterhelm` to `origin/main`. Watch it go green.
9. **Clean up after a green deploy.**
   ```bash
   sudo podman image rm localhost/pihole-manager:latest localhost/pihole-manager:good
   ```
   Optionally update the runner unit's `Description`.
10. **Dev machine.** Rename `PIHOLE_MANAGER_FORGE_API`/`_TOKEN` to `FILTERHELM_FORGE_*`
    in `~/.zshenv`.

## Contracts

The hard cut has no fallback. Backend and frontend change together in this branch.

| Old | New | Defined in |
| --- | --- | --- |
| `PIHOLE_MANAGER_DATA` | `FILTERHELM_DATA` | `app/config.py` |
| `PIHOLE_MANAGER_AUTH_MODE` | `FILTERHELM_AUTH_MODE` | `app/auth.py` |
| `PIHOLE_MANAGER_PROXY_HEADER` | `FILTERHELM_PROXY_HEADER` | `app/auth.py` |
| `PIHOLE_MANAGER_PROXY_TRUSTED_IPS` | `FILTERHELM_PROXY_TRUSTED_IPS` | `app/auth.py` |
| `PIHOLE_MANAGER_PROXY_USERS` | `FILTERHELM_PROXY_USERS` | `app/auth.py` |
| `PIHOLE_MANAGER_COOKIE_SECURE` | `FILTERHELM_COOKIE_SECURE` | `app/auth.py` |
| `PIHOLE_MANAGER_TRUSTED_HOSTS` | `FILTERHELM_TRUSTED_HOSTS` | `app/http_hardening.py` |
| `PIHOLE_MANAGER_MAX_BODY_BYTES` | `FILTERHELM_MAX_BODY_BYTES` | `app/http_hardening.py` |
| `PIHOLE_MANAGER_RATE_LIMIT_REQUESTS` | `FILTERHELM_RATE_LIMIT_REQUESTS` | `app/http_hardening.py` |
| `PIHOLE_MANAGER_RATE_LIMIT_WINDOW_SECONDS` | `FILTERHELM_RATE_LIMIT_WINDOW_SECONDS` | `app/http_hardening.py` |
| `PIHOLE_MANAGER_FANOUT_WORKERS` | `FILTERHELM_FANOUT_WORKERS` | 7 route modules |
| `PIHOLE_MANAGER_PUBLIC_ALLOWLIST` | `FILTERHELM_PUBLIC_ALLOWLIST` | `app/url_policy.py` |
| `PIHOLE_MANAGER_REQUIRE_DENYLIST` | `FILTERHELM_REQUIRE_DENYLIST` | `tests/test_repo_hygiene.py` |
| `PIHOLE_MANAGER_FORGE_API` / `_TOKEN` | `FILTERHELM_FORGE_API` / `_TOKEN` | `.claude/commands/verify.md` |
| cookie `pihole_manager_session` | `filterhelm_session` | `app/auth.py` `SESSION_COOKIE` |
| header `x-pihole-manager-poll` | `x-filterhelm-poll` | `app/auth.py` `POLL_HEADER` ↔ `api.ts` `'X-FilterHelm-Poll'` |
| keyring service `pihole-manager` | `filterhelm` | `CredentialStore.SERVICE_NAME` |
| default data dir `~/.config/pihole-manager` | `~/.config/filterhelm` | `app/config.py` `DATA_DIR` |

Header names are case-insensitive, so `POLL_HEADER` stays lowercase in `auth.py` and
`api.ts` sends the mixed-case form, as today.

There are no API request/response shape changes and no DB schema changes.

## Tests

- `tests/test_branding.py::test_tracked_files_do_not_use_old_name` — scans
  `git ls-files` text with `re.compile(r"pihole[-_ ]?manager|pi-hole manager", re.I)`.
  Reuse `from test_repo_hygiene import _tracked_texts`. `tests/` has no `__init__.py`,
  and pytest's default import mode puts `tests/` on `sys.path`. Exempt paths: anything under `docs/plans/`, `tests/test_branding.py` itself, and the
  two files in `MIGRATION_SECTIONS` below, which the next test guards instead. Fails with
  `path:line: match` findings.
- `tests/test_branding.py::test_old_name_only_in_migration_sections` — uses
  `MIGRATION_SECTIONS = {"docs/deploy.md": "## 8. Cutover from pihole-manager", "README.md": "## Upgrading from pihole-manager"}`.
  For each file, the heading line appears exactly once. Every old-name match falls in
  that section, which runs from the heading line through the line before the next line
  matching `^#{1,2} `, or to end of file.
- `tests/test_branding.py::test_frontend_mark_matches_brand_kit` — the two frontend SVG
  copies and `favicon-32.png` are byte-identical to their `branding/` sources.
- `tests/test_container.py` — every assertion moves to the new names: the workflow
  script path, the sudoers rule, `deploy/filterhelm-redeploy` mode `100755`, the Quadlet
  `Image=`/`Environment=`/`Volume=` lines, and the redeploy-log expectations. Assertion
  counts are unchanged.
- `tests/test_auth.py` — cookie and poll-header literals use the new names.
- `tests/test_api.py`, `tests/test_url_policy.py` — `FILTERHELM_PUBLIC_ALLOWLIST`.
- `tests/test_repo_hygiene.py` — `FILTERHELM_REQUIRE_DENYLIST`.

Frontend has no component-test setup (Vitest runs pure modules, no DOM). The header and
favicon are checked through the build output in Acceptance criteria.

## Invariants at risk

1. **One instance mutated at a time** — untouched. No mutation code changes.
2. **Diff before write** — untouched.
3. **Gravity gating** — untouched.
4. **No background scheduler** — untouched.
5. **Credentials never reach the browser** — the keyring service rename only changes the
   lookup key. The credential flow and responses are unchanged. `app/routes/auth.py`
   sets and deletes the cookie through `SESSION_COOKIE`, so its attributes (`httponly`,
   `secure` per env, `samesite="strict"`) are unchanged.
6. **Outbound requests via `url_policy`** — only the allowlist env var's name changes.
   `test_url_policy.py` still exercises the allowlist under the new name.
7. **Auth middleware on every route** — the poll-header rename must happen on both sides,
   or background polls count as user activity and break idle timeout (plan 0005). The
   `test_auth.py` poll tests cover this. No routes are added.
8. **Compiled frontend committed** — Step 4 rebuilds and commits `app/static/ui/`.
9. **`main` is production** — this is the change most at risk. Merging before the host
   cutover fails the deploy, because the runner label, checkout path, and sudo script do
   not exist yet. The §8 runbook runs before merge, and the Acceptance checklist gates
   the merge on it. The redeploy script's rollback logic is unchanged.

## Acceptance criteria

- [ ] `git grep -niE 'pihole[-_ ]?manager|pi-hole manager' -- ':!docs/plans' ':!docs/deploy.md' ':!README.md' ':!tests/test_branding.py'` prints nothing, and old names in `README.md` and `docs/deploy.md` appear only in their migration sections.
- [ ] `grep -c 'PIHOLE_MANAGER' app/static/ui -r` is 0, and the built JS contains `X-FilterHelm-Poll`.
- [ ] `app/static/ui/index.html` has `<title>FilterHelm</title>` and links `/app/favicon.svg` and `/app/favicon-32.png`, and both files exist in `app/static/ui/`.
- [ ] With the app running locally (`FILTERHELM_DATA` pointed at a scratch dir), `GET /app/favicon.svg` returns 200 `image/svg+xml`. The login page shows the FilterHelm hexagon mark and "FilterHelm" title, with no π.
- [ ] After sign-in, the response sets a `filterhelm_session` cookie and the app header shows the mark.
- [ ] `git ls-files -s deploy/` lists `filterhelm.container` (100644) and `filterhelm-redeploy` (100755) and no `pihole-manager*` files.
- [ ] `docs/deploy.md` §8 is the pihole-manager cutover runbook in the step order above, and the old systemd cutover is gone.
- [ ] README opens with the logo, `# FilterHelm`, the tagline, and the non-affiliation sentence.
- [ ] `branding/` is tracked, with all 19 files unchanged from the working copy.
- [ ] `./.venv/bin/pytest` is green, including the new `test_branding.py` tests.
- [ ] `cd frontend && npm run build && npm run lint && npm test` is clean, and `app/static/ui/` is committed.
- [ ] **Before merge:** the user has run the §8 cutover on the host, and `/api/auth/status` answers from `filterhelm.service`.

## Revisions

- **Revision 1 (2026-10-11).** Codex stopped before writing code. Steps 9 and 11 kept old
  names in `README.md` and `docs/design.md`, but the guard test and the acceptance grep
  exempted neither file. README now confines old names to `## Upgrading from
  pihole-manager`, and the guard checks it per section like deploy.md §8. design.md drops
  the old path, so it needs no exemption.
