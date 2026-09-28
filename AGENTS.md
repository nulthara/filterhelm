# AGENTS.md

Coding rules for anyone — Claude or Codex — writing code in this repo. The *process*
(plan → implement → review, the gates, the invariants) lives in
[ARCHITECT.md](ARCHITECT.md); read it first.

## Process rules

- Implement the approved plan in `docs/plans/NNNN-slug.md`. Stay inside its Scope.
  If the plan is wrong or forces an invariant violation, stop and report — do not improvise.
- Work on `feature/NNNN-slug`, branched from `main`. Never commit to `main`.
- A change is done only when the [Definition of done](ARCHITECT.md#definition-of-done)
  checks pass and the diff matches the plan's Files list.

## Repo shape

- `app/` — FastAPI backend. Routes in `app/routes/`, one module per concern
  (`config`, `security`, `auth`, `url_policy`, `http_hardening`, `pihole_client`, `audit`).
- `frontend/src/` — React app. `api.ts` is the single client for `/api`; `pages/` holds
  one component per screen.
- `app/static/ui/` — the committed production build of `frontend/`. Do not hand-edit.
- `tests/` — pytest, one `test_<module>.py` per backend concern.
- `docs/` — `design.md` (product rules), `deploy.md`, `plans/` (change records).

## Backend (Python / FastAPI)

- Python 3.14, `from __future__ import annotations`, full type hints on public functions.
- Keep the module boundaries above. Credential handling, Pi-hole access, and mutation
  safety stay in the backend — never leak them toward the frontend.
- Every new route goes through the auth middleware unless there is a documented reason
  (like `/api/auth/status`). New outbound HTTP goes through `app/url_policy.py`.
- Mutations follow the diff-before-write rule and never touch more than one instance per
  operation. No background jobs, schedulers, or startup mutation.
- Pydantic models for request/response bodies; mirror any shape change into `frontend/src/api.ts`.
- New backend behaviour ships with a test in `tests/`. Run `./.venv/bin/pytest` before handoff.
- Don't log secrets, passwords, app-passwords, or full Pi-hole URLs with credentials.

## Frontend (React / TypeScript / Ant Design)

- Functional components with hooks, PascalCase filenames and component names, one screen
  per file in `pages/`. Group related components together, keep them small and single-purpose.
- TypeScript throughout; define prop interfaces for every component. Use spaces for indent.
- All server calls go through `frontend/src/api.ts` — components never `fetch` directly.
  Keep its types in sync with the backend Pydantic models.
- Use Ant Design components and its `App` context (`AntApp.useApp()` for `message`); don't
  hand-roll UI that AntD already provides.
- `useState` for local state; `useContext` + `useReducer` for shared state; lift state up
  only when needed; avoid prop drilling via composition or context.
- `useEffect` only for real side effects, with correct deps and cleanup (`AbortController`
  for fetches, unsubscribe for subscriptions). Compute derived values during render, not
  in an effect. `useCallback`/`useMemo` only when profiling shows a need.
- Prefer controlled inputs. Break complex JSX expressions into named variables.
- After any `frontend/` change: `cd frontend && npm run build && npm run lint`, then
  commit the regenerated `app/static/ui/`. The deploy target has no Node.js.

## Dependency updates

- Accept only releases public for at least 24 hours. This mitigates hijacked upstreams.
- npm: `frontend/.npmrc` enforces `min-release-age=1`. Run `npm audit` after any change.
  Majors need their own plan.
- Python: edit pins in `requirements.lock` (and `pyproject.toml` for direct deps).
  Install with `--uploaded-prior-to P1D`. Run `pip check` and `pip-audit`. Then run
  `python3 scripts/lock_hashes.py`. Never hand-edit `--hash` lines.
