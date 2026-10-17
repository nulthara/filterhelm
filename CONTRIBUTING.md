# Contributing to FilterHelm

Thanks for your interest. Bug reports, compatibility reports, and small focused fixes are
the most useful contributions right now.

## Before you start

- Search existing issues first.
- Open an issue before starting a large change, so we can agree on the approach.
- Report security problems privately. See [SECURITY.md](SECURITY.md).

## How changes land

This GitHub repository mirrors the main development repository, and `main` only moves
forward. The maintainer applies accepted pull requests to the development repository with
your authorship kept, then pushes them here. Your pull request is closed with a link to
the resulting commit.

## Development setup

Requires Python 3.11 or newer and Node.js for frontend work.

```bash
python3 -m venv .venv
./.venv/bin/pip install --require-hashes -r requirements.lock -r requirements-dev.lock
./.venv/bin/pip install --no-deps --no-build-isolation -e .
cd frontend && npm ci
```

Run the backend and the Vite dev server in two terminals:

```bash
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8420
cd frontend && npm run dev
```

## Checks

GitHub CI runs the same backend and frontend checks on every pull request, and fails if
`app/static/ui/` does not match a fresh build.

Run these before opening a pull request:

```bash
./.venv/bin/pytest -q
cd frontend && npm run lint && npm run build && npm test
```

The build writes the compiled frontend to `app/static/ui/`. Commit those files together
with the frontend source, because deployments run without Node.js.

## Rules a change must keep

FilterHelm manages live DNS filtering, so a few rules are fixed. A pull request that breaks
one needs an issue and discussion first.

- Change one instance at a time.
- Show the difference before writing anything.
- Run nothing in the background.
- Keep Pi-hole credentials on the server. They never reach the browser.
- Send every outbound request through `app/url_policy.py`.

The full list is in [ARCHITECT.md](ARCHITECT.md#project-invariants). Coding conventions for
both stacks are in [AGENTS.md](AGENTS.md).

## Pull requests

- Keep each pull request to one change.
- Add or update tests for behavior changes.
- Describe what you tested, including the Pi-hole FTL version if you tested against a
  real instance.
- Do not include real hostnames, IP addresses, or credentials. Use `example.com` or the
  documentation ranges such as `192.0.2.0/24`. The test suite rejects many private
  addresses and hostnames.

## How the maintainer works

The maintainer builds FilterHelm with AI coding agents, using the plan, implement, and
review loop in [ARCHITECT.md](ARCHITECT.md). Contributors do not need to follow that loop.
A clear pull request with tests is enough.

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
