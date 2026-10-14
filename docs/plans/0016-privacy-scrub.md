# 0016 — Privacy scrub of the working tree

Status: shipped            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0016-privacy-scrub
Author: Claude (Architect)
Date: 2026-09-13, revised 2026-09-15

## Revision 2 — 2026-09-15

Escalated during Phase 3 review. Revision 1's guard defeated the plan's own goal, so
this revision replaces the guard and keeps everything else. Steps 1–15 are already
implemented and verified on `feature/0016-privacy-scrub` through `2c3a5ad`; they are
correct and stay. Steps 16–22 are the remaining work.

**What was wrong.** Revision 1 put the denylist in `tests/test_repo_hygiene.py`, a
tracked file, and had the guard skip itself. The tracked tree therefore published the
subnet prefix, the personal domain, the internal DNS tier, the hypervisor, the NAS
host, the macvlan name, the SSH port, the token variable, and every private note
filename — each one labelled with the category it belongs to. The scattered mentions
the scrub removed were a map. The guard was a map with a legend, and an orphan-commit
export carries it intact, which is the publish path the Out list commits to.

Revision 1 reasoned that a denylist must spell out what it forbids. That is true of a
denylist and not true of a guard, so the mechanism was the wrong choice rather than
the execution.

An adversarial review (Codex, 2026-09-15) confirmed this and found four smaller gaps.
Steps 16–22 carry all of them.

## Problem

The repo documents its own design against the real network it runs on. Tracked files name
the two Pi-hole addresses, the router, a personal domain and its internal DNS tier, the
hypervisor, numbered containers, a private Gitea endpoint with its non-standard SSH port,
a reverse-proxy inventory of unrelated services, and half a dozen notes that live only in
a private infrastructure repo. One line even records where a Gitea API token is stored.
README and `docs/design.md` §14 have both carried a "scrub before public release" TODO
since the project started, and the list has only grown. Nothing here is a live secret, but
together it is a map of a private network, and it blocks any public release.

## Scope

In:
- Replace real addresses, the personal domain, the internal DNS tier, container and host
  identifiers, the hypervisor name, and the Gitea SSH endpoint across tracked files.
- Rewrite `docs/design.md` §1's instance table and §11 wholesale, keeping the design
  reasoning and dropping the infrastructure inventory. Delete the sentence recording
  where a Gitea API token is stored.
- Genericize `docs/deploy.md` in place into a self-hosted Gitea Actions runbook that no
  longer names this deployment.
- Repoint the source-comment references in four backend modules from private notes to
  `docs/design.md`.
- Replace the two private-address UI placeholders and rebuild `app/static/ui/`.
- Add `tests/test_repo_hygiene.py`, a denylist scan over tracked files so the scrub is
  enforced rather than done once.
- Update the README and `docs/design.md` §14 TODOs to say the tree is clean and history
  is the remaining step.
- Scrub this plan file. Its mapping table is the densest concentration of private
  identifiers in the tree, so the last step redacts the table's left column and the
  literal examples in Approach and Steps.
- **(Rev 2)** Replace the tracked denylist guard with an allowlist guard that names no
  private identifier, and move the token denylist to a gitignored file.
- **(Rev 2)** Fix the residual disclosure at `docs/plans/0003-…:531`, restore the
  history warning to the README, and scan tracked path names and symlink targets.

Out:
- **Git history.** All 166 commits keep the current values, and so does the author email
  on every one of them. The user chose a tree-only scrub. Publish-time plan, decided
  2026-09-13: after this plan merges, take the scrubbed tree onto an orphan commit, push
  that to a new GitHub repo as the canonical remote, and keep the existing Gitea repo as a
  second push target so the deploy workflow still fires. `git filter-repo` over the
  existing history was considered and rejected as hours of work for a log nobody needs.
- **The generic `192.168.1.x` fixtures**, roughly 200 uses across tests and UI
  placeholder text. They read as textbook defaults and disclose nothing.
- **`app/url_policy.py`'s RFC1918 constants** (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`) and the `tests/test_url_policy.py` cases and README paragraph that
  cover them. Those are the SSRF policy itself, not example data.
- **`lab-pihole`**, the instance name in plans 0009 through 0015. A bare instance name
  carries no topology, and renaming it would churn roughly 40 sites of shipped records.
- **`Kyrnveil`** as plan author and in design narrative. It is authorship, not
  infrastructure, and the same name is on all 166 commits regardless.
- Licensing, distribution, and every other item under "Before a public release".
- Any behaviour change. No route, model, or frontend logic is touched.
- **(Rev 2) Encoding and transformation evasion** — base64, hex, percent-encoding,
  string reversal, runtime concatenation, homoglyphs. Codex demonstrated all of them
  against the guard. They assume an author deliberately smuggling their own subnet
  past their own test, which is the wrong threat model. The risk being managed is an
  accidental paste, not self-exfiltration.
- **(Rev 2) Semantic disclosure.** A sentence like an inventory of unrelated
  self-hosted services names no address or hostname and no pattern-based guard can
  catch it. That stays a human review item.
