# 0019 — /verify posts its verdict to the pull request

Status: in-progress           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0019-verify-posts-pr-review
Author: Claude (Architect)
Date: 2026-09-20

## Problem

The review phase produces a real artifact — a rubric pass, invariant walk, Definition-of-done
output, and an Accept/Iterate/Escalate verdict — and then throws it away. It lives in the chat
transcript and nowhere else. The merge happens separately in the Gitea web UI, which knows
nothing about it, so the pull request carries no record of whether the change was reviewed,
against what, or what was found. Plan 0016 is the case in point: an adversarial pass flipped an
Accept into an Escalate over a defect that would have shipped, and the only durable trace of
that is a plan file revision. Nothing on PR #34 or any other PR says a review happened at all.

## Scope

In:
- A new final step in `/verify` that posts the verdict and rubric summary to the pull request
  for `feature/NNNN-slug` as a Gitea review.
- Verdict-to-review-event mapping, with a fallback path for the self-review restriction.
- Resolving the PR by listing open PRs and matching the head branch, since the API has no
  head-branch filter.
- Offering to open a PR when none exists, rather than failing.
- Configuration through generically-named environment variables and runtime derivation from
  the git remote, so no tracked file names the forge host, the account, or the token variable.
- `ARCHITECT.md` Phase 3 documentation for the new step.

Out:
- **GitHub PRs.** GitHub is the public mirror; merges and deploys happen on the self-hosted
  forge. Opening PRs on GitHub would move merges off the deploy path, which is the divergence
  that already bit plan 0018.
- **Inline line-level comments.** `CreatePullReviewOptions` accepts a `comments` array, so this
  is possible later, but anchoring findings to diff positions is materially more work than one
  summary body and was explicitly deferred.
- **Blocking the merge.** Nothing enforces the gate. A review is a record, not a branch
  protection rule; adding required-approval rules is a forge setting, not a plan step.
- **Changing how the review is produced.** The rubric, the invariant walk, and the verdict
  logic are unchanged. This plan only gives the output a home.
- **Automating the post-merge push to the mirror.** Related irritation, separate change.
- **Any application behaviour.** No route, model, client, or frontend file is touched.

## Approach

**The hygiene guard sets the shape of this change.** `.claude/commands/verify.md` is tracked,
so `tests/test_repo_hygiene.py` scans it. Appending a realistic integration line to that file
and running the guard produced four findings at once — the forge host, the personal domain, the
internal DNS tier, and the stored token name. That is the guard working correctly, and it means
the integration may not spell any of them in a tracked file. Two consequences follow, and they
are the reason this is a plan rather than a five-minute edit.

**Nothing identifying is written down; it is all resolved at runtime.**

| Value | Source | Why not hardcode |
| --- | --- | --- |
| API base URL | `PIHOLE_MANAGER_FORGE_API` | Host and port are denylisted |
| API token | `PIHOLE_MANAGER_FORGE_TOKEN` | The current variable name is denylisted |
| Owner and repo | parsed from `git remote get-url origin` | The account name is denylisted |

Deriving owner/repo from the remote is better than a third variable. It is already correct in
every clone, it cannot drift from the repo it describes, and it keeps the configuration surface
to two variables. The existing forge token variable is renamed rather than the denylist
weakened — removing that pattern to satisfy tooling would trade a privacy guarantee for
convenience, which is the wrong direction. Its current name is in the denylist and so is not
written here; Step 8 is what changes it.

**Verified API contracts, not remembered ones.** All shapes below were read from the forge's own
`swagger.v1.json` (version 1.27.2), not assumed. One assumption did not survive: there is **no
`head_branch` query parameter** on the pull list endpoint. Only `base_branch`, `state`, `sort`,
`milestone`, `labels`, `poster`, `page`, and `limit` exist. The PR must therefore be found by
listing open PRs and matching `head.ref` client-side.

**The self-review restriction is the main risk.** A forge typically refuses to let an author
approve their own pull request, and this is a single-maintainer repo, so every review is a
self-review. `POST .../reviews` documents `422` as a response, which is the likely result of an
`APPROVED` event on one's own PR. This cannot be tested from here — authenticated calls are
blocked in this environment — so the design does not depend on the answer. The step attempts
the mapped event, and on `422` retries once as `COMMENT` with the verdict stated in the body
text, then reports which path it took. The record lands either way; only the badge differs.

