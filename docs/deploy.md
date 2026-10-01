# Deploy runbook — Gitea Actions to a self-hosted target

**Design decision: the runner lives on the deploy host, the deploy target itself.**
Not a separate CI host reaching over SSH — the job runs directly on the box
being deployed to, so a deploy step is just `git reset --hard` + restart the
service, in-place. No SSH keys to manage, no cross-host access to secure.
The `.gitea/workflows/deploy.yml` workflow in this repo assumes exactly this.

---

## 0. What's still unknown

- **The deploy host's IP address** — not yet confirmed to this session. Needed for
  the reverse-proxy target, not for the workflow
  itself (which runs locally on the box regardless of its IP).
- **The deploy host's OS/Python version** — assumed Debian 13 (Trixie) with a
  packaged Python ≥3.11. Confirm with
  `cat /etc/debian_version && python3 --version` before trusting §3 below
  verbatim.
- ~~**act_runner's exact CLI flags**~~ — **verified 2026-08-30.** The
  project renamed itself: `gitea.com/gitea/act_runner` is now
  `gitea.com/gitea/runner`, and the binary is `gitea-runner`, not
  `act_runner` — the original draft of this doc used the old name and its
  download URL 404s. Confirmed by actually downloading and running
  `gitea-runner v3.3.1 --help` / `register --help` / `daemon --help`; the
  commands below match that binary's real output, not a guess.

---

## 1. Confirm Gitea Actions is enabled server-side

On the Gitea host, check `app.ini`'s `[actions]` section:

```bash
grep -A3 '^\[actions\]' /etc/gitea/conf/app.ini
```

