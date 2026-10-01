# 0025 — Container image and Podman production deploy

Status: in-progress           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0025-container-deploy
Author: Claude (architect)
Date: 2026-09-30

## Problem

Production runs from a venv plus a hand-written systemd unit on the deploy LXC. The host's
Python version, the venv, and the unit drift independently of the repo, and every deploy
runs `pip install` straight into the live venv. Nobody outside this deploy can install the
app without repeating that bootstrap, and design.md §14 and the public-release list both
call for a container path. A container image built from the repo fixes the Python version,
installs the hashed lock at build time, and gives one install path that works both on the
deploy host and for anyone else.

## Scope

In:
- `Dockerfile` building a non-root, read-only-capable image from the committed
  `app/static/ui/` bundle and the hashed `requirements.lock`.
- `.dockerignore` to keep the build context small and secret-free.
- `compose.yaml` as the portable, build-from-source example.
- `deploy/pihole-manager.container` — the Podman Quadlet unit production installs.
- `deploy/pihole-manager-redeploy` — the root-owned build-and-restart script the runner
  calls through one sudoers rule.
- `.gitea/workflows/deploy.yml` switched from venv install to the redeploy script.
- `docs/deploy.md` rewritten for the container bootstrap, plus a cutover runbook from the
  existing systemd install and a rollback runbook.
- README, design.md, and supply-chain test updates that follow from the above.

Out:
- Publishing images to any registry, or any CI image-build job.
- Building the frontend inside the image. The image copies the committed bundle.
- Rootless Podman, Podman auto-update, container healthchecks, multiple workers.
- Any change under `app/` or `frontend/`.
- Executing the cutover. Codex writes the runbook; the user runs it on the host.

## Approach

**One image, two front doors.** The `Dockerfile` serves both production and portable
installs. Production runs it through a Podman Quadlet. Everyone else runs it through
`compose.yaml` with Docker Compose or `podman compose`.

**Image shape.** Verified by a throwaway Podman build on 2026-09-30.
- Base is `docker.io/library/python:3.14-slim-trixie` pinned to the multi-arch index digest
  `sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d`
  (Python 3.14.7, built 2026-09-19, so it clears the 24-hour age rule).
- `pip install --no-cache-dir --root-user-action=ignore --require-hashes --only-binary=:all: -r requirements.lock`
  succeeds on that base. Every locked package ships a wheel.
- The app is not pip-installed. `uvicorn app.main:app` and `python -m app.auth` run from
  `WORKDIR /opt/pihole-manager`, and `app/main.py` mounts `app/static/ui` by relative path,
  so the workdir must be the app root.
- The app runs as uid/gid 10001 with `PIHOLE_MANAGER_DATA=/data`. `/data` is pre-created
  `0700` and owned by 10001, so named volumes inherit that ownership on first use.
- The root filesystem can be read-only. The app writes only under the data dir
  (`app/config.py:_atomic_write` creates temp files beside the target).
- The probe served `/api/auth/status` and `/app/` and created `/data/auth.db` mode `0600`.

**Production runtime is rootful Podman with a Quadlet.** The Quadlet generates
`pihole-manager.service`, so the unit name, `systemctl restart`, and
`journalctl -u pihole-manager` stay as they are today. Rootless Podman was rejected
because it needs lingering, subuid ranges, and a user systemd instance inside an LXC,
which is more moving parts for no gain on a single-purpose box. Docker was rejected by the
user in favour of the daemonless runtime.

**Host networking in production.** `Network=host` keeps today's listen posture
(`0.0.0.0:8420` on the host). It also keeps real client addresses. With NAT those
addresses would turn into a bridge gateway. That would break `PIHOLE_MANAGER_PROXY_TRUSTED_IPS`
matching in proxy mode, blur audit-log client addresses, and collapse pre-auth rate-limit
keys. `compose.yaml` uses bridge networking with a loopback-only port, because a
try-it-out install has no proxy.

**Privilege boundary.** The runner gets no container-runtime access. It keeps exactly one
passwordless sudo rule, now for `/usr/local/sbin/pihole-manager-redeploy` with no
arguments. The script and the Quadlet are installed root-owned from the repo copies by
hand. A pushed edit to `deploy/` therefore does nothing until an admin reinstalls it, so a
compromised runner cannot rewrite what root executes. The runner still controls the
`Dockerfile` and build context. That is accepted, because a rootful `podman build` runs
build steps in an unprivileged container and the runner already controls the app it ships.

