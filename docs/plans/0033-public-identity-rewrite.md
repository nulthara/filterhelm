# 0033 — Rewrite history for the public identity

Status: shipped           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0033-public-identity-rewrite
Author: Claude (architect)
Date: 2026-10-13

## Problem

FilterHelm moves to a new GitHub organization repo, `nulthara/filterhelm`, starting
private. `main` would publish the owner's personal identity on the first push. Every
commit's author and committer is one of four personal identities. Nine forge merge
commits carry a `Reviewed-on:` trailer that names the private forge host. Old blobs and
the tree hold the owner's old handle and first name. The public identity is the
organization `nulthara` and the person `Kyrnveil`. Forge merges would add new trailers
and personal identities after any cleanup, so the merge style must change too.

## Scope

In:
- A one-time `git filter-repo` pass over `main` (operator runbook, Part A):
  - set every author and committer to `Kyrnveil <340844984+Kyrnveil@users.noreply.github.com>`
  - drop every `Reviewed-on:` line from commit messages
  - replace the old handle with `nulthara`, and the first name with `Kyrnveil`
    (lowercase `kyrnveil`), in every blob
- Force-push the rewritten `main` to the forge once and adopt it locally.
- Switch the forge repo to fast-forward-only merges.
- Set this clone's git identity, remotes, and delete stale branches and refs.
- A history guard and process-doc updates on the feature branch (Codex, Part B).
- Push `main`, and only `main`, to `git@github.com:nulthara/filterhelm.git` (Part C).

Out:
- The product's pre-rebrand name in history. Plan 0030 accepted it.
- `Co-Authored-By: Claude ...` trailers. They stay.
- The forge archive branch `archive/pre-public-history` and forge PR refs. They stay on
  the private forge and never go to GitHub.
- The old GitHub repo the `github` remote points at today. Deleting it or keeping it
  private is the user's call outside git.
- `docs/design.md` §14's licensing TODO, which is stale but unrelated.
- Any code change.

## Approach

**Rewrite once and adopt it everywhere.** The forge and GitHub then share one history,
so later pushes to GitHub are plain fast-forwards. A GitHub-only rewrite was rejected,
because the two histories would never share commits again and every sync would need
another rewrite. A squashed single-commit export was rejected for the same reason.

**The rewrite makes the tree edits too.** `--replace-text` rewrites every blob version,
the tip included. That covers `LICENSE`, `docs/design.md`, ten plan files and
`tests/test_auth.py` in one pass, so no separate tree-edit branch has to land first.
The uncommitted `LICENSE` edit is discarded before the rewrite, which reproduces it.

**Callbacks set identity without naming the old identities.** `--name-callback` and
`--email-callback` return Kyrnveil for every author, committer and tagger, so no
private address has to appear in the plan or a mailmap. `main` has no tags. One commit
carries a GitHub web-UI signature. filter-repo drops it, which is expected.

**Private literals stay out of tracked files.** The replace rules live in scratchpad
files built at run time. The tracked guard reads the untracked `.private-denylist.toml`,
the same way `test_tracked_files_carry_no_known_private_tokens` does today.

**Fast-forward-only merges keep forge identity out of history.** A fast-forward creates
no commit, so neither the forge account's identity nor a `Reviewed-on:` trailer enters
`main`. The forge supports it, and `allow_fast_forward_only_merge` is already `true`.
Branches are cut from `origin/main` and land one at a time, so they stay
fast-forwardable. A merge-message template was rejected, because a merge commit would
still carry the forge account's identity.

**Rejected:**
- Rewriting with a `.mailmap` file. It would have to list the private addresses.
- Leaving history as is. The user wants no personal identity in the published repo.

## Files

Part A (operator) rewrites these files through `--replace-text`. Codex does not edit them.

- `LICENSE` — handle → `nulthara`
- `docs/design.md` — first name → `Kyrnveil` (lines 58, 323)
- `docs/plans/0001`, `0002`, `0003`, `0004`, `0005`, `0006`, `0007`, `0009`, `0013`,
  `0016` — first name → `Kyrnveil`
- `tests/test_auth.py` — test username → `kyrnveil` (lines 125, 132, 133, 239)

Part B (Codex):

- `tests/test_repo_hygiene.py` — shared denylist loader, two history tests.
- `ARCHITECT.md` — fast-forward merge rule and the pre-push check for GitHub.
- `.claude/commands/verify.md` — Accept step names the fast-forward merge and the
  pre-push check.
- `README.md` — delete the "Git history still carries…" TODO bullet.
- `docs/design.md` — §14 privacy-scrub bullet marked done.
- `docs/plans/0033-public-identity-rewrite.md` — this plan.

## Steps

### Part A — operator runbook (Claude runs it with the user present)

`$S` is the session scratchpad. `OLD` is the current `main` SHA, recorded in A1.

