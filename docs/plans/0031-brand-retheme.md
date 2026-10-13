# 0031 — Retheme the UI to the FilterHelm brand

Status: in-progress                <!-- draft | approved | in-progress | in-review | shipped -->
Branch: feature/0031-brand-retheme
Author: Claude (architect)
Date: 2026-10-13

## Problem

Plan 0030 renamed the product and swapped the mark but left the look alone. The UI still
uses AntD's default blue `#1677ff`, the stock `#001529` header and sider, and no dark mode.
The brand kit in `branding/` asks for Navy/Blue/Cyan/Ice colors, Inter, and light plus dark
themes with checked contrast. The app looks like a generic AntD admin panel, so it does not
look like FilterHelm.

## Scope

In:
- Brand colors, radii, and Inter applied through AntD `ConfigProvider` tokens.
- Light and dark themes. The default follows the OS. A header control picks System, Light,
  or Dark, and `localStorage` remembers the choice.
- Inter self-hosted from the `@fontsource-variable/inter` npm package.
- Navy header and sider in both themes.
- Replace hardcoded colors in `styles.css` and `DriftPage.tsx` with theme-aware values.
- `index.html` `theme-color` and `color-scheme` metas.
- Byte-identical copies of the brand token files in `frontend/src/assets/`, guarded by a test.

Out:
- New pages, navigation changes, or renamed menu items. The kit's "suggested navigation" is
  not adopted.
- Changing any status semantics. Status tags already pair color with text.
- A server-side theme preference. The choice lives in each browser only.
- Italic and optical-size Inter files. Only the upright weight axis ships.
- The horizontal wordmark in the app header. The kit prefers the standalone icon for app
  navigation, so the icon plus text title stays.
- Backend changes of any kind, including the CSP.

## Approach

**Tokens in one module.** `frontend/src/theme.ts` holds every theme decision as pure
functions. It imports brand hex values from `frontend/src/assets/brand-tokens.json`, a
byte-identical copy of `branding/tokens/brand-tokens.json`. Copying follows plan 0030's
reason: Vite's dev server refuses files outside `frontend/`. A pytest guard keeps the copy
in sync with the kit.

**AA-safe primary in light mode.** Brand Blue `#1E88FF` gives white text 3.49:1, below WCAG
AA's 4.5:1 for button labels. The kit says to verify contrast for real text pairs.
- Light theme `colorPrimary` is `#0A6FE0`, a darker shade of brand Blue. White on it is 4.81:1.
- Dark theme `colorPrimary` is brand Blue `#1E88FF`. On Navy it is 4.73:1 as link text.
  Primary buttons get Navy text (`Button.primaryColor`), also 4.73:1.
- A global `colorTextLightSolid: navy` was rejected. It also recolors danger buttons, badges,
  and solid tags, and Navy on AntD's dark red fails AA.

**Navy chrome in both themes.** The header and sider stay Navy and keep `Menu theme="dark"`.
The selected menu item gets a 24% brand-Blue tint, which keeps white text far above AA. In
dark mode the content also sits on Navy, so a faint Ice border separates the chrome from it.

**Theme state.** `ThemeModeProvider` owns the stored mode and the OS preference. It renders
`ConfigProvider` and AntD's `App`, so it replaces the wrapper in `main.tsx`. The OS preference
comes from `useSyncExternalStore` over `matchMedia('(prefers-color-scheme: dark)')`, so an OS
switch updates live while the mode is System. The provider mirrors the resolved theme onto
`<html data-theme>` so `styles.css` can switch its own variables. Storage access is wrapped in
try/catch, because `localStorage` throws in some private-mode browsers. A failure falls back
to System.

**Font.** `import '@fontsource-variable/inter'` in `main.tsx`. Its `index.css` references
seven upright woff2 subsets, about 224 KB in total. The browser downloads only the subsets
its text uses, through `unicode-range`. The files are served from `/app/assets/`, which the
current CSP (`default-src 'self'`) already allows. The architect ran the seven files through
the `test_repo_hygiene.py` host and IP checks, and they came back clean.

**Rejected:**
- Google Fonts. The CSP blocks it, and it would make every page load call a third party.
- A fallback font stack only. The user chose self-hosting.
- Brand Blue as-is for the light primary. It fails AA for button text.
- Following the OS only, or light only. The user chose OS plus a toggle.

## Files

