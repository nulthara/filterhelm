# 0043 — First-instance onboarding prompt and Vite 7.3.7

Status: in-progress           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0043-first-instance-onboarding
Author: Kyrnveil
Date: 2026-10-22

## Problem

A fresh FilterHelm install lands on the Status page with an empty table and no hint about
what to do next. Every page is useless until an instance exists, so a new user should be
offered a direct path to the Add instance form. Separately, Dependabot reports three
advisories against `vite` 7.3.6 in `frontend/package-lock.json` (GHSA-698v-4r3x-3vcv,
GHSA-r825-w8mm-5pxw, GHSA-587h-7wmj-h5pm). `npm audit` lists two more on the same range
(GHSA-9jrq-w75r-8gcw, GHSA-rq7h-c2jc-7f22). All five affect the dev server only, so the
shipped bundle is not exposed, but a clean audit matters before 1.0. Vite 7.3.7 fixes all
five and was published 2026-10-06, well past the 24-hour release-age gate.

## Scope

In:
- New read-only route `GET /api/instances/count` that reads local config only.
- A confirm dialog after authentication when the count is zero.
- "Set up instance" opens the Instances page with the Add instance modal already open.
- "Not now" closes the dialog. It returns on the next app load while the count is still zero.
- One sentence each in the first-run tutorial and the Manage instances guide that mentions the prompt.
- Lockfile bump of `vite` from 7.3.6 to 7.3.7 and the matching rebuild of `app/static/ui/`.

Out:
- Remembering a dismissal in browser storage.
- A multi-step wizard, connection test inside the prompt, or any change to the Add instance form.
- Empty-state changes on Status, Instances, or other pages.
- The 1.0.0 version bump. It ships as a separate step after this plan lands.
- Vite 8 or any other dependency change.

## Approach

The frontend needs to know whether any instance exists. `GET /api/instances` answers that,
but it calls every configured Pi-hole to fill `status`. The Status page also calls every
Pi-hole on load, and two simultaneous authenticated calls to one Pi-hole can race and
return 401. A dedicated count route avoids that, because it reads `instances.yaml` and
contacts no Pi-hole.

Rejected alternatives:
- Reuse `GET /api/instances`. It contacts every Pi-hole at login and races the Status page.
- Add the count to `/api/auth/status`. That route is unauthenticated, so the count would leak to anyone.
- Add a `probe=false` flag to `GET /api/instances`. It changes the meaning of an existing
  response, where `status` would have to become optional.

The prompt lives in `App.tsx`, because only the shell knows when authentication completes
and can switch pages. It uses `modal.confirm` from `AntApp.useApp()`, the same pattern
`InstancesPage` uses for service actions. On confirm, `App` sets `page` to `'instances'` and
a `pendingAdd` flag. `InstancesPage` takes a new `initialAddOpen` prop and seeds its
`modalOpen` state from it. Seeding avoids a mount effect. `App` clears `pendingAdd` on every
navigation, so leaving and returning to Instances does not reopen the modal. Accepting the
prompt also bumps an `addRequest` counter that `App` passes as the `key` on `InstancesPage`.
The key change remounts the page, so the seeded state applies even when Instances was
already open before the count response arrived. `App` already remounts `DNSPage` this way.

The Add instance modal already works when opened without `openAdd()`. Its `notes` and
`verify_ssl` fields carry `initialValue`, and `editing` starts undefined.

The Vite fix stays inside the existing `^7.0.0` range, so `package.json` does not change.
Vitest 5.0.3 and `@vitejs/plugin-react` 5.2.0 both accept Vite 7.

## Files

