# 0044 — Instances table collapses when a health row is expanded

Status: shipped           <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0044-instances-table-expanded-row-width
Author: Kyrnveil
Date: 2026-10-22

## Problem

Plan 0042 added `scroll={{ x: 'max-content' }}` to the Instances table. That sizes the
outer `<table>` to its content's intrinsic width. The expanded health row renders
`HealthDetails`, whose antd `Descriptions` is a nested `<table>` with `width: 100%`. A
percentage-width table inside an intrinsically sized table has no width to resolve
against, so Chromium clamps its max-content contribution to 1,000,000 px. The outer table
becomes 1,000,032 px wide and spreads that width across its columns (Name ≈ 56,000 px,
Actions ≈ 330,000 px). Only Name is visible, the health panel stretches off-screen, and
the user sees an empty table. The bug appears at 1280 px, 1920 px, and 375 px whenever any
row is expanded. Rows expand automatically when an instance is not healthy, which is why
0042's acceptance run with healthy instances missed it.

Measured with the committed `app/static/ui/` build in Chromium and a mocked `/api` (one
healthy instance, one warning):

| Viewport | `.ant-table table` width | Scroller width |
| --- | --- | --- |
| 1920 | 1,000,032 px | 1636 px |
| 1280 | 1,000,032 px | 996 px |
| 375 | 1,000,032 px | 342 px |

## Scope

In:
- Size the Instances health panel from the table's visible width, so the panel no longer
  feeds an unresolvable width into the outer table.
- Keep the panel inside the visible scroll area while the table scrolls sideways.

Out:
- Changing column order, column visibility, or the compact action buttons from 0042.
- Pinning the Actions column (`fixed: 'right'`). That is a layout change, not a fix.
- Audit page expanded rows. `AuditPage.tsx` is exempt from `max-content` scroll, so its
  table sizes to its container and is unaffected.
- The 375 px header overflow seen in proxy auth mode ("Proxy-managed session" button).
  It is unrelated to the table.

## Approach

Make `.ant-table-container` an inline-size container on the Instances table. Then give the
`HealthDetails` root a definite width taken from that container with `cqi` units, and
pin it with `position: sticky`.

- `.ant-table-container` wraps the horizontal scroller and has the visible width, not the
  scroll width. `container-type: inline-size` lets descendants read that width as `100cqi`.
- The panel's width becomes `calc(100cqi - 2 * <cell padding>)`. That width is definite,
  so the nested `Descriptions` table resolves `width: 100%` against it. The outer table's
  width then comes from its real columns.
- `position: sticky; inset-inline-start: <cell padding>` keeps the panel in view when the
  table scrolls horizontally on narrow screens.
- The cell padding comes from antd's `--ant-table-cell-padding-inline` (16 px today), with
  a `16px` fallback.

Verified by injecting the CSS into the committed build:

| Viewport | Table width | Panel width | Panel x, table scrolled 400 px |
| --- | --- | --- | --- |
| 1920 | 1636 px (fits) | 1604 px | — |
| 1280 | 1514 px (scrolls, as 0042 intends) | fits scroller | — |
| 375 | 1039 px (scrolls) | 310 px | 33 px (stays put) |

Rejected options:
- `contain: inline-size` on the panel. It fixes the width bug, but at 375 px the panel
  spans the full 1007 px scroll width, so the health details leave the screen.
- A numeric `scroll.x` (for example `1200`). It needs a guessed width, it breaks the 0042
  guard that every page table uses `'max-content'`, and the panel still is not pinned.
- `fixed: 'right'` on Actions. It triggers rc-table's built-in pinned expanded row, but it
  also makes the Actions column sticky, which takes about 210 px of a 343 px phone scroller.
- Reverting 0042's scroll. That brings back the phone overflow 0042 fixed.

## Files

- `frontend/src/pages/InstancesPage.tsx` — `className` on the `<Table>` and on the `HealthDetails` root `Flex`
- `frontend/src/styles.css` — container rule and pinned health panel rule
- `app/static/ui/**` — rebuilt frontend bundle (invariant 8)
- `tests/test_frontend_tables.py` — source guard for the new classes and CSS

## Steps

1. **Table class.** In `InstancesPage`, add `className="instances-table"` to the `<Table>`.
   Leave `scroll={{ x: 'max-content' }}` and every other prop unchanged.
2. **Panel class.** In `HealthDetails`, add `className="health-details"` to the root
   `<Flex vertical gap="middle">`. Do not change its children.
3. **CSS.** In `frontend/src/styles.css`, after the `.status-actions` rule and before the
   `@media` block, add:

   ```css
   .instances-table .ant-table-container { container-type: inline-size; }
   .health-details {
     position: sticky;
     inset-inline-start: var(--ant-table-cell-padding-inline, 16px);
     width: calc(100cqi - 2 * var(--ant-table-cell-padding-inline, 16px));
   }
   ```

   Put a one-line comment above the pair saying the panel takes the visible table width so
   the nested `Descriptions` table cannot widen the `max-content` table.
4. **Test.** Add the test below.
5. **Build.** Run `cd frontend && npm run build && npm run lint && npm test` and commit
   `app/static/ui/`.

## Contracts

N/A — no API, config, or DB change. The page still calls only
`api.listInstances({ health: true })` and the existing instance actions.

## Tests

- `tests/test_frontend_tables.py::test_instances_health_panel_uses_visible_width`
  - `InstancesPage.tsx` contains `className="instances-table"` and `className="health-details"`.
  - `styles.css` contains `.instances-table .ant-table-container` with
    `container-type: inline-size`.
  - `styles.css` has a `.health-details` rule containing `position: sticky` and `100cqi`.
- `test_page_tables_scroll_horizontally` and `test_instances_actions_have_accessible_names`
  stay unchanged and must still pass.

Layout itself cannot be checked in jsdom or pytest. The acceptance criteria cover it with
a browser check.

## Invariants at risk

1. **One instance mutated at a time** — not touched. Handlers and the `actionRunning`
   gate do not change.
2. **Diff before write** — not touched.
3. **Gravity gated** — not touched.
4. **No background scheduler** — not touched.
5. **Credentials never reach the browser** — not touched. No new data reaches the frontend.
6. **Outbound URLs through `url_policy`** — not touched.
7. **Auth middleware** — not touched. No routes.
8. **Compiled frontend committed** — applies. Step 5 rebuilds and commits `app/static/ui/`.
9. **`main` is production** — work lands on `feature/0044-instances-table-expanded-row-width`.

## Acceptance criteria

Run each check with at least one instance whose health row is expanded (any `warning` or
`critical` instance, or expand a healthy one by hand).

- [ ] At 1920 px, all nine columns are visible, `.ant-table table` is no wider than `.ant-table-content`, and the health panel spans the table width
- [ ] At 1280 px, the table scrolls horizontally inside the content area, the health panel fits the visible area, and the table is under 2000 px wide
- [ ] At 375 px, the health panel fits the visible area and stays in place while the table scrolls sideways
- [ ] The health alert text wraps normally. No column renders one character per line
- [ ] Collapsing and re-expanding a row leaves the widths unchanged
- [ ] Light and dark themes both render the panel on the row background with no gap or overlap at the edges
- [ ] Actions, tooltips, and compact buttons behave as they did after 0042
- [ ] No new entries in `frontend/package.json` and no lockfile changes
- [ ] `FILTERHELM_REQUIRE_DENYLIST=1 ./.venv/bin/pytest` green
- [ ] `npm run build` + `npm run lint` + `npm test` clean in `frontend/`, `app/static/ui/` committed