Added:
- `frontend/src/assets/brand-tokens.json` — byte-identical copy of `branding/tokens/brand-tokens.json`.
- `frontend/src/assets/brand-tokens.css` — byte-identical copy of `branding/tokens/brand-tokens.css`.
- `frontend/src/theme.ts` — mode types, storage helpers, `resolveTheme`, `themeConfig`.
- `frontend/src/theme.test.ts` — Vitest coverage for `theme.ts`.
- `frontend/src/themeContext.ts` — `ThemeModeContext` and the `useThemeMode` hook.
- `frontend/src/ThemeModeProvider.tsx` — owns mode state and renders `ConfigProvider` + `AntApp`.
- `frontend/src/ThemeToggle.tsx` — header dropdown for System, Light, and Dark.

Modified:
- `frontend/package.json`, `frontend/package-lock.json` — add `@fontsource-variable/inter`.
- `frontend/src/main.tsx` — font import, and `ThemeModeProvider` replaces the inline `ConfigProvider`.
- `frontend/src/App.tsx` — `ThemeToggle` in both headers, `app-sider` class on `Sider`.
- `frontend/src/styles.css` — brand variables, light/dark variable sets, no hardcoded colors.
- `frontend/src/pages/DriftPage.tsx` — selected-card border and drift count read theme tokens.
- `frontend/index.html` — `theme-color` and `color-scheme` metas.
- `app/static/ui/**` — rebuilt bundle, including the seven Inter woff2 files.
- `tests/test_branding.py` — guard for the two token copies.

## Steps

0. **Prerequisite (architect, before handoff).** Step 1 needs registry access. Enable network
   in `~/.codex/config.toml`, or the architect runs the install on the feature branch first
   and commits it.

1. **Dependency.** In `frontend/`, run `npm install @fontsource-variable/inter@^5.3.0`. 5.3.0
   is OFL-1.1 and older than `.npmrc`'s `min-release-age=1`. Run `npm audit` and confirm it
   reports no new findings.

2. **Token copies.** Copy `branding/tokens/brand-tokens.json` and `brand-tokens.css` into
   `frontend/src/assets/` unchanged (`cp`, no reformatting).

3. **`theme.ts`.** It contains no React imports.
   ```ts
   import type { ThemeConfig } from 'antd'
   import { theme } from 'antd'
   import brand from './assets/brand-tokens.json'

   export type ThemeMode = 'system' | 'light' | 'dark'
   export type ResolvedTheme = 'light' | 'dark'
   export const THEME_STORAGE_KEY = 'filterhelm.theme'
   export const LIGHT_PRIMARY = '#0A6FE0'
   export const LIGHT_LAYOUT_BG = '#F4F8FD'
   export const FONT_FAMILY = `'Inter Variable', ${brand.typography.preferred}, ${brand.typography.fallback}`
   ```
   - `parseThemeMode(value: string | null): ThemeMode` returns the value if it is one of the
     three modes, otherwise `'system'`.
   - `readThemeMode(storage: Pick<Storage, 'getItem'> | undefined): ThemeMode` calls
     `storage.getItem(THEME_STORAGE_KEY)` inside try/catch. `undefined` storage or a throw
     returns `'system'`.
   - `writeThemeMode(storage: Pick<Storage, 'setItem'> | undefined, mode: ThemeMode): void`
     calls `setItem` inside try/catch and swallows failures.
   - `resolveTheme(mode: ThemeMode, prefersDark: boolean): ResolvedTheme` gives `mode` unless
     it is `'system'`, then `prefersDark ? 'dark' : 'light'`.
   - `themeConfig(resolved: ResolvedTheme): ThemeConfig` returns:

     | Key | Light | Dark |
     | --- | --- | --- |
     | `algorithm` | `theme.defaultAlgorithm` | `theme.darkAlgorithm` |
     | `token.colorPrimary` | `LIGHT_PRIMARY` | `brand.colors.blue` |
     | `token.colorInfo` | `LIGHT_PRIMARY` | `brand.colors.blue` |
     | `token.colorTextBase` | `brand.colors.navy` | `brand.colors.ice` |
     | `token.colorBgBase` | `brand.colors.white` | `brand.colors.navy` |
     | `token.colorBgLayout` | `LIGHT_LAYOUT_BG` | `brand.colors.navy` |
     | `token.fontFamily` | `FONT_FAMILY` | `FONT_FAMILY` |
     | `token.borderRadius` | `6` | `6` |
     | `token.borderRadiusLG` | `10` | `10` |
     | `components.Button.primaryColor` | (unset) | `brand.colors.navy` |

     Both themes share these component tokens:
     - `Layout`: `headerBg`, `siderBg`, and `triggerBg` are all `brand.colors.navy`.
     - `Menu`: `darkItemBg`, `darkSubMenuItemBg`, and `darkPopupBg` are `brand.colors.navy`.
       `darkItemSelectedBg` is `'rgba(30, 136, 255, 0.24)'` and `darkItemSelectedColor` is
       `brand.colors.white`.

     The radii are numbers because AntD tokens take pixels as numbers. They match the kit's
     `--fh-radius-sm` and `--fh-radius-md`.

