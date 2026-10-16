# 0034 — Publish the image to GHCR and show the app version

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0034-ghcr-image-and-version
Author: Kyrnveil
Date: 2026-10-14

## Problem

FilterHelm has no published container image, no release tags, and no version visible
anywhere at runtime. A user who wants the app must clone and build it, and an operator
cannot tell which release a running instance is. The GitHub mirror (`nulthara/filterhelm`)
is about to go public, so it needs a repeatable way to publish a versioned multi-arch image
to GHCR. The running app also needs to show its version and link to that release.

## Scope

In:
- `app.__version__` as the single source of the version, with `pyproject.toml` reading it
- `GET /api/version`, authenticated, returning `{"version": "<x.y.z>"}`
- A version link in the signed-in header, pointing at the GitHub tag page
- `.github/workflows/publish-image.yml`, which builds `linux/amd64` + `linux/arm64` and
  pushes to `ghcr.io/nulthara/filterhelm` when a `v*` tag is pushed to GitHub
- A guard step that fails the workflow when the tag does not match `app.__version__`
- Tests that keep the Python and npm versions in step and pin the workflow's shape
- A "Releasing" section in `docs/deploy.md`
- Bumping the version from 0.1.0 to 0.2.0, so the first tag is `v0.2.0`. A hand-pushed
  `0.1.0` image may already exist on GHCR, and this release does not overwrite it.

Out:
- Build-provenance attestation and SBOM. GitHub attestations need a public repo, so a
  follow-up adds them once the mirror is public.
- GitHub Releases (release notes, assets). The link targets the tag page, which exists
  without a Release.
- Changing `compose.yaml` or the forge deploy to pull from GHCR. Both keep building locally.
- Running pytest inside the publish workflow. The tag is pushed only after the local
  denylist pytest that CLAUDE.local.md requires.
- Showing the version on the sign-in screen

## Approach

**Version source.** `app/__init__.py` holds `__version__ = "0.2.0"`. `pyproject.toml`
declares `dynamic = ["version"]` and reads it through `[tool.setuptools.dynamic]`. The
backend serves the version over the API, so a version bump does not require rebuilding
the committed frontend bundle. `frontend/package.json` keeps a matching version, and a
test enforces that, so the npm and Python manifests never disagree.

Rejected alternatives:
- Baking the version into the bundle with Vite `define`. Every bump would then need a
  rebuild of `app/static/ui/`, and no cheap test can prove the committed bundle carries
  the current version.
- Reading `pyproject.toml` at runtime. The image does not copy that file, and the app is
  not pip-installed in the image, so `importlib.metadata` cannot find it either.
- Adding the version to `/api/auth/status`. That route is public, and the version should
  not reach unauthenticated visitors.

**Workflow.** The workflow uses GitHub-hosted runners with QEMU for arm64. Every pin in
`requirements.lock` is either pure Python or ships a cp314/abi3 `manylinux` aarch64
wheel, and `scripts/lock_hashes.py` already hashes every release file, so the arm64
wheel hashes are in the lock (checked against PyPI on 2026-10-14). The Python base image
is pinned by index digest, and that index includes `arm64/v8`. The Dockerfile therefore
builds unchanged on arm64.

The job logs in with `GITHUB_TOKEN` (`packages: write`), so no personal access token is
stored. `docker/metadata-action` derives image tags and OCI labels from the git tag. Its
`org.opencontainers.image.source` label links the package to the repo. The build sets
`provenance: false` and `sbom: false` explicitly, matching the decision to defer
attestation.

Every action is pinned to a full commit SHA with a version comment, following the
AGENTS.md supply-chain posture. These releases were all public for more than 24 hours on
2026-10-14:

