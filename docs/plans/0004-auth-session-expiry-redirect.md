# 0004 — Redirect to login when a session expires (issue #14)

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0004-auth-session-expiry-redirect
Author: Kyrnveil
Date: 2026-09-04

## Problem

Gitea issue #14: when a session times out (idle 30 min or absolute 8 hr —
`app/auth.py`'s `AuthConfig`), the app does not log the user out or redirect to the
login screen. The header, sidebar, and "Sign out" button all stay visible; the user can
still click around, but every data-loading call now 401s and shows as a per-page toast
or inline error reading "Authentication required." Root cause (confirmed by reading the
code, not guessed): `frontend/src/App.tsx` calls `refreshAuth()` — which hits `GET
/api/auth/status` and sets the `auth` state — exactly once, in a `useEffect` with an
empty dependency array (`App.tsx:22`), and never again except after an explicit
login/logout. `frontend/src/api.ts`'s shared `request()` helper (`api.ts:119-136`)
turns every non-OK response, 401 included, into a generic thrown `Error` with no
special handling — nothing tells `App.tsx` that the reason a call failed is "your
session is gone," so `auth.authenticated` never flips back to `false` on its own.

## Scope

In:
- `frontend/src/api.ts`: `request()` recognizes a `401` response (excluding the login
  endpoint itself, which legitimately 401s on a wrong password while the user isn't
  authenticated yet) and notifies a small subscriber list.
- `frontend/src/App.tsx`: subscribes to that notification, re-runs `refreshAuth()`, and
  shows a one-time "session expired" message — which, combined with `auth.authenticated`
  becoming `false`, makes the existing login-screen branch (`App.tsx:49-71`) render
  automatically. No new route/component is needed since there is no router (App.tsx is
  already the sole gate between the login screen and the app shell).
- `README.md`'s session paragraph gains one sentence noting the client-side behavior.

Out:
- Any backend change. `app/auth.py`'s idle/absolute timeouts, `app/auth_middleware.py`'s
  401 response shape, and `app/routes/auth.py`'s `/api/auth/status` contract are all
  unchanged — the bug is purely that the frontend never reacts to a 401 it already
  receives.
- A shared "toast on every error" refactor, a router/route-guard library, or moving auth
  state out of `App.tsx` into a context — all bigger changes than this bug needs. The
  existing per-page `.catch()` → `message.error(...)` pattern is untouched for non-401
  errors; only 401 gets new behavior.
- Reading `AuthConfig.idle_seconds`/`absolute_seconds` from the environment
  (`AuthConfig.from_env()` currently hardcodes them) — a real gap, but unrelated to this
  bug and not mentioned in issue #14.
- A client-side idle timer that proactively logs the user out before the next failed
  request — this plan is reactive (detect the 401 that already happens), not predictive.

## Approach

**A tiny pub/sub in `api.ts`, not a global state library.** `api.ts` is a plain module
with no framework state today (`AuthStatus` etc. are just types); `App.tsx` is the only
place that holds `auth` state and there's no router or context provider to hook into.
The smallest change that lets `request()` (a plain function with no access to React
state) tell `App.tsx` (a component) "the session just died" is a module-level
subscriber list: `onSessionExpired(handler)` to subscribe, called internally whenever a
non-login-endpoint response is `401`. `App.tsx` subscribes once, mirroring the existing
`useEffect(() => { void refreshAuth() }, [])` pattern already at `App.tsx:22`.

