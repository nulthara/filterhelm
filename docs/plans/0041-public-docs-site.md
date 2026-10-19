# 0041 — Public documentation and landing site

Status: in-progress      <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0041-public-docs-site
Author: Kyrnveil
Date: 2026-10-18

## Problem

FilterHelm has a public GitHub mirror and a published image, but its only public
documentation is a 380-line README and a few runbooks in `docs/`. A newcomer has no place
to see what the app does, how to install it, or why it is safe to point at live Pi-hole
instances. A small static site at `filterhelm.nulthara.com` gives the project a front door
and keeps the README as the reference. The site must look made for FilterHelm rather than
like a stock template. It must not leak private infrastructure, add a secret to the Gitea
runner, or disturb the app's build or deploy.

## Scope

In:
- A new top-level `site/` directory holding an Astro Starlight project
- A landing page and five pages (one tutorial, two guides, one reference, one explanation)
- A brand theme in CSS, three component overrides (`Hero`, `PageTitle`, `Footer`), one
  custom component (`Screenshot`), and code-block styling
- Placeholder images for eight screenshot slots, which the user replaces later
- Cloudflare delivery through Git integration with the GitHub mirror, with build settings
  and manual dashboard steps in `docs/site.md`
- Hygiene coverage for `site/`, including a guard against metadata in screenshot PNGs
- A `site` job in `.github/workflows/ci.yml`
- `paths-ignore` for `site/**` in the Gitea deploy workflow
- Ignore rules for `site/node_modules/`, `site/dist/`, and `site/.astro/`
- A link to the site from the README

Out:
- Any change to `app/`, `frontend/`, `app/static/ui/`, or the Dockerfile
- Taking the real screenshots. The user supplies them.
- A custom `src/pages/index.astro` landing page. A later plan may add one.
- Overrides of `Sidebar`, `Search`, `PageFrame`, `Header`, or `TableOfContents`
- A Cloudflare API token, `wrangler` call, or committed Wrangler config
- Analytics, cookies, third-party scripts, or remote fonts
- Search beyond Starlight's built-in Pagefind index
- Versioned docs, a blog, translations, or a changelog page
- Copying `docs/design.md`, `docs/deploy.md`, or `docs/upgrade-and-backup.md` into the
  site. The site links to them on GitHub.
- Creating the Cloudflare project or DNS record. The user does that by hand.

## Approach

### Design brief

| Item | Decision |
| --- | --- |
| Audience | Home and small-office admins running two or more Pi-hole instances |
| First job | Install FilterHelm, add two instances, and see where they differ |
| Landing claim | FilterHelm shows the difference before it changes anything |
| Evidence | A real Drift page screenshot as the hero |
| Voice | The README's voice. Second person, short declaratives, calm, precise. No hype and no security guarantees (brand guidelines). |
| Liked | Docker docs (intent-based routing on the homepage), Django start page (preview of what the tutorial builds, heading-paragraph-button sections, "Read more" links), Homepage (the product shown up front), GitLab Resources (journey-grouped links, one card template, a help ladder at the end) |
| Disliked | SAP (fragmented, deep, clunky navigation) and .NET (dense, reference-first, unfriendly) |
| Effort | CSS plus component overrides |
| Constraints | No third-party requests, self-hosted fonts, light and dark themes, WCAG AA |

The brief's lessons are few pages, a flat sidebar, routing by reader intent, the product
shown before any claim, and every concept linking to depth instead of inlining it.

### Content map (Diátaxis)

| Sidebar group | Page | Type | Purpose |
| --- | --- | --- | --- |
| Start here | `start/first-run.mdx` | Tutorial | Compose install, sign in, add two instances, open Drift |
| Guides | `guides/install-options.mdx` | How-to | `docker run`, Compose, from source, verifying the image, HTTPS notes |
| Guides | `guides/manage-instances.mdx` | How-to | App passwords, fields, connection test, public allowlist, removal |
| Reference | `reference/pages.mdx` | Reference | Every app page and what it does; Pi-hole compatibility |
| Understand | `understand/safety-model.mdx` | Explanation | Why FilterHelm works one instance at a time, and the limits of that |

Group names describe what the reader wants to do. The sidebar stays one level deep, which
answers the SAP dislike. The tutorial is one page so the first run never crosses pages.

### Landing page

The landing page uses `template: splash` with an overridden `Hero`. Sections in order:

