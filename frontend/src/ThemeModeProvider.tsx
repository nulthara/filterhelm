import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { App as AntApp, ConfigProvider } from 'antd'
import { readThemeMode, resolveTheme, themeConfig, writeThemeMode, type ThemeMode } from './theme'
import { ThemeModeContext } from './themeContext'

interface ThemeModeProviderProps {
  children: ReactNode
}

function storage(): Storage | undefined {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

function subscribe(onChange: () => void): () => void {
  const query = window.matchMedia('(prefers-color-scheme: dark)')
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

function getSnapshot(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export default function ThemeModeProvider({ children }: ThemeModeProviderProps) {
  const [mode, setModeState] = useState(() => readThemeMode(storage()))
  const prefersDark = useSyncExternalStore(subscribe, getSnapshot, () => false)
  const resolved = resolveTheme(mode, prefersDark)

  useEffect(() => {
    document.documentElement.dataset.theme = resolved
  }, [resolved])

  const setMode = (next: ThemeMode) => {
    writeThemeMode(storage(), next)
    setModeState(next)
  }

  return (
    <ThemeModeContext.Provider value={{ mode, resolved, setMode }}>
      <ConfigProvider theme={themeConfig(resolved)}>
        <AntApp>{children}</AntApp>
      </ConfigProvider>
    </ThemeModeContext.Provider>
  )
}