- **(Rev 2) Index-versus-worktree divergence.** The guard reads `git ls-files` for
  names and the working copy for contents, so staged-but-not-checked-out content is
  invisible to it. A clean CI checkout collapses the two, and the Gitea workflow
  deploys from a fresh clone.

## Approach

**Replace, do not delete.** Every site is documentation of a real decision. Losing the
reasoning to protect the identifiers would be a bad trade, so each replacement keeps the
fact that mattered and drops the name. The replacement is
"Docker container on a macvlan network (not SSH-able)" — the
not-SSH-able part is why the project uses the REST API at all, so it stays.

**One mapping, applied everywhere.** Replacements use RFC 2606 reserved names and the
same `192.168.1.x` range the existing generic fixtures use, so the tree ends up with one
convention instead of two.

| Current | Replacement |
| --- | --- |
| the first real instance address (pihole1) | `192.168.1.2` |
| the second real instance address (pihole2) | `192.168.1.3` |
| the lab instance address (plan 0015) | `192.168.1.2` |
| the DNS host placeholder address | `192.168.1.10` |
| the router address for conditional forwarding | `192.168.1.1` |
| the abbreviated addresses in the §8 mockup | `.1.2` / `.1.18` / `.1.40` / `.1.41` |
| the personal domain and its Gitea subdomain | `example.com`, `git.example.com` |
| the router hostname and internal DNS tier hostnames | `router.example.com`, `switch.example.com`, `nas.example.com` |
| the application hostnames | `filterhelm.example.com` |
| the Gitea SSH port | `2222` |
| the numbered containers | `the deploy host`, `the Gitea host`, `a container`, `the proxy host` |
| the hypervisor host | `the hypervisor host` |
| the NAS host | dropped |
| the macvlan name | dropped |
| the hypervisor host's repo | `a private infrastructure repo` |
| the private note filenames | dropped, or "internal notes" where the sentence needs a referent |
| the old private-repo design-note name and section | `docs/design.md §N` |
| the reverse-proxy product name | `the reverse proxy` |
| the private webhook path | dropped |
| the informal network noun | `home or small-office network` |

**Do not case-fold `NPM`.** `README.md` lines 88, 95, and 96 run `npm install`,
`npm run lint`, and `npm run build`. A case-insensitive replace of the proxy's initials
would destroy the build instructions. Every `NPM` to replace is in `docs/`; no occurrence
in `README.md` is the proxy.

**A test, not a checklist.** A one-time find-and-replace regresses the first time someone
pastes a real address into a plan file. `tests/test_repo_hygiene.py` walks `git ls-files`
and fails with file, line, and matched text for any denylisted pattern, so the scrub is
enforced by the same `./.venv/bin/pytest` run that gates every change. This is the one
file in `tests/` that is not a per-backend-concern module, which `AGENTS.md` otherwise
calls for; the deviation is deliberate because the thing under test is the repo.

**The plan file is in scope for its own guard.** `git ls-files` includes
`docs/plans/0016-privacy-scrub.md`, and this document is the largest single concentration
of private identifiers in the repo — 102 denylist hits at `ee22c40`, more than
`docs/design.md`. It is deliberately **not** added to the test's skip list, because
skipping it would publish the map the scrub exists to remove. Step 15 redacts it instead,
after every other step has used it.

The hits are spread wider than the mapping table. Roughly a third sit outside it, in the
Scope list, the worked example, the traps paragraph, the regeneration command, the Tests
notes, and several of the Steps. Step 15 names every one of those sections, and the guard
test is what confirms the pass was complete.

**The denylist lives in exactly one file.** ~~`tests/test_repo_hygiene.py` has to spell
out what it forbids, so it skips itself.~~ **Superseded by Revision 2.** Making the guard
skip itself is what published the denylist; see "Rev 2 — the guard is split in two". What
still holds is the rule that kept this plan usable — the patterns are never copied into
this document, and the acceptance criteria never restate them as greps, because a grep for
a pattern is itself a disclosure of that pattern.

**Two guard-test traps.** ~~A lock-file integrity hash collides with a short host token,
so that file is skipped outright; a bare port number could appear by chance in the
minified bundle, so the port pattern requires the punctuated form.~~ **Superseded by
Revision 2.** Both workarounds narrowed the guard to dodge a false positive, and both are
gone — the allowlist scan excludes no file, and the port pattern moves to the untracked
denylist where a bundle collision cannot arise. Codex evaded Revision 1 through exactly
these two narrowings.

### Rev 2 — the guard is split in two

**A tracked guard that names nothing, and an untracked one that names everything.** The
two jobs have different disclosure costs, so they get different homes.

| | Tracked `tests/test_repo_hygiene.py` | Untracked `.private-denylist.toml` |
|---|---|---|
| Mechanism | Allowlist | Denylist |
| Ships publicly | Yes | No — gitignored |
| Catches | Any private IPv4, any unapproved host in URL position, anywhere in the tree | The specific known tokens, including bare words and abbreviated forms |
| Discloses | Nothing. Every entry is a public documentation range or a public domain | Everything, which is why it is not tracked |

**The allowlist checks two things.**

