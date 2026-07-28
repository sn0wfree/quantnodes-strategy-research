import { useEffect, useCallback } from 'react'
import { useCommandPaletteStore } from '../stores/commandPalette'
import { useLayoutStore } from '../stores/layout'

type KeyBinding = {
  key: string
  meta?: boolean
  shift?: boolean
  action: () => void
}

export function useKeyboardShortcuts() {
  const togglePalette = useCommandPaletteStore((s) => s.toggle)
  const setRightPanelTab = useLayoutStore((s) => s.setRightPanelTab)
  const toggleRightPanel = useLayoutStore((s) => s.toggleRightPanel)

  const bindings: KeyBinding[] = [
    { key: 'k', meta: true, action: togglePalette },
    { key: 'g', meta: true, action: () => setRightPanelTab('goal') },
    { key: 'w', meta: true, action: () => setRightPanelTab('dag') },
    { key: 'b', meta: true, action: toggleRightPanel },
    { key: '1', meta: true, action: () => setRightPanelTab('dag') },
    { key: '2', meta: true, action: () => setRightPanelTab('goal') },
    { key: '3', meta: true, action: () => setRightPanelTab('agent') },
  ]

  const handler = useCallback(
    (e: KeyboardEvent) => {
      const isMeta = e.metaKey || e.ctrlKey
      for (const b of bindings) {
        if (b.key === e.key.toLowerCase() && b.meta === isMeta && (b.shift ?? false) === e.shiftKey) {
          e.preventDefault()
          b.action()
          return
        }
      }
    },
    [togglePalette, setRightPanelTab, toggleRightPanel]
  )

  useEffect(() => {
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handler])
}