**Verdict mapping.**

| Verdict | Event | On 422 |
| --- | --- | --- |
| Accept | `APPROVED` | retry as `COMMENT`, body still leads with the verdict |
| Iterate | `REQUEST_CHANGES` | retry as `COMMENT` |
| Escalate | `REQUEST_CHANGES` | retry as `COMMENT` |

Escalate maps to `REQUEST_CHANGES` rather than `COMMENT` because the branch must not merge —
the plan itself was wrong, which is a stronger objection than Iterate, not a weaker one.

**Failure is never silent and never fatal.** A missing variable, an unreachable forge, a `401`,
or no matching PR ends with a printed notice and the verdict still rendered in full in the
terminal. The review is the valuable output; posting it is a convenience. A `/verify` run that
cannot reach the forge must still be a complete review, so the posting step reports what it
could not do and exits cleanly rather than failing the command.

**Rejected alternatives.**
- **A `tea` CLI dependency.** Not installed, another tool to keep current, and it wraps the
  same three endpoints this needs. `curl` plus the two variables is smaller.
- **Storing config in `.claude/settings.json`.** Tracked, therefore scanned, therefore it would
  have to hold the same forbidden strings. The environment is the right place for both values.
- **Posting as a plain issue comment** (`/issues/{index}/comments`). Simpler, but it does not
  appear in the PR's review timeline and carries no verdict state at all.
- **Claude implementing without a plan.** It touches tracked tooling, introduces a credential
  path, and interacts with the hygiene guard. ARCHITECT.md's skip criteria require *all* of one
  or two files, no new dependency, no config change, and no invariant nearby. This meets none
  of the last three.

**Implementer deviation, agreed with the user on 2026-09-20.** Claude implements this plan
rather than Codex. Phase 2 exists to keep the architect and implementer roles separate for
application code; this change is a slash-command prompt and a process document, where the
handoff contract has no code for Codex to write. The user reviews the diff. This is a
deliberate, recorded deviation from ARCHITECT.md Phase 2, not an accident.

## Files

- `.claude/commands/verify.md` — add the posting step, extend `allowed-tools` with a narrow
  `curl` grant. Tracked, so it stays free of every denylisted literal.
- `ARCHITECT.md` — document the new step under Phase 3 and record the two environment
  variables under Branch and deploy discipline.
- `docs/plans/0019-verify-posts-pr-review.md` — this file.

No file under `app/`, `frontend/`, or `tests/` changes.

## Steps

1. **`.claude/commands/verify.md` — `allowed-tools`.** Add `Bash(curl:*)` to the existing list.
   The other entries stay as they are.

2. **`.claude/commands/verify.md` — configuration preamble.** Before the new step, state that
   posting requires `PIHOLE_MANAGER_FORGE_API` (base URL, scheme and port included, no trailing
   slash) and `PIHOLE_MANAGER_FORGE_TOKEN`, and that owner and repo come from
   `git remote get-url origin`. Say that when either variable is unset the step is skipped with
   a one-line notice and the verdict is still reported in full.

3. **`.claude/commands/verify.md` — Step 7, resolve the PR.** After the verdict:
   - Parse owner and repo from the `origin` remote URL. Handle both the SSH alias form
     (`<user>@<host-alias>:<owner>/<repo>.git`) and the `ssh://` form with an explicit port.
     Strip the trailing `.git`.
   - `GET {api}/repos/{owner}/{repo}/pulls?state=open&limit=50`
   - Select the entry whose `head.ref` equals the branch under review. Keep its `number` and
     `html_url`.
   - Zero matches is not an error. Say so, offer to open one (Step 5), and continue.
   - More than one match cannot happen for one head branch, but if it does, report and skip
     rather than guessing.

4. **`.claude/commands/verify.md` — Step 7, post the review.**
   - `POST {api}/repos/{owner}/{repo}/pulls/{number}/reviews` with the body in Contracts.
   - Body text is the rubric summary already produced: verdict, the rubric table, the
     Definition-of-done output, and any review notes. Lead with the verdict word so it is
     legible in a collapsed timeline.
   - On `422`, retry once with `event` set to `COMMENT`, keeping the same body. Report which
     event actually posted.
   - On `401` or `403`, report that the token lacks `repository` write scope and stop.
   - On success, print the `html_url` so the user can open the PR.