- `app/routes/api.py` — add `InstanceCount` model and `GET /instances/count` route.
- `frontend/src/api.ts` — add `InstanceCount` type and `api.instanceCount`.
- `frontend/src/App.tsx` — count check after auth, confirm dialog, `pendingAdd` state, prop to `InstancesPage`.
- `frontend/src/pages/InstancesPage.tsx` — `InstancesPageProps` with `initialAddOpen`, seeds `modalOpen`.
- `frontend/package-lock.json` — `vite` 7.3.6 → 7.3.7.
- `app/static/ui/` — rebuilt production bundle.
- `site/src/content/docs/start/first-run.mdx` — mention the prompt in step 3.
- `site/src/content/docs/guides/manage-instances.mdx` — mention the prompt under "Add an instance". Added after review.
- `tests/test_api.py` — route tests.
- `tests/test_auth.py` — middleware test for the new route.

## Steps

1. **Backend route.** In `app/routes/api.py`, next to `VersionInfo`, add:
   ```python
   class InstanceCount(BaseModel):
       count: int
   ```
   Add the route directly above `instances_list`:
   ```python
   @router.get("/instances/count", response_model=InstanceCount)
   def instances_count() -> InstanceCount:
       return InstanceCount(count=len(load_instances()))
   ```
   It must not construct a `PiholeClient` or call `_connection`.

2. **API client.** In `frontend/src/api.ts`, add `export interface InstanceCount { count: number }`
   beside `VersionInfo`. Add to the `api` object, next to `listInstances`:
   `instanceCount: (signal?: AbortSignal) => request<InstanceCount>('/api/instances/count', { signal }),`

3. **InstancesPage prop.** Add
   ```ts
   interface InstancesPageProps {
     initialAddOpen?: boolean
   }
   ```
   Change the signature to `export default function InstancesPage({ initialAddOpen = false }: InstancesPageProps)`
   and the state to `useState(initialAddOpen)` for `modalOpen`. No other change in this file.

4. **App prompt.** In `frontend/src/App.tsx`:
   - Destructure `modal` alongside `message` from `AntApp.useApp()`.
   - Add `const [pendingAdd, setPendingAdd] = useState(false)` and
     `const [addRequest, setAddRequest] = useState(0)`.
   - Add a `navigate(key: string)` helper that runs `setDnsTab('hosts')`, `setPendingAdd(false)`,
     and `setPage(key)`. Use it in the `Menu` `onClick`. In `openFromDrift`, also call `setPendingAdd(false)`.
   - Add an effect keyed on `auth?.authenticated`, shaped like the version effect. It returns
     early when not authenticated. It creates an `AbortController` and calls
     `api.instanceCount(controller.signal)`. When `count === 0` and the signal is not aborted, it calls:
     ```ts
     modal.confirm({
       title: 'Set up your first Pi-hole',
       content: 'It looks like you need to set up your first Pi-hole instance. Would you like to do that now?',
       okText: 'Set up instance',
       cancelText: 'Not now',
       onOk: () => { setPage('instances'); setPendingAdd(true); setAddRequest((count) => count + 1) },
     })
     ```
     Errors are swallowed with `.catch(() => {})`, as the version effect does. The cleanup aborts the controller.
     Under `StrictMode` the first effect run is aborted, so only one dialog appears.
   - Render `<InstancesPage key={addRequest} initialAddOpen={pendingAdd} />` in the page switch.

5. **Vite bump.** In `frontend/`, run `npm update vite --save=false`. That leaves
   `package.json` at `"vite": "^7.0.0"` and moves only the lockfile entry to 7.3.7. Confirm with `git diff frontend/package.json` (empty) and
   `npm audit` (0 vulnerabilities). The `.npmrc` age gate stays in force.

6. **Docs.** In `site/src/content/docs/start/first-run.mdx` step 3, add this sentence before
   "Open **Instances**":
   "If no instances exist yet, FilterHelm offers to open the Add instance form for you after you sign in."

7. **Build.** Run `cd frontend && npm run build && npm run lint && npm test` and commit the
   regenerated `app/static/ui/`. Run `cd site && npm ci && npm run build && npm audit`.

## Contracts

Backend (`app/routes/api.py`):
```python
class InstanceCount(BaseModel):
    count: int

@router.get("/instances/count", response_model=InstanceCount)
def instances_count() -> InstanceCount: ...
```
Response: `200 {"count": 0}`. Unauthenticated: `401`, from the middleware.

