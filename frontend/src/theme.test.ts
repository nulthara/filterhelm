import { describe, expect, it, vi } from 'vitest'
import { theme } from 'antd'
import brand from './assets/brand-tokens.json'
import { FONT_FAMILY, LIGHT_LAYOUT_BG, LIGHT_PRIMARY, THEME_STORAGE_KEY, parseThemeMode, readThemeMode, resolveTheme, themeConfig, writeThemeMode, type ThemeMode } from './theme'

function contrastRatio(hexA: string, hexB: string): number {
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map((offset) => {
      const channel = parseInt(hex.slice(offset, offset + 2), 16) / 255
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    })
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
  }
  const a = luminance(hexA)
  const b = luminance(hexB)
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
    expect(config.algorithm).toBe(dark ? theme.darkAlgorithm : theme.defaultAlgorithm)
    expect(config.token).toEqual({
      colorPrimary: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorInfo: dark ? brand.colors.blue : LIGHT_PRIMARY,
      colorTextBase: dark ? brand.colors.ice : brand.colors.navy,
      colorBgBase: dark ? brand.colors.navy : brand.colors.white,
      colorBgLayout: dark ? brand.colors.navy : LIGHT_LAYOUT_BG,
      fontFamily: FONT_FAMILY, borderRadius: 6, borderRadiusLG: 10,
    })
    expect(config.components?.Button?.primaryColor).toBe(dark ? brand.colors.navy : undefined)
    expect(config.components?.Layout).toEqual({ headerBg: brand.colors.navy, siderBg: brand.colors.navy, triggerBg: brand.colors.navy })
    expect(config.components?.Menu).toEqual({
      darkItemBg: brand.colors.navy, darkSubMenuItemBg: brand.colors.navy, darkPopupBg: brand.colors.navy,
      darkItemSelectedBg: 'rgba(30, 136, 255, 0.24)', darkItemSelectedColor: brand.colors.white,
    })
    expect(FONT_FAMILY).toBe(`'Inter Variable', ${brand.typography.preferred}, ${brand.typography.fallback}`)
    expect(LIGHT_PRIMARY).toBe('#0A6FE0')
    expect(LIGHT_LAYOUT_BG).toBe('#F4F8FD')
  })
  it.each([
    [brand.colors.white, LIGHT_PRIMARY], [brand.colors.navy, brand.colors.blue],
    [brand.colors.blue, brand.colors.navy], [brand.colors.navy, LIGHT_LAYOUT_BG],
    [brand.colors.ice, brand.colors.navy],
  ])('provides AA text contrast for %s on %s', (foreground, background) => {
    expect(contrastRatio(foreground, background)).toBeGreaterThanOrEqual(4.5)
  })
})