**Rollback.** The redeploy script re-tags the running image as
`localhost/pihole-manager:previous` before building. Rolling back is one re-tag and a
restart. The cutover copies the old data dir and leaves the original in place, so a full
return to the systemd unit stays possible.

Alternatives rejected:
- Keeping the venv deploy and adding the container only as a portable option. The user
  chose to migrate production.
- Putting the runner in a `podman`/`docker` group. That is root-equivalent.
- Running the repo's `compose.yaml` in production. It would make a runner-editable file
  part of what root executes.

## Files

- `Dockerfile` — new. The image.
- `.dockerignore` — new. Excludes VCS, venvs, node_modules, data, and local secrets from
  the build context.
- `compose.yaml` — new. Portable build-from-source example.
- `deploy/pihole-manager.container` — new. Production Quadlet unit.
- `deploy/pihole-manager-redeploy` — new, mode 0755 in git. Build-and-restart script.
- `.gitea/workflows/deploy.yml` — replace the install and restart steps with the redeploy
  script.
- `docs/deploy.md` — rewrite §4–§6 for the container bootstrap. Add a cutover section and a
  rollback section. Keep §1–§3 (runner setup).
- `README.md` — add a container run path, and switch the production `set-password` command
  to `podman exec`.
- `docs/design.md` — update the "Deployment shape (systemd, not Docker)" block and the
  §14 distribution TODO.
- `tests/test_supply_chain.py` — move the lock-install check from the workflow to the
  `Dockerfile`, and add the digest-pin check.
- `tests/test_container.py` — new. Guards the workflow ↔ sudoers ↔ script coupling and the
  Quadlet's no-pull, no-auto-update settings.

## Steps

1. **`Dockerfile`.** Write exactly this, with comments kept to the style shown:

   ```dockerfile
   # Python is pinned by index digest; refresh it under the 24-hour age rule in AGENTS.md.
   FROM docker.io/library/python:3.14-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

   ENV PYTHONDONTWRITEBYTECODE=1 \
       PYTHONUNBUFFERED=1 \
       PIP_DISABLE_PIP_VERSION_CHECK=1 \
       PIHOLE_MANAGER_DATA=/data

   WORKDIR /opt/pihole-manager

   COPY requirements.lock ./
   RUN pip install --no-cache-dir --root-user-action=ignore --require-hashes --only-binary=:all: -r requirements.lock

   RUN groupadd --gid 10001 pihole-manager \
       && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
          --shell /usr/sbin/nologin pihole-manager \
       && install -d -o 10001 -g 10001 -m 0700 /data

   # The frontend is the committed build; the image never runs Node.
   COPY app ./app

   USER 10001:10001
   VOLUME /data
   EXPOSE 8420
   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8420"]
   ```

2. **`.dockerignore`.** One pattern per line:
   `.git`, `.gitea`, `.venv`, `venv`, `**/__pycache__`, `*.egg-info`, `.pytest_cache`,
   `frontend/node_modules`, `data`, `.env`, `.claude`, `.superpowers`,
   `.private-denylist.toml`, `before_public.md`.

3. **`compose.yaml`.**

   ```yaml
   # Portable install. Build from a clone: docker compose up -d --build
   services:
     pihole-manager:
       build: .
       image: localhost/pihole-manager:latest
       restart: unless-stopped
       read_only: true
       tmpfs:
         - /tmp
       cap_drop:
         - ALL
       security_opt:
         - no-new-privileges:true
       ports:
         - "127.0.0.1:8420:8420"
       environment:
         PIHOLE_MANAGER_AUTH_MODE: local
         # Loopback HTTP only. Behind an HTTPS proxy, set this to "true" and add the public host below.
         PIHOLE_MANAGER_COOKIE_SECURE: "false"
         PIHOLE_MANAGER_TRUSTED_HOSTS: localhost,127.0.0.1
       volumes:
         - pihole-manager-data:/data

   volumes:
     pihole-manager-data:
   ```

