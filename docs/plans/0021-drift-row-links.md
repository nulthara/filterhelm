# 0021 — Drift rows link to the owning page

Status: in-progress               <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0021-drift-row-links
Author: Claude (architect)
Date: 2026-09-23

## Problem

The Drift page (plan 0020) shows where instances disagree, but it gives no way to reach the
page that fixes the disagreement. The user reads a row, remembers its category, and finds the
matching screen in the sidebar. For CNAME rows it is worse, because Local DNS always opens on
the A/AAAA tab, so the user also has to switch tabs. Fixing drift stays on the owning pages,
where the confirm and audit paths already exist. Each drift row should link there in one click.

## Scope

In:
- A "Fix in" column on the Drift table. Each row gets a link button naming the owning page.
  Clicking it switches to that page.
- CNAME rows open Local DNS on the CNAME records tab.
- `DNSPage` accepts an optional starting tab.
- `docs/design.md` §5.10 and `docs/potential-features.md` updated to match.

Out:
- Finding or highlighting the specific entry on the target page. Only the A/AAAA tab has a
  search box, and adding one to five other pages is a separate change.
- Preselecting an instance on Settings. A settings drift row is a disagreement *between*
  instances, so no single instance is the right one to open.
- Keeping Drift's filters when the user comes back. Returning to Drift remounts it and
  refetches, which also shows whether the fix worked.
- Browser history or back-button support. `App.tsx` has no router, and adding one is out of
  proportion to this change.
- Any backend change.

## Approach

**App-level navigation callback.** `App.tsx` already owns `page` state. It passes a callback
`onNavigate(target)` to `DriftPage`, which calls it with a `DriftTarget`. `DriftPage` owns the
category-to-page map because the categories are its own. `App.tsx` stays a dumb router, so it
sets state and never learns about categories.

**The DNS starting tab lives in `App.tsx` state.** A new `dnsTab` state, `'hosts'` by default,
feeds a new `initialTab` prop on `DNSPage`, which passes it to `Tabs defaultActiveKey`.
`App.tsx` also passes `key={dnsTab}`. Arriving from Drift always mounts `DNSPage` fresh,
because the render chain unmounts every page but the current one. But a sidebar click on
"Local DNS" while already on Local DNS keeps `page` unchanged and `DNSPage` mounted, and `Tabs`
ignores a changed `defaultActiveKey` after mount. The `key` forces a remount whenever `dnsTab`
changes. A sidebar click resets `dnsTab` to `'hosts'`, so "Local DNS" in the menu opens on
A/AAAA after a CNAME link has been followed, even straight from the CNAME tab.

Rejected alternatives:
- Controlled `activeKey` on `DNSPage`, with tab state lifted into `App.tsx`. That handles tab
  switches the user makes afterwards, which `defaultActiveKey` already covers inside `Tabs`.
- A router such as React Router. It gives real URLs and back-button support, but it rewrites
  navigation across every page for one link column.
- Making the category cell itself the link. The cell already shows the category tag. A
  separate column labelled with the *destination* is clearer, because two categories (Local
  DNS and CNAME records) land on the same page.

## Files

- `frontend/src/pages/DriftPage.tsx` — `DriftPageProps`, `DriftTarget` type, the
  category-to-target map, and the "Fix in" column.
- `frontend/src/pages/DNSPage.tsx` — `DNSPageProps` with optional `initialTab`, exported
  `DNSTab` type, `defaultActiveKey` on `Tabs`.
- `frontend/src/App.tsx` — `dnsTab` state, the `openFromDrift` handler, the menu `onClick`
  reset, and the props passed to `DriftPage` and `DNSPage`.