| Action | Version | Commit SHA |
| --- | --- | --- |
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `docker/setup-qemu-action` | v4.4.0 | `99012661954931238ded8c8b007157a8430204e1` |
| `docker/setup-buildx-action` | v4.4.1 | `f87e5991a6d7451dcb8d9637bfbc97413f497069` |
| `docker/login-action` | v4.6.0 | `dbcb813823bdd20940b903addbd779551569679f` |
| `docker/metadata-action` | v6.2.0 | `dc802804100637a589fabce1cb79ff13a1411302` |
| `docker/build-push-action` | v7.4.0 | `c3c9e263c25d99ce0380d002d59b67737d91b0dc` |

Use these SHAs verbatim. The implementer has no network, so it must not try to resolve
or update them.

The forge ignores the new file. Gitea reads only `.gitea/workflows/` while that directory
exists, and `deploy.yml` triggers on `main` only, so a tag reaches neither runner.

**UI.** A small `v0.2.0` link sits after the "FilterHelm" title in the signed-in header.
It opens `https://github.com/nulthara/filterhelm/releases/tag/v0.2.0` in a new tab. If
the version request fails, the header renders nothing and shows no error.

## Files

- `app/__init__.py` — add `__version__`
- `pyproject.toml` — make `version` dynamic and read it from `app.__version__`
- `frontend/package.json` — bump `version` to 0.2.0
- `frontend/package-lock.json` — bump both root `version` fields to 0.2.0
- `app/routes/api.py` — add the `VersionInfo` model and the `GET /api/version` route
- `frontend/src/api.ts` — add the `VersionInfo` type and `api.version()`
- `frontend/src/release.ts` — new; holds `REPOSITORY_URL` and `releaseUrl(version)`
- `frontend/src/release.test.ts` — new; Vitest coverage for `releaseUrl`
- `frontend/src/App.tsx` — fetch the version once signed in and render the header link
- `frontend/src/styles.css` — add the `.app-version` style
- `app/static/ui/` — rebuilt bundle
- `.github/workflows/publish-image.yml` — new; the publish workflow
- `tests/test_api.py` — test the version route
- `tests/test_auth.py` — test that the version route requires auth
- `tests/test_supply_chain.py` — test version agreement and workflow pinning and shape
- `docs/deploy.md` — add a "Releasing" section

## Steps

1. **Version source.** In `app/__init__.py`, add `__version__ = "0.2.0"` (the file is
   empty today). In `pyproject.toml`, remove `version = "0.1.0"` from `[project]` and add
   `dynamic = ["version"]`, then add the block below. In `frontend/package.json`, set
   `"version": "0.2.0"`. In `frontend/package-lock.json`, set both the top-level
   `version` and `packages[""].version` to `"0.2.0"`. Edit the lock by hand, because
   `npm install` needs network and the lock's other contents must not change.

   ```toml
   [tool.setuptools.dynamic]
   version = {attr = "app.__version__"}
   ```

2. **Backend route.** In `app/routes/api.py`, add next to the other models:

   ```python
   class VersionInfo(BaseModel):
       version: str
   ```

   Add `@router.get("/version", response_model=VersionInfo)` returning
   `VersionInfo(version=__version__)`, with `from app import __version__`. The route touches no Pi-hole, no config and no
   credentials. It stays behind `AuthenticationMiddleware` because `/api/version` is not
   in the middleware's public set, and that set must not change.

3. **API client.** In `frontend/src/api.ts`, add `export interface VersionInfo { version: string }`
   and, inside `api`, add
   `version: (signal?: AbortSignal) => request<VersionInfo>('/api/version', { signal })`.

4. **Release link helper.** Create `frontend/src/release.ts`:

   ```ts
   export const REPOSITORY_URL = 'https://github.com/nulthara/filterhelm'

   export const releaseUrl = (version: string) =>
     `${REPOSITORY_URL}/releases/tag/v${encodeURIComponent(version)}`
   ```