1. *Every IPv4 literal that falls in a private range* — `10/8`, `172.16/12`, `192.168/16`,
   `127/8`, `169.254/16` — must be in the approved set. Public addresses are not checked at
   all, because a public resolver address in a doc discloses nothing about a LAN. Approved:
   `10/8`, `172.16/12`, `127/8`, `169.254/16`, `192.168.1.0/24`, `192.168.0.0`,
   `192.168.2.0/24`, `192.168.10.0/24`. Measured against the tree at `2c3a5ad`, this rule
   produces **zero** hits across all 96 tracked files, the minified bundle and
   `frontend/package-lock.json` included, and it fails on the real subnet prefix.
2. *Every host in URL position* — after a scheme, or after `user@` — must be a single label,
   end in a generic suffix (`.lan`, `.local`, `.invalid`, `.test`, `.example`, `.arpa`,
   `.internal`, `localhost`), or be in `ALLOWED_DOMAINS`. An entry matches a host that
   equals it or ends in `"." + entry`, so one entry covers a domain and its subdomains.

   `ALLOWED_DOMAINS` is **exactly this set**, and it is the complete list — measured by
   running the URL-position scan over all 96 tracked files with an empty allowlist, which
   returns 15 distinct multi-label hosts and nothing else:

   | Entry | Covers | Seen at |
   |---|---|---|
   | `example.com` | `example.com`, `git.example.com`, `pihole.example.com` | `docs/deploy.md:51` |
   | `npmjs.org` | `registry.npmjs.org` (292 uses) | `frontend/package-lock.json:32` |
   | `github.com` | `github.com` | `frontend/package-lock.json:985` |
   | `githubusercontent.com` | `raw.githubusercontent.com` | `docs/plans/0013-…:511` |
   | `jsdelivr.net` | `cdn.jsdelivr.net` | `docs/plans/0012-…:61` |
   | `opencollective.com` | itself | `frontend/package-lock.json:176` |
   | `tidelift.com` | itself | `frontend/package-lock.json:2843` |
   | `eslint.org` | itself | `frontend/package-lock.json:998` |
   | `react.dev` | itself | `app/static/ui/assets/antd-*.js:1` |
   | `w3.org` | `www.w3.org` | `app/static/ui/assets/antd-*.js:8` |
   | `gitea.com` | itself | `docs/deploy.md:78` |
   | `pi.hole` | itself — Pi-hole's own default hostname | `docs/plans/0002-…:453` |

   One further literal is approved as a documentation placeholder rather than a host,
   `192.168.1.x` at `docs/design.md:398`. It reaches the host check only because the `x`
   stops it parsing as an address. Single-label hosts are never checked; the scan found
   sixteen of them (`pihole1`, `latest`, `user`, `alice`, and so on) and none can disclose
   a private FQDN.

**Why hostnames are only checked in URL position.** A general dotted-token scan is
unusable. Measured at `2c3a5ad` it returns **9044** distinct matches, almost all of them
code property chains (`i.createElement`, `Object.assign`, `pytest.raises`) and filenames
(`design.md`, `api.ts`). A regex cannot tell a hostname from a method call. Restricting to
URL position drops that to 23. The cost is that a private hostname mentioned in prose, not
in a URL, is invisible to the tracked guard — the untracked denylist covers that case, and
this is the one real hole in the split.

**`.lan` cannot be policed.** It appears about 500 times as generic test fixtures
(`server.lan`, `a.lan`, `x.lan`). The allowlist therefore permits the whole suffix, which
is why a real `.lan` hostname is a one-time content fix (Step 18) plus a denylist entry
rather than something the tracked guard can catch.

**`SKIP` becomes empty.** No tracked file is excluded from the scan. Revision 1 skipped the
guard itself, which is what published the denylist, and `frontend/package-lock.json` for a
base64 collision with a short host token. Neither exclusion survives the switch to an
allowlist — the measurements above include both files and both are clean. This closes the
whole-file-exclusion gap rather than narrowing it.

**Absence of the denylist file must be loud on the author's machine and quiet elsewhere.**
A public clone cannot supply the file, so its test skips with a printed notice. On a machine
that is supposed to have it, a silent skip is how the check dies unnoticed, so setting
`FILTERHELM_REQUIRE_DENYLIST=1` turns absence into a failure.

**Decoding replaces skipping.** Revision 1 silently skipped any file that was not valid
UTF-8, so one stray byte suppressed a whole file. Decoding with `errors="replace"` scans
what is readable and removes the evasion class without an exclusion list.

### Rev 2 — rejected alternatives

- **Tracked allowlist only, no denylist file.** Simplest, and it makes the tree safe to
  publish. Rejected because it drops the regression check on the bare word-shaped
  tokens — the hypervisor, the NAS host, the informal network noun, the container ids,
  the port, the token variable — which is the check that has already proved its worth.
- **Untracked denylist only, nothing tracked.** Smallest change, but the published tree
  then carries no hygiene test, and a new private address introduced later trips nothing.
- **Hashing the denylist tokens into the tracked file.** Appealing until the numbers are
  checked. A `192.168.x.y` address is 65 536 candidates and a personal name comes from a
  wordlist, so the hashes are brute-forceable in seconds. Hashing hides the tokens from a
  casual reader and not from anyone who cares, which is the wrong kind of protection.