- `app/static/ui/**` — rebuilt bundle (invariant #8).
- `docs/design.md` — §5.10 describes row links in place of "no row navigation".
- `docs/potential-features.md` — the 0020 shipped entry mentions the row links and plan 0021.

## Steps

1. **`DNSPage.tsx`.** Export `type DNSTab = 'hosts' | 'cnames'`. Add
   `interface DNSPageProps { initialTab?: DNSTab }`. Change the signature to
   `DNSPage({ initialTab = 'hosts' }: DNSPageProps)` and pass
   `defaultActiveKey={initialTab}` to `Tabs`. The tab keys `'hosts'` and `'cnames'` already
   exist, so change nothing else.

2. **`DriftPage.tsx` — types and map.** Export:
   ```ts
   export interface DriftTarget {
     page: 'dns' | 'adlists' | 'domains' | 'groups' | 'clients' | 'settings'
     dnsTab?: DNSTab
   }

   interface DriftPageProps {
     onNavigate: (target: DriftTarget) => void
   }
   ```
   Add a module-level constant mapping each `DriftCategory` to its target and link label:

   | Category | Target | Label |
   | --- | --- | --- |
   | `dns` | `{ page: 'dns', dnsTab: 'hosts' }` | `Local DNS` |
   | `cname` | `{ page: 'dns', dnsTab: 'cnames' }` | `Local DNS › CNAME` |
   | `adlist` | `{ page: 'adlists' }` | `Block Lists` |
   | `domain` | `{ page: 'domains' }` | `Domains` |
   | `group` | `{ page: 'groups' }` | `Groups` |
   | `client` | `{ page: 'clients' }` | `Clients` |
   | `setting` | `{ page: 'settings' }` | `Settings` |

   Type it `Record<DriftCategory, { target: DriftTarget; label: string }>` so a new category
   fails the type check until it gets a destination. The labels match the sidebar menu labels.

3. **`DriftPage.tsx` — column.** Change the signature to `DriftPage({ onNavigate }: DriftPageProps)`.
   The name avoids the existing local `onOpen`, the `useEffectEvent` that runs the initial load.
   Leave that one as it is.
   Append a final column after `...instanceColumns`:
   `{ title: 'Fix in', key: 'open', render: (_, item) => <Button type="link" size="small" icon={<ArrowRightOutlined />} onClick={() => onNavigate(DESTINATIONS[item.category].target)}>{DESTINATIONS[item.category].label}</Button> }`.
   Use `Button type="link"`, not an `<a href>`, because navigation is in-app state and there is
   no URL to link to. Import `ArrowRightOutlined` from `@ant-design/icons`.

4. **`App.tsx`.** Import `DriftTarget` from `./pages/DriftPage` and `DNSTab` from
   `./pages/DNSPage`. Add `const [dnsTab, setDnsTab] = useState<DNSTab>('hosts')`. Add
   `const openFromDrift = (target: DriftTarget) => { setDnsTab(target.dnsTab ?? 'hosts'); setPage(target.page) }`.
   Change the menu handler from `({ key }) => setPage(key)` to
   `({ key }) => { setDnsTab('hosts'); setPage(key) }`. In the render chain, pass
   `<DriftPage onNavigate={openFromDrift} />` and `<DNSPage key={dnsTab} initialTab={dnsTab} />`. Leave
   `page`'s `useState('status')` type as it is. Tightening it to a union is a separate refactor.

5. **Docs.** In `docs/design.md` §5.10, replace "Refresh is explicit, with no polling,
   mutation, reconciliation, or row navigation." with a sentence saying that refresh is
   explicit with no polling, mutation, or reconciliation, and that each row links to its
   owning page, with CNAME rows opening the CNAME records tab. In `docs/potential-features.md`, extend the
   0020 shipped entry with "rows link to the owning page — plan 0021".

6. **Build and commit the bundle.** `cd frontend && npm run build && npm run lint`, then
   commit the regenerated `app/static/ui/`.

## Contracts

N/A. No API, config, or DB change. `api.ts` is untouched.

The new component contracts are the `DriftPageProps`, `DriftTarget`, `DNSPageProps`, and
`DNSTab` types written out in Steps 1–2.

## Tests

No automated tests. The change is frontend-only, and `frontend/` has no test runner
(`docs/potential-features.md` §5 records the gap). The backend is untouched, so
`./.venv/bin/pytest` must stay at the current baseline of 539 passed, 0 failed.

The `Record<DriftCategory, …>` typing makes `npm run build` fail if a category has no
destination. That type check is the only automated guard. The Acceptance criteria below cover
the rest by hand.

## Invariants at risk

1. **One instance mutated at a time** — no mutation. The link switches page and calls nothing.
2. **Diff before write** — untouched. Fixes still go through the owning page's existing confirm
   path. The link only gets the user there faster.
3. **Gravity gated behind real changes** — untouched.
4. **No background scheduler** — untouched. No new effect, timer, or fetch.
5. **FastAPI owns all credentials** — untouched. No new data reaches the browser.
6. **Outbound requests through `url_policy`** — untouched. No backend change.
7. **Every route behind auth** — untouched. No new route.
8. **Compiled frontend is committed** — `frontend/` changes, so Step 6 rebuilds and commits
   `app/static/ui/`.
9. **`main` is production** — work on `feature/0021-drift-row-links`, merged after Accept.

## Acceptance criteria

- [ ] Every Drift row has a "Fix in" link as its last column, labelled per the Step 2 table.
- [ ] A Local DNS row's link opens Local DNS on the A/AAAA records tab.
- [ ] A CNAME row's link opens Local DNS on the CNAME records tab.
- [ ] Block list, domain, group, client, and setting row links open Block Lists, Domains,
      Groups, Clients, and Settings respectively, and the sidebar highlights the new page.
- [ ] After following a CNAME link, clicking "Local DNS" in the sidebar opens the A/AAAA tab.
- [ ] Opening Local DNS from the sidebar without going through Drift still opens A/AAAA.
- [ ] Returning to Drift from the sidebar reloads it.
- [ ] No console error on the Drift page or any link destination.
- [ ] `./.venv/bin/pytest` green at 539 passed, 0 failed.
- [ ] `npm run build` + `npm run lint` clean apart from the existing `react-hooks/exhaustive-deps`
      warning on the session-expired `useEffect` in `App.tsx`. Its line number shifts with this
      change. `app/static/ui/` committed.
