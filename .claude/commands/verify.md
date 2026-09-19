---
description: Review phase — check the feature branch against the plan and invariants
argument-hint: "<NNNN>"
allowed-tools: Read, Glob, Grep, Bash(git:*), Bash(./.venv/bin/pytest:*), Bash(ls:*), Bash(cd frontend && npm run build:*), Bash(cd frontend && npm run lint:*)
---

You are the **Reviewer** as defined in `ARCHITECT.md` Phase 3. Judge the work; do not
silently rewrite it (a typo or missed import is fine to fix and note — anything larger
goes back as review notes).

Arguments:
$ARGUMENTS

Steps:

1. Parse `NNNN`, resolve `docs/plans/NNNN-*.md`, read it. Check out / confirm the branch
   `feature/NNNN-slug`.
2. Get the diff: `git diff main...HEAD` and `git log main..HEAD --stat`.
3. Run the [rubric](ARCHITECT.md#rubric):
   - **Scope** — every change traces to a plan step; flag anything outside Scope.
   - **Correctness** — logic, edge cases, error paths; backend ↔ `api.ts` types agree.
   - **Invariants** — walk the `ARCHITECT.md` project-invariants list; explicit check on
     each one the diff comes near.
   - **AGENTS.md conformance** — both stacks.
   - **Tests** — the plan's Tests section is implemented with real assertions.
   - **Security** — new routes behind auth middleware; new outbound URLs through
     `url_policy`; no secrets in logs/responses.
   - **Build hygiene** — if `frontend/` changed, `app/static/ui/` was rebuilt and committed.
4. Run the Definition-of-done checks: `./.venv/bin/pytest`, and if `frontend/` changed,
   `cd frontend && npm run build && npm run lint`. Report actual output.
5. Verdict, one of:
   - **Accept** — summarize; tell the user they can merge `feature/NNNN-slug` to `main`
     (which auto-deploys) and mark the plan `Status: shipped`.
   - **Iterate** — write numbered review notes referencing plan steps / rubric items;
     tell the user to run `/handoff NNNN --resume`.
   - **Escalate** — the plan itself is wrong; explain why and send them back to `/plan`
     to revise it, then `/handoff NNNN --fresh`.
6. For architectural or security-sensitive changes, suggest an independent
   `/codex:review` or `/codex:adversarial-review` pass before Accept, but keep the final
   verdict yours.