4. **`deploy/pihole-manager.container`.** Installed to
   `/etc/containers/systemd/pihole-manager.container`.

   ```ini
   # Installed root-owned to /etc/containers/systemd/. See docs/deploy.md.
   [Unit]
   Description=Pi-hole Manager
   Wants=network-online.target
   After=network-online.target

   [Container]
   Image=localhost/pihole-manager:latest
   ContainerName=pihole-manager
   Pull=never
   Network=host
   Volume=/var/lib/pihole-manager:/data
   EnvironmentFile=/etc/pihole-manager/env
   ReadOnly=true
   NoNewPrivileges=true
   DropCapability=ALL

   [Service]
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

   Do not add `AutoUpdate=`, `HealthCmd=`, or any timer. `ReadOnly=true` already mounts a
   tmpfs at `/tmp` by Podman's default.

5. **`deploy/pihole-manager-redeploy`.** POSIX sh, `chmod 0755` in git
   (`git update-index --chmod=+x` if needed).

   ```sh
   #!/bin/sh
   # Installed root-owned to /usr/local/sbin/. The runner may run it only through sudo.
   set -eu

   image=localhost/pihole-manager

   # Keep the running image for one-step rollback.
   if podman image exists "$image:latest"; then
       podman tag "$image:latest" "$image:previous"
   fi

   podman build --pull=missing --tag "$image:latest" /opt/pihole-manager
   systemctl restart pihole-manager.service
   podman image prune --force
   ```

   The script takes no arguments and reads no environment. A failed build exits before the
   restart, which leaves the running container alone.

6. **`.gitea/workflows/deploy.yml`.**
   - Keep "Pull latest main" unchanged.
   - Delete the "Install dependencies" step.
   - Replace "Restart service" with:

     ```yaml
     - name: Build image and restart
       # Must exactly match /etc/sudoers.d/gitea-runner-pihole-manager.
       run: sudo /usr/local/sbin/pihole-manager-redeploy
     ```
   - Keep "Confirm it came back up" unchanged. Its comment cites "docs/deploy.md §4" for
     the sudoers rule; point it at the section that holds the rule after step 7.
   - Update the header comment so it describes pull → build → restart and still explains
     why there is no `actions/checkout`.

7. **`docs/deploy.md`.** Keep §0–§3. Replace §4 onward with these sections, in this order.
   Use only `example` hostnames and documentation addresses, because
   `tests/test_repo_hygiene.py` scans this file.
   - **§4 Prepare the host.**
     - Enable LXC nesting on the hypervisor (`pct set <vmid> --features nesting=1`, then
       restart the container).
     - `apt install podman`, and confirm with `sudo podman info`.
     - Keep the deploy-key and `ssh://…:2222` clone steps from today's §4. Drop the venv
       and pip lines.
   - **§5 Install the container units.** Use these commands:
     ```bash
     sudo install -d -m 0700 /etc/pihole-manager
     sudo install -m 0600 /dev/null /etc/pihole-manager/env   # then edit; see below
     sudo install -d -o 10001 -g 10001 -m 0700 /var/lib/pihole-manager
     sudo install -o root -g root -m 0644 /opt/pihole-manager/deploy/pihole-manager.container /etc/containers/systemd/
     sudo install -o root -g root -m 0755 /opt/pihole-manager/deploy/pihole-manager-redeploy /usr/local/sbin/
     sudo systemctl daemon-reload
     ```
     Show the `env` file contents as `KEY=value` lines carrying today's `Environment=`
     values (`PIHOLE_MANAGER_AUTH_MODE`, `PIHOLE_MANAGER_COOKIE_SECURE`,
     `PIHOLE_MANAGER_TRUSTED_HOSTS` including `127.0.0.1`, and an optional commented
     `PIHOLE_MANAGER_PUBLIC_ALLOWLIST`). Say that edits to `deploy/` in git take effect
     only after this step is re-run.
   - **§6 The one sudoers rule.** Replace today's rule with exactly:
     ```
     gitea-runner ALL=(root) NOPASSWD: /usr/local/sbin/pihole-manager-redeploy
     ```
     Explain why the runner is not in a container-runtime group.
   - **§7 First start.** `sudo /usr/local/sbin/pihole-manager-redeploy`, then
     `sudo podman exec -it pihole-manager python -m app.auth set-password --username admin`,
     then `curl -fsS http://127.0.0.1:8420/api/auth/status`. A Quadlet unit is not
     `systemctl enable`d, because the `[Install]` section is applied by the generator.
   - **§8 Cutover from the systemd install.** For a host already running the venv unit.
     0. Put the new files on the host before merging. With the feature branch pushed, run
        `sudo -u gitea-runner git -C /opt/pihole-manager fetch origin feature/0025-container-deploy`
        and then `sudo -u gitea-runner git -C /opt/pihole-manager checkout --detach FETCH_HEAD`.
        The next deploy run resets the checkout to `origin/main`.
     1. Find the current data dir. It is the unit's `PIHOLE_MANAGER_DATA` from
        `systemctl show -p Environment pihole-manager.service`, else
        `<home of pihole-manager>/.config/pihole-manager` from `getent passwd pihole-manager`.
        Confirm it holds `auth.db`, `instances.yml`, and `secrets.yml`.
     2. Complete §4–§6, copying the unit's `Environment=` lines into
        `/etc/pihole-manager/env`.
     3. `sudo systemctl disable --now pihole-manager.service`, then
        `sudo rm /etc/systemd/system/pihole-manager.service` and `sudo systemctl daemon-reload`.
        State why: a unit file in `/etc/systemd/system` overrides the Quadlet's
        generated unit of the same name.
     4. `sudo cp -a <old data dir>/. /var/lib/pihole-manager/` and
        `sudo chown -R 10001:10001 /var/lib/pihole-manager`. Leave the old dir in place.
     5. Run §7's redeploy and curl, then sign in with the existing account. Skip
        `set-password`.
     6. After the next push deploys green, `sudo rm -rf /opt/pihole-manager/.venv`.
   - **§9 Rollback.**
     - Bad image: `sudo podman tag localhost/pihole-manager:previous localhost/pihole-manager:latest`,
       then `sudo systemctl restart pihole-manager.service`.
     - Abandon containers: remove the Quadlet file, restore the old unit and sudoers rule,
       and recreate the venv from the old runbook in git history. The old data dir is
       untouched.
   - **§10 First deploy.** Today's §6 text, updated to three green steps (pull, build and
     restart, confirm-up) and `journalctl -u pihole-manager` for failures.

