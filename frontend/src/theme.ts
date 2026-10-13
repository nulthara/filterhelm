import type { ThemeConfig } from 'antd'
import { theme } from 'antd'
import brand from './assets/brand-tokens.json'

export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export const THEME_STORAGE_KEY = 'filterhelm.theme'
export const LIGHT_PRIMARY = '#0A6FE0'
export const LIGHT_LAYOUT_BG = '#F4F8FD'
export const FONT_FAMILY = `'Inter Variable', ${brand.typography.preferred}, ${brand.typography.fallback}`

export function parseThemeMode(value: string | null): ThemeMode {
  return value === 'system' || value === 'light' || value === 'dark' ? value : 'system'
}

export function readThemeMode(storage: Pick<Storage, 'getItem'> | undefined): ThemeMode {
  try {
    return parseThemeMode(storage?.getItem(THEME_STORAGE_KEY) ?? null)
  } catch {
    return 'system'
  }
}

export function writeThemeMode(storage: Pick<Storage, 'setItem'> | undefined, mode: ThemeMode): void {
  try {
    storage?.setItem(THEME_STORAGE_KEY, mode)
  } catch {
    // Theme changes still work when browser storage is unavailable.
  }
}

export function resolveTheme(mode: ThemeMode, prefersDark: boolean): ResolvedTheme {
  return mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode
}

export function themeConfig(resolved: ResolvedTheme): ThemeConfig {
  const dark = resolved === 'dark'
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorInfo: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorTextBase: dark ? brand.colors.ice : brand.colors.navy,
      colorBgBase: dark ? brand.colors.navy : brand.colors.white,
      colorBgLayout: dark ? brand.colors.navy : LIGHT_LAYOUT_BG,
      fontFamily: FONT_FAMILY,
      borderRadius: 6,
      borderRadiusLG: 10,
    },
    components: {
      ...(dark ? { Button: { primaryColor: brand.colors.navy } } : {}),
      Layout: {
        headerBg: brand.colors.navy,
        siderBg: brand.colors.navy,
        triggerBg: brand.colors.navy,
      },
      Menu: {
        darkItemBg: brand.colors.navy,
        darkSubMenuItemBg: brand.colors.navy,
        darkPopupBg: brand.colors.navy,
        darkItemSelectedBg: 'rgba(30, 136, 255, 0.24)',
        darkItemSelectedColor: brand.colors.white,
      },
    },
  }
}