5. **Header link.** In `App.tsx`, add `const [version, setVersion] = useState<string>()`.
   Add an effect keyed on `auth?.authenticated`. When it is true, the effect creates an
   `AbortController`, calls `api.version(controller.signal)`, sets `version` on success,
   ignores errors, and aborts on cleanup. In the signed-in shell header only, render this
   right after the `brand-title` `Typography.Title` when `version` is set:

   ```tsx
   <Typography.Link className="app-version" href={releaseUrl(version)} target="_blank" rel="noopener noreferrer">v{version}</Typography.Link>
   ```

   The sign-in header stays unchanged.

6. **Style.** In `styles.css`, add `.app-version.ant-typography` with a small font size.
   The link color must meet WCAG AA (4.5:1) against the header background in both light
   and dark themes. Measure it on the resolved tokens, not the seed values.

7. **Rebuild.** Run `cd frontend && npm run build && npm run lint && npm test`, and commit
   the regenerated `app/static/ui/`.

8. **Workflow.** Create `.github/workflows/publish-image.yml`:
   - `name: Publish image`, triggered by `on: push: tags: ["v*"]` only
   - One job, `publish`, with `runs-on: ubuntu-latest` and the job-level permissions
     `contents: read` and `packages: write`, and nothing more
   - `env: IMAGE: ghcr.io/${{ github.repository }}`
   - The steps, each `uses:` pinned to the SHA from the Approach table with a
     `# vX.Y.Z` comment:
     1. Checkout
     2. A `run` step named "Check tag matches app version" that reads `__version__` from
        `app/__init__.py` with `python3` and the stdlib `ast` module, then exits non-zero
        unless `"$GITHUB_REF_NAME" == "v$version"`
     3. `setup-qemu-action` with `platforms: arm64`
     4. `setup-buildx-action`
     5. `login-action` with `registry: ghcr.io`, `username: ${{ github.actor }}` and
        `password: ${{ secrets.GITHUB_TOKEN }}`
     6. `metadata-action` (`id: meta`) with `images: ${{ env.IMAGE }}` and the tags
        `type=semver,pattern={{version}}` and `type=semver,pattern={{major}}.{{minor}}`.
        The action adds `latest` for semver tags on its own.
     7. `build-push-action` with `context: .`, `platforms: linux/amd64,linux/arm64`,
        `push: true`, `provenance: false`, `sbom: false`,
        `tags: ${{ steps.meta.outputs.tags }}` and
        `labels: ${{ steps.meta.outputs.labels }}`
   - Comments are short and present tense. No `pip install`, no secrets other than
     `GITHUB_TOKEN`, and no `pull_request` or `workflow_dispatch` trigger.

9. **Docs.** In `docs/deploy.md`, add a "Releasing" section covering:
   - Bump `app/__init__.py`, `frontend/package.json` and `frontend/package-lock.json`
     through a normal plan.
   - After merge, run `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest`, then
     `git push github main`.
   - Tag the merged commit with `git tag -a vX.Y.Z -m "FilterHelm X.Y.Z"` and push only
     that tag, `git push github vX.Y.Z`.
   - The image lands at `ghcr.io/nulthara/filterhelm`, tagged `X.Y.Z`, `X.Y` and `latest`,
     for `amd64` and `arm64`.

   Write registry names without a URL scheme (`ghcr.io/...`) so that
   `test_tracked_files_use_only_approved_addresses_and_hosts` needs no new allowed domain.

## Contracts

`GET /api/version`, authenticated. Unauthenticated requests get the middleware's existing
401.

Backend (`app/routes/api.py`):

```python
class VersionInfo(BaseModel):
    version: str
```

Response: `200 {"version": "0.2.0"}`

Frontend (`frontend/src/api.ts`):

```ts
export interface VersionInfo {
  version: string
}
// in api:
version: (signal?: AbortSignal) => request<VersionInfo>('/api/version', { signal }),
```

`pyproject.toml`: `[project].version` becomes `dynamic = ["version"]`, and the new
`[tool.setuptools.dynamic] version = {attr = "app.__version__"}` supplies it.

No config keys or DB changes.