- **Reading blobs with `git cat-file` instead of the working copy.** Closes the
  index-versus-worktree gap, but the deploy clone is clean and the added indirection buys
  little. See the Out list.

**Rejected alternatives.**
- Deleting `docs/deploy.md`: the runbook is a working example of a self-hosted runner that
  deploys to itself, which is genuinely useful to a reader and expensive to rewrite later.
- Moving the runbook to a private repo: it would then be maintained in two places and
  drift from `.gitea/workflows/deploy.yml`, which stays here.
- A `.gitignore`-style allowlist of approved example addresses: heavier than a denylist
  and would need updating for every new fixture.

**Sequencing.** Cleared. Plans 0015 and 0017 both shipped on 2026-09-13 and are merged
into `main`; nothing else is in flight. Branch `feature/0016-privacy-scrub` from `main` at
or after `ee22c40`.

**Line numbers are as of `ee22c40`.** 0017 shifted `docs/design.md` by roughly ten lines
and `frontend/src/pages/InstancesPage.tsx` by ninety, so the references below were
re-checked against that commit. If the branch point moves, regenerate the work list rather
than trusting the numbers:

`./.venv/bin/pytest tests/test_repo_hygiene.py` prints every remaining hit as
`path:line: label: text`, which is the work list. The guard carries the category labels
alongside the matches.

## Files

- `README.md` — reword the "Why this exists" opener; rewrite the pre-release scrub bullet.
- `docs/design.md` — §1 table and cross-reference note, §8 mockups, §11 wholesale, §14
  scrub TODO, plus the scattered private-note references.
- `docs/deploy.md` — genericize the runbook end to end, including its title.
- `docs/potential-features.md` — one informal-network-noun phrase.
- `.gitea/workflows/deploy.yml` — the container-id comment on line 1.
- `app/config.py` — module docstring design-doc pointer; `CredentialStore` docstring.
- `app/main.py` — module docstring deployment note.
- `app/pihole_client.py` — module docstring provenance note and endpoint-inventory header.
- `app/audit.py` — module docstring design-doc pointer.
- `frontend/src/pages/InstancesPage.tsx` — base-URL placeholder.
- `frontend/src/pages/DNSHostsTab.tsx` — address placeholder.
- `docs/plans/0003-domain-allow-deny-management.md` — one address in a verification note.
- `docs/plans/0009-group-deletion-orphans.md` — one informal-network-noun tool description.
- `docs/plans/0015-instance-health.md` — one address in the Contracts example.
- `docs/plans/0016-privacy-scrub.md` — this file, redacted last (Step 15).
- `tests/test_repo_hygiene.py` — the guard. Rev 2 rewrites it as an allowlist scan
  plus a loader for the untracked denylist.
- `app/static/ui/` — rebuilt so the bundle stops carrying the old placeholders.
- `.gitignore` — ignores `before_public.md` (Rev 2 adds `.private-denylist.toml`).
  Absent from Revision 1's Files list; recorded here rather than reverted.
- `.private-denylist.toml` — **created but never tracked.** Holds the token patterns
  that Revision 1 published. Listed so the reviewer knows to confirm it is ignored.
- `docs/plans/0003-domain-allow-deny-management.md` — Rev 2 also replaces the real
  resolver hostname left beside the address Step 10 replaced.

## Steps

**Steps 1–15 are done.** They are implemented and verified on
`feature/0016-privacy-scrub` through `2c3a5ad`, and Revision 2 does not change them.
Read them for context; the remaining work is Steps 16–22. The note below describes the
original single-commit sequencing and is kept as the record of how the tree got clean.

The guard test is red from the moment it exists until Step 15 finishes, because this plan
file still carries the mapping the earlier steps read from. That is expected, and it sets
the order of the last three steps. The guard reads `git ls-files`, so it needs the new
file **staged**, not committed:

1. Do Steps 1 through 14, writing `tests/test_repo_hygiene.py` at Step 13.
2. `git add -A`, then run the guard. It reports every remaining hit as
   `path:line: label: text`, and at this point they are all in this plan file.
3. Do Step 15 against that list. Re-stage and re-run until the guard is green.
4. Make **one commit** on the feature branch. Do not commit step by step into a red tree.

1. **`docs/design.md` §1.** Replace the cross-reference note at lines 17–19 with a
   sentence saying the double-bracket wiki links and `.md` names in this document refer to
   internal notes kept outside this repo. Drop the private infrastructure-note attribution on line
   30. Rewrite the table at lines 34–35:

   | Name | Address | Host |
   |---|---|---|
   | pihole1 | 192.168.1.2 | Docker container on a macvlan network (not SSH-able) |
   | pihole2 | 192.168.1.3 | Container on a separate host, normal Debian |

   Line 8's clone URL becomes `git@git.example.com:filterhelm.git`. Keep the
   nebula-sync failure narrative verbatim; it is the source of the project invariants.

2. **`docs/design.md` scattered references.** Lines 12, 15, 23, 44, 71, 77, 174, 294, 336,
   428, 518–519, 527–528, and 568 each name a private-repo file, the private infrastructure repo, or
   the informal network noun. Drop each per the mapping. Where a sentence needs a referent, use "internal
   notes" or "a private infrastructure repo". Line 294's tooling list becomes "the other
   small Python services this runs alongside". Line 600 is §14 and is handled by Step 5.

