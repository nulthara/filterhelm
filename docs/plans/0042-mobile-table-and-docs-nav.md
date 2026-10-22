# 0042 — Mobile-friendly Instances table, docs menu tab, and screenshot zoom

Status: in-progress           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0042-mobile-table-and-docs-nav
Author: Kyrnveil
Date: 2026-10-21

## Problem

The Instances table is the only table in the app without `scroll={{ x: 'max-content' }}`.
When its cells get wide (long base URLs, version tags, five labelled action buttons), the
table squeezes or overflows the content area instead of scrolling. Plan 0041 flagged this.
On a phone the table is close to unusable because of the Actions column. On the public docs
site, phones hide the navigation behind a small button in the top-right corner, and
screenshots render at column width with no way to enlarge them.

## Scope

In:
- Instances table scrolls horizontally at every width.
- Below the `md` breakpoint (768 px), the Instances action buttons render icon-only, with
  tooltips and accessible names.
- Docs site, below 50rem: the menu button becomes a tab pinned to the left edge at
  mid-screen height, and the menu opens as a left drawer with a dimmed backdrop.
- Docs site: clicking or tapping a screenshot opens it in a full-viewport lightbox.

Out:
- A pinned, always-visible docs sidebar on phones. At 375 px it would leave about 135 px
  for content. Starlight pins the sidebar from 50rem up, and that stays unchanged.
- Card-list layout for the Instances table on phones (rejected by the user in favour of
  the compact table).
- Horizontal scroll on the Audit and Settings tables. Neither was reported. A later plan
  can add it.
- Overriding any Starlight component. The docs changes are CSS in `theme.css` plus the
  existing `Screenshot.astro`.
- New npm dependencies in `frontend/` or `site/` (no `starlight-image-zoom`, no lightbox
  library).

## Approach

### Instances table

Add `scroll={{ x: 'max-content' }}` to the `<Table>` in `InstancesPage`, matching the
13 tables in `frontend/src/pages/` that already scroll. Read the breakpoint with antd's
`Grid.useBreakpoint()`. When `md` is false, the Actions column renders each button with
its icon only. Each button keeps its `aria-label` and gets a `Tooltip` with the label
text. Behaviour, handlers, loading and disabled states do not change.

Rejected options:
- Collapsing all actions into one dropdown on phones. That would move Remove's
  `Popconfirm` into a menu item and change the confirmation flow.
- Hiding Versions or Base URL on phones. Horizontal scroll already handles width, and
  hiding them drops information, including the compatibility tag.

### Docs menu tab

Starlight 0.42.6 renders `.sl-menu-button` as a `position: fixed` button at top-right.
That button toggles the `#starlight__sidebar` popover (`popover="auto"`), which fills the
full width below the header. All of Starlight's rules sit in `@layer starlight.core`, so
unlayered rules in `site/src/styles/theme.css` override them without
`!important` and without a component override. The new rules apply only below 50rem
(`@media (max-width: 49.9375rem)`). From 50rem up, Starlight's pinned sidebar is untouched.

- The tab is `.sl-menu-button`, fixed at `inset-inline-start: 0`, `top: 50%`,
  `translateY(-50%)`. It is a narrow vertical tab with the right corners rounded, using
  brand tokens and the same light/dark swap Starlight uses.
- The drawer is `.sidebar-pane`, fixed to the left at `width: min(20rem, 85vw)`. It keeps
  `inset-block: var(--sl-nav-height) 0`, gets a right border, and uses
  `--sl-color-bg-sidebar`.
- The backdrop is `.sidebar-pane::backdrop`, a navy dim (`rgb(11 31 59 / 0.6)`). The
  popover is `auto`, so a tap on the backdrop or Esc closes the drawer natively.
- The drawer is in the top layer, so it covers the tab while open. Closing works through a
  backdrop tap, Esc, or following a link.
- Header: Starlight reserves right-hand padding for the old button
  (`[data-has-sidebar] .header`). Below 50rem, reset it to `var(--sl-nav-pad-x)`.
- Content: below 50rem, add the tab width to `.main-frame`'s inline-start padding so the
  tab never covers body text.

Declare the tab width once as `--fh-menu-tab-width: 1.5rem` and reuse it.

These rules depend on Starlight's class names (`.sl-menu-button`, `.sidebar-pane`,
`.main-frame`, `.header`). Add a comment in `theme.css` saying so, and a line in
`docs/site.md` telling whoever upgrades Starlight to recheck the 375 px menu.

Rejected options:
- Overriding `MobileMenuToggle` or `PageFrame`. That breaks
  `test_site_config_overrides_only_planned_components` and copies Starlight internals
  that CSS can already reach.
- Moving the button to the top-left of the header. The user chose the left-edge tab.

### Screenshot lightbox

`Screenshot.astro` wraps the existing `<Image>` in an anchor whose `href` points to the
full-size image. A single `<dialog class="fh-lightbox">` per figure holds the full-size
`<img>`, a close button, and an "Open full size" link. A small bundled `<script>` in
`Screenshot.astro` intercepts anchor clicks, calls `dialog.showModal()`, and closes on a
backdrop click. Esc closes it natively. Without JavaScript, the anchor opens the image
directly.