4. **`themeContext.ts`.**
   ```ts
   export interface ThemeModeValue { mode: ThemeMode; resolved: ResolvedTheme; setMode: (mode: ThemeMode) => void }
   export const ThemeModeContext = createContext<ThemeModeValue | null>(null)
   export function useThemeMode(): ThemeModeValue  // throws if used outside the provider
   ```

5. **`ThemeModeProvider.tsx`.** It exports one component, `ThemeModeProvider({ children }: { children: ReactNode })`.
   - `storage()` returns `window.localStorage` inside try/catch, or `undefined` on a throw.
   - `const [mode, setModeState] = useState(() => readThemeMode(storage()))`.
   - `prefersDark` comes from `useSyncExternalStore`. Its subscribe callback adds and removes
     a `change` listener on `matchMedia('(prefers-color-scheme: dark)')`. Its snapshot reads
     `.matches`. The server snapshot is `false`.
   - `resolved = resolveTheme(mode, prefersDark)` is computed during render.
   - A `useEffect` on `[resolved]` sets `document.documentElement.dataset.theme = resolved`.
   - `setMode(next)` calls `writeThemeMode(storage(), next)`, then `setModeState(next)`.
   - It renders `ThemeModeContext.Provider` > `ConfigProvider theme={themeConfig(resolved)}` >
     `AntApp` > `children`.

6. **`ThemeToggle.tsx`.** `ThemeToggle()` takes no props and reads `useThemeMode()`.
   - It renders an AntD `Dropdown` whose `menu` has `selectable`, `selectedKeys={[mode]}`, and
     three items: `system` (`DesktopOutlined`, "System"), `light` (`SunOutlined`, "Light"), and
     `dark` (`MoonOutlined`, "Dark"). `onClick` calls `setMode(key as ThemeMode)`.
   - The trigger is `<Button icon={…current mode's icon…} aria-label={`Theme: ${label}`} />`
     wrapped in `<Tooltip title="Theme">`.

7. **`main.tsx`.** Add `import '@fontsource-variable/inter'` above `./styles.css`. Render
   `<StrictMode><ThemeModeProvider><App /></ThemeModeProvider></StrictMode>`. Remove the
   `ConfigProvider`, `theme`, and `AntApp` imports here.

8. **`App.tsx`.**
   - The login header gets `<Space className="auth-actions"><ThemeToggle /></Space>` after the title.
   - The app header puts `<ThemeToggle />` first inside the existing `auth-actions` `Space`.
   - `<Sider … className="app-sider">`.
   - `Menu theme="dark"` stays as it is.

9. **`styles.css`.**
   - First line is `@import './assets/brand-tokens.css';`.
   - `:root` sets `color-scheme: light`, `font-family: 'Inter Variable', var(--fh-font)`, and
     these variables:
     - `--fh-layout-bg: #F4F8FD`
     - `--fh-text: var(--fh-navy)`
     - `--fh-chrome-border: transparent`
     - `--fh-highlight: rgba(30, 136, 255, 0.08)`
     It drops `color: #1f1f1f` and `background: #f5f5f5`.
   - `:root[data-theme='dark']` sets `color-scheme: dark` and these variables:
     - `--fh-layout-bg: var(--fh-navy)`
     - `--fh-text: var(--fh-ice)`
     - `--fh-chrome-border: rgba(230, 244, 255, 0.12)`
     - `--fh-highlight: rgba(30, 136, 255, 0.16)`
   - `body` gets `background: var(--fh-layout-bg); color: var(--fh-text);`.
   - `.app-header` adds `border-bottom: 1px solid var(--fh-chrome-border);`.
   - A new `.app-sider` rule is `border-inline-end: 1px solid var(--fh-chrome-border);`.
   - `.brand-title.ant-typography` and `.auth-user` use `color: var(--fh-white)`.
   - `.bulk-bar` uses `background: var(--fh-highlight)`.
   - After this step no hex or `rgb`/`rgba` literal remains in `styles.css` outside the two
     theme blocks.

10. **`DriftPage.tsx`.** Add `theme` to the `antd` import and call
    `const { token } = theme.useToken()` in the component. Replace `'#1677ff'` with
    `token.colorPrimary` and `'#cf1322'` with `token.colorError`.