1. **Hero.** On desktop the text is in the left column and the Drift screenshot in the
   right; on mobile they stack, text first. The text holds the tagline "Multiple instances.
   One control plane." as the h1, one sentence saying what FilterHelm is, and two actions
   ("Get started" to the tutorial, "View on GitHub"). A caption under the screenshot states
   the claim with its mechanism. Its text comes from `docs/design.md` and plans 0020 and
   0021, not invented.
2. **How a change happens.** An ordered list of three steps: compare on Drift, follow the
   row's "Fix in" link to the page that owns the entry (plan 0021), and confirm the change
   for one instance in its dialog. Each step is a full sentence.
   It describes the real flow, so it is not a row of adjective cards.
3. **Choose your path.** Four `LinkCard`s, one per sidebar group, each with a title and
   one-line description. They have no icons. This follows the Docker and GitLab routing
   pattern.
4. **What you need.** A short fact list: Pi-hole FTL 6.x, Docker or Podman (or Python
   3.11+), `amd64` or `arm64`.
5. **Get help.** An ordered list from GitHub issues, to private security reporting, to
   contributing, following the GitLab help ladder.

### Visual direction

- **Signature element.** The framed Drift screenshot in the hero. It is the one thing no
  other project could show, and it is evidence for the landing claim. Nothing else on the
  site competes with it.
- **Surfaces.** Dark theme grays tint toward brand navy `#0B1F3B` instead of Starlight's
  neutral gray. Light theme uses white content on ice `#E6F4FF` side surfaces.
- **Accent.** Brand blue `#1E88FF` in dark. A darker derived blue in light, because
  `#1E88FF` on white is below 4.5:1. Cyan `#00D6FF` appears in one place only, the hero
  frame's top edge in dark theme.
- **Type.** Inter (self-hosted from `@fontsource-variable/inter`, same as the app) for
  text and headings, using the brand scale (36 px landing h1, 28 px page h1, 16 px body).
  The system monospace stack is used for code.
- **Shape.** Brand radius tokens (6 px, 10 px). No drop shadows, gradients, or glow.
- **Code blocks.** Expressive Code with style overrides on navy surfaces. Shell commands
  use the terminal frame.
- **Orientation.** The `PageTitle` override shows the page type ("Tutorial", "Guide",
  "Reference", "Explanation") as an eyebrow above each h1, so readers know what kind of
  page they are on.
- **Footer.** The `Footer` override keeps Starlight's pagination and adds one line on every
  page. That line holds the non-affiliation notice, the MIT license, and the source link.

### Screenshots as placeholders

The `Screenshot` component takes a slot name. It loads `site/src/assets/screenshots/<slot>.png`
when that file exists and falls back to `<slot>.svg` otherwise. The plan commits one SVG
placeholder per slot. Each placeholder is a 16:10 box labeled with the slot name and what
the shot should show. The user later drops in PNGs with the same names, and no code
changes. A build fails if a slot has neither file.

| Slot | Used on | Should show |
| --- | --- | --- |
| `drift` | Landing hero | Drift page with three instances and a few differing DNS, block-list, and domain entries |
| `first-run-instances` | Tutorial | Instances page with two healthy instances |
| `add-instance` | Manage instances | Add-instance form filled with demo values, connection test passed |
| `diff-confirm` | Safety model | The confirmation dialog showing the exact change for one instance |
| `audit-log` | Safety model | Audit log page with a few demo entries |
| `local-dns` | Pages reference | Local DNS comparison |
| `lookup` | Pages reference | Lookup verdicts per instance |
| `query` | Pages reference | Merged query log |

Shot rules go in `docs/site.md`. Every shot uses the same window size (1600 × 1000),
the same app theme, and one demo dataset. Hostnames come from `.lan` or `example.com`,
addresses from `192.168.1.0/24`, and there are no real names. PNGs carry no text
metadata (see Tests).

### Delivery

- **Built by Cloudflare, not committed.** Invariant 8 exists because the deploy target has
  no Node.js. The site never reaches that target. `site/dist/` stays untracked, and
  `ARCHITECT.md` records the carve-out.
- **Triggered by the GitHub mirror.** The mirror receives `main` only after the denylist
  pytest passes, so every published page has cleared the hygiene gate. A
  `wrangler pages deploy` step on the Gitea runner would skip that gate and need a token.
  Rejected.
