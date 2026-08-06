import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles/globals.css'
import { useThemeStore } from './stores/theme'

// Apply the persisted theme before first paint (store applies on import,
// this guards against later module-order changes).
void useThemeStore.getState().theme

// E2E test hook: expose stores on window when running against a TEST_MODE
// backend. Lets Playwright inject session state without going through the
// UI (no session creation UI exists yet). Only enabled when VITE_E2E is
// set (CI/E2E builds) — previously this ran unconditionally in production.
declare global {
  interface Window {
    __sessionStore?: unknown
    __chatStore?: unknown
    __workflowStore?: unknown
    __commandPalette?: unknown
  }
}

if (typeof window !== 'undefined' && import.meta.env.VITE_E2E) {
  import('./stores/session').then(({ useSessionStore }) => {
    window.__sessionStore = useSessionStore
  })
  import('./stores/chat').then(({ useChatStore }) => {
    window.__chatStore = useChatStore
  })
  import('./stores/workflow').then(({ useWorkflowStore }) => {
    window.__workflowStore = useWorkflowStore
  })
  import('./stores/commandPalette').then(({ useCommandPaletteStore }) => {
    window.__commandPalette = useCommandPaletteStore
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