Needs `ENABLED = true`. If it's missing or false, add it and restart Gitea
(`systemctl restart gitea` if it runs as a systemd unit — confirm with
`systemctl status gitea` first; adjust if it's actually running in Docker).

A quicker check that doesn't need a shell on the Gitea host at all: open the
`pihole-manager` repo on Gitea's web UI and look for an **Actions** tab. If
it's not there, Actions is disabled server-wide and the config change above
is the fix.

## 2. Register a runner scoped to this repo

In the `pihole-manager` repo: **Settings → Actions → Runners → Create new
Runner**. This gives a registration token and confirms the instance URL to
register against (probably `http://git.example.com:3000` or the LXC's
direct address — either works for registration, but prefer whichever this
network's other internal-to-internal traffic uses).

Repo-scoped deliberately, not instance-wide — this runner does one thing
(deploy this one app to this one box), and scoping it that narrowly means a
compromised or malfunctioning runner can't touch anything else on Gitea.

## 3. Install the runner on the deploy host

**It's called `gitea-runner`, not `act_runner`** — the upstream project
renamed itself (`gitea.com/gitea/act_runner` → `gitea.com/gitea/runner`)
before this doc's first draft, which used the old name/URL and 404'd. The
download below is the real one, confirmed working 2026-08-30 by actually
fetching and running it:

```bash
# Confirm architecture first — use amd64 for an x86 target; a Pi needs
# the matching ARM binary instead.
uname -m

# -f matters: without it, curl saves a 404 error page as if it were the
# binary, which is exactly the failure this replaces — the file downloads
# "successfully" with content-length 10 and the string "Not found" as its
# entire contents, `chmod +x` doesn't care, and only `./the-binary` fails,
# confusingly, as if the *shell* had a typo.
curl -fLo /usr/local/bin/gitea-runner \
  https://gitea.com/gitea/runner/releases/download/v3.3.1/gitea-runner-3.3.1-linux-amd64
chmod +x /usr/local/bin/gitea-runner
/usr/local/bin/gitea-runner --version   # expect: gitea-runner version v3.3.1
```

Pinned to v3.3.1 deliberately (confirmed-working, not a moving "latest"
path — there isn't one; that's what 404'd originally). Check
`https://gitea.com/gitea/runner/releases` for anything newer before
reusing this in six months.

Create a dedicated, unprivileged system user for the runner rather than
running it as root:

```bash
useradd --system --create-home --shell /usr/sbin/nologin gitea-runner
```

(Same name as the binary — a Unix account and an executable can share a
name without conflict; every command below is explicit about which is
which.)

Register it — non-interactively, with the token from §2. Passed via
environment variable rather than `--token` on the command line, so it
doesn't sit in shell history or `ps` output:

```bash
sudo -u gitea-runner \
  GITEA_RUNNER_REGISTRATION_TOKEN=<TOKEN_FROM_STEP_2> \
  /usr/local/bin/gitea-runner register \
  --no-interactive \
  --instance http://git.example.com:3000 \
  --name pihole-manager-runner \
  --labels pihole-manager-deploy:host
```

`:host` is the part that matters: it tells the runner to execute job steps
directly on this OS, not inside a Docker container. Deliberate — the deploy host is
an LXC, nested Docker-in-LXC is its own can of worms, and this deploy is
just shell commands anyway. The label `pihole-manager-deploy` must match
`runs-on:` in `.gitea/workflows/deploy.yml` exactly.

Run it as a systemd service so it survives reboots:

```bash
sudo tee /etc/systemd/system/gitea-runner.service >/dev/null <<'EOF'
[Unit]
Description=Gitea Actions runner (pihole-manager)
After=network.target

[Service]
User=gitea-runner
WorkingDirectory=/home/gitea-runner
ExecStart=/usr/local/bin/gitea-runner daemon
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now gitea-runner.service
systemctl status gitea-runner.service --no-pager
```

Confirm it shows up: repo **Settings → Actions → Runners** should list
`pihole-manager-runner` as online/idle.

## 4. Prepare the host

Production uses rootful Podman in the deploy LXC. On the hypervisor, enable
nesting and restart the container:

```bash
pct set <vmid> --features nesting=1
pct reboot <vmid>
```

On the deploy host, install Podman and confirm the runtime works:

```bash
sudo apt install podman
sudo podman info
```

One-time, by hand — the workflow only ever updates this in place afterward,
it doesn't create it. Create a read-only SSH deploy key for the runner first:

```bash
sudo -u gitea-runner mkdir -p /home/gitea-runner/.ssh
sudo -u gitea-runner chmod 700 /home/gitea-runner/.ssh
sudo -u gitea-runner ssh-keygen \
  -t ed25519 \
  -f /home/gitea-runner/.ssh/id_ed25519 \
  -N '' \
  -C pihole-manager-deploy
sudo cat /home/gitea-runner/.ssh/id_ed25519.pub
```

Add the printed public key in Gitea under **pihole-manager → Settings →
Deploy Keys**, with write access disabled. Gitea's SSH service is on the
non-standard port `2222`, so record that exact endpoint in `known_hosts`:

```bash
sudo -u gitea-runner ssh-keyscan -p 2222 -H git.example.com \
  | sudo -u gitea-runner tee /home/gitea-runner/.ssh/known_hosts >/dev/null
sudo -u gitea-runner chmod 600 /home/gitea-runner/.ssh/known_hosts
```

Then clone using `ssh://` syntax; the scp-style `git@host:path` form cannot
express a custom SSH port:

```bash
sudo mkdir -p /opt/pihole-manager
sudo chown gitea-runner:gitea-runner /opt/pihole-manager
sudo -u gitea-runner git clone ssh://git@git.example.com:2222/example/pihole-manager.git /opt/pihole-manager
```

If the checkout already exists with the old HTTP origin, repair it in place:

```bash
sudo -u gitea-runner git -C /opt/pihole-manager remote set-url \
  origin ssh://git@git.example.com:2222/example/pihole-manager.git
sudo -u gitea-runner git -C /opt/pihole-manager fetch origin main
```

The React/Ant Design production bundle is committed in `app/static/ui`.
Node.js is intentionally not required on the deploy host; frontend builds happen on a
development machine and the generated assets deploy with the repository.

## 5. Install the container units

Install the environment file, data directory, Quadlet, and redeploy script by hand:

```bash
sudo install -d -m 0700 /etc/pihole-manager
sudo install -m 0600 /dev/null /etc/pihole-manager/env   # then edit; see below
sudo install -d -o 10001 -g 10001 -m 0700 /var/lib/pihole-manager
sudo install -o root -g root -m 0644 /opt/pihole-manager/deploy/pihole-manager.container /etc/containers/systemd/
sudo install -o root -g root -m 0755 /opt/pihole-manager/deploy/pihole-manager-redeploy /usr/local/sbin/
sudo systemctl daemon-reload
```

Edit `/etc/pihole-manager/env` as root. Use `KEY=value` lines, without the
systemd unit's `Environment=` prefix:

```ini
PIHOLE_MANAGER_AUTH_MODE=local
PIHOLE_MANAGER_COOKIE_SECURE=true
# Replace manager.example with the public hostname presented by the proxy.
PIHOLE_MANAGER_TRUSTED_HOSTS=manager.example,127.0.0.1
# Optional: exact public hostname, IP, and CIDR entries (private LANs need no entry)
# PIHOLE_MANAGER_PUBLIC_ALLOWLIST=pihole.example.com,198.51.100.1,2001:db8:ab09::3574
```

The environment file is root-owned, mode `0600`. The host data directory is
owned by `10001:10001`, mode `0700`, and mounted at `/data` in the container.
Production uses host networking, preserving real client addresses for proxy
trust, audit logs, and pre-authentication rate limits.

The installed script and Quadlet are root-owned. Edits to `deploy/` in git take
effect only after this installation step is re-run by an administrator. When
reinstalling them, preserve the existing environment file rather than replacing
it with the empty template.

## 6. The one sudoers rule

The runner owns the checkout but receives only this passwordless sudo command:

```bash
sudo visudo -f /etc/sudoers.d/gitea-runner-pihole-manager
```

```text
gitea-runner ALL=(root) NOPASSWD: /usr/local/sbin/pihole-manager-redeploy
```

The workflow calls the script without arguments. The script builds the image
from `/opt/pihole-manager` and restarts `pihole-manager.service`; a failed build
exits before restart, leaving the running container alone. The runner is not
in a container-runtime group: runtime access would be root-equivalent. It cannot
rewrite the installed root-owned script or Quadlet through a pushed repo edit.

## 7. First start

```bash
sudo /usr/local/sbin/pihole-manager-redeploy
sudo podman exec -it pihole-manager python -m app.auth set-password --username admin
curl -fsS http://127.0.0.1:8420/api/auth/status
```

A Quadlet unit is not `systemctl enable`d. Its `[Install]` section is applied by
the generator, producing `pihole-manager.service` for boot and normal service
management. Podman auto-update is not enabled; deployments are initiated by
pushes to `main`.

## 8. Cutover from the systemd install

For a host already running the venv unit, perform this manual cutover before
merging the container branch to `main`:

0. Put the new files on the host first. With the feature branch pushed, run:

   ```bash
   sudo -u gitea-runner git -C /opt/pihole-manager fetch origin feature/0025-container-deploy
   sudo -u gitea-runner git -C /opt/pihole-manager checkout --detach FETCH_HEAD
   ```

   The next deploy run resets the checkout to `origin/main`.

1. Find the current data directory. Read the unit's `PIHOLE_MANAGER_DATA` from:

   ```bash
   systemctl show -p Environment pihole-manager.service
   getent passwd pihole-manager
   ```

   If the unit does not set it, use `<home of pihole-manager>/.config/pihole-manager`.
   Confirm it holds `auth.db`, `instances.yml`, and `secrets.yml`. Save the old
   unit and sudoers rule for rollback.

2. Complete §4–§6, copying the old unit's `Environment=` values into
   `/etc/pihole-manager/env` as `KEY=value` lines.

3. Stop and remove the old unit:

   ```bash
   sudo systemctl disable --now pihole-manager.service
   sudo rm /etc/systemd/system/pihole-manager.service
   sudo systemctl daemon-reload
   ```

   A unit file in `/etc/systemd/system` overrides the Quadlet's generated unit
   of the same name, so it must be removed before the first redeploy.

4. Copy the data, leaving the old directory in place:

   ```bash
   sudo cp -a <old data dir>/. /var/lib/pihole-manager/
   sudo chown -R 10001:10001 /var/lib/pihole-manager
   ```

5. Run §7's redeploy command and curl probe, then sign in with the existing
   account. Skip `set-password`.

6. Merge to `main` only after the cutover succeeds. After the next push deploys
   green, remove the old venv:

   ```bash
   sudo rm -rf /opt/pihole-manager/.venv
   ```

## 9. Rollback

For a bad image, restore the previous image tag and restart:

```bash
sudo podman tag localhost/pihole-manager:previous localhost/pihole-manager:latest
sudo systemctl restart pihole-manager.service
```

The redeploy script retains the prior image as `:previous` before each build.

To abandon containers, stop the service, remove
`/etc/containers/systemd/pihole-manager.container`, restore the saved old unit
and sudoers rule, and recreate the venv from the old runbook in git history.
Reload systemd and enable/start the restored service. The old data directory
is untouched by cutover; changes made after cutover are in the container's
host data directory.

## 10. First deploy

Push any commit to `main` (or re-push the current HEAD) and watch the
**Actions** tab on the `pihole-manager` repo. Expect three green steps: pull,
build and restart, confirm-up. If "Confirm it came back up" fails, the
service didn't come back within ~20s — check
`journalctl -u pihole-manager -n 50 --no-pager` before assuming the workflow
itself is wrong.

Once green, confirm from the LAN (not just `127.0.0.1` on the box itself):

```bash
curl -sI http://192.0.2.20:8420/app/ | head -1
```