5. **`.claude/commands/verify.md` — opening a PR when none exists.** Offer, do not assume. On
   agreement, `POST {api}/repos/{owner}/{repo}/pulls` with `head` set to the feature branch,
   `base` to `main`, `title` from the plan's title line, and `body` from the plan's Problem
   paragraph plus a link to the plan file. `201` returns the created PR; then post the review
   to it as in Step 4.

6. **`.claude/commands/verify.md` — verdict wording.** The Accept branch currently tells the
   user to merge and mark the plan shipped. Add that the mirror needs `main` pushed to it after
   the merge, since merging on the forge does not update the public remote. Plan 0018 shipped
   four commits that never reached the mirror for exactly this reason.

7. **`ARCHITECT.md` — Phase 3.** Add the posting step to the phase description and note that a
   review is a record rather than a gate. Under Branch and deploy discipline, name the two
   environment variables and say they are set outside the repo because the hygiene guard
   forbids the forge's identifying details in tracked files.

8. **Rename the environment variable.** The user sets `PIHOLE_MANAGER_FORGE_TOKEN` in their
   fish configuration and removes the old one. This is a user action outside the repo; the plan
   records it so the reviewer knows the step exists. Nothing tracked references either name.

9. **Run the guard.** `./.venv/bin/pytest tests/test_repo_hygiene.py` must be green with the
   edited `verify.md` and `ARCHITECT.md` staged. This is the check that the whole approach
   exists to satisfy.

## Contracts

No application contract changes. `frontend/src/api.ts`, the Pi-hole client, config keys, and
every route are untouched. The contracts below are with the forge's API, read from its
`swagger.v1.json` at version 1.27.2.

**Configuration (new, environment only, never tracked):**

| Variable | Example shape | Required |
| --- | --- | --- |
| `PIHOLE_MANAGER_FORGE_API` | `http://<host>:<port>/api/v1` | yes, else step skips |
| `PIHOLE_MANAGER_FORGE_TOKEN` | opaque token string | yes, else step skips |

**Find the PR — request:**

```
GET {api}/repos/{owner}/{repo}/pulls?state=open&limit=50
Authorization: token {PIHOLE_MANAGER_FORGE_TOKEN}
```

Response `200` is an array of `PullRequest`. Fields this plan relies on, all confirmed present:

| Field | Type | Use |
| --- | --- | --- |
| `number` | integer | path parameter for the review POST |
| `head.ref` | string | matched against `feature/NNNN-slug` |
| `html_url` | string | printed for the user |
| `state` | string | sanity check that it is `open` |

There is no `head_branch` query parameter. Matching is client-side.

**Post the review — request:**

```
POST {api}/repos/{owner}/{repo}/pulls/{number}/reviews
Authorization: token {PIHOLE_MANAGER_FORGE_TOKEN}
Content-Type: application/json

{"body": "<verdict and rubric summary>", "event": "APPROVED"}
```

`event` accepts exactly `APPROVED`, `PENDING`, `COMMENT`, `REQUEST_CHANGES`, `REQUEST_REVIEW`.
This plan uses the first, third, and fourth. Documented responses are `200`, `404`, `422`; no
field in `CreatePullReviewOptions` is required, so a body-only `COMMENT` is valid.

**Open a PR — request:**

```
POST {api}/repos/{owner}/{repo}/pulls
{"head": "feature/NNNN-slug", "base": "main", "title": "...", "body": "..."}
```

Response `201` on success. `409` and `422` indicate an existing or invalid PR and are reported,
not retried.

## Tests

No automated test is added, and the reason is not laziness. `AGENTS.md` requires a test in
`tests/` for new *backend behaviour*; this plan adds none — no Python, no TypeScript, no route.
The change is a prompt file and a process document. A test asserting the contents of a
slash-command prompt would assert the prompt's wording, which is not behaviour and would break
on every rewording.

