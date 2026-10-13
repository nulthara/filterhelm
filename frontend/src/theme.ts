import type { ThemeConfig } from 'antd'
import type { MappingAlgorithm } from 'antd/es/theme'
import { theme } from 'antd'
import brand from './assets/brand-tokens.json'

export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export const THEME_STORAGE_KEY = 'filterhelm.theme'
export const LIGHT_PRIMARY = '#0A6FE0'
export const LIGHT_LAYOUT_BG = '#F4F8FD'
export const FONT_FAMILY = `'Inter Variable', ${brand.typography.preferred}, ${brand.typography.fallback}`

export const PINNED_TOKENS: Record<ResolvedTheme, Record<string, string>> = {
  light: {
    colorPrimaryHover: '#0A5FC2',
    colorPrimaryActive: '#0051BA',
    colorPrimaryTextHover: '#0A5FC2',
    colorLinkHover: '#0A5FC2',
    colorLinkActive: '#0051BA',
    colorInfo: '#0A5FC2',
    colorInfoText: '#0A5FC2',
    colorErrorHover: '#B71C1C',
    colorErrorTextHover: '#B71C1C',
    colorSuccessBg: '#EDF7EE',
    colorWarningBg: '#FFF5E6',
    colorErrorBg: '#FDECEC',
    colorInfoBg: '#EAF3FF',
    colorTextTertiary: 'rgba(11, 31, 59, 0.62)',
    green7: '#2E7D32',
    orange7: '#A15C00',
  },
  dark: {
    colorPrimary: '#62AFFF',
    colorPrimaryHover: '#8CC4FF',
    colorPrimaryActive: '#4DA3FF',
    colorPrimaryText: '#62AFFF',
    colorPrimaryTextHover: '#8CC4FF',
    colorPrimaryTextActive: '#4DA3FF',
    colorLink: '#62AFFF',
    colorLinkHover: '#8CC4FF',
    colorLinkActive: '#4DA3FF',
    colorInfo: '#62AFFF',
    colorInfoText: '#62AFFF',
    colorError: '#FF8A87',
    colorErrorHover: '#FFA9A6',
    colorErrorActive: '#FF7875',
    colorErrorText: '#FF8A87',
    colorSuccess: '#73D13D',
    colorWarning: '#FFC53D',
    colorTextTertiary: 'rgba(230, 244, 255, 0.62)',
  },
}

function pinTokens(resolved: ResolvedTheme): MappingAlgorithm {
  // Must run after the base algorithm to pin the derived map tokens.
  return (_seed, map) => ({ ...map!, ...PINNED_TOKENS[resolved] })
}

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
  const base = dark ? theme.darkAlgorithm : theme.defaultAlgorithm
  return {
    algorithm: [base, pinTokens(resolved)],
    token: {
      colorPrimary: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorInfo: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorTextBase: dark ? brand.colors.ice : brand.colors.navy,
      colorBgBase: dark ? brand.colors.navy : brand.colors.white,
      colorBgLayout: dark ? brand.colors.navy : LIGHT_LAYOUT_BG,
      ...(!dark ? { colorLink: LIGHT_PRIMARY, colorError: '#C62828', colorSuccess: '#2E7D32', colorWarning: '#A15C00' } : {}),
      colorTextPlaceholder: PINNED_TOKENS[resolved].colorTextTertiary,
      colorTextDescription: PINNED_TOKENS[resolved].colorTextTertiary,
      fontFamily: FONT_FAMILY,
      borderRadius: 6,
      borderRadiusLG: 10,
    },
    components: {
      ...(dark ? { Button: { primaryColor: brand.colors.navy, dangerColor: brand.colors.navy } } : {}),
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