3. **`docs/design.md` §8 mockups.** Lines 376–378 and 398 take the mapped hostnames and
   addresses. Column widths in the ASCII boxes must stay aligned after substitution, and
   the replacements are shorter than the originals, so pad rather than let the boxes close
   early.

4. **`docs/design.md` §11.** Rewrite. Keep: the standalone-repo-not-folded-in decision, the
   dedicated-host-rather-than-sharing-the-proxy decision and its reasoning, the
   access-control note for a credential-mutating tool, the runner-lives-on-the-target
   decision, the systemd unit verbatim, the `127.0.0.1` to `0.0.0.0` bind rationale, and
   the credential-storage paragraph. Drop: the Gitea endpoints and both
   commit hashes, **the entire sentence recording where the Gitea API token is
   stored**, the proxy-host provisioning detail, the proxied-service inventory,
   the internal DNS convention paragraph, the private-note Jenkins reference, and the
   comparison to another internal Python service. "Kyrnveil's own desktop session" on line 557 becomes "a
   developer's desktop session". §11 runs from line 484 to line 559; the stored token name
   sentence is line 494.

5. **`docs/design.md` §14.** Rewrite the privacy-scrub bullet from TODO to partially done:
   the working tree was scrubbed by plan 0016 and is guarded by
   `tests/test_repo_hygiene.py`; Git history still carries the original values and must be
   handled at publish time by exporting a fresh history or running `git filter-repo`.

6. **`docs/deploy.md`.** Title becomes `# Deploy runbook — Gitea Actions to a self-hosted
   target`. Apply the mapping throughout: the deploy host, the Gitea host, `git.example.com`,
   port `2222`, `the reverse proxy`. Line 4's "Claude has no access to" note and line 6's
   "Kyrnveil runs each step" stay, since they describe how the runbook is executed. Line 80's
   architecture comment keeps the amd64-versus-Pi reasoning without naming the hypervisor.
   §0's unknowns and §7's open items lose the private infrastructure-note and internal DNS entries and
   keep the reverse-proxy and IP items in generic form.

7. **`.gitea/workflows/deploy.yml`.** Line 1 becomes
   `# Deploys to the host this runner lives on — see docs/deploy.md.` No other line changes;
   the `filterhelm-deploy` runner label is not identifying.

8. **Source comments.** Docstring text only; no code moves.
   - `app/config.py:6` → `Design doc: docs/design.md §7.3.`
   - `app/config.py:144–146` → keep the keyring-first and 0600-fallback explanation, drop
     the informal network noun and the private webhook-path example, end with
     `See docs/design.md §7.3.`
   - `app/main.py:4–5` → `docs/design.md §6/§7` for the localhost-only rule, and "a hosted
     deployment behind a reverse proxy" in place of the `LXC`/`NPM` phrasing.
   - `app/pihole_client.py:5–10` → drop the private automation-design note and the private
     mirrored-scripts pointer; keep the recorded-versus-confirmed distinction as a plain
     sentence. Line 16 → `Settings (subset used by the MVP, docs/design.md §5.4):`.
   - `app/audit.py:3` → `see docs/design.md §6`.

9. **UI placeholders.** `InstancesPage.tsx:301` placeholder becomes
   `http://192.168.1.2`, matching the validation message directly above it that already
   says `http://192.168.1.2`. `DNSHostsTab.tsx:294` placeholder becomes `192.168.1.10`.

10. **Plan files.** `0003-domain-allow-deny-management.md:531` address → `192.168.1.2`.
    `0009-group-deletion-orphans.md:125` informal-network-noun tool description → "self-hosted tool".
    `0015-instance-health.md:314` `base_url` → `https://192.168.1.2`. No other content in
    shipped plans changes.

11. **`README.md`.** Line 12's informal-network-noun opener becomes "A home or
    small-office network". The pre-release bullet at 261–262 becomes: the working tree is
    scrubbed and guarded by `tests/test_repo_hygiene.py`; Git history still carries
    internal addresses, hostnames, and endpoints, so a public release must publish from a
    rewritten or fresh history.

12. **`docs/potential-features.md`.** Line 95's informal-network-noun phrase → "household or small
    office".