- **Pages or Workers.** Both take the same four inputs (see the inputs table). A Workers
  project also needs a `wrangler.jsonc`. A Pages project would read that file too, so
  none is committed. `docs/site.md` carries the exact file as a contingency.
- **Security headers.** `site/public/_headers` sets `X-Content-Type-Options`,
  `Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`, and long caching for
  `/_astro/*`. There is no strict CSP, because Starlight emits small inline scripts.

### Rejected

- **Stock splash hero with feature cards.** It is the generic default the brief rules out.
- **A custom `index.astro` landing.** It costs more than the agreed effort level.
- **Separate install and add-instance tutorial pages.** That splits the first run across
  pages, the SAP failure mode.
- **Reusing the README screenshots.** They mix sizes and data. Fresh shots replace them.
- **Symlinked or build-copied brand assets.** The hygiene scan treats symlinks specially,
  and a copy step breaks a `site/`-only checkout. The plan uses committed copies with
  equality tests, following `tests/test_branding.py`.

## Files

Created:
- `site/package.json` — private `filterhelm-site`; scripts `dev`, `build`, `preview`; `engines.node`; deps `astro`, `@astrojs/starlight`, `@fontsource-variable/inter`
- `site/package-lock.json`
- `site/.npmrc` — `min-release-age=1`
- `site/.node-version` — `22`
- `site/astro.config.mjs` — site origin, Starlight options, sidebar, overrides, Expressive Code
- `site/tsconfig.json`
- `site/src/content.config.ts` — docs collection with the extended frontmatter schema
- `site/src/content/docs/index.mdx` — landing
- `site/src/content/docs/start/first-run.mdx`
- `site/src/content/docs/guides/install-options.mdx`
- `site/src/content/docs/guides/manage-instances.mdx`
- `site/src/content/docs/reference/pages.mdx`
- `site/src/content/docs/understand/safety-model.mdx`
- `site/src/components/Hero.astro` — `Hero` override
- `site/src/components/PageTitle.astro` — `PageTitle` override
- `site/src/components/Footer.astro` — `Footer` override
- `site/src/components/Screenshot.astro` — slot-based screenshot figure
- `site/src/styles/theme.css` — brand mapping, fonts, both themes
- `site/src/assets/filterhelm-horizontal-light.svg`, `site/src/assets/filterhelm-horizontal-dark.svg` — brand logo copies
- `site/src/assets/screenshots/<slot>.svg` — eight placeholders
- `site/public/favicon.svg`, `site/public/favicon-32.png` — brand icon copies
- `site/public/_headers`, `site/public/robots.txt`
- `docs/site.md` — maintainer runbook, screenshot rules, Cloudflare settings, manual steps, Workers contingency
- `tests/test_site.py`

Modified:
- `.gitignore`, `.dockerignore`
- `.github/workflows/ci.yml` — `site` job
- `.gitea/workflows/deploy.yml` — `paths-ignore: ["site/**"]`
- `tests/test_repo_hygiene.py` — `nulthara.com` and four lockfile funding hosts in `ALLOWED_DOMAINS`
- `tests/test_supply_chain.py` — `.npmrc` age gate parametrized over both packages
- `tests/test_branding.py` — site logo and favicon copies match the brand kit
- `README.md` — one line linking to the site
- `AGENTS.md`, `ARCHITECT.md`, `CLAUDE.md` — `site/` in repo shape, dependency rules, Definition of done, invariant 8 carve-out

## Steps

The Codex sandbox needs network access for `npm install`. If it has none, stop and report.

1. **Ignore rules and Node pin.** Edit `.gitignore` and `.dockerignore`. Create
   `site/.node-version` and `site/.npmrc`.
2. **Scaffold.** Install `astro`, `@astrojs/starlight`, and `@fontsource-variable/inter` at
   the latest releases public for at least 24 hours. Lay out the config files the way the
   Starlight template for the installed version does. Commit no template demo pages,
   `.vscode/`, Houston art, or boilerplate README. Add
   `"overrides": { "postcss-selector-parser": "7.1.6" }` to `site/package.json` and
   reinstall. This clears GHSA-rj75-hqrm-r3gf, which reaches the site through Starlight,
   Expressive Code, and `postcss-nested` 6.2.0. No patched 6.x exists, and the
   architect's probe showed byte-identical build output with the override. Run
   `npm audit`. It must report 0 vulnerabilities. Any other advisory that needs a major
   bump is a stop and report.
