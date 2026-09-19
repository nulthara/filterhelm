---
description: Architect phase — turn a request into an approved plan file under docs/plans/
argument-hint: "<what to build or change>"
allowed-tools: Read, Glob, Grep, Write, Edit, Bash(git:*), Bash(ls:*), AskUserQuestion
---

You are the **Architect** as defined in `ARCHITECT.md`. Do Phase 1 only — do not write
implementation code.

Request:
$ARGUMENTS

Steps:

1. Read `ARCHITECT.md` and `AGENTS.md` if not already in context.
2. Clarify the request with the user until the goal and its boundaries are unambiguous.
   Use `AskUserQuestion` for real forks; don't interrogate.
3. Explore the codebase. Identify every file the change touches and every contract it
   depends on (backend route ↔ `frontend/src/api.ts` shapes, config keys, DB access,
   the Pi-hole client).
4. Determine the next plan number: look at `docs/plans/` for the highest `NNNN` and add 1,
   zero-padded to 4 digits. Pick a short kebab-case slug.
5. Copy `docs/plans/_TEMPLATE.md` to `docs/plans/NNNN-slug.md` and fill every section per
   the "Plan file contract" in `ARCHITECT.md`. Be concrete: name functions, endpoints,
   components, props. Write out API/config/DB contract changes exactly, both sides.
6. In "Invariants at risk", walk the `ARCHITECT.md` project-invariants list and state how
   the plan keeps each nearby one intact.
7. Present the plan path and a summary. Ask the user to approve it. **Stop there** — do not
   hand off to Codex. When they approve, set the plan's `Status:` to `approved` and tell
   them to run `/handoff NNNN`.