13. **`tests/test_repo_hygiene.py`.** New file, and the single home for the denylist.
    Nothing else in the tree — this plan included — spells out the patterns. Structure:

    - Module docstring: "Guards the public tree against private infrastructure details."
    - `ROOT = Path(__file__).resolve().parent.parent`.
    - `FORBIDDEN`, a dict of label to compiled pattern. Patterns group **by category, not
      one per mapping-table row** — the table's 20 rows collapse to eleven categories,
      because both instance addresses share one pattern and the private note filenames are
      a single alternation. The eleven: real instance address, personal domain, internal
      DNS tier, hypervisor, NAS host, macvlan name, container id, informal network noun,
      private notes, Gitea SSH port, and stored token name. Labels are the category names the mapping
      table's left column carries after Step 15, so a failure reads as a category plus the
      offending text.
    - The **stored token name** entry is new in this revision. `docs/design.md` records the
      environment variable that held a Gitea API token, and Step 4 deletes that sentence;
      the pattern keeps it from coming back. It lives here rather than in an acceptance
      grep for the same reason as every other pattern — a grep for it would match the line
      of this plan that spelled it.
    - Word boundaries on the short host tokens, so `proxy`, `proxied` and the hypervisor
      name do not trip the bare-hostname pattern. The port pattern requires the `:` form,
      so a chance digit run in the minified bundle does not match.
    - `SKIP = {"tests/test_repo_hygiene.py", "frontend/package-lock.json"}` and nothing
      else. This file skips itself because it is the denylist; `package-lock.json` is
      skipped because its base64 integrity hashes contain fragments that collide with the
      short tokens. **This plan file is not in `SKIP`.**
    - `_tracked_files()` shells `git ls-files -z` with `cwd=ROOT` and splits on NUL.
    - One test, `test_tracked_files_carry_no_private_infrastructure_details`, which reads
      each tracked file, skips anything that is not decodable UTF-8 or is not a regular
      file, collects every match as `f"{name}:{line}: {label}: {match.group(0)}"`, and
      asserts the list is empty with those findings as the message.

    Write the patterns straight from the mapping table while it is still unredacted. Once
    this file is committed it is the authority, and Step 15 removes the duplicate copy
    from this document.

14. **Build.** `cd frontend && npm run build && npm run lint`; commit the regenerated
    `app/static/ui/`. The guard test fails until this runs, because the current bundle
    embeds both old placeholders.

15. **Redact this plan file.** Last, after every other step has consumed the mapping and
    after `tests/test_repo_hygiene.py` is written and staged. This file is scanned by the guard on
    the same terms as every other tracked file, so it must end the step with zero hits.
    Work section by section; a partial pass leaves the test red.

    - **Approach, mapping table.** The **Current** column becomes category descriptions —
      "the two real instance addresses", "the personal domain and its Gitea subdomain",
      "the internal DNS tier", "the hypervisor host and its repo", "the NAS host", "the
      macvlan name", "the numbered containers", "the Gitea SSH port", "the private note
      filenames", "the informal network noun". The **Replacement** column stays verbatim. Those values are the
      convention a future reader needs and are already public-safe.
    - **Approach, worked example.** Becomes "Docker container on a macvlan network (not
      SSH-able)", the same string Step 1 writes into `docs/design.md`.
    - **Approach, guard-test traps paragraph.** Describe the two collisions by category —
      a lock-file integrity hash that collides with a short host token, and a bare port
      number that needs the `:` form. Drop the literals.
    - **Approach, the regeneration grep.** Replace the command with a pointer:
      `./.venv/bin/pytest tests/test_repo_hygiene.py` prints every remaining hit as
      `path:line: label: text`, which is the work list. It is a better regeneration tool
      than the grep was, because it carries the labels.
    - **Scope.** The In-list names the DNS tier by category instead of by name, and the
      token bullet says "the sentence recording where a Gitea API token is stored".
    - **The token variable name.** Scope and Step 4 spell it three times between them.
      Replace each with "the Gitea API token" or "the stored token name". After this the
      only tracked file containing the literal is `tests/test_repo_hygiene.py`, which
      skips itself — which is the whole point of keeping the pattern there.
    - **The Files list.** Its three annotations name a container id, a phrase, and a
      filename. Describe what each file needs by category instead.
    - **Steps 1, 2, 4, 6, 7, 8, 9, 10, 11, 12, and this step.** Each loses its remaining
      literals the same way. Step 4's drop-list names its two inventories by category —
      "the proxied-service inventory" and "the Gitea endpoints". Line-number references
      stay throughout; they identify nothing.
    - **Step 13.** The structural description stays. Its category list is written to name
      no literal — note that the informal-network-noun category is deliberately not called
      by the word it forbids, since the guard would match its own category name.
    - **Tests.** Describe the pattern set by count and category rather than naming the
      three patterns the dry run added.
    - **Acceptance criteria.** Free of denylist literals as of this revision. Confirm
      with the guard test; do not rewrite.
    - Every other section stays. The reasoning is the record of why the scrub took the
      shape it did, and none of it identifies anything once the literals are gone.
    - Verify by running the guard test, not by eye. 75 hits live in this file when Step 15
      starts, so a visual pass will miss some.


### Rev 2 steps

16. **`.private-denylist.toml`, untracked.** Create at the repo root and add it to
    `.gitignore` in the same step, before it has any content, so it can never be staged.
    `tomllib` is in the standard library on Python 3.14, so no dependency is added.
    Structure:

    ```toml
    [patterns]
    "real instance address" = "..."
    "personal domain" = "..."
    ```

    Move all eleven categories out of `tests/test_repo_hygiene.py` verbatim, then widen
    the four that Codex evaded. The current tracked guard is the source for every
    literal, so nothing outside the repo is needed and nothing is copied into this file:

    - **real instance address** — also match the abbreviated third-and-fourth-octet form.
      The restored `ee22c40:docs/design.md:328` diagram passes the current pattern.
    - **Gitea SSH port** — drop the `:` requirement; match the bare number on a word
      boundary. `ssh-keyscan -p <port>` passes the current pattern.
    - **container id** — allow zero or more whitespace characters of any kind between the
      prefix and the digits, not exactly one ASCII space.
    - **host tokens** — the hypervisor, NAS host and DNS tier patterns use `\b`, which an
      underscore defeats. Use an explicit separator class instead.

    Add one new entry for the resolver hostname that Step 18 removes.