3. **Schema.** In `content.config.ts`, extend `docsSchema` with
   `type: z.enum(["tutorial", "guide", "reference", "explanation"]).optional()` and
   `heroScreenshot: z.string().optional()`.
4. **Config.** In `astro.config.mjs` set the following.
   - `site: "https://filterhelm.nulthara.com"`
   - `title: "FilterHelm"` and a `description` from the landing sentence
   - `logo` with `light` and `dark` SVG copies and `replacesTitle: true`
   - `favicon: "/favicon.svg"`
   - one `social` GitHub link to `https://github.com/nulthara/filterhelm`
   - `customCss: ["@fontsource-variable/inter", "./src/styles/theme.css"]`
   - `components` mapping `Hero`, `PageTitle`, and `Footer` to the overrides
   - `sidebar` with the four groups from the content map, one level deep
   - `expressiveCode` themes and `styleOverrides` per the visual direction
   - `lastUpdated` off and `editLink` unset
5. **Assets.** Copy the logos and favicon files byte-for-byte. Create the eight placeholder
   SVGs. Each contains only the slot name and its "Should show" text, has a 1600 × 1000
   viewBox, and uses brand colors. Add `_headers` and `robots.txt`.
6. **Theme.** Write `theme.css`. Dark values go on `:root` and light values under
   `:root[data-theme='light']`. Set `--sl-color-accent-low`, `--sl-color-accent`,
   `--sl-color-accent-high`, `--sl-color-white`, `--sl-color-gray-1` through `-6`,
   `--sl-color-black`, `--sl-font`, and `--sl-font-mono`, plus heading sizes from the
   brand scale. Add the classes the components use (`.fh-hero`, `.fh-shot`, `.fh-eyebrow`,
   `.fh-footer-note`). Measure contrast for body text, links, the active sidebar item,
   buttons, the eyebrow, captions, inline code, and code-block text in both themes. Every
   text pair meets 4.5:1. Report the ratios.
7. **Components.**
   - `Screenshot.astro` takes props `name`, `alt`, and optional `caption`. It resolves the
     file through `import.meta.glob("../assets/screenshots/*.{png,svg}", { eager: true })`,
     preferring PNG. It renders `<figure class="fh-shot">` with Astro's `<Image>` and an
     optional `<figcaption>`. A missing slot throws with the slot name.
   - `Hero.astro` reads `hero` and `heroScreenshot` from `Astro.locals.starlightRoute.entry.data`.
     It renders the two-column layout with Starlight's `LinkButton` for actions and
     `Screenshot` for the image. It falls back to the built-in `Hero` when
     `heroScreenshot` is unset.
   - `PageTitle.astro` renders `<p class="fh-eyebrow">` with the capitalized `type`
     when set, then the built-in `PageTitle` with props spread. The h1 keeps `id="_top"`.
   - `Footer.astro` renders the built-in `Footer` with props spread, then
     `<p class="fh-footer-note">` holding the exact non-affiliation sentence, "Released
     under the MIT License.", and a "Source on GitHub" link.
8. **Pages.** Write the landing and five pages per the content map and landing sections.
   Every page except the landing sets `type`. Rules:
   - Source every command, variable, and number from the README, `compose.ghcr.yaml`,
     `docs/design.md`, and the plans named above. Invent none.
   - Use second person and short declaratives. Do not use "blazing", "seamless",
     "effortless", "powerful", "robust", "supercharge", "unlock", or "in minutes".
   - End each concept that has a deeper document with a "Read more" link to it on GitHub.
   - Use `Steps` for the tutorial and `Tabs` for install variants.
   - Use no emoji, no email address, no `mailto:`, no private hostname, and no IP outside
     the README's documentation examples.
   - Place each `Screenshot` per the slot table, with descriptive alt text.
   - The non-affiliation sentence is "FilterHelm is an independent, third-party project.
     It is not affiliated with or endorsed by Pi-hole."
9. **Maintainer doc.** Write `docs/site.md` covering local commands, the
   `postcss-selector-parser` override (why it exists, and that it goes once
   `@expressive-code/core` depends on `postcss-nested` 8 or later), the screenshot slot
   table and shot rules (including stripping PNG metadata, for example with
   `oxipng --strip all` or `exiftool -all=`), the Cloudflare inputs table, the manual
   steps, the Workers contingency `wrangler.jsonc`, and the reminder that publishing
   happens only through `git push github main` after the denylist pytest. It also
   records that Pagefind's vendor bundles (`dist/pagefind/*.js` and the Pagefind UI chunk
   under `dist/_astro/`) carry third-party translator credits with names and emails. A
   `dist/` scan that hits them is a known false positive, not a leak.