## Tests

- `tests/test_api.py::test_version_route_returns_app_version` — `GET /api/version`, called
  the same way neighbouring route tests call routes, returns `{"version": app.__version__}`.
- `tests/test_auth.py::test_version_route_requires_authentication` — using the existing
  `middleware_status` helper, `/api/version` without a session is rejected with the same
  status as other protected routes.
- `tests/test_supply_chain.py::test_versions_agree` — `app.__version__` equals the
  `frontend/package.json` `version`, the `frontend/package-lock.json` top-level `version`,
  and `packages[""].version`, and it matches `^\d+\.\d+\.\d+$`. `pyproject.toml` has no
  static `[project].version`, lists `"version"` in `dynamic`, and has
  `tool.setuptools.dynamic.version.attr == "app.__version__"`.
- `tests/test_supply_chain.py::test_publish_workflow_pins_actions_by_sha` — every `uses:`
  line in `.github/workflows/publish-image.yml` matches `@[0-9a-f]{40}` followed by a
  `# v` comment.
- `tests/test_supply_chain.py::test_publish_workflow_shape` — parse the workflow with
  `yaml.safe_load` and assert all of the following:
  - The only trigger is `push.tags == ["v*"]`. PyYAML loads the `on` key as `True`, so
    read it under that key.
  - The permissions are exactly `{"contents": "read", "packages": "write"}`.
  - The build step's `platforms` is `linux/amd64,linux/arm64`.
  - `provenance` and `sbom` are both false.
  - The text contains no `pip install` and no `secrets.` reference other than
    `secrets.GITHUB_TOKEN`.
- `frontend/src/release.test.ts` — `releaseUrl('0.2.0')` equals
  `https://github.com/nulthara/filterhelm/releases/tag/v0.2.0`.

## Invariants at risk

1. One instance mutated at a time — not affected, because the change has no mutation path.
2. Diff before write — not affected, for the same reason.
3. Gravity gating — not affected.
4. No background scheduler — the workflow runs on GitHub's runners on a manual tag push,
   not inside the app. It publishes an image and never contacts a Pi-hole. The app gains
   no background task.
5. Credentials never reach the browser — `/api/version` returns only a version string.
6. Outbound requests go through `url_policy` — the backend makes no new outbound request.
   The GitHub link is a browser navigation the user starts, and it is outside the backend.
   The CSP is unchanged, because `connect-src 'self'` does not govern link navigation.
7. Every route behind auth — `/api/version` is protected by default, and
   `test_version_route_requires_authentication` locks that in. The middleware's public
   set stays unchanged.
8. Compiled frontend committed — Step 7 rebuilds and commits `app/static/ui/`.
9. `main` is production — the forge deploy is untouched. The new workflow lives in
   `.github/workflows/`, which Gitea ignores while `.gitea/workflows/` exists, and it
   triggers on tags, not branches.

The repo hygiene guard also applies. The new files name only `github.com` (already
allowed) and scheme-less `ghcr.io` references. `nulthara` is the approved public
identity. No forge hostname appears.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including the five new backend tests
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] `npm run build`, `npm run lint` and `npm test` clean, and `app/static/ui/` committed
- [ ] Signed in, the header shows `v0.2.0` after the title. The link opens the tag URL in
      a new tab with `rel="noopener noreferrer"`, and its contrast passes AA in both themes.
- [ ] Signed out, `GET /api/version` returns 401, and the sign-in screen shows no version
- [ ] Every `uses:` in the workflow is a 40-character SHA from the Approach table with a
      version comment
- [ ] `git diff main...HEAD --stat` touches only the Files list
- [ ] After merge (maintainer, not the implementer), pushing `v0.2.0` to GitHub runs the
      workflow green. `ghcr.io/nulthara/filterhelm:0.2.0` lists `linux/amd64` and
      `linux/arm64`. Pushing a tag that does not match `app.__version__` fails at the guard
      step before anything is built.
