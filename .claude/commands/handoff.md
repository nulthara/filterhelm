---
description: Implement phase — hand an approved plan to Codex on a feature branch
argument-hint: "<NNNN> [--resume|--fresh]"
allowed-tools: Read, Glob, Bash(git:*), Bash(ls:*), Agent
---

You are running Phase 2 of `ARCHITECT.md`. Hand the plan to Codex; do not implement it
yourself and do not review afterwards.

Arguments:
$ARGUMENTS

Steps:

1. Parse the plan number `NNNN` from the arguments. Resolve the plan file with
   `ls docs/plans/NNNN-*.md`. If none matches, stop and tell the user to run `/plan` first.
2. Read the plan file. If its `Status:` is not `approved` / `in-progress` / `in-review`,
   stop and ask the user to approve it first.
3. Note the branch name from the plan (`feature/NNNN-slug`). You will need to create this branch
   as codex agents are set to have a read-only git configuration. 
4. Routing:
   - `--resume` in the args → pass `--resume` through (continue the same Codex thread).
   - `--fresh` in the args → pass `--fresh` through.
   - Neither, and the user is clearly iterating on review notes → default to `--resume`.
   - Neither, and this is the first handoff for the plan → fresh.
5. Invoke the `codex:codex-rescue` subagent via the `Agent` tool
   (`subagent_type: "codex:codex-rescue"`), **in the background**, with this prompt
   (fill in the real path, branch, and routing flag):

   > --background --write [--resume|--fresh]
   > Implement `docs/plans/NNNN-slug.md` exactly. Create and work on branch
   > `feature/NNNN-slug` from `main`. Follow `AGENTS.md` and `ARCHITECT.md`. Stay strictly
   > inside the plan's Scope — do not refactor adjacent code or expand the change. If the
   > plan is wrong, incomplete, or forces a project-invariant violation, stop and explain
   > instead of guessing. When done: run every check in ARCHITECT.md "Definition of done"
   > (`./.venv/bin/pytest`; if `frontend/` changed, `npm run build` + `npm run lint` and
   > commit the regenerated `app/static/ui/`), commit on the feature branch, and report
   > what you changed plus any deviations from the plan.

6. Set the plan's `Status:` to `in-progress`.
7. Return the subagent's output verbatim. Tell the user to run `/verify NNNN` once Codex
   reports done, and `/handoff NNNN --resume` to send back review notes.