8. **`README.md`.**
   - Under "Run the application", add a short "Or with containers" block:
     `docker compose up -d --build` (or `podman compose`), then
     `docker compose exec pihole-manager python -m app.auth set-password --username admin`,
     then open `http://127.0.0.1:8420/`. Point production readers to `docs/deploy.md`.
   - In "Authentication", replace the `sudo -u pihole-manager …/.venv/bin/python` command
     with `sudo podman exec -it pihole-manager python -m app.auth set-password --username admin`.
     Reword the sentence above it, because the container user now fixes the data dir.
   - Leave the venv dev and test commands as they are.

9. **`docs/design.md`.**
   - Replace "Deployment shape (systemd, not Docker)" and its unit block with a short
     paragraph and the Quadlet's `[Container]` keys. Point to `docs/deploy.md`.
   - Keep the bind-address paragraph, and note that `Network=host` preserves it.
   - Update the credential-storage paragraph. The fallback file now lives at
     `/var/lib/pihole-manager/secrets.yml` on the host (`/data` in the container).
   - Change the §14 "TODO — distribution" bullet to say the container image and Compose
     example ship, and that a versioned release archive is still open.

10. **Tests.** See the Tests section.

## Contracts

No API, `frontend/src/api.ts`, or DB change.

Deployment contracts, which must agree across files:

| Item | Value | Files that must agree |
| --- | --- | --- |
| Redeploy command | `/usr/local/sbin/pihole-manager-redeploy` | `deploy.yml`, `docs/deploy.md` sudoers line, script header |
| Image name | `localhost/pihole-manager:latest` (rollback `:previous`) | script, Quadlet `Image=`, `compose.yaml` `image:` |
| Systemd unit | `pihole-manager.service` (generated from `pihole-manager.container`) | script, docs |
| Container data path | `/data`, via `PIHOLE_MANAGER_DATA` | `Dockerfile`, Quadlet `Volume=`, `compose.yaml` |
| Host data path | `/var/lib/pihole-manager`, owner `10001:10001`, mode `0700` | Quadlet, docs |
| Env file | `/etc/pihole-manager/env`, root `0600` | Quadlet, docs |
| Listen | `0.0.0.0:8420` in the container; host network in production | `Dockerfile` `CMD`, Quadlet |

Existing env keys (`PIHOLE_MANAGER_*`) are unchanged in name and meaning.

## Tests

`tests/test_supply_chain.py`:
- `test_deploy_lock_install_requires_hashes` → rename to `test_image_lock_install_requires_hashes`.
  It collects lines in `Dockerfile` containing `pip install` and `-r requirements.lock`,
  asserts at least one, and asserts each has `--require-hashes` and `--only-binary=:all:`
  and lacks `--uploaded-prior-to`.