- `tests/test_repo_hygiene.py::test_tracked_files_use_only_approved_addresses_and_hosts` and
  `::test_tracked_files_carry_no_known_private_tokens` — **the real test for this change.** Both
  must be green with the edited `verify.md` and `ARCHITECT.md` staged. Appending a hardcoded
  integration line to `verify.md` currently produces four findings; the finished change must
  produce none. Verify by making the tracked edits and running the guard, not by reading.
- Every other test unchanged and green. Nothing under `app/`, `frontend/`, or `tests/` is
  touched, so any failure elsewhere means the diff escaped its Files list.
- **Manual, after implementation.** Run `/verify` against a branch with an open PR and confirm
  the review appears on it. This is the only way to exercise the API path, because authenticated
  calls cannot be made from the implementing environment.
- **Manual, negative.** Unset `PIHOLE_MANAGER_FORGE_TOKEN` and confirm `/verify` still prints a
  complete review and skips posting with a notice rather than failing.

## Invariants at risk

Walking the `ARCHITECT.md` list. This change touches no application code, so most are not
engaged — stated explicitly so the reviewer can confirm rather than assume.

1. **One instance mutated at a time** — no mutation code exists here. N/A.
2. **Diff before write** — N/A.
3. **Gravity gated** — N/A.
4. **No background scheduler** — nothing runs on its own. The posting step runs only inside a
   `/verify` invocation the user starts. Intact.
5. **FastAPI owns credentials and Pi-hole access** — unaffected. The forge token is a developer
   workflow credential read from the environment by a slash command. It never enters `app/`,
   never reaches the browser, and is not a Pi-hole credential. No file under `app/` changes.
6. **All outbound through `url_policy`** — **the one worth arguing.** This plan adds outbound
   HTTP via `curl`. That invariant governs requests *the application* makes, because its purpose
   is SSRF hardening of a credential-holding proxy that takes URLs from users. The new requests
   come from a developer tool, target an operator-supplied environment variable rather than user
   input, and run on the developer's machine. `app/url_policy.py` is not modified and no
   application code path gains a new destination. Intact, and the distinction is recorded here
   so a future reviewer does not have to relitigate it.
7. **Every route behind auth middleware** — no route added, removed, or moved. Intact.
8. **Compiled frontend committed** — `frontend/` is untouched, so `app/static/ui/` must not
   change. A diff entry under either directory means something went wrong. Intact.
9. **`main` is production** — work on `feature/0019-verify-posts-pr-review`, merge after an
   Accept. Step 6 additionally strengthens this by reminding the user to push `main` to the
   mirror after merging, which is currently a silent manual step. Intact.

A tenth constraint, not on the list but established by plan 0016 and binding here: **no tracked
file names the forge host, the account, the internal DNS tier, or the token variable.** Step 9
enforces it with the guard.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including both hygiene guard tests, with the edited
      `.claude/commands/verify.md` and `ARCHITECT.md` staged.
- [ ] No tracked file contains the forge hostname, the personal domain, the internal DNS tier,
      the SSH port, or the old token variable name. The denylist test is this check; do not
      write a grep that spells them.
- [ ] `.claude/commands/verify.md` `allowed-tools` includes `Bash(curl:*)` and still includes
      every tool it had before.
- [ ] With both environment variables set and a branch that has an open PR, `/verify` posts a
      review that appears in the PR's review timeline, and prints its `html_url`.
- [ ] The posted body contains the verdict word, the rubric summary, and the pytest output.
- [ ] With `PIHOLE_MANAGER_FORGE_TOKEN` unset, `/verify` prints a complete review and a
      one-line skip notice, and exits without error.
- [ ] With a deliberately wrong token, `/verify` reports the scope problem and does not claim
      to have posted anything.
- [ ] On a branch with no open PR, `/verify` offers to open one and does not post silently or
      fail.
- [ ] If the forge refuses `APPROVED` on a self-review, the step retries as `COMMENT` and says
      which event it used. The verdict is still legible in the posted body.
- [ ] `git diff --stat` shows exactly three files: the two tracked edits and this plan. No
      entry under `app/`, `frontend/`, `tests/`, or `app/static/ui/`.
- [ ] `ARCHITECT.md` Phase 3 describes the step, and says a review is a record rather than a
      merge gate.