- Full-size source: `getImage({ src: source.default })` from `astro:assets`. The same URL
  serves the dialog `<img>`, the anchor `href`, and the "Open full size" link.
- Dialog image: `max-width: 100%`, `max-height: calc(100dvh - 5rem)`,
  `object-fit: contain`. Do not set `touch-action: none`, so browser pinch-zoom keeps
  working on phones.
- The dialog `<img>` has `alt=""` because the thumbnail already carries the alt text. The
  dialog has `aria-label={alt}`.
- The anchor has `aria-label={\`Enlarge screenshot: ${alt}\`}` and shows a `zoom-in`
  cursor.
- The close button is `<form method="dialog"><button>Close</button></form>`.
- The hero screenshot (`Hero.astro` → `Screenshot`) gets the same behaviour with no extra
  code.

Rejected options:
- `starlight-image-zoom`. It adds a dependency and a plugin registration, it has to clear
  the 24-hour release gate and the audit, and it only reaches markdown images. Every
  screenshot here goes through `Screenshot.astro`.
- Linking straight to the image with no dialog. That leaves the page on every tap.

## Files

- `frontend/src/pages/InstancesPage.tsx` — table scroll; compact action buttons below `md`
- `app/static/ui/**` — rebuilt frontend bundle (invariant 8)
- `site/src/styles/theme.css` — left-edge menu tab, left drawer, backdrop, header/content padding, lightbox styles
- `site/src/components/Screenshot.astro` — anchor wrapper, `<dialog>` lightbox, script
- `docs/site.md` — Starlight-upgrade note for the menu CSS; one line that screenshots enlarge on click
- `tests/test_site.py` — lightbox and menu-tab source assertions
- `tests/test_frontend_tables.py` (new) — every page table scrolls horizontally, with an explicit exemption list

## Steps

1. **Instances table scroll.** In `InstancesPage`, add `scroll={{ x: 'max-content' }}` to
   the `<Table>`.
2. **Breakpoint.** Import `Grid` from `antd`. In `InstancesPage`, call
   `const { md } = Grid.useBreakpoint()` and derive `const compact = md === false`.
   `useBreakpoint` returns `{}` on the first render, so treat `undefined` as wide.
3. **Compact actions.** In the `actions` column render, when `compact` is true:
   - Test, Backup, and Edit render with no text child, with `aria-label` set to
     `` `Test ${item.name}` ``, `` `Back up ${item.name}` ``, and `` `Edit ${item.name}` ``.
     Each is wrapped in a `Tooltip` titled "Test", "Edit", or (for Backup) the existing
     Teleporter tooltip text.
   - The Service dropdown button renders `<ToolOutlined />` and `<DownOutlined />` without
     the word "Service", with `aria-label={`Service actions for ${item.name}`}` and a
     "Service" tooltip.
   - Remove is already icon-only. Leave it unchanged.
   - When `compact` is false, the output matches today's markup.
   - Wrap the column's `<Space>` in `<Space size={compact ? 4 : 8}>`. Keep the
     `align: 'right'`.
4. **Menu tab CSS.** In `theme.css`, add a block under
   `@media (max-width: 49.9375rem)` that implements the tab, drawer, backdrop, header
   padding, and content padding described in Approach. Use `--fh-menu-tab-width`. Give
   the tab a visible `:focus-visible` outline in `--sl-color-accent-high`. Light theme
   colours go under `:root[data-theme='light']`, following the existing pattern. Put a
   one-line comment above the block naming the Starlight version and the class names it
   relies on.
5. **Lightbox markup.** In `Screenshot.astro`:
   - `import { Image, getImage } from "astro:assets"`
   - `const full = await getImage({ src: source.default })`
   - Inside `<figure class="fh-shot">`, render
     `<a class="fh-shot-zoom" href={full.src} aria-label=...><Image .../></a>`, then
     `<dialog class="fh-lightbox" aria-label={alt}>` containing
     `<img src={full.src} alt="" loading="lazy" decoding="async" />`, an "Open full size"
     link (`href={full.src}`, `target="_blank"`, `rel="noopener"`), and the
     `method="dialog"` close form. `figcaption` stays where it is.
6. **Lightbox script.** Add one `<script>` to `Screenshot.astro` (Astro bundles and dedupes
   it). The script uses `document.querySelectorAll('.fh-shot-zoom')` and, on each click,
   calls `preventDefault()` and then `showModal()` on the sibling `dialog`. It closes the
   dialog when `event.target === dialog`, which is the backdrop. It sets no global
   listeners beyond those, and has no inline `onclick`.
7. **Lightbox CSS.** In `theme.css`, style `.fh-shot-zoom` (block, `cursor: zoom-in`,
   visible `:focus-visible` outline), `.fh-lightbox` (no border, transparent background,
   `max-width: 100vw`, `max-height: 100dvh`, padding `1rem`), `.fh-lightbox::backdrop`
   (same navy dim as the menu), and the dialog's controls row (close and "Open full size",
   right-aligned, readable in both themes). The thumbnail's existing `.fh-shot img` border
   and the hero accent border must still apply to the image inside the anchor.
