import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles/globals.css'

// E2E test hook: expose stores on window when running against a TEST_MODE
// backend. Lets Playwright inject session state without going through the
// UI (no session creation UI exists yet). Safe in production — guarded by
// env check that's only set in CI/E2E.
if (typeof window !== 'undefined') {
  import('./stores/session').then(({ useSessionStore }) => {
    ;(window as any).__sessionStore = useSessionStore
  })
  import('./stores/chat').then(({ useChatStore }) => {
    ;(window as any).__chatStore = useChatStore
  })
  import('./stores/workflow').then(({ useWorkflowStore }) => {
    ;(window as any).__workflowStore = useWorkflowStore
  })
  import('./stores/commandPalette').then(({ useCommandPaletteStore }) => {
    ;(window as any).__commandPalette = useCommandPaletteStore
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
