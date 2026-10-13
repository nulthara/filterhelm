import { describe, expect, it, vi } from 'vitest'
import { theme } from 'antd'
import brand from './assets/brand-tokens.json'
import { FONT_FAMILY, LIGHT_LAYOUT_BG, LIGHT_PRIMARY, THEME_STORAGE_KEY, parseThemeMode, readThemeMode, resolveTheme, themeConfig, writeThemeMode, type ThemeMode } from './theme'

function parseColor(color: string): number[] {
  if (/^#[0-9a-f]{6}$/i.test(color)) {
    return [1, 3, 5].map((offset) => parseInt(color.slice(offset, offset + 2), 16)).concat(1)
  }
  const match = color.match(/^rgba?\(([^)]+)\)$/)
  if (!match) throw new Error(`Unsupported color: ${color}`)
  const channels = match[1].split(',').map(Number)
  return channels.length === 3 ? [...channels, 1] : channels
}

function flatten(fg: string, bg: string): string {
  const foreground = parseColor(fg)
  const background = parseColor(bg)
  const alpha = foreground[3]
  return `rgb(${foreground.slice(0, 3).map((channel, index) => channel * alpha + background[index] * (1 - alpha)).join(', ')})`
}

function contrastRatio(fg: string, bg: string): number {
  const luminance = (color: string) => {
    const channels = parseColor(color).slice(0, 3).map((value) => {
      const channel = value / 255
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    })
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
  }
  const a = luminance(flatten(fg, bg))
  const b = luminance(bg)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

describe('theme modes', () => {
  it.each(['system', 'light', 'dark'] as const)('parses %s unchanged', (mode) => {
    expect(parseThemeMode(mode)).toBe(mode)
  })
  it.each([null, '', 'blue'])('defaults invalid value %s to system', (value) => {
    expect(parseThemeMode(value)).toBe('system')
  })
  it.each(['system', 'light', 'dark'] as const)('reads stored %s', (mode) => {
    const getItem = vi.fn().mockReturnValue(mode)
    expect(readThemeMode({ getItem })).toBe(mode)
    expect(getItem).toHaveBeenCalledWith(THEME_STORAGE_KEY)
  })
  it('handles unavailable or blocked storage', () => {
    expect(readThemeMode(undefined)).toBe('system')
    expect(readThemeMode({ getItem: () => { throw new Error('blocked') } })).toBe('system')
  })
  it.each(['system', 'light', 'dark'] as const)('writes %s', (mode) => {
    const setItem = vi.fn()
    writeThemeMode({ setItem }, mode)
    expect(setItem).toHaveBeenCalledWith(THEME_STORAGE_KEY, mode)
  })
  it('swallows write failures and unavailable storage', () => {
    expect(() => writeThemeMode(undefined, 'light')).not.toThrow()
    expect(() => writeThemeMode({ setItem: () => { throw new Error('blocked') } }, 'dark')).not.toThrow()
  })
  it.each<[ThemeMode, boolean, string]>([
    ['system', false, 'light'], ['system', true, 'dark'],
    ['light', false, 'light'], ['light', true, 'light'],
    ['dark', false, 'dark'], ['dark', true, 'dark'],
  ])('resolves %s with prefersDark=%s', (mode, prefersDark, expected) => {
    expect(resolveTheme(mode, prefersDark)).toBe(expected)
  })
})

describe('theme tokens', () => {
  it.each(['light', 'dark'] as const)('matches the %s configuration', (resolved) => {
    const dark = resolved === 'dark'
    const config = themeConfig(resolved)
    expect(config.algorithm).toEqual([dark ? theme.darkAlgorithm : theme.defaultAlgorithm, expect.any(Function)])
    expect(config.token).toEqual({
      colorPrimary: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorInfo: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorTextBase: dark ? brand.colors.ice : brand.colors.navy,
      colorBgBase: dark ? brand.colors.navy : brand.colors.white,
      colorBgLayout: dark ? brand.colors.navy : LIGHT_LAYOUT_BG,
      ...(!dark ? { colorLink: LIGHT_PRIMARY, colorError: '#C62828', colorSuccess: '#2E7D32', colorWarning: '#A15C00' } : {}),
      colorTextPlaceholder: dark ? 'rgba(230, 244, 255, 0.62)' : 'rgba(11, 31, 59, 0.62)',
      colorTextDescription: dark ? 'rgba(230, 244, 255, 0.62)' : 'rgba(11, 31, 59, 0.62)',
      fontFamily: FONT_FAMILY, borderRadius: 6, borderRadiusLG: 10,
    })
    expect(config.components?.Button).toEqual(dark ? { primaryColor: brand.colors.navy, dangerColor: brand.colors.navy } : undefined)
    expect(config.components?.Layout).toEqual({ headerBg: brand.colors.navy, siderBg: brand.colors.navy, triggerBg: brand.colors.navy })
    expect(config.components?.Menu).toEqual({
      darkItemBg: brand.colors.navy, darkSubMenuItemBg: brand.colors.navy, darkPopupBg: brand.colors.navy,
      darkItemSelectedBg: 'rgba(30, 136, 255, 0.24)', darkItemSelectedColor: brand.colors.white,
    })
    expect(FONT_FAMILY).toBe(`'Inter Variable', ${brand.typography.preferred}, ${brand.typography.fallback}`)
    expect(LIGHT_PRIMARY).toBe('#0A6FE0')
    expect(LIGHT_LAYOUT_BG).toBe('#F4F8FD')
  })
  for (const resolved of ['light', 'dark'] as const) {
    const config = themeConfig(resolved)
    const t = theme.getDesignToken(config)
    const pairs: [string, string, string][] = []
    const textTokens = [
      'colorText', 'colorTextSecondary', 'colorTextTertiary', 'colorTextDescription',
      'colorTextPlaceholder', 'colorPrimary', 'colorPrimaryHover', 'colorPrimaryText',
      'colorLink', 'colorLinkHover', 'colorInfo', 'colorInfoText', 'colorError',
      'colorErrorText', 'colorSuccess', 'colorWarning',
    ] as const
    for (const background of ['colorBgContainer', 'colorBgLayout', 'colorBgElevated'] as const) {
      for (const foreground of textTokens) {
        pairs.push([`${foreground} on ${background}`, t[foreground], t[background]])
      }
    }
    for (const status of ['Success', 'Warning', 'Error', 'Info'] as const) {
      pairs.push([`${status} tag`, t[`color${status}`], flatten(t[`color${status}Bg`], t.colorBgContainer)])
    }
    for (const preset of ['green', 'red', 'orange', 'blue'] as const) {
      pairs.push([`${preset} tag`, t[`${preset}7`], flatten(t[`${preset}1`], t.colorBgContainer)])
    }
    for (const state of ['', 'Hover', 'Active'] as const) {
      pairs.push([`primary button ${state}`, config.components?.Button?.primaryColor ?? '#FFFFFF', t[`colorPrimary${state}`]])
      pairs.push([`danger button ${state}`, config.components?.Button?.dangerColor ?? '#FFFFFF', t[`colorError${state}`]])
    }
    pairs.push(['selected menu item', '#FFFFFF', flatten('rgba(30, 136, 255, 0.24)', brand.colors.navy)])
    it.each(pairs)(`${resolved} provides AA derived contrast for %s`, (_name, foreground, background) => {
      expect(contrastRatio(foreground, background)).toBeGreaterThanOrEqual(4.5)
    })
  }
})