Frontend (`frontend/src/api.ts`):
```ts
export interface InstanceCount {
  count: number
}

instanceCount: (signal?: AbortSignal) => request<InstanceCount>('/api/instances/count', { signal }),
```

Component prop (`frontend/src/pages/InstancesPage.tsx`):
```ts
interface InstancesPageProps {
  initialAddOpen?: boolean
}
```

No config key or DB change.

## Tests

- `tests/test_api.py::test_instances_count_reports_configured_instances` — with `setup_api`,
  `api.instances_count().count == 1`. After saving a second instance via `api.instance_save`, it is 2.
- `tests/test_api.py::test_instances_count_is_zero_without_instances` — monkeypatch
  `api.load_instances` to return `[]`; the count is 0.
- `tests/test_api.py::test_instances_count_does_not_contact_pihole` — monkeypatch
  `api.PiholeClient` with a class whose `__init__` raises `AssertionError`; `api.instances_count()`
  still returns the count.
- `tests/test_api.py::test_instances_count_route_is_registered` — `api.router.routes`
  contains a route with path `/api/instances/count` whose methods include `GET`. Read the
  `APIRouter`, not `app.main.app.routes`, because FastAPI 0.141 stores included routers in
  `app.routes` as `_IncludedRouter` objects that have no `.path`.
- `tests/test_auth.py::test_instance_count_route_requires_authentication` —
  `middleware_status(local_manager(tmp_path), "/api/instances/count") == 401`.

Frontend unit tests: N/A. The repo's Vitest suite tests pure functions and has no DOM
renderer. Adding one is a new dependency outside this scope. The reviewer checks the
dialog flow in a running app (see Acceptance criteria).

## Invariants at risk

1. **One instance mutated at a time** — untouched. The new route and prompt are read-only.
2. **Diff before write** — untouched. Adding an instance uses the existing form and route.
3. **Gravity gating** — untouched.
4. **No background scheduler** — the count check runs once per app load in the browser,
   after authentication. It mutates nothing and adds no server-side job.
5. **Credentials stay in FastAPI** — the route returns an integer only. No name, URL, or password.
6. **Outbound URLs through `url_policy`** — the route makes no outbound request.
   `test_instances_count_does_not_contact_pihole` guards that.
7. **Auth middleware** — the route sits under `/api` and is not on the public allowlist.
   `test_instance_count_route_requires_authentication` guards that.
8. **Compiled frontend committed** — Step 7 rebuilds and commits `app/static/ui/`.
   `site/dist/` stays uncommitted.
9. **`main` is production** — work lands on `feature/0043-first-instance-onboarding`.

## Acceptance criteria

- [ ] `GET /api/instances/count` returns `{"count": N}` for an authenticated session and 401 without one.
- [ ] With an empty `instances.yaml`, signing in shows one dialog titled "Set up your first Pi-hole" with the requested wording.
- [ ] "Set up instance" switches to Instances with the Add instance modal open, Name focused, and TLS verification on.
- [ ] Cancelling that modal, leaving Instances, and returning does not reopen it.
- [ ] Opening Instances before the prompt appears, then clicking "Set up instance", opens the Add modal.
  Simulate a slow count by throttling `/api/instances/count` in the browser.
- [ ] "Not now" closes the dialog and leaves the user on Status. A browser refresh shows the dialog again.
- [ ] With one or more instances configured, no dialog appears.
- [ ] Network tab on sign-in shows `/api/instances/count` and no `/api/instances` request from the shell.
- [ ] `frontend/package.json` unchanged; lockfile shows `vite` 7.3.7; `npm audit` in `frontend/` reports 0 vulnerabilities.
- [ ] `./.venv/bin/pytest` green.
- [ ] `npm run build` + `npm run lint` + `npm test` clean, `app/static/ui/` committed.
- [ ] `cd site && npm ci && npm run build` clean; `npm audit` clean; `site/dist/` not committed.
- [ ] Dependabot alerts 1–3 on the GitHub mirror close after `main` is pushed there.