8. **Docs.** In `docs/site.md`, under Screenshots, say that readers can click or tap a
   shot to enlarge it. Add a short note that the 375 px menu tab depends on Starlight's
   class names, so recheck it after any Starlight upgrade.
9. **Tests.** Add the tests listed below.
10. **Build.** Run `cd frontend && npm run build && npm run lint && npm test` and commit
    `app/static/ui/`. Run `cd site && npm ci && npm run build && npm audit`.

## Contracts

N/A — no API, config, or DB change. The frontend still calls only the existing
`/api/instances` endpoints through `api.ts`. The docs site has no backend.

## Tests

- `tests/test_frontend_tables.py::test_page_tables_scroll_horizontally` — every `<Table`
  opening tag in `frontend/src/pages/*.tsx` contains `scroll={{ x: 'max-content' }}`.
  The tag match must allow generics (`<Table<T>`, `<Table<DriftItem>`) and tags that span
  several lines. The match runs from `<Table` to the tag's closing `>` or `/>`, counting
  nested braces.
  `AuditPage.tsx` and `SettingsPage.tsx` sit in a named exemption set with a comment saying
  they are out of scope for 0042. The test also asserts that each exempt file still exists
  and still lacks the prop, so that the exemption fails once someone fixes it.
- `tests/test_frontend_tables.py::test_instances_actions_have_accessible_names` —
  `InstancesPage.tsx` contains `Grid.useBreakpoint()` and the `aria-label` templates for
  Test, Back up, Edit, and Service actions.
- `tests/test_site.py::test_screenshot_lightbox_markup` — `Screenshot.astro` contains
  `getImage`, `class="fh-shot-zoom"`, `<dialog`, `showModal`, `method="dialog"`, and
  `aria-label`. It contains no `onclick=`.
- `tests/test_site.py::test_mobile_menu_tab_css` — `theme.css` contains a
  `max-width: 49.9375rem` block that sets `.sl-menu-button` with `inset-inline-start: 0`
  and `top: 50%`, sets `.sidebar-pane` `width: min(20rem, 85vw)`, and styles
  `.sidebar-pane::backdrop`.
- `tests/test_site.py::test_site_config_overrides_only_planned_components` — unchanged, and
  it must still pass. This proves no Starlight component override was added.

## Invariants at risk

1. **One instance mutated at a time** — not touched. Instances action handlers (`test`,
   `backup`, `runAction`, `remove`) and the `actionRunning` gate stay byte-identical.
   Only their button presentation changes.
2. **Diff before write** — not touched. No mutation path changes.
3. **Gravity gated** — not touched.
4. **No background scheduler** — not touched. The lightbox script runs only on user clicks
   in the static docs site.
5. **Credentials never reach the browser** — not touched. No new data reaches the
   frontend, and the docs site has no app data.
6. **Outbound URLs through `url_policy`** — not touched. No new fetches.
7. **Auth middleware** — not touched. No routes.
8. **Compiled frontend committed** — applies. Step 10 rebuilds and commits
   `app/static/ui/`. `site/dist/` stays uncommitted, and the existing
   `test_site_build_outputs_are_ignored` still guards it.
9. **`main` is production** — work lands on `feature/0042-mobile-table-and-docs-nav`.

## Acceptance criteria

- [ ] At 1280 px and 375 px, the Instances page scrolls the table horizontally inside the content area, and the page itself does not scroll horizontally
- [ ] At 375 px, the Instances action buttons are icon-only. Each shows a tooltip on hover or focus and has an accessible name naming the instance
- [ ] At ≥ 768 px, the Instances action buttons look exactly as they do on `main`
- [ ] Test, Backup, Service actions, Edit, and Remove all still work at 375 px, and the Service dropdown still disables while an action runs
- [ ] Docs at 375 px: a tab sits on the left edge at mid-screen height, and no button remains at top-right
- [ ] Tapping the tab opens a left drawer of at most 85% width over a dimmed backdrop. A backdrop tap, Esc, or a link click closes it
- [ ] The tab does not cover body text, and the header title is not clipped by leftover right padding
- [ ] Docs at ≥ 800 px: the sidebar is pinned exactly as before, and no tab is visible
- [ ] Both themes: the tab, drawer, and lightbox controls meet WCAG AA contrast against their backgrounds
- [ ] Clicking any screenshot, including the landing hero, opens the lightbox. Esc, the Close button, and a backdrop click all close it. "Open full size" opens the image in a new tab
- [ ] With JavaScript disabled, clicking a screenshot opens the image file
- [ ] Keyboard: Tab reaches each screenshot anchor with a visible focus ring, Enter opens the lightbox, and focus returns to the anchor on close
- [ ] No new entries in `frontend/package.json` or `site/package.json`, and no lockfile dependency changes
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] `npm run build` + `npm run lint` + `npm test` clean in `frontend/`, `app/static/ui/` committed
- [ ] `cd site && npm ci && npm run build` succeeds and `npm audit` reports 0 vulnerabilities