10. **Workflows.** Add the CI `site` job modeled on `frontend`, with the same pinned
    action SHAs, `node-version-file: site/.node-version`, `npm ci`, and `npm run build`.
    Add `paths-ignore: ["site/**"]` to `deploy.yml`, keeping exactly one `sudo `.
11. **Hygiene.** Add `nulthara.com` to `ALLOWED_DOMAINS`, plus the four funding hosts
    from `site/package-lock.json`: `paulmillr.com`, `patreon.com`, `locize.com`, and
    `i18next.com`. These follow the existing `opencollective.com` and `tidelift.com`
    entries. Run the hygiene tests with `FILTERHELM_REQUIRE_DENYLIST=1`. If any other
    host is flagged, stop and report it.
12. **Docs edits.** Add the README link and the `AGENTS.md`, `CLAUDE.md`, and `ARCHITECT.md`
    lines. The invariant 8 note says `site/dist/` is built by Cloudflare, never
    committed, and never reaches the deploy target.
13. **Tests and verify.** Add the tests below. Run the full pytest with the denylist
    required, then `cd site && npm ci && npm run build`.

### Cloudflare inputs (in `docs/site.md`)

| Input | Value |
| --- | --- |
| Repository | `nulthara/filterhelm` (GitHub) |
| Production branch | `main` |
| Root directory | `site` |
| Build command | `npm ci && npm run build` |
| Output directory | `dist` |
| Environment variable | `NODE_VERSION=22` |
| Build watch paths | include `site/*` |
| Preview branches | none |

### Manual steps (in `docs/site.md`)

1. Merge on the forge after an Accept verdict.
2. Run `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest`, then `git push github main`.
3. In Cloudflare, open Workers & Pages, create a project from Git, and authorize the
   GitHub app for `nulthara/filterhelm` only.
4. Enter the inputs. For a Workers project, first add the contingency `wrangler.jsonc`
   in a follow-up commit.
5. Leave Web Analytics off.
6. Check the first build on its `*.pages.dev` or `*.workers.dev` URL.
7. Add the custom domain `filterhelm.nulthara.com` in the project. The zone is in the same
   account, so Cloudflare creates the DNS record and certificate. Do not create the
   record first.
8. Check `curl -sI https://filterhelm.nulthara.com` returns `200` with the `_headers` values.

## Contracts

N/A — no API, config key, or DB change. The new external contract is the Cloudflare input
table. The frontmatter schema adds the optional `type` and `heroScreenshot` fields, and
`tests/test_site.py` guards them.

## Tests

In `tests/test_site.py` unless noted. None of them runs Node.

- `test_site_config_declares_public_origin` — `astro.config.mjs` contains `site: "https://filterhelm.nulthara.com"`.
- `test_site_config_overrides_only_planned_components` — the `components` block maps exactly `Hero`, `PageTitle`, and `Footer`.
- `test_site_selector_parser_override_pinned` — `site/package.json` `overrides["postcss-selector-parser"]` is `"7.1.6"` or later, and `site/package-lock.json` resolves `node_modules/postcss-selector-parser` to that version.
- `test_site_build_outputs_are_ignored` — `git check-ignore` matches `site/node_modules/x`, `site/dist/x`, `site/.astro/x`, and not `site/package-lock.json`.
- `test_site_excluded_from_image_context` — `.dockerignore` has a `site` line.
- `test_site_node_pin_matches_ci` — `site/.node-version` is a bare major, and the CI `site` job reads it through `node-version-file`.
- `test_ci_site_job_shape` — job `site` has `working-directory: site`, runs `npm ci` then `npm run build`, and pins every action to a 40-character SHA.
- `test_deploy_workflow_ignores_site_only_pushes` — `on.push.paths-ignore == ["site/**"]` and `branches == ["main"]`.
- `test_site_pages_match_content_map` — the five page files exist; each has `title:` and the `type:` from the content map; the landing sets `heroScreenshot: drift`.
- `test_site_screenshot_slots_complete` — each of the eight slots has a `.png` or `.svg` in `site/src/assets/screenshots/`, and no other files are there.
- `test_site_screenshot_pngs_carry_no_metadata` — every PNG there has no `tEXt`, `zTXt`, `iTXt`, `eXIf`, or `tIME` chunk. This passes trivially until the user adds PNGs.
- `test_site_install_options_match_compose_sample` — the install-options page contains the image reference from `compose.ghcr.yaml` and the raw URL for that file.
- `test_site_footer_carries_nonaffiliation_line` — `Footer.astro` contains the exact sentence.
- `test_site_copy_avoids_banned_phrases` — page files contain none of the Step 8 banned words (case-insensitive) and no emoji.
- `test_site_has_no_email_addresses` — files under `site/src`, `site/public`, `astro.config.mjs`, and `package.json` contain no `mailto:` and no address-shaped string.
- `tests/test_supply_chain.py::test_npmrc_sets_min_release_age` — parametrized over both `.npmrc` files.
- `tests/test_branding.py::test_site_assets_match_brand_kit` — logos, favicon SVG, and 32 px PNG are byte-identical to `branding/`.
- `tests/test_repo_hygiene.py` — unchanged tests now also scan `site/`.

