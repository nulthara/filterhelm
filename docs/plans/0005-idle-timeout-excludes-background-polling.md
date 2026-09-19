# 0005 — Exclude background polling from the session idle timer

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0005-idle-timeout-excludes-background-polling
Author: Kyrnveil
Date: 2026-09-04

## Problem

Discovered while manually verifying plan 0004 (issue #14's redirect-on-expiry fix):
after logging in and leaving the app open on the default Status page with zero
interaction for over an hour, the session was never treated as expired. Plan 0004's
fix is working correctly — it reacts to a real `401` — but no `401` ever occurred,
because `StatusPage.tsx`'s self-rescheduling `setTimeout` loop
(`frontend/src/pages/StatusPage.tsx:61-86`) calls `GET /api/status` every 10 seconds
for as long as that page stays mounted (the default landing page, per `App.tsx`'s
initial `page` state), and `AuthManager.session_user` (`app/auth.py:173-184`) refreshes
`last_seen` on **every** successful authenticated request, this background poll
included. With `last_seen` reset every 10 seconds, `last_seen + idle_seconds <= now`
(the idle check) can never become true while the tab is open — only the 8-hour
absolute cap (`expires_at`) can still fire. The 30-minute idle timeout, whose entire
purpose is to end an unattended session, is effectively unreachable in the one
situation it exists for: a browser tab left open and forgotten.

## Scope

In:
- Backend: `AuthManager.session_user` gains a `refresh_last_seen: bool` keyword
  (default `True`) that skips the `last_seen` update without changing the idle/
  absolute expiry check itself — an already-idle-expired session still returns `None`
  regardless of this flag.
- Backend: a new header constant (`POLL_HEADER`) in `app/auth.py`, read by
  `AuthenticationMiddleware` (`app/auth_middleware.py`) to decide whether to pass
  `refresh_last_seen=False` for local-mode sessions.
- Frontend: `frontend/src/api.ts`'s `api.status()` gains an optional
  `{ background?: boolean }` parameter that sets that header.
- Frontend: `frontend/src/pages/StatusPage.tsx`'s recurring timer-driven poll passes
  `background: true`; the initial mount fetch and the manual "Refresh" button do not
  (both represent genuine reasons to consider the session active: the user navigated
  to the page, or explicitly clicked refresh).

Out:
- Changing the idle (30 min) or absolute (8 hr) durations themselves.
- Making those durations configurable via environment variables — `AuthConfig.from_env`
  already doesn't read `idle_seconds`/`absolute_seconds` from the environment; that's a
  separate, pre-existing gap, not part of this fix.
- Pausing `StatusPage`'s poll via the Page Visibility API when the tab is hidden.
  Considered: it would reduce background request volume, but doesn't fix the actual
  problem — a tab left open and *visible* but unattended (exactly the dinner scenario
  that surfaced this) would still poll and stay "active" forever. The `last_seen`-skip
  approach fixes the real unattended-session case directly; visibility-based pausing
  would be additive polish, not a substitute, and isn't needed to close the gap.
- Any other page's polling — no other page auto-refreshes on a timer today
  (confirmed: `StatusPage`'s is the only `setInterval`/`setTimeout`-driven network call
  in `frontend/src/`; every other page fetches once on mount or on explicit action).
- The proxy auth mode. `session_user` (and therefore `last_seen`) is only used in local
  mode; `AuthManager.proxy_user` (`app/auth.py:191-202`) has no session/idle concept at
  all, so this header is inert for proxy deployments.

## Approach

**A header the frontend sets on the recurring poll call only, read by the middleware,
threaded through to `session_user` as a keyword.** This is the minimal change that
distinguishes "the browser polled in the background" from "a person did something" at
the one place that currently conflates them (every authenticated request refreshes
`last_seen` identically). The header carries no session data — it's a plain marker,
checked only in local mode (`app/auth_middleware.py`'s `else` branch); the `proxy` mode
branch never touches `session_user`, so it's untouched by this plan.

**The idle *check* is unconditional; only the idle *refresh* is skippable.** A poll
request still runs the full `row["expires_at"] <= now or row["last_seen"] +
idle_seconds <= now` check before deciding whether to skip the `UPDATE`. This matters:
if a session has genuinely gone idle (the person left and the poll has been ticking
every 10s for 31 minutes since their last real action), the *next* poll after the
30-minute mark correctly finds the session already expired and returns `None` — which
the poll's own request goes through the same `frontend/src/api.ts` `request()` helper
plan 0004 added, so that `401` still fires `onSessionExpired` and bounces to the login
screen, same as any other request. The two fixes compose: this plan makes the idle
clock reachable again; plan 0004 makes reaching it visible to the user. Neither works
right without the other now that both exist.

**Only the recurring poll is marked background — not the initial mount fetch.** On
first navigating to the Status page (or reloading the browser tab), `api.status()` is
called once immediately, outside `scheduleRefresh`'s loop
(`StatusPage.tsx:70-81`). That call, and the manual "Refresh" button's `load(true)`
(`StatusPage.tsx:160`), both stem from something the person actually did (navigated
here, or clicked a button) — they should keep counting as activity. Only the
subsequent unattended `load(false, true).finally(scheduleRefresh)` calls, which fire
every 10 seconds with zero further input, are the ones that must not count.

Rejected alternatives:
- *Key off the request path (`/api/status`) instead of a header.* Doesn't work: the
  same endpoint is called by the initial mount fetch and the manual Refresh button too,
  and those **should** count as activity. Path alone can't distinguish "the timer
  fired" from "the user clicked."
- *Debounce/coalesce polling instead of tagging it.* Doesn't address the root cause —
  any interval short enough to keep the status view useful (10s) is far shorter than
  the 30-minute idle window, so *any* untagged periodic call defeats idle timeout
  regardless of its exact interval.
- *Move the idle check to the frontend (a client-side inactivity timer that stops
  polling / forces logout after N minutes of no mouse/keyboard input).* Bigger change,
  duplicates state the server already owns, and is bypassable by anything that keeps
  dispatching synthetic events. The server-side `last_seen` is the source of truth;
  this plan keeps it that way and just stops one specific caller from corrupting it.

## Files

- `app/auth.py` — `POLL_HEADER` constant; `session_user` gains `refresh_last_seen: bool
  = True`.
- `app/auth_middleware.py` — read `POLL_HEADER` from request headers (local mode only);
  pass `refresh_last_seen=not is_poll` to `session_user`.
- `frontend/src/api.ts` — `api.status(options?: { background?: boolean })` sets the
  header when `background` is true.
- `frontend/src/pages/StatusPage.tsx` — `load` gains a `background` parameter; the
  recurring `scheduleRefresh` call passes `background: true`; the manual Refresh button
  and initial mount fetch do not.
- `README.md` — session paragraph (around the sentence plan 0004 added) gains a note
  that background status polling does not keep an unattended session alive.
- `tests/test_auth.py` — `session_user` unit tests for `refresh_last_seen`; a
  middleware test that a poll-tagged request with a valid cookie does not refresh
  `last_seen` while an untagged one does, and that an already-idle-expired session
  returns `None` either way.

## Steps

1. `app/auth.py`: add `POLL_HEADER = "x-pihole-manager-poll"` next to `SESSION_COOKIE`
   (module-level constant, same style).
2. `app/auth.py::AuthManager.session_user`: change the signature to `def
   session_user(self, token: str | None, now: int | None = None, *, refresh_last_seen:
   bool = True) -> str | None`. Wrap the existing `database.execute("UPDATE sessions
   SET last_seen = ? WHERE token_hash = ?", (now, token_hash))` in `if
   refresh_last_seen:`. No other change — the expiry check above it (`row is None or
   row["expires_at"] <= now or row["last_seen"] + self.config.idle_seconds <= now`)
   stays unconditional.
3. `app/auth_middleware.py`: import `POLL_HEADER` alongside the existing `from app.auth
   import AuthManager, SESSION_COOKIE, current_user`. In the `else` branch (local
   mode), before calling `session_user`:
   ```python
   is_poll = headers.get(POLL_HEADER, "") == "1"
   user = self.manager.session_user(morsel.value if morsel else None, refresh_last_seen=not is_poll)
   ```
   (`headers` here is the already-lowercased dict built earlier in `__call__`, so
   `POLL_HEADER`'s lowercase form matches directly.) The `proxy` branch is untouched.
4. `frontend/src/api.ts`: change
   ```ts
   status: () => request<{ instances: BlockingStatus[] }>('/api/status'),
   ```
   to
   ```ts
   status: (options?: { background?: boolean }) =>
     request<{ instances: BlockingStatus[] }>(
       '/api/status',
       options?.background ? { headers: { 'X-Pihole-Manager-Poll': '1' } } : undefined,
     ),
   ```
5. `frontend/src/pages/StatusPage.tsx`: change `load`'s signature to `const load =
   useCallback(async (showLoading = false, background = false) => { ... const result =
   await api.status(background ? { background: true } : undefined) ... }, [message])`.
   In the `useEffect` at line 61, change the recurring call from `void
   load().finally(scheduleRefresh)` to `void load(false, true).finally(scheduleRefresh)`.
   Leave the initial `api.status()` call (lines 70-81, outside `load`) and the Refresh
   button's `load(true)` (line 160) unchanged — both omit the background flag,
   defaulting to `false`.
6. `README.md`: append to the sentence plan 0004 added ("The web UI detects an expired
   session on its next request and returns to the sign-in screen automatically.") a
   second sentence: "Background status polling does not count as activity, so an
   unattended tab still times out on schedule."
7. Add the tests below; `./.venv/bin/pytest`; `cd frontend && npm run build && npm run
   lint`; commit the regenerated `app/static/ui/`.

## Contracts

**New request header:** `X-Pihole-Manager-Poll: 1`, sent only by
`StatusPage.tsx`'s recurring auto-refresh call. Read by `AuthenticationMiddleware`
in local auth mode only; ignored (and harmless if sent) in proxy mode, since
`proxy_user` never touches `last_seen`. Not part of the `/api` JSON contract — it's a
transport-level marker, not a body/query field, so no Pydantic model changes.

**Backend internal API change:** `AuthManager.session_user(token, now=None, *,
refresh_last_seen=True)` — additive, keyword-only, defaults preserve today's behavior
for every existing caller (`app/auth_middleware.py`'s proxy branch doesn't call it at
all; the only other caller is this same middleware's local branch, updated in Step 3).

**`frontend/src/api.ts`:** `status: (options?: { background?: boolean }) => ...` —
existing callers (`StatusPage.tsx`'s initial fetch and Refresh button) that call
`api.status()` with no argument are unaffected; `options` is optional.

## Tests

- `tests/test_auth.py`
  - `test_session_user_skips_last_seen_refresh_when_requested` — create a session at
    `now=100`; call `manager.session_user(token, now=105, refresh_last_seen=False)`;
    read the row directly (`SELECT last_seen FROM sessions WHERE token_hash = ?`) and
    assert it's still `100`, not `105`. Then call `session_user(token, now=106)`
    (default `refresh_last_seen=True`) and assert `last_seen` becomes `106`.
  - `test_session_user_still_enforces_idle_expiry_when_refresh_skipped` — with
    `idle_seconds=10`, create a session at `now=100`; call `session_user(token, now=105,
    refresh_last_seen=False)` → returns `"admin"` (not yet idle) and does not extend
    `last_seen`; call `session_user(token, now=111, refresh_last_seen=False)` → returns
    `None` (now idle-expired relative to the *original* `last_seen=100`, proving the
    skip didn't quietly keep it alive).
  - `test_authentication_middleware_poll_header_skips_last_seen_refresh` — extend the
    `middleware_status` helper (or add a variant) to pass a `Cookie` header for a real
    session token alongside `[("x-pihole-manager-poll", "1")]`; call it, then read
    `manager`'s `sessions.last_seen` directly and assert it's unchanged from creation.
    A second call with the same cookie and no poll header updates `last_seen`.
  - `test_authentication_middleware_poll_header_does_not_resurrect_expired_session` —
    an idle-expired session (per the pattern in
    `test_sessions_enforce_idle_and_absolute_expiration`) still gets `401` through the
    middleware whether or not the poll header is present.

## Invariants at risk

(ARCHITECT.md § "Project invariants")

1, 2, 3, 4, 6, 8, 9. Not applicable — no mutation, gravity, scheduler, outbound-URL, or
   build-hygiene surface touched (frontend changes are covered by the standard rebuild
   step, Step 7).
5. **FastAPI owns all credentials; browser talks only to `/api`.** The new header
   carries no credential or session data — it's a boolean marker read only by the
   middleware. Kept.
7. **Every route behind auth middleware.** Unchanged — this plan changes *what the
   middleware does after* authenticating a request, not which routes require it. A
   poll-tagged request with no valid session still gets `401` exactly as before (Test:
   `test_authentication_middleware_poll_header_does_not_resurrect_expired_session`).
   Kept.

This plan is itself an auth-adjacent change per `ARCHITECT.md`'s "gets a plan" list —
hence a full plan rather than a skip-loop fix, despite being small.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including every test in the Tests section.
- [ ] `cd frontend && npm run build && npm run lint` clean; `app/static/ui/` committed.
- [ ] Manual check: with `idle_seconds` temporarily lowered for a local test (or by
      waiting out the real 30 minutes once), leaving the app open and idle on the
      Status page for longer than the idle window results in the next background poll
      (or any subsequent action) redirecting to the login screen — not staying
      authenticated indefinitely.
- [ ] Manual check: actively using the app (navigating between pages, clicking
      Refresh) keeps the session alive as before — this plan must not make idle
      timeout *more* aggressive for a genuinely active user.
- [ ] `git diff main...HEAD` is limited to the Files list (+ this plan).
