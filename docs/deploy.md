# Deploy runbook

This runbook sets up continuous deployment with Gitea Actions. Every push to `main`
rebuilds the FilterHelm container on the deploy host and restarts it. A failed health
check rolls the deploy back automatically.

To run FilterHelm without CI, use the Compose setup in the [README](../README.md#run-in-a-container-from-a-clone),
or follow §3–§6 and run the redeploy script by hand.

---

## How a deploy works

The Gitea Actions runner runs on the deploy host itself, not on a separate CI machine. A
deploy is a local `git reset --hard` followed by a rebuild and restart, so no SSH keys or
cross-host access are needed for the deploy steps.

On each push to `main`, [.gitea/workflows/deploy.yml](../.gitea/workflows/deploy.yml):

1. Fetches `main` into `/opt/filterhelm` and resets the checkout to it.
2. Runs `sudo /usr/local/sbin/filterhelm-redeploy`, the runner's only sudo permission.
3. Checks that `/api/auth/status` answers within about 20 seconds.

The redeploy script:

- Builds the image from `/opt/filterhelm` and tags it `localhost/filterhelm:latest`.
- Restarts `filterhelm.service` and probes `/api/auth/status`.
- Tags the image `:good` if the probe passes.
- Restores `:good` and exits non-zero if the restart or probe fails.
- Exits before restarting if the build fails, leaving the running container alone.

## Requirements

- A Gitea server with Actions available.
- A Linux deploy host with systemd and Podman 4.4 or newer, for Quadlet support.
- `git` and `curl` on the deploy host.

Node.js and Python are not needed on the host. The compiled frontend is committed in
`app/static/ui`, and Python runs inside the container.

---

## 1. Enable Gitea Actions

Open the repository in Gitea's web UI. If it has an **Actions** tab, Actions is enabled and
you can skip to §2.

Otherwise, set `ENABLED = true` in the `[actions]` section of Gitea's `app.ini` and restart
Gitea. To check the current value on the Gitea host:

```bash
grep -A3 '^\[actions\]' /etc/gitea/conf/app.ini
```

The path to `app.ini` varies by install.

## 2. Install and register the runner

In the repository, open **Settings → Actions → Runners → Create new Runner**. Copy the
registration token. Scope the runner to this repository rather than the whole Gitea
instance, so a compromised or broken runner cannot touch other repositories.

On the deploy host, download the runner. The binary is `gitea-runner`. Its older name was
`act_runner`, and download URLs that use that name no longer work.

```bash
uname -m   # use the amd64 build on x86_64 and the matching ARM build on ARM
curl -fLo /usr/local/bin/gitea-runner \
  https://gitea.com/gitea/runner/releases/download/v3.3.1/gitea-runner-3.3.1-linux-amd64
chmod +x /usr/local/bin/gitea-runner
/usr/local/bin/gitea-runner --version   # expect: gitea-runner version v3.3.1
```

Keep `-f` on `curl`. Without it, a 404 saves an error page as the binary, and the failure
only shows up later as a confusing exec error. Check the
[runner releases](https://gitea.com/gitea/runner/releases) for a newer version.

Create an unprivileged system user for the runner:

```bash
useradd --system --create-home --shell /usr/sbin/nologin gitea-runner
```

Register the runner. Passing the token through an environment variable keeps it out of
shell history and `ps` output. Replace `https://gitea.example.com` with your Gitea URL.

```bash
sudo -u gitea-runner \
  GITEA_RUNNER_REGISTRATION_TOKEN=<token> \
  /usr/local/bin/gitea-runner register \
  --no-interactive \
  --instance https://gitea.example.com \
  --name filterhelm-runner \
  --labels filterhelm-deploy:host
```

- `filterhelm-deploy` must match `runs-on:` in the workflow exactly.
- `:host` makes job steps run directly on the host, not inside a container. The deploy
  steps are plain shell commands, so they need no container.

Run the runner as a systemd service:

```bash
sudo tee /etc/systemd/system/gitea-runner.service >/dev/null <<'EOF'
[Unit]
Description=Gitea Actions runner (filterhelm)
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

**Settings → Actions → Runners** should now list `filterhelm-runner` as idle.

## 3. Clone the repository

Install Podman and confirm it works:

```bash
sudo apt install podman   # or your distribution's equivalent
sudo podman info
```

Create a read-only SSH deploy key for the runner:

```bash
sudo -u gitea-runner mkdir -p /home/gitea-runner/.ssh
sudo -u gitea-runner chmod 700 /home/gitea-runner/.ssh
sudo -u gitea-runner ssh-keygen \
  -t ed25519 \
  -f /home/gitea-runner/.ssh/id_ed25519 \
  -N '' \
  -C filterhelm-deploy
sudo cat /home/gitea-runner/.ssh/id_ed25519.pub
```

Add the printed public key under the repository's **Settings → Deploy Keys**, with write
access turned off.

Record Gitea's SSH host key. Replace `<port>` with Gitea's SSH port, which is 22 unless
your server changes it:

```bash
sudo -u gitea-runner ssh-keyscan -p <port> -H gitea.example.com \
  | sudo -u gitea-runner tee /home/gitea-runner/.ssh/known_hosts >/dev/null
sudo -u gitea-runner chmod 600 /home/gitea-runner/.ssh/known_hosts
```

Clone into `/opt/filterhelm`. Use the `ssh://` form, because the shorter `git@host:path`
form cannot specify a port:

```bash
sudo mkdir -p /opt/filterhelm
sudo chown gitea-runner:gitea-runner /opt/filterhelm
sudo -u gitea-runner git clone \
  ssh://git@gitea.example.com:<port>/<owner>/filterhelm.git /opt/filterhelm
```

The workflow only updates this checkout. It never creates it.

## 4. Install the container units

Install the environment file, data directory, Quadlet, and redeploy script:

```bash
sudo install -d -m 0700 /etc/filterhelm
sudo install -m 0600 /dev/null /etc/filterhelm/env
sudo install -d -o 10001 -g 10001 -m 0700 /var/lib/filterhelm
sudo install -o root -g root -m 0644 /opt/filterhelm/deploy/filterhelm.container /etc/containers/systemd/
sudo install -o root -g root -m 0755 /opt/filterhelm/deploy/filterhelm-redeploy /usr/local/sbin/
sudo systemctl daemon-reload
```

Edit `/etc/filterhelm/env` as root. Use plain `KEY=value` lines, without systemd's
`Environment=` prefix:

```ini
FILTERHELM_AUTH_MODE=local
FILTERHELM_COOKIE_SECURE=true
# Replace manager.example with the hostname your reverse proxy serves.
FILTERHELM_TRUSTED_HOSTS=manager.example,127.0.0.1
# Optional. Only needed for Pi-hole instances on public addresses.
# FILTERHELM_PUBLIC_ALLOWLIST=pihole.example.com,198.51.100.1,2001:db8:ab09::3574
```

Do not set `FILTERHELM_DATA` here. The Quadlet sets it to `/data`, and the Quadlet value
wins.

| Item | Owner | Mode | Notes |
| --- | --- | --- | --- |
| `/etc/filterhelm/env` | root | `0600` | Environment for the container |
| `/var/lib/filterhelm` | `10001:10001` | `0700` | Mounted at `/data` in the container |
| `/etc/containers/systemd/filterhelm.container` | root | `0644` | Quadlet unit |
| `/usr/local/sbin/filterhelm-redeploy` | root | `0755` | Build, restart, and rollback script |

The container uses host networking, so FilterHelm sees real client addresses. Proxy trust,
the audit log, and pre-sign-in rate limits all depend on them.

The installed Quadlet and script are copies. Changes to `deploy/` in git take effect only
after an administrator re-runs the two `install` lines for them. Do not re-run the
`/etc/filterhelm/env` line, because it replaces the file with an empty one.

## 5. Allow the redeploy command

The runner owns the checkout but gets exactly one passwordless sudo command. Create the
rule:

```bash
sudo visudo -f /etc/sudoers.d/gitea-runner-filterhelm
```

```text
gitea-runner ALL=(root) NOPASSWD: /usr/local/sbin/filterhelm-redeploy
```

The workflow calls the script with no arguments. The runner is not in any container-runtime
group, because access to the container runtime is equivalent to root. A pushed commit
cannot change the installed root-owned script or Quadlet.

## 6. First start

```bash
sudo /usr/local/sbin/filterhelm-redeploy
sudo podman exec -it filterhelm python -m app.auth set-password --username admin
curl -fsS http://127.0.0.1:8420/api/auth/status
```

Quadlet units are not enabled with `systemctl enable`. The Quadlet generator reads the
unit's `[Install]` section and creates `filterhelm.service`, which starts at boot and
works with normal `systemctl` commands. Podman auto-update is off, so only pushes to `main`
start a deploy.

## 7. First deploy

Push a commit to `main` and watch the repository's **Actions** tab. Expect three green
steps:

1. Pull latest main
2. Build image and restart
3. Confirm it came back up

If the last step fails, the service did not answer within about 20 seconds. Check the
service log before suspecting the workflow:

```bash
journalctl -u filterhelm -n 50 --no-pager
```

Then confirm FilterHelm answers from another machine on the network, not only from the
host itself:

```bash
curl -sI http://<deploy-host>:8420/app/ | head -1
```

Put a TLS-terminating reverse proxy in front before regular use, because session cookies
require HTTPS. See the README's [Security](../README.md#security) section.

## 8. Rollback

A failed restart or probe restores `:good` automatically, if it exists, and the deploy run
fails. Failed deploys and retries never overwrite `:good`.

To restore the last good image by hand:

```bash
sudo podman tag localhost/filterhelm:good localhost/filterhelm:latest
sudo systemctl restart filterhelm.service
```

An image becomes `:good` only after passing the probe. If a deployed image passes the
probe but misbehaves, revert the commit on `main` and push.

## Releasing

Bump `app/__init__.py`, `frontend/package.json` and both root version fields in
`frontend/package-lock.json` on a feature branch. The script edits all four and refuses a
version that is not higher than the current one:

```bash
./.venv/bin/python scripts/bump_version.py patch   # or minor, major, X.Y.Z
```

After merge, run the required hygiene checks and push main to the GitHub mirror:

```bash
FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest
git push github main
```

Tag the merged commit with the matching version and push only that tag:

```bash
git tag -a vX.Y.Z -m "FilterHelm X.Y.Z"
git push github vX.Y.Z
```

The publish workflow checks that the tag matches `app.__version__` before building.
The image lands at `ghcr.io/nulthara/filterhelm` with tags `X.Y.Z`, `X.Y`, `X` and
`latest`, for `amd64` and `arm64`.

The workflow signs build provenance and an SPDX SBOM describing the `amd64` image,
then pushes both attestations to GHCR. Check the release with:

```bash
gh attestation verify oci://ghcr.io/nulthara/filterhelm:X.Y.Z --owner nulthara
gh attestation verify oci://ghcr.io/nulthara/filterhelm:X.Y.Z --owner nulthara \
  --predicate-type https://spdx.dev/Document/v2.3
```