## Invariants at risk

1. **One instance at a time, diff before write, gravity gating.** Untouched. The site has
   no code that reaches a Pi-hole. The safety-model page and the landing's "How a change
   happens" list restate these rules, so their text is checked against `docs/design.md`.
2. **No background scheduler.** Untouched. CI and Cloudflare builds are repository
   steps, not application jobs.
3. **Credentials stay server-side.** Untouched. Screenshot rules forbid real hosts and
   names, and the PNG metadata test blocks embedded text.
4. **Outbound requests through `url_policy.py`.** Untouched. The manage-instances page
   describes the policy without restating its internals.
5. **Auth middleware on every route.** Untouched. No route is added.
6. **Compiled frontend committed (invariant 8).** `site/dist/` is not committed, because
   the deploy target never serves it. `ARCHITECT.md` records the carve-out.
   `app/static/ui/` is unchanged.
7. **`main` is production (invariant 9).** `paths-ignore` stops site-only pushes from
   restarting the app. Mixed pushes still deploy. The `sudo ` count stays at one.
8. **Public identity and hygiene.** The existing scans cover `site/`. The plan adds one
   allowed domain, `nulthara.com`, a no-email test, and a PNG metadata test. The publish
   path is unchanged.
9. **Supply chain.** `site/.npmrc` carries the 24-hour gate under test, the lockfile is
   committed, and CI actions use the existing pinned SHAs.

## Acceptance criteria

- [ ] `cd site && npm ci && npm run build` succeeds and writes the landing page, five pages, a 404, a sitemap, and the Pagefind index
- [ ] `git status` is clean of `site/dist`, `site/node_modules`, and `site/.astro` after a build
- [ ] The landing renders the hero with the Drift placeholder and caption, then "How a change happens", "Choose your path", "What you need", and "Get help", in that order
- [ ] Every docs page shows its type eyebrow, and every page shows the footer note
- [ ] Replacing `drift.svg` with a `drift.png` changes the hero image with no code edit
- [ ] Both themes meet 4.5:1 for every text pair in Step 6; the handback lists the ratios
- [ ] At 375 px the hero stacks text first, and no page scrolls horizontally
- [ ] `dist/` has no script or stylesheet from another origin
- [ ] The HTML pages, `sitemap*.xml`, and site CSS in `dist/` have no email address and no match for any `.private-denylist.toml` pattern. Vendor JS bundles are exempt, and their known Pagefind translator credits are recorded in `docs/site.md`.
- [ ] Commands and variables on the tutorial and guide pages match the README and `compose.ghcr.yaml`
- [ ] Review-phase anti-generic check passes. The site has no purple or gradient, no icon-card grid, no template "Next steps" cards, no Houston art, and no default code theme. With the logo hidden, the hero still identifies FilterHelm.
- [ ] `docs/site.md` has the slot table, shot rules, Cloudflare inputs, manual steps, and Workers contingency, with no token or personal detail
- [ ] `deploy.yml` has exactly one `sudo ` and ignores `site/**`; `.dockerignore` excludes `site`
- [ ] `git diff main...HEAD --stat` touches nothing under `app/` or `frontend/`
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] `npm audit` in `site/` reports 0 vulnerabilities with the `postcss-selector-parser` override in place
- [ ] After the manual steps, `https://filterhelm.nulthara.com` returns `200` with the `_headers` values
