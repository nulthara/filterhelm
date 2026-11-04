import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { watchPasswordManagerIgnore } from './passwordManagerIgnore'
import ThemeModeProvider from './ThemeModeProvider'
import '@fontsource-variable/inter'
import './styles.css'

watchPasswordManagerIgnore(document.body)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeModeProvider>
      <App />
    </ThemeModeProvider>
  </StrictMode>,
)
