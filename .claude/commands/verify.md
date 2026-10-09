---
description: Review phase — check the feature branch against the plan and invariants
argument-hint: "<NNNN>"
allowed-tools: Read, Glob, Grep, Bash(git:*), Bash(./.venv/bin/pytest:*), Bash(ls:*), Bash(cd frontend && npm run build:*), Bash(cd frontend && npm run lint:*), Bash(cd frontend && npm test:*), Bash(curl:*)
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
   `cd frontend && npm run build && npm run lint && npm test`. Report actual output.
5. Verdict, one of:
   - **Accept** — summarize; tell the user they can merge `feature/NNNN-slug` to `main`
     (which auto-deploys) and mark the plan `Status: shipped`. After merging on the forge,
     push `main` to the public mirror too; the forge merge does not update that remote.
   - **Iterate** — write numbered review notes referencing plan steps / rubric items;
     tell the user to run `/handoff NNNN --resume`.
   - **Escalate** — the plan itself is wrong; explain why and send them back to `/plan`
     to revise it, then `/handoff NNNN --fresh`.
6. For architectural or security-sensitive changes, suggest an independent
   `/codex:review` or `/codex:adversarial-review` pass before Accept, but keep the final
   verdict yours.

Posting configuration (set outside the repo): `PIHOLE_MANAGER_FORGE_API` is the API base
URL including scheme and port, with no trailing slash; `PIHOLE_MANAGER_FORGE_TOKEN` is
the API token. Owner and repo come from `git remote get-url origin`. If either variable
is unset or empty, skip posting with a one-line notice and still report the verdict and
complete review in full. Never print the token, expand it into a literal in tool calls,
or write identifying forge details into tracked files.

7. Post the final verdict and existing review summary to the forge PR. This is a review
   record, not a merge gate; do not change how the rubric or verdict is produced.
   - Use `curl` with `Authorization: token {PIHOLE_MANAGER_FORGE_TOKEN}` (read the token
     from the environment at execution time). Capture the HTTP status and response body
     separately; do not use verbose/trace output or expose request headers. For JSON
     POSTs, use `Content-Type: application/json` and correctly JSON-escape body text.
   - Resolve owner/repo from the origin URL. For `<user>@<host-alias>:<owner>/<repo>.git`,
     take the path after the colon. For `ssh://<user>@<host-alias>:<port>/<owner>/<repo>.git`,
     take the path after the authority, excluding the port. Strip the trailing `.git`
     and validate that the path provides owner and repo. If parsing fails, report and
     skip posting rather than guessing. Do not hardcode host, port, account, or repo.
   - `GET {api}/repos/{owner}/{repo}/pulls?state=open&limit=50`. On `200`, select entries
     whose `head.ref` equals the feature branch under review and whose `state` is `open`.
     Match client-side: the endpoint has no `head_branch` filter. Keep the matched PR's
     `number` and `html_url`. If more than one matches, report ambiguity and skip.
   - If none matches, say that no open PR was found and offer to open one. Only on user
     agreement, `POST {api}/repos/{owner}/{repo}/pulls` with
     `{"head": "<feature branch>", "base": "main", "title": "<plan title line>",
     "body": "<plan Problem paragraph plus a link to the plan file>"}`. Use a relative
     Markdown link to the plan file from the PR description. On `201`, keep the created
     PR's `number` and `html_url` and continue to post the review. On `409` or `422`,
     report the existing/invalid PR response without retrying. If the user declines,
     skip posting and continue with the complete terminal review.
   - Lead the review body with the verdict word (Accept, Iterate, or Escalate), followed
     by the rubric summary table, actual Definition-of-done output (including pytest),
     and any review notes already produced. Do not add inline line-level comments.
   - Map Accept to `APPROVED`; map Iterate and Escalate to `REQUEST_CHANGES`.
     `POST {api}/repos/{owner}/{repo}/pulls/{number}/reviews` with
     `{"body": "<verdict and rubric summary>", "event": "<mapped event>"}`.
     On `422`, retry exactly once with `event: COMMENT` and the identical body, to
     accommodate self-review restrictions. Do not retry a failed COMMENT.
   - On a successful review POST (`200`), report which event actually posted (including
     whether the COMMENT fallback was used) and print the PR's `html_url`.
   - On `401` or `403` from any endpoint, report the authentication/scope problem and
     that the token needs `repository` write scope; stop the posting step. Missing
     configuration, an unreachable forge, malformed responses, or any other API failure
     must produce a notice and leave the complete verdict visible in the terminal.
     Posting failures never fail `/verify` and must never be described as success.