17. **Rewrite `tests/test_repo_hygiene.py`.** No private identifier appears anywhere in it.
    Two tests, both driven by `_tracked_files()`, which keeps `git ls-files -z`.

    - Module constants: `ALLOWED_PRIVATE_NETS`, `CHECKED_PRIVATE_NETS`,
      `GENERIC_HOST_SUFFIXES`, `ALLOWED_DOMAINS`, all as written in the Approach table.
      Parse addresses with `ipaddress`, not string prefixes, so that an approved `/24`
      written as a prefix string cannot accidentally approve every wider range that
      shares its leading digits.
    - `SKIP` is deleted. Every tracked file is scanned.
    - Read each file as `path.read_bytes().decode("utf-8", errors="replace")`. No
      `UnicodeDecodeError` branch and no silent `continue`.
    - Scan the **path name** as well as the contents, so a tracked
      `docs/<private-name>.md` fails even when its body is clean.
    - Read symlink targets from `git ls-files -s` mode `120000` and scan the target string
      rather than following the link.
    - `test_tracked_files_use_only_approved_addresses_and_hosts` — the allowlist scan.
      Reports each hit as `path:line: category: text`.
    - `test_tracked_files_carry_no_known_private_tokens` — loads `.private-denylist.toml`
      when present and applies every pattern case-insensitively. When the file is absent it
      calls `pytest.skip()` naming the file, unless `FILTERHELM_REQUIRE_DENYLIST=1` is
      set, in which case it fails.

18. **`docs/plans/0003-domain-allow-deny-management.md:531`.** The `dig @<host>` argument is
    the real resolver's hostname, left in place when Step 10 replaced the address on the
    same line. Replace it with the address already used two lines above. Keep the rest of
    the verification record; it is evidence the manual check happened.

19. **`README.md`.** Restore one line under the TODO section stating that Git history still
    carries internal addresses and endpoints, and pointing at `docs/design.md` §14 for the
    detail. Revision 1's Step 11 required this notice; the review pass removed the section
    that carried it. `docs/design.md` §14 keeps the full statement.

20. **Re-run the guard against the pre-scrub tree.** Check out `ee22c40` into a scratch
    worktree, point the new guard at it, and confirm it reports the lines the scrub removed,
    including the four Codex restored past Revision 1. This is the test that Revision 1's
    acceptance criterion claimed and did not have.

21. **No rebuild.** `frontend/` is untouched by Steps 16–22, so `app/static/ui/` does not
    change and `npm run build` is not required. Confirm `git diff --stat` shows no
    `app/static/ui/` entry.

22. **Commit on the existing branch.** Do not start a new branch — Steps 1–15 are already
    on `feature/0016-privacy-scrub`, and this work is a second commit on top.

## Contracts

N/A — no API request or response shape, config key, or database structure changes. The
two edited strings are `placeholder` props on Ant Design `Input` components and carry no
contract. `frontend/src/api.ts` is not touched.

## Tests

### Rev 2

- `tests/test_repo_hygiene.py::test_tracked_files_use_only_approved_addresses_and_hosts`
  — scans every tracked file with no exclusions, plus tracked path names and symlink
  targets. Asserts every private-range IPv4 literal is in `ALLOWED_PRIVATE_NETS` and
  every host in URL position is single-label, generically suffixed, or in
  `ALLOWED_DOMAINS`. Reports `path:line: category: text` per hit. Must be green on the
  current tree, which the Approach section's measurement already establishes.
- `tests/test_repo_hygiene.py::test_tracked_files_carry_no_known_private_tokens` —
  loads `.private-denylist.toml`, applies each pattern case-insensitively to every
  tracked file, and reports hits the same way. Skips with a printed notice when the
  file is absent; fails instead when `FILTERHELM_REQUIRE_DENYLIST=1`.
- **Negative control (Step 21).** Both tests run against a scratch worktree at
  `ee22c40` and must report the removed lines, including the abbreviated address in the
  §8 diagram, the bare-port prose in the runbook, the un-spaced container id, and the
  resolver hostname in plan 0003. A guard that cannot fail on the pre-scrub tree is not
  a guard.
- `tests/` is otherwise untouched, exactly as in Revision 1.

### Rev 1 (implemented)

- `tests/test_repo_hygiene.py::test_tracked_files_carry_no_private_infrastructure_details`
  — walks `git ls-files`, skips itself and `frontend/package-lock.json`, skips files that
  are not UTF-8 text, and asserts no denylisted pattern matches. On failure it reports
  every hit as `path:line: label: matched text` rather than a bare boolean, so the fix
  list is the failure message. Covers all eleven pattern categories, including the
  compiled bundle under `app/static/ui/`. Dry-run at `ee22c40`: 95 tracked files, 94
  scanned (`frontend/package-lock.json` skipped), hits in exactly the 16 files this plan's
  Files list names, no false positives outside them. That run is what added `app/audit.py`,
  `docs/plans/0016-privacy-scrub.md`, and the hypervisor, NAS host, and macvlan name patterns.
