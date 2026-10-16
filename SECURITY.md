# Security policy

## Supported versions

Only the latest release receives security fixes.

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

Do not open a public issue for a security problem.

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/nulthara/filterhelm/security/advisories/new).
Include:

- the FilterHelm version or commit
- how FilterHelm is deployed (Compose, container image, or local)
- the authentication mode (local or proxy)
- steps to reproduce, and what an attacker gains

You should get an acknowledgement within 7 days. Fixes are released as a new version, and
the advisory is published once a fixed release exists.

## Scope

In scope:

- authentication and session bypass
- cross-site request forgery past the origin checks
- Pi-hole credentials reaching the browser, logs, or the audit log
- outbound requests that escape `app/url_policy.py`
- changes applied to an instance without the confirmation the UI promises

Out of scope:

- vulnerabilities in Pi-hole itself, which go to the Pi-hole project
- attacks that need root on the FilterHelm host or write access to its data directory
- a deliberate account lockout through repeated failed sign-ins, which is a documented
  trade-off

## Known limitations

On a host without an OS keyring, FilterHelm stores Pi-hole passwords in `secrets.yml`
inside the data directory. File permissions (`0600`) protect that file, but it is not
encrypted. Anyone who can read the data directory or its backups can read the passwords.