A0. **Prerequisites.**
  - The user installs filter-repo with `sudo pacman -S git-filter-repo`.
  - The user creates `nulthara/filterhelm` as a private, empty repo with no README and
    no license. `git ls-remote git@github.com:nulthara/filterhelm.git` must exit 0 from
    this machine. If the default key's account lacks access, the user adds an SSH host
    alias for the Kyrnveil account, and the `github` remote uses that alias.
  - The user adds two patterns to `.private-denylist.toml`: `"personal handle"` (the old
    handle) and `"personal name"` (the first name, word-bounded). Confirm that
    `git show main:LICENSE` and `git show main:docs/design.md` each produce a hit.
  - The forge has no open PRs.
  - `git restore LICENSE` makes the working tree clean apart from this plan file.

A1. **Back up.**
  - `OLD=$(git rev-parse main)`
  - `git bundle create $S/pre-0033.bundle --all`
  - `git push origin main:refs/heads/archive/pre-identity-rewrite`

A2. **Rewrite in a fresh clone.**
  - `git clone --no-local --single-branch --branch main . $S/rewrite`
  - `$S/blob-rules.txt` holds three literal rules: old handle `==>nulthara`, capitalized
    first name `==>Kyrnveil`, lowercase first name `==>kyrnveil`.
  - `$S/message-rules.txt` holds `regex:\n+Reviewed-on: [^\n]*==>` and the literal rule
    old handle `==>nulthara` as a backstop.
  - Run filter-repo in `$S/rewrite`:
    ```bash
    git filter-repo --force \
      --name-callback 'return b"Kyrnveil"' \
      --email-callback 'return b"340844984+Kyrnveil@users.noreply.github.com"' \
      --replace-text $S/blob-rules.txt \
      --replace-message $S/message-rules.txt
    ```

A3. **Verify the rewrite before any push.** Each check runs in `$S/rewrite`.
  - `git rev-list --count HEAD` equals `git rev-list --count $OLD` in the main clone.
  - `git log --format='%an <%ae>%n%cn <%ce>' | sort -u` prints exactly one line, the
    Kyrnveil identity.
  - `git log --format=%B | grep -c '^Reviewed-on:'` prints `0`.
  - The `Co-Authored-By:` count is unchanged from `$OLD`.
  - `git diff $OLD HEAD --stat`, after fetching `$OLD` from the main clone, lists only
    the Part A files, and `--word-diff` shows only the three substitutions.
  - A one-pass scan of every blob reachable from `HEAD` and every commit message with
    the denylist patterns finds nothing. A case-insensitive search for the handle and
    the first name also finds nothing.
  - Copy `.private-denylist.toml` into `$S/rewrite`, then run
    `FILTERHELM_REQUIRE_DENYLIST=1 <main clone>/.venv/bin/pytest -q` there. It must be
    green.

A4. **Force-push to the forge.**
  - In `$S/rewrite`, run
    `git remote add origin git@gitea:Kyrnveil/filterhelm.git`
  - Then run
    `git push --force-with-lease=main:$OLD origin HEAD:main`
  - Watch the deploy run go green. The workflow runs `git fetch origin main` with the
    default `+` refspec, then `git reset --hard origin/main`, so it accepts the forced
    update.
  - If the fetch on the host fails because of the old repo path, the user runs
    `git -C /opt/filterhelm remote set-url origin` with the new path as the runner
    user, then re-runs the workflow.

A5. **Forge settings and cleanup.**
  - PATCH `/repos/Kyrnveil/filterhelm` with these values:
    - `allow_merge_commits: false`
    - `allow_squash_merge: false`
    - `allow_rebase: false`
    - `allow_rebase_explicit: false`
    - `allow_fast_forward_only_merge: true`
    - `default_merge_style: "fast-forward-only"`
  - Read the repo back and confirm each value.
  - Delete every forge branch except `main`, `archive/pre-public-history` and
    `archive/pre-identity-rewrite`.

A6. **Adopt locally.** Run these in the main clone.
  - `git remote set-url origin git@gitea:Kyrnveil/filterhelm.git`
  - `git remote set-url github git@github.com:nulthara/filterhelm.git`, or the alias
    from A0
  - `git fetch origin --prune`
  - `git switch main && git reset --hard origin/main`
  - Delete every local `feature/*` branch and every `refs/codex/*` ref.
  - Delete every `refs/remotes/github/*` ref, since those refs point at the old repo.
  - `git config user.name Kyrnveil`
  - `git config user.email 340844984+Kyrnveil@users.noreply.github.com`

### Part B — Codex, on `feature/0033-public-identity-rewrite` cut from the rewritten `main`

