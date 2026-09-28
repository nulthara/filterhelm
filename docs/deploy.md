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

## 4. Bootstrap the deploy target

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
cd /opt/pihole-manager
sudo -u gitea-runner python3 -m venv .venv
sudo -u gitea-runner ./.venv/bin/pip install --require-hashes --uploaded-prior-to P1D -r requirements.lock
sudo -u gitea-runner ./.venv/bin/pip install --no-deps --no-build-isolation -e .
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

Create the app's own service user (separate from `gitea-runner` —
principle of least privilege again: the thing that runs untrusted-ish CI
steps and the thing that runs the app itself don't need to be the same
account):

```bash
sudo useradd --system --shell /usr/sbin/nologin pihole-manager
```

Then the systemd unit from `docs/design.md` §12, adjusted for the real
paths and users decided here:

```bash
sudo tee /etc/systemd/system/pihole-manager.service >/dev/null <<'EOF'
[Unit]
Description=Pi-hole Manager

[Service]
WorkingDirectory=/opt/pihole-manager
ExecStart=/opt/pihole-manager/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8420
Environment=PIHOLE_MANAGER_AUTH_MODE=local
Environment=PIHOLE_MANAGER_COOKIE_SECURE=true
# Replace manager.example with the public hostname presented by the proxy.
Environment=PIHOLE_MANAGER_TRUSTED_HOSTS=manager.example,127.0.0.1
# Optional: exact public hostname, IP, and CIDR entries (private LANs need no entry)
# Environment=PIHOLE_MANAGER_PUBLIC_ALLOWLIST=pihole.example.com,198.51.100.1,2001:db8:ab09::3574
Restart=on-failure
User=pihole-manager

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo -u pihole-manager /opt/pihole-manager/.venv/bin/python -m app.auth set-password --username admin
sudo systemctl enable --now pihole-manager.service
curl -fsS http://127.0.0.1:8420/api/auth/status
```

## 5. The one sudoers rule the workflow needs

`gitea-runner` owns the checkout and can `pip install` freely (it owns
`/opt/pihole-manager`), but restarting the app runs as `pihole-manager`, a
different account — so `gitea-runner` needs exactly one narrow,
passwordless sudo rule, nothing broader:

```bash
sudo visudo -f /etc/sudoers.d/gitea-runner-pihole-manager
```
```
gitea-runner ALL=(root) NOPASSWD: /usr/bin/systemctl restart pihole-manager.service
```

Deliberately scoped to that one exact command — not `ALL`, not a wildcard
`systemctl restart *` that would let a compromised runner restart anything
else on the box.

## 6. First deploy

Push any commit to `main` (or re-push the current HEAD) and watch the
**Actions** tab on the `pihole-manager` repo. Expect four green steps: pull,
install, restart, confirm-up. If "Confirm it came back up" fails, the
service didn't come back within ~20s — check
`journalctl -u pihole-manager -n 50 --no-pager` before assuming the workflow
itself is wrong.

Once green, confirm from the LAN (not just `127.0.0.1` on the box itself):

```bash
curl -sI http://<deploy-host-ip>:8420/app/ | head -1
```