11. **`index.html`.** Change to `<meta name="theme-color" content="#0B1F3B" />` and add
    `<meta name="color-scheme" content="light dark" />`.

12. **Build.** Run `npm run build && npm run lint && npm test`. Commit `app/static/ui/`,
    which now includes the seven `inter-*-wght-normal-*.woff2` files.

## Contracts

N/A — frontend-only. There are no API, config, or DB changes. The only new persisted value is
the browser `localStorage` key `filterhelm.theme`, with value `'system' | 'light' | 'dark'`.
Any other value reads as `'system'`.

## Tests

- `frontend/src/theme.test.ts`:
  - `parseThemeMode` returns each valid mode unchanged, and `'system'` for `null`, `''`, and `'blue'`.
  - `readThemeMode` returns the stored mode from a stub storage. It returns `'system'` for
    `undefined` storage and for a stub whose `getItem` throws.
  - `writeThemeMode` calls `setItem(THEME_STORAGE_KEY, mode)`, and does not throw when
    `setItem` throws.
  - `resolveTheme` covers all six (mode, prefersDark) pairs.
  - `themeConfig('light')` and `themeConfig('dark')` match the Step 3 table, key by key.
  - A test-local WCAG `contrastRatio(hexA, hexB)` helper asserts these pairs are at least 4.5:
    - white on `LIGHT_PRIMARY`
    - Navy on brand Blue (dark primary button)
    - brand Blue on Navy (dark links)
    - Navy on `LIGHT_LAYOUT_BG`
    - Ice on Navy
- `tests/test_branding.py::test_frontend_tokens_match_brand_kit` asserts that
  `frontend/src/assets/brand-tokens.json` and `.css` are byte-identical to their
  `branding/tokens/` sources.
- `tests/test_repo_hygiene.py` is unchanged and must stay green with the woff2 files tracked.
- No component tests. Vitest runs without a DOM, so the provider and toggle are checked in
  Acceptance criteria.

## Invariants at risk

1. **One instance mutated at a time** — untouched. No mutation code changes.
2. **Diff before write** — untouched.
3. **Gravity gating** — untouched.
4. **No background scheduler** — untouched. The `matchMedia` listener only re-renders.
5. **Credentials never reach the browser** — untouched. `localStorage` holds only the theme
   mode.
6. **Outbound requests via `url_policy`** — untouched. The font is same-origin, and the
   browser makes no third-party request.
7. **Auth middleware on every route** — untouched. The new files are served from the existing
   static mount, the same as today's assets.
8. **Compiled frontend committed** — Step 12 rebuilds and commits `app/static/ui/`, fonts
   included.
9. **`main` is production** — normal feature-branch flow. No deploy files change.

## Acceptance criteria

- [ ] `./.venv/bin/pytest` green, including `test_frontend_tokens_match_brand_kit` and both
      `test_repo_hygiene.py` tests.
- [ ] `cd frontend && npm run build && npm run lint && npm test` clean, and `app/static/ui/`
      committed.
- [ ] `npm audit` reports no new findings.
- [ ] `app/static/ui/assets/` holds exactly seven `inter-*-wght-normal-*.woff2` files.
      `GET /app/assets/<one of them>` returns 200 locally.
- [ ] `grep -nE '#[0-9a-fA-F]{3,6}|rgba?\(' frontend/src/styles.css` hits only the two theme
      blocks. `grep -n '1677ff\|cf1322\|001529' -r frontend/src` prints nothing.
- [ ] `git diff main...HEAD --stat` touches only the Files list.
- [ ] Local run with the OS in light mode looks like this:
  - The header and sider are Navy.
  - The content background is `#F4F8FD`.
  - Primary buttons are `#0A6FE0` with white text.
  - Computed `font-family` on body text starts with `"Inter Variable"`, and DevTools shows the
    woff2 loading from `/app/assets/`.
- [ ] Switching the OS to dark while the toggle is on System flips the app live. The content
      turns Navy, text turns Ice, and primary buttons show Navy text on `#1E88FF`.
- [ ] Choosing Light or Dark in the toggle overrides the OS and survives a reload. Choosing
      System hands control back to the OS.
- [ ] The toggle shows on the login screen and in the signed-in header, and both work.
- [ ] In dark mode, Drift's selected card border and nonzero drift counts use the theme's
      primary and error colors. Every status tag stays readable in both themes.
- [ ] No console errors. With `localStorage` blocked (DevTools → Application → block site
      data), the app loads in System mode.