- `test_deploy_workflow_does_not_pip_install` — `.gitea/workflows/deploy.yml` contains no
  `pip install`.
- `test_dockerfile_base_images_pinned_by_digest` — every `FROM` line in `Dockerfile`
  matches `@sha256:[0-9a-f]{64}`.

`tests/test_container.py` (new):
- `test_workflow_runs_redeploy_script_through_sudo` — `deploy.yml` contains the line
  `run: sudo /usr/local/sbin/pihole-manager-redeploy`, and that is the only `sudo` use in
  the workflow.
- `test_sudoers_rule_matches_workflow` — `docs/deploy.md` contains exactly
  `gitea-runner ALL=(root) NOPASSWD: /usr/local/sbin/pihole-manager-redeploy`.
- `test_redeploy_script_is_executable` — `git ls-files -s deploy/pihole-manager-redeploy`
  reports mode `100755`.
- `test_quadlet_never_pulls_or_auto_updates` — `deploy/pihole-manager.container` has
  `Pull=never`, has `Image=localhost/pihole-manager:latest`, and has no `AutoUpdate=`
  key (invariant 4).
- `test_container_runs_as_non_root` — `Dockerfile` has a `USER 10001:10001` line after its
  last `RUN`, and `compose.yaml` and the Quadlet both set a read-only root
  (`read_only: true` / `ReadOnly=true`).

`tests/test_repo_hygiene.py` must pass unchanged over the new files.

Codex has no network or container runtime in its sandbox (see memory). The image build
and smoke run are reviewer acceptance criteria, not Codex tests.

## Invariants at risk

1. **One instance mutated at a time** — not touched. No app code changes.
2. **Diff before write** — not touched.
3. **Gravity gated** — not touched.
4. **No background scheduler** — the Quadlet has no `AutoUpdate=`, healthcheck, or timer,
   and the image has no entrypoint beyond uvicorn. Podman's auto-update timer is never
   enabled. `test_quadlet_never_pulls_or_auto_updates` guards this.
5. **Credentials stay in the backend** — `secrets.yml` and `auth.db` live in the host
   volume, which is root/10001 `0700`. `.dockerignore` keeps `data/`, `.env`, and local
   secret files out of the build context, so no secret enters an image layer. The env file
   is root `0600`.
6. **Outbound via `url_policy`** — not touched. Host networking keeps today's resolution
   path, and loopback destinations stay denied by policy.
7. **Auth middleware on every route** — not touched. The workflow still probes only
   `/api/auth/status`.
8. **Compiled frontend committed** — kept and relied on. The image copies `app/static/ui/`
   and never runs Node. No `frontend/` change.
9. **`main` is production** — kept. A push to `main` still deploys through the same runner
   and label, and the confirm-up probe is unchanged. The cutover is a manual, one-time host
   change described in the runbook. Merging this branch before the cutover would fail the
   deploy at the sudo step, because the redeploy script is not installed yet. So §8 has the
   host check out the pushed feature branch, run the cutover, and only then merge to
   `main`. That merge's deploy is the first one through the new path.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including the new and renamed tests.
- [ ] Diff touches only the Files list. Nothing under `app/` or `frontend/` changes.
- [ ] Reviewer runs `podman build -t localhost/pihole-manager:review .` from the branch,
      and it succeeds.
- [ ] Reviewer runs the image with `--read-only -p 127.0.0.1:18420:8420 -e PIHOLE_MANAGER_COOKIE_SECURE=false`.
      `curl http://127.0.0.1:18420/api/auth/status` returns `200` with `"configured":false`,
      and `curl -I …/app/` returns `200`.
- [ ] In that container, `id` shows uid 10001, and `/data/auth.db` exists with mode `0600`.
- [ ] `podman exec -it … python -m app.auth set-password --username admin` succeeds, and
      signing in through the browser works.
- [ ] `podman run --rm --entrypoint sh <image> -c 'ls -A /opt/pihole-manager'` shows only `app` and
      `requirements.lock`, with no `.git`, `.venv`, `data`, or `frontend`.
- [ ] `docker compose config` (or `podman compose config`) parses `compose.yaml`.
- [ ] `deploy.yml`, the sudoers line in `docs/deploy.md`, and the script path agree
      character for character.
- [ ] `docs/deploy.md` §8 orders the steps so the host checks out the feature branch
      first, removes the old unit before the first redeploy, and merges to `main` last.
