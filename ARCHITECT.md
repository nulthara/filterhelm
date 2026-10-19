# ARCHITECT.md — orchestrated change process

This repo uses a three-role loop for any non-trivial change:

```
  ┌─────────────┐      plan file      ┌─────────────┐     diff + tests    ┌─────────────┐
  │  ARCHITECT  │ ──────────────────▶ │ IMPLEMENTER │ ──────────────────▶ │  REVIEWER   │
  │  (Claude)   │  docs/plans/NNNN   │  (Codex)    │   on a feature      │  (Claude)   │
  └─────────────┘                     └─────────────┘   branch             └─────────────┘
        ▲                                                                        │
        └──────────────────  iterate: revised plan / review notes  ◀─────────────┘
```

- **Architect** and **Reviewer** are the same Claude Code session, wearing different hats.
- **Implementer** is Codex, invoked through the `codex` plugin (`/codex:rescue`, or the
  `/handoff` wrapper). See CLAUDE.md § Toolchain prerequisites for installation.
- The **plan file** is the contract between the phases. Codex implements the plan, not the
  chat transcript. The Reviewer checks the diff against the plan, not against memory.

`AGENTS.md` holds the coding rules every role obeys (Codex reads it natively). This file
holds the *process*: what each phase produces, the gates between them, and the invariants a
review must protect.

---

## Roles and boundaries

### Architect (Claude)

Owns: understanding the request, reading the codebase, deciding the approach, writing the
plan file, getting it approved.

Does **not**: write implementation code. If the Architect is tempted to "just make the
change," see [When to skip the loop](#when-to-skip-the-loop) — either it qualifies as
trivial, or it needs a plan.

