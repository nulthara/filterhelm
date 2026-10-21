# Public documentation site

`site/` is the Astro Starlight project for `https://filterhelm.nulthara.com`.
It builds independently from the app. Cloudflare builds `site/dist/`; never commit
that directory or send it to the app deploy target.

## Local work

Use Node 22, as pinned in `site/.node-version` (the installed Astro version requires
22.22.0 or later).

```bash
cd site
npm ci
npm run dev
npm run build
npm run preview
npm audit
```

`site/.npmrc` requires dependencies to have been public for at least 24 hours.
Commit the lockfile with dependency changes. Run the full backend and hygiene suite
from the repository root before publishing.

```bash
FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest
```

The package override pins `postcss-selector-parser` to `7.1.6` to clear
GHSA-rj75-hqrm-r3gf. Starlight reaches the vulnerable parser through Expressive Code
and `postcss-nested` 6.2.0; no patched parser 6.x exists. Remove the override once
`@expressive-code/core` depends on `postcss-nested` 8 or later, then reinstall,
build, and require `npm audit` to report 0 vulnerabilities.

## Screenshots

Each slot lives in `site/src/assets/screenshots/`. The `Screenshot` component prefers
`<slot>.png` and otherwise uses `<slot>.svg`. Drop in a PNG with the same name to
replace a placeholder without editing code. You can keep the SVG as a fallback.
The build fails if neither file exists.

| Slot | Used on | Should show |
| --- | --- | --- |
| `drift` | Landing hero | Drift page with two instances and a few differing DNS and domain entries |
| `first-run-instances` | Tutorial | Instances page with two healthy instances |
| `add-instance` | Manage instances | Add-instance form filled with demo values, connection test passed |
| `diff-confirm` | Safety model | The confirmation dialog showing the exact change for one instance |
| `audit-log` | Safety model | Audit log page with a few demo entries |
| `local-dns` | Pages reference | Local DNS comparison |
| `lookup` | Pages reference | Lookup verdicts per instance |
| `query` | Pages reference | Merged query log |

Capture every shot at 1600 × 1000, with the same app theme and one demo dataset.
Use `.lan` or `example.com` hostnames and addresses from `192.168.1.0/24`.
Use no real names, credentials, private hosts, or personal data. Check all visible
rows, dialogs, and browser chrome before saving.

Strip all PNG metadata before adding a shot. For example, run one of these commands
on each PNG.

```bash
oxipng --strip all site/src/assets/screenshots/drift.png
exiftool -all= site/src/assets/screenshots/drift.png
```

Remove any backup file created by the metadata tool. The tests reject extra files
in the screenshot directory and PNG chunks `tEXt`, `zTXt`, `iTXt`, `eXIf`, and `tIME`.
Review both themes and the 375 px layout after replacing a shot.

## Cloudflare inputs

The site deploys as a static-only Cloudflare Worker through Workers Builds, connected
to the GitHub mirror. `site/wrangler.jsonc` names the Worker and points it at `dist`.
No Cloudflare token belongs in the repository or on the Gitea runner. Publishing
happens only through `git push github main` after the required denylist pytest has
passed.

| Setting | Value |
| --- | --- |
| Repository | `nulthara/filterhelm` (GitHub) |
| Worker name | `filterhelm-site` (must match `name` in `site/wrangler.jsonc`) |
| Production branch | `main` |
| Root directory | `site` |
| Build command | `npm ci && npm run build` |
| Deploy command | `npx wrangler deploy` (the default) |
| Build variable | `NODE_VERSION=22` |
| Build watch paths | include `site/*` |
| Non-production branch builds | off |

`wrangler.jsonc` sets `not_found_handling` to `404-page`, so unknown paths return
Starlight's `404.html` with a `404` status. Workers static assets read
`site/public/_headers` the same way Pages does.

## Manual setup and publishing

1. Merge on the forge after an Accept verdict.
2. Run `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest`, then `git push github main`.
3. In Cloudflare, open Workers & Pages, create a Worker, and choose to import a
   repository. Authorize the Cloudflare GitHub app for `nulthara/filterhelm` only.
4. Name the Worker `filterhelm-site` and enter the settings above.
5. Leave Web Analytics off.
6. Check the first build on its `*.workers.dev` URL.
7. In the Worker's Settings, open Domains & Routes and add the custom domain
   `filterhelm.nulthara.com`. The zone is in the same account, so Cloudflare creates
   the DNS record and certificate. Do not create the record first.
8. Check `curl -sI https://filterhelm.nulthara.com` returns `200` with the `_headers`
   values, and that `curl -sI https://filterhelm.nulthara.com/no-such-page/` returns `404`.

`site/public/_headers` sets MIME-sniffing, referrer, frame, and permissions headers,
plus immutable caching for `/_astro/*`. There is no strict CSP because Starlight
emits small inline scripts. The site uses self-hosted fonts and Pagefind search,
with no analytics or third-party scripts.

## Generated-output hygiene

Pagefind's vendor bundles in `dist/pagefind/*.js` and the Pagefind UI chunk under
`dist/_astro/` carry third-party translator credits with names and email addresses.
Email or denylist matches in those credits are known false positives, not leaks.
Keep the upstream credits intact; do not strip or patch them.

Scan the HTML pages, `sitemap*.xml`, and site CSS in `dist/` for email addresses
and every `.private-denylist.toml` pattern. Vendor JavaScript bundles are exempt
from that scan. Check separately that the generated site loads no script or
stylesheet from another origin.
