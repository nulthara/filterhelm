# 0039 — Sample Compose file for the published image

Status: in-progress            <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0039-sample-compose-ghcr
Author: Kyrnveil
Date: 2026-10-17

## Problem

A new user who wants to run FilterHelm from the published image has a `docker run`
command in the README and nothing else. The repository's `compose.yaml` builds from a
clone, so it cannot be copied on its own. A standalone sample that pulls
`ghcr.io/nulthara/filterhelm` lets a new user run the app with a single downloaded file.

## Scope

In:
- A new `compose.ghcr.yaml` at the repo root that pulls the published image
- A README "Run with Compose" section under Quick start that uses it
- A hygiene or workflow test that keeps the sample consistent with the image
- Two one-line docs fixes made by the architect during review: the README version example
  (`0.2.0` to `0.2.1`) and the stale `pihole1`/`pihole2` example in `docs/potential-features.md`

Out:
- Changing `compose.yaml`. It stays the build-from-clone file contributors use.
- Changing the Dockerfile, the image, or the release workflow
- Reverse-proxy or HTTPS setup. The sample binds to loopback, like the existing files.
- Any other README section, and `docs/deploy.md`

## Approach

**A second file, not an edit of `compose.yaml`.** `compose.yaml` backs the clone-and-build
workflow in `docs/deploy.md`. Rewriting it to pull the image would break that workflow.
`compose.ghcr.yaml` carries the same hardening so both files behave alike.

**Named volume mounted at `/data`.** The image keeps all state in `/data`
(`FILTERHELM_DATA=/data` in the Dockerfile) and runs as uid 10001. A named volume is
initialised with the image's `/data` ownership, so users need no `mkdir` or `chown`.
A bind mount would need `chown 10001:10001` on the host, which is an easy step to miss.
Rejected for a first-run sample.

**Image tag.** `ghcr.io/nulthara/filterhelm:latest`, with a comment telling users to pin
`X.Y.Z` for anything long-lived. This matches the README's existing advice.

**Sample content** (the implementer writes it to match `compose.yaml`, minus `build:`):

- service `filterhelm`, `image: ghcr.io/nulthara/filterhelm:latest`
- `restart: unless-stopped`, `read_only: true`, `tmpfs: [/tmp]`
- `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`
- `ports: ["127.0.0.1:8420:8420"]`
- environment `FILTERHELM_AUTH_MODE: local`, `FILTERHELM_COOKIE_SECURE: "false"`,
  `FILTERHELM_TRUSTED_HOSTS: localhost,127.0.0.1`, each with the same comments as
  `compose.yaml`
- `volumes: [filterhelm-data:/data]` and a top-level `filterhelm-data:` volume

**README flow.** The section shows `curl -fsSLO` of the raw file from the GitHub repo
(`https://raw.githubusercontent.com/nulthara/filterhelm/main/compose.ghcr.yaml`), then
`docker compose -f compose.ghcr.yaml up -d` and
`docker compose -f compose.ghcr.yaml exec filterhelm python -m app.auth set-password --username admin`,
then "open `http://127.0.0.1:8420/`". Users who rename the file to `compose.yaml`
can drop the `-f`; the section says so.

## Files

- `compose.ghcr.yaml` — new sample
- `README.md` — new "Run with Compose" subsection after "Run the published image"
- `tests/test_supply_chain.py` — new test for the sample (or a new `tests/test_compose_sample.py` if that file's scope does not fit; the implementer picks the better fit and says which)
- `docs/plans/0039-sample-compose-for-published-image.md` — this plan

## Steps

1. Create `compose.ghcr.yaml` per the content list above, with a header comment saying it
   pulls the published image and `compose.yaml` builds from a clone.
2. Add the README subsection directly after "Run the published image", before "Verify an image".
   Add `raw.githubusercontent.com` to the hygiene `ALLOWED_DOMAINS` set in
   `tests/test_repo_hygiene.py` if the URL scan flags it (the implementer runs the suite
   to find out; no other host may be added).
3. Add the tests below.

## Contracts

N/A — documentation and a sample file. No API, config key, or DB change, and no
`frontend/src/api.ts` change.

## Tests

- `::test_sample_compose_pulls_published_image` — `compose.ghcr.yaml` parses as YAML
  (`yaml.safe_load` if PyYAML is already a dependency; otherwise the implementer uses a
  line-based check and adds no dependency), the service has `image` starting with
  `ghcr.io/nulthara/filterhelm:` and no `build` key.
- `::test_sample_compose_keeps_hardening` — the service sets `read_only: true`,
  `cap_drop` contains `ALL`, `security_opt` contains `no-new-privileges:true`, and every
  `ports` entry starts with `127.0.0.1:`.
- `::test_sample_compose_data_volume_matches_image` — the volume mounts at the path in the
  Dockerfile's `FILTERHELM_DATA` value.
- The existing hygiene tests pass over the new file and the README.

## Invariants at risk

Invariant 5 (credentials and Pi-hole access stay in the backend) is near, because the
sample carries environment settings. It sets no password, token, or Pi-hole URL.
Invariant 9 (`main` is production) is untouched, because the sample is documentation and
does not change the deploy workflow. No other invariant is near.

## Acceptance criteria

- [ ] `compose.ghcr.yaml` exists, has no `build:` key, and `docker compose -f compose.ghcr.yaml config` validates it if Docker is available
- [ ] `compose.yaml` is unchanged
- [ ] The README section matches the flow above and links the sample
- [ ] The diff touches only the Files list
- [ ] `./.venv/bin/pytest` green, and `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] No frontend change, so no rebuild of `app/static/ui/`