**Re-run `refreshAuth()` rather than locally setting `authenticated: false`.** `GET
/api/auth/status` is on the middleware's public allowlist (`app/auth_middleware.py:24`)
and always returns the true current `mode`/`configured`/`authenticated` — reusing it
keeps `App.tsx` from having to fabricate a plausible `AuthStatus` object by hand (it
doesn't know `mode`/`configured` a priori) and gives a real confirmation of the log-out
state rather than assuming the first 401 was correct.

**Dedupe with a ref, not a debounce library.** A page that fires several requests in
parallel (e.g. `DomainsPage`/`AdlistsPage`/`DNSPage` all mounted, or one page's own
concurrent calls) can all 401 within the same tick once a session expires. Without
dedup, the user would see the "session expired" toast N times and `refreshAuth()` would
fire N redundant requests. A `useRef<boolean>` guard around the handler — set before
calling `refreshAuth()`, cleared in its `.finally()` — collapses a burst into one
toast and one status re-check, and resets cleanly so a *later* real expiry (after the
user logs back in and the session eventually times out again) still triggers.

**Excluding `/api/auth/login` from the notification.** A wrong password on the sign-in
form is `POST /api/auth/login` → `401 {"detail": "Invalid username or password"}`
(`app/routes/auth.py:40`) while the user was never authenticated in the first place —
that must stay exactly what it is today (an inline `authError` on the login card,
`App.tsx:33`), not trigger a "session expired" toast that would be nonsensical pre-login.
`request()` checks the request `path` before notifying.

Rejected alternatives:
- *A `fetch` wrapper / `Response` interceptor library* — no such dependency exists in
  this frontend (`AGENTS.md` says server calls go through `api.ts` directly); adding one
  for a single status-code check is disproportionate.
- *Polling `/api/auth/status` on an interval* — works, but adds a background timer to a
  browser tab for something that only needs to be noticed reactively, right when a real
  request already fails; also less immediate (a poll interval delay) than reacting to
  the 401 that was going to happen anyway.
- *Have every page's own `.catch()` check for the "Authentication required" string* —
  fragile (string-matching a backend detail message) and repeats the same logic in
  every page component instead of once in the shared `request()` helper.

## Files

- `frontend/src/api.ts` — `onSessionExpired` subscriber list; `request()` calls it on a
  qualifying `401`.
- `frontend/src/App.tsx` — subscribe to `onSessionExpired`; show a one-time message and
  re-run `refreshAuth()`.
- `README.md` — one sentence added to the session paragraph (around line 52).
- `docs/plans/0004-auth-session-expiry-redirect.md` — this plan.

## Steps

1. `frontend/src/api.ts`: below the existing type definitions and above `request()`,
   add:
   ```ts
   type SessionExpiredHandler = () => void
   let sessionExpiredHandlers: SessionExpiredHandler[] = []

   export function onSessionExpired(handler: SessionExpiredHandler): () => void {
     sessionExpiredHandlers.push(handler)
     return () => {
       sessionExpiredHandlers = sessionExpiredHandlers.filter((existing) => existing !== handler)
     }
   }
   ```
2. `frontend/src/api.ts::request()`: after computing `detail` in the `!response.ok`
   branch, before `throw new Error(detail)`, add:
   ```ts
   if (response.status === 401 && !path.startsWith('/api/auth/login')) {
     sessionExpiredHandlers.forEach((handler) => handler())
   }
   ```
   (`path` is the function's existing first parameter — no new argument needed.)
3. `frontend/src/App.tsx`: add `useRef` to the existing `import { useEffect, useState }
   from 'react'`. After the existing `useEffect(() => { void refreshAuth() }, [])`
   (line 22), add:
   ```tsx
   const sessionExpiredHandling = useRef(false)
   useEffect(() => api.onSessionExpired(() => {
     if (sessionExpiredHandling.current) return
     sessionExpiredHandling.current = true
     message.warning('Your session has expired. Please sign in again.')
     void refreshAuth().finally(() => { sessionExpiredHandling.current = false })
   }), [])
   ```
   Placed after `refreshAuth` is defined (it already is, at line 21) so the closure
   captures it. This mirrors the file's existing style of an empty-dependency-array
   `useEffect` for a one-time subscription (consistent with the `eslint-plugin-react-
   hooks` `exhaustive-deps` rule being `warn`, not `error`, in
   `frontend/eslint.config.js` — same as the pre-existing line 22 effect).
4. `README.md`: append to the sentence at line 52-53 ("Sessions are opaque, server-side,
   idle after 30 minutes, expire after eight hours, and use `HttpOnly; Secure;
   SameSite=Strict` cookies.") a new sentence: "The web UI detects an expired session on
   its next request and returns to the sign-in screen automatically."
5. `cd frontend && npm run build && npm run lint`; commit the regenerated
   `app/static/ui/`.

## Contracts

N/A — no backend route, request/response shape, or config key changes. This is a
frontend-only behavioral fix. The only new surface is `frontend/src/api.ts`'s exported
`onSessionExpired(handler: () => void): () => void`, an internal frontend function, not
part of the `/api` contract.

## Tests

N/A — no `app/` (backend) change, so no `tests/test_*.py` addition applies (per
`AGENTS.md`, backend behaviour changes need a pytest test; this plan has none). This
repo has no frontend test runner configured (`frontend/package.json` has no `test`
script; `AGENTS.md`'s Frontend section names `npm run build && npm run lint` as the
complete frontend check) — verification is manual, per Acceptance criteria below, plus
the existing `npm run build && npm run lint` gate for type/lint correctness of the new
code.

## Invariants at risk

(ARCHITECT.md § "Project invariants")

1–4, 6, 9. Not applicable — no mutation logic, gravity, scheduler, or outbound-URL
   change; this only reacts to a 401 the backend was already producing.
5. **FastAPI owns all credentials; browser talks only to `/api`.** Unchanged —
   `onSessionExpired` carries no data, just a signal; `refreshAuth()` calls the same
   `GET /api/auth/status` the app already calls today. Kept.
7. **Every route behind auth middleware.** Unchanged — no route added or exempted;
   `app/auth_middleware.py` is not touched by this plan. Kept.
8. **Compiled frontend committed.** `frontend/` changes (Steps 1–3) are rebuilt (`npm
   run build`) and `app/static/ui/` committed (Step 5). Kept.

## Acceptance criteria

- [ ] `cd frontend && npm run build && npm run lint` clean; `app/static/ui/` committed.
- [ ] Manual check: log in, then delete the `pihole_manager_session` cookie (or wait out
      `PIHOLE_MANAGER_COOKIE_SECURE`/idle window in a dev setup) and click any page
      action that calls the API — the app shows a "session expired" message and returns
      to the sign-in screen, instead of staying on the authenticated shell with a
      per-page error.
- [ ] Manual check: entering a wrong password on the sign-in form still shows "Invalid
      username or password" inline on the login card — no "session expired" toast fires
      for a login attempt that was never authenticated.
- [ ] Manual check: a page with several concurrent requests that all 401 at once (e.g.
      reload a data-heavy page right as the session expires) produces exactly one
      "session expired" toast, not one per failed request.
- [ ] `git diff main...HEAD` is limited to the Files list (+ this plan).
