# Upgrading and backups

## What to back up

All FilterHelm state lives in one data directory.

| Install | Data directory |
| --- | --- |
| Compose | the `filterhelm-data` volume, mounted at `/data` |
| Podman Quadlet ([deploy.md](deploy.md)) | `/var/lib/filterhelm` |
| Local virtualenv | `~/.config/filterhelm/`, or `FILTERHELM_DATA` if set |

| File | Contents |
| --- | --- |
| `instances.yml` | Instance names and URLs |
| `secrets.yml` | Pi-hole passwords, when no OS keyring exists |
| `pending-gravity.yml` | Instances waiting for a gravity update |
| `auth.db` | FilterHelm accounts and sessions |
| `audit.log` | Sign-in and change history |

`secrets.yml` holds Pi-hole passwords in plain text. Store backups with the same care as
the passwords themselves.

FilterHelm does not back up Pi-hole. Use the Teleporter download on the Instances page,
or Pi-hole's own Teleporter, for instance backups.

## Back up

Stop the service first, so `auth.db` is not written mid-copy.

Compose:

```bash
docker compose stop
docker run --rm -v filterhelm_filterhelm-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/filterhelm-data.tar.gz -C /data .
docker compose start
```

Compose prefixes the volume name with the project name. Check it with `docker volume ls`.

Podman Quadlet:

```bash
sudo systemctl stop filterhelm.service
sudo tar czf filterhelm-data.tar.gz -C /var/lib/filterhelm .
sudo systemctl start filterhelm.service
```

## Restore

Stop the service, extract the archive into an empty data directory, and start it again.
For the Quadlet install, keep the owner and mode:

```bash
sudo systemctl stop filterhelm.service
sudo tar xzf filterhelm-data.tar.gz -C /var/lib/filterhelm
sudo chown -R 10001:10001 /var/lib/filterhelm
sudo chmod 0700 /var/lib/filterhelm
sudo systemctl start filterhelm.service
```

## Upgrade

1. Read the release notes for the target version.
2. Back up the data directory.
3. Upgrade with the steps for your install.
4. Sign in and check the Instances page.

Compose from a clone:

```bash
git pull
docker compose up -d --build
```

Container image from GHCR:

```bash
docker pull ghcr.io/nulthara/filterhelm:X.Y.Z
```

Then restart the container with the new tag.

Podman Quadlet: pushes to `main` deploy automatically. See [deploy.md](deploy.md).

## Downgrade

Restore the backup taken before the upgrade, then run the older version. A newer version
may change the data files in ways an older version cannot read.

## Version notes

No release so far needs manual upgrade steps.