B1. **`tests/test_repo_hygiene.py`.**
  - Extract `_denylist_patterns() -> dict[str, re.Pattern[str]]` from
    `test_tracked_files_carry_no_known_private_tokens`. It keeps the current skip and
    fail behaviour around `FILTERHELM_REQUIRE_DENYLIST`, and the existing test calls it.
  - Add `_commit_metadata() -> list[tuple[str, str]]`. It returns `(short_sha, text)`
    per commit reachable from `HEAD`, where `text` joins author name, author email,
    committer name, committer email and the full message. Use one `git log` call with a
    NUL record separator.
  - Add `test_commit_history_carries_no_known_private_tokens`. It applies
    `_denylist_patterns()` to `_commit_metadata()` and reports `"<sha>: <label>: <match>"`.
  - Add `test_commit_history_has_no_forge_review_trailers`. It needs no denylist and
    fails on any message line starting `Reviewed-on:`.
  - No private identifier appears in the test file.

B2. **`ARCHITECT.md` § "Branch and deploy discipline".**
  - Change "One plan → one branch → one merge to `main`" to say the branch lands on
    `main` by a fast-forward-only merge on the forge. If `main` moved, rebase the branch
    on `origin/main` first.
  - Add a bullet that pushes go to GitHub only from `main`, with `git push github main`,
    after `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` passes.

B3. **`.claude/commands/verify.md`, step 5 Accept.** Say "fast-forward merge"
  in place of "merge". Replace the public-mirror sentence with the same pre-push check
  as B2.

B4. **`README.md`.** Delete the two-line TODO bullet that starts "Git history still
  carries internal addresses".

B5. **`docs/design.md` §14.** Rewrite the privacy-scrub bullet as "Done — privacy
  scrub". Plan 0016 scrubbed the tree. Plan 0033 rewrote history for the public
  identity. `tests/test_repo_hygiene.py` guards both.

### Part C — publish (operator, after Part B lands)

C1. In the main clone, run `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest`.
C2. `git push github main`. Do not use `--all`, `--mirror` or `--tags`.
C3. `git ls-remote github` lists only `HEAD` and `refs/heads/main`.

## Contracts

N/A. Tests, docs and git metadata only. No API, config key, env var or DB change.

## Tests

- `tests/test_repo_hygiene.py::test_commit_history_carries_no_known_private_tokens` —
  no commit's identity or message matches a denylist pattern. It skips without the
  denylist, and fails without it under `FILTERHELM_REQUIRE_DENYLIST=1`.
- `tests/test_repo_hygiene.py::test_commit_history_has_no_forge_review_trailers` — no
  commit message holds a `Reviewed-on:` line.
- `tests/test_repo_hygiene.py::test_tracked_files_carry_no_known_private_tokens` —
  unchanged behaviour, now built on `_denylist_patterns()`. With the two new patterns
  it covers the handle and the first name in the tree.
- `tests/test_auth.py` proxy-user tests — still green after the username substitution.

Both history tests would fail on today's `main`. They pass only after Part A, which is
why Part B branches from the rewritten `main`.

## Invariants at risk

1. **One instance mutated at a time** — untouched.
2. **Diff before write** — untouched.
3. **Gravity gating** — untouched.
4. **No background scheduler** — untouched.
5. **Credentials never reach the browser** — untouched.
6. **Outbound requests via `url_policy`** — untouched.
7. **Auth middleware on every route** — untouched. `tests/test_auth.py` changes only a
   test username.
8. **Compiled frontend committed** — `frontend/` and `app/static/ui/` are unchanged.
   `--replace-text` finds no match in them, as A3's diff confirms.
9. **`main` is production** — this plan breaks the rule once, deliberately. A4
   force-pushes `main` outside a feature branch. The rewritten tip differs from `$OLD`
   only in LICENSE, docs and one test file, so the deploy builds the same app.
   `--force-with-lease` guards against a concurrent push, the A1 archive branch and
   bundle allow a full restore, and A3 gates the push.

## Acceptance criteria

- [ ] `git log main --format='%an <%ae>%n%cn <%ce>' | sort -u` prints only the Kyrnveil identity.
- [ ] `git log main --format=%B | grep -c '^Reviewed-on:'` prints `0`.
- [ ] A case-insensitive search of every blob and message reachable from `main` finds neither the old handle nor the first name.
- [ ] The forge deploy after A4 is green.
- [ ] The forge repo reports `default_merge_style: fast-forward-only`, with merge commits, squash and rebase disabled.
- [ ] The local clone has the Kyrnveil `user.name` and `user.email`, no `feature/*` branches and no `refs/codex/*`.
- [ ] Part B's diff touches only the Part B files.
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green on the Part B branch.
- [ ] Part B lands on `main` by fast-forward, with no merge commit.
- [ ] `git ls-remote github` lists only `HEAD` and `refs/heads/main`, at the same SHA as `origin/main`.
