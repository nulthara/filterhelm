import { createContext, useContext } from 'react'
import type { ResolvedTheme, ThemeMode } from './theme'

export interface ThemeModeValue {
  mode: ThemeMode
  resolved: ResolvedTheme
  setMode: (mode: ThemeMode) => void
}

export const ThemeModeContext = createContext<ThemeModeValue | null>(null)

export function useThemeMode(): ThemeModeValue {
  const value = useContext(ThemeModeContext)
  if (!value) throw new Error('useThemeMode must be used within ThemeModeProvider')
  return value
}