Deliverable: a file at `docs/plans/NNNN-slug.md` following the
[plan contract](#plan-file-contract), plus explicit user sign-off before handoff.

### Implementer (Codex)

Owns: turning an approved plan into a working diff on a feature branch — code, tests, and
build artifacts — staying inside the plan's stated scope.

Does **not**: redesign, expand scope, or "improve" adjacent code. If the plan is wrong or
underspecified, Codex stops and reports back rather than guessing.

Deliverable: committed changes on `feature/NNNN-slug`, all checks green
(see [Definition of done](#definition-of-done)), a short summary of what it did and any
deviations from the plan.

### Reviewer (Claude)

Owns: reading the full diff, running the checks, judging it against the plan, `AGENTS.md`,
and the [project invariants](#project-invariants). Produces an accept / iterate / escalate
verdict.

Does **not**: silently rewrite Codex's work. Small fixups (a typo, a missed import) are
fine and should be noted; anything larger goes back to the Implementer as review notes.

---

## Phase 1 — Plan

1. Clarify the request with the user until the goal and its boundaries are unambiguous.
2. Explore the codebase. Identify every file the change touches and every contract
   (API shapes in `frontend/src/api.ts` ↔ `app/routes/`, config keys, DB access) it
   depends on.
3. Write `docs/plans/NNNN-slug.md` (`NNNN` = next zero-padded integer). Copy
   `docs/plans/_TEMPLATE.md`.
4. Present the plan. **Do not hand off until the user approves it.** Approval is the gate.

### Plan file contract

Every plan must contain, in this order:

| Section | Content |
| --- | --- |
| **Problem** | What's wrong or missing, and why it matters. One paragraph. |
| **Scope** | Bullet list of what's in. A second list of what's explicitly out. |
| **Approach** | The design decision and the alternatives rejected. Enough that the Reviewer can judge whether the result matches intent. |
| **Files** | Every file to be created or modified, one line each with the reason. |
| **Steps** | Ordered, concrete changes. Each step should be independently reviewable. Name functions, endpoints, components, props. |
| **Contracts** | Any API request/response shape, config key, or DB change — written out exactly, both sides (backend + `api.ts`). |
| **Tests** | The specific tests to add or change, and what each asserts. New backend behaviour → a test in `tests/`. |
| **Invariants at risk** | Which entries from [project invariants](#project-invariants) this change gets near, and how the plan keeps them intact. |
| **Acceptance criteria** | A checklist the Reviewer runs. Observable outcomes, not "code looks good." |

If a section is genuinely N/A, write "N/A — <reason>", don't delete it.

---

## Phase 2 — Implement (Codex)

Handoff is a single Codex task, write-enabled, pointed at the plan file:

```
/handoff NNNN
```

…which expands to a `codex:codex-rescue` invocation equivalent to:

> Implement `docs/plans/NNNN-slug.md` exactly. Work on branch `feature/NNNN-slug`
> (create it from `main`). Follow `AGENTS.md` and `ARCHITECT.md`. Stay strictly inside
> the plan's Scope. If the plan is wrong, incomplete, or forces an invariant violation,
> stop and explain — do not improvise. When done: run every check in
> ARCHITECT.md "Definition of done", commit, and report what you did plus any deviations.

`--write` is required (Codex must edit). Use `--background` for anything larger than a
couple of files; `--wait` only for small, bounded plans.

### Iterating with Codex

Review notes go back to the **same** Codex thread so it keeps context:

```
/handoff NNNN --resume
```

Start a fresh thread (`--fresh`) only when the plan itself changed materially.

---

## Phase 3 — Review (Claude)

Run against the diff on `feature/NNNN-slug` (`git diff main...HEAD`).

After producing the final verdict, `/verify` posts the verdict, rubric summary,
Definition-of-done output, and review notes as a review on the forge PR for the feature
branch. Accept maps to `APPROVED`; Iterate and Escalate map to `REQUEST_CHANGES`. A `422`
retries once as `COMMENT` with the same verdict body to accommodate self-review restrictions.
The command reports the event posted and links to the PR. If no open PR matches, it offers
to create one and does so only on agreement. Missing configuration or posting failures
produce a notice while leaving the full terminal review intact. A review is a record
rather than a merge gate; it does not enforce branch protection.

### Rubric

1. **Scope** — every change traces to a plan step. Flag anything outside Scope, even if
   it's an improvement.
2. **Correctness** — logic, edge cases, error paths. Backend and `api.ts` types still
   agree. No broken contract with Pi-hole's API.
3. **Invariants** — walk the [project invariants](#project-invariants) list. Any change
   within reach of one gets an explicit check.
4. **`AGENTS.md` conformance** — the coding rules, both stacks.
5. **Tests** — the plan's Tests section is implemented and the assertions are real.
   `./.venv/bin/pytest` green. Coverage for the new behaviour, not just a smoke test.
6. **Security** — this app is a credential-holding proxy. New endpoints go through auth
   middleware; new outbound URLs go through `url_policy`; no secrets in logs or responses;
   request-body limits and same-origin checks still apply.
7. **Build hygiene** — if `frontend/` changed, `app/static/ui/` was rebuilt and committed
   (see invariants).

### Optional second pass

For architectural or security-sensitive changes, get an independent read before accepting:

- `/codex:review` — implementation-defect pass over the branch.
- `/codex:adversarial-review` — challenges the approach and its assumptions.

The Reviewer still owns the final verdict; treat Codex's review as input.

### Outcomes

- **Accept** — merge the branch (see [Branch and deploy discipline](#branch-and-deploy-discipline)).
- **Iterate** — write review notes referencing plan steps / rubric items, send back via
  `/handoff NNNN --resume`.
- **Escalate** — the plan was wrong. Return to Phase 1, revise the plan file, note why,
  re-approve, then `/handoff NNNN --fresh`.

---

## Project invariants

Derived from `README.md` and `docs/design.md`. A change may not break these without an
explicit, approved plan decision that says so.

1. **One instance mutated at a time.** Never write to individual pihole instances at the same
   operation or on the same schedule. This is partly why alternative solutions were rejected.
2. **Diff before write.** Mutations are computed from a comparison against current state,
   not applied blind.
3. **Gravity updates are gated behind real changes.** No gravity rebuild unless an adlist
   actually changed.
4. **No background scheduler.** All mutation is user-initiated. Do not add cron, APScheduler,
   background tasks, or startup jobs that mutate a Pi-hole.
5. **FastAPI owns all credentials and Pi-hole access.** The browser talks only to `/api`.
   No Pi-hole URL, password, or app-password reaches the frontend bundle.
6. **All outbound requests go through `app/url_policy.py`.** SSRF hardening is not optional
   for any new feature that fetches a URL.
7. **Every route is behind the auth middleware** unless there is a deliberate, documented
   reason (e.g. `/api/auth/status`).
8. **The compiled frontend is committed** under `app/static/ui/`. The deploy target has no
   Node.js. Any `frontend/` change is incomplete until `npm run build` output is committed.
   `site/dist/` is built by Cloudflare, never committed, and never reaches the deploy target.
9. **`main` is production.** Push to `main` auto-deploys via the Gitea runner
   (`.gitea/workflows/deploy.yml`). Work never lands directly on `main`.

Keep this list in sync with `README.md` when the product rules change.

---

## Definition of done

`site/` holds the Astro Starlight public documentation; it is separate from `frontend/`
and is never served by the app. See `docs/site.md` for build and publishing settings.

The Implementer runs all of these; the Reviewer re-runs them.

```bash
# Backend
./.venv/bin/pytest

# Frontend (only if frontend/ changed)
cd frontend && npm run build && npm run lint && npm test
# then commit the regenerated app/static/ui/ output

# Public documentation (if site/ changed; built separately by Cloudflare)
cd site && npm ci && npm run build
# audit dependency changes; site/.npmrc enforces the 24-hour release age gate
npm audit
```

Plus: the plan's Acceptance criteria checklist passes, and the diff is limited to the
plan's Files list (additions must be justified in the handback report).

---

## When to skip the loop

Skip straight to a direct Claude edit, no plan file, when **all** of these hold:

- One or two files, no new dependency, no schema/contract/config change.
- No project invariant is anywhere near the change.
- It's mechanical: a typo, a copy fix, a version bump, a comment, a lint fix, a
  one-line bug fix with an obvious cause.

Everything else — new endpoints, new UI surfaces, cross-cutting refactors, anything
touching auth / `url_policy` / mutation logic / the Pi-hole client — gets a plan.

When in doubt, write the plan. It's cheap.

---

## Branch and deploy discipline

- Branch name: `feature/NNNN-slug`, matching the plan file.
- One plan → one branch → one fast-forward-only merge to `main` on the forge.
  If `main` moved, rebase the branch on `origin/main` first.
- Merge only after an **Accept** verdict.
- The plan file stays in the repo as the record of why the change looks the way it does;
  mark it `Status: shipped` on merge.
- After merge, confirm the deploy came back up (the workflow curls
  `/api/auth/status` — watch the run).
- `/verify` posting requires `FILTERHELM_FORGE_API` (API base URL including scheme
  and port, no trailing slash) and `FILTERHELM_FORGE_TOKEN` (token with `repository`
  write scope). Set both outside the repo: the hygiene guard forbids the forge's
  identifying details in tracked files. Owner and repo are derived from the origin
  git remote at runtime.