- Every existing test is unchanged and must stay green. The scrub deliberately avoids
  `tests/`, so a failure anywhere else means a source comment edit went wrong. Of
  particular note, `tests/test_url_policy.py` and its RFC1918 cases are untouched.

## Invariants at risk

1. **One instance mutated at a time** — no mutation code is touched. N/A.
2. **Diff before write** — N/A.
3. **Gravity gated** — N/A.
4. **No background scheduler** — nothing is added that runs on its own. The guard test
   runs only under pytest. Intact.
5. **FastAPI owns credentials and Pi-hole access** — improved, not risked. Step 4 deletes
   the only tracked sentence that records where a Gitea API token is stored. No credential
   handling code changes; `app/config.py`'s edits are docstring text only.
6. **All outbound through `url_policy`** — `app/url_policy.py` is explicitly out of scope
   and unmodified. The RFC1918 networks it defines are policy, not example data, and the
   guard test's patterns do not match them. Intact.
7. **Every route behind auth middleware** — no route is added, removed, or moved. Intact.
8. **Compiled frontend committed** — Step 14 rebuilds and commits `app/static/ui/`, and
   the guard test fails if that step is skipped, since the shipped bundle contains both
   old placeholders. Intact and enforced.
9. **`main` is production** — work on `feature/0016-privacy-scrub`, merge after Accept.
   The deploy is unaffected: no Python or TypeScript logic changes, and the Gitea runner
   pulls by branch, not by commit. Intact.

**Rev 2 note.** Steps 16–22 touch one test file, two documents, `.gitignore`, and an
untracked file. No route, model, credential path, outbound URL, or frontend source is
involved, so invariants 1, 2, 3, 5, 6 and 7 are untouched. Invariant 4 stays intact —
the new tests run only under pytest. Invariant 8 is not engaged, because `frontend/`
does not change and `app/static/ui/` is not rebuilt. Invariant 9 is unchanged.

## Acceptance criteria

The guard test is the scrub's acceptance check. It scans every tracked file, reports each
hit as `path:line: label: text`, and covers strictly more ground than a grep list would —
so there is no separate list of literal greps here, and adding one would put the map back
into this file.

- [ ] `./.venv/bin/pytest` green, including `tests/test_repo_hygiene.py`.
- [ ] `tests/test_repo_hygiene.py` `SKIP` contains exactly two entries, itself and
      `frontend/package-lock.json`. **This plan file is not among them**, and neither is
      any file the scrub was supposed to clean.
- [ ] Reverting any single scrubbed line makes the guard test fail, and its message names
      that file and line.
- [ ] `cd frontend && npm run build && npm run lint` clean; `app/static/ui/` committed.
- [ ] `grep -n "npm run" README.md` still shows the install, lint, and build commands
      intact.
- [ ] `app/url_policy.py` is unchanged: `git diff main...HEAD -- app/url_policy.py` is empty.
- [ ] `tests/` is unchanged apart from the new file:
      `git diff --name-only main...HEAD -- tests/` lists only `tests/test_repo_hygiene.py`.
- [ ] `docs/deploy.md` still reads as a followable runbook: a reader who has a Gitea server
      and a target host can register a runner, bootstrap the target, and deploy.
- [ ] The Instances add-instance modal and the DNS host form show the new placeholders in
      the running app.
- [ ] Diff limited to the Files list — 15 edited files, the new `tests/test_repo_hygiene.py`,
      and the rebuilt `app/static/ui/`.

### Rev 2

- [ ] Every pattern in `.private-denylist.toml`, applied to the whole tracked tree,
      matches nothing — `tests/` included, with no file skipped. The denylist test
      itself is this check, so run it rather than writing the tokens into a grep. A
      grep spelled out here would be the disclosure it is meant to prevent.
- [ ] `git check-ignore -v .private-denylist.toml` confirms it is ignored, and
      `git ls-files .private-denylist.toml` is empty.
- [ ] `tests/test_repo_hygiene.py` contains no `SKIP` set and no `UnicodeDecodeError`
      handler.
- [ ] Moving `.private-denylist.toml` aside makes the denylist test skip with a message
      naming the file; with `FILTERHELM_REQUIRE_DENYLIST=1` set it fails instead.
- [ ] Both tests run against a scratch worktree at `ee22c40` report hits, including the
      four restorations Codex used to evade Revision 1.
- [ ] Appending any private address outside the approved set to a tracked file fails
      the allowlist test naming that file and line; the same for a URL pointing at an
      unapproved domain. Use a throwaway address, not the real prefix — writing the
      real one into this file is the mistake the guard exists to catch.
- [ ] `docs/plans/0003-domain-allow-deny-management.md:531` names no hostname.
- [ ] `README.md` states that Git history is unscrubbed and points to `docs/design.md`
      §14.
- [ ] `./.venv/bin/pytest` green.
- [ ] `git diff --stat` shows no `app/static/ui/` or `frontend/` entry.
