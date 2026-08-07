// layout store — covers setNavWidth, setWorkMode (toggling
// rightPanelVisible), setSettingsOpen, and chatLayout persistence.

import { describe, it, expect, beforeEach } from 'vitest'
import { useLayoutStore } from '../stores/layout'

beforeEach(() => {
  localStorage.clear()
  useLayoutStore.setState({
    navWidth: 64,
    sidebarOpen: true,
    rightPanelVisible: true,
    workMode: 'monitor',
    rightRatio: 0.3,
    settingsOpen: false,
    chatLayout: 'bubble',
  })
})

describe('useLayoutStore', () => {
  it('setNavWidth updates navWidth', () => {
    useLayoutStore.getState().setNavWidth(96)
    expect(useLayoutStore.getState().navWidth).toBe(96)
  })

  it('setWorkMode("monitor") forces rightPanelVisible=true', () => {
    useLayoutStore.setState({ rightPanelVisible: false })
    useLayoutStore.getState().setWorkMode('monitor')
    const s = useLayoutStore.getState()
    expect(s.workMode).toBe('monitor')
    expect(s.rightPanelVisible).toBe(true)
  })

  it('setWorkMode("chat") hides the right panel', () => {
    useLayoutStore.setState({ rightPanelVisible: true })
    useLayoutStore.getState().setWorkMode('chat')
    const s = useLayoutStore.getState()
    expect(s.workMode).toBe('chat')
    expect(s.rightPanelVisible).toBe(false)
  })

  it('setWorkMode("focus") hides the right panel (only monitor shows it)', () => {
    useLayoutStore.getState().setWorkMode('focus')
    expect(useLayoutStore.getState().rightPanelVisible).toBe(false)
  })

  it('toggleRightPanel persists visibility to localStorage', () => {
    useLayoutStore.getState().toggleRightPanel()
    expect(useLayoutStore.getState().rightPanelVisible).toBe(false)
    expect(localStorage.getItem('sr-right-visible')).toBe('false')
  })

  it('setSettingsOpen flips settingsOpen', () => {
    useLayoutStore.getState().setSettingsOpen(true)
    expect(useLayoutStore.getState().settingsOpen).toBe(true)
    useLayoutStore.getState().setSettingsOpen(false)
    expect(useLayoutStore.getState().settingsOpen).toBe(false)
  })

  it('setChatLayout persists to localStorage', () => {
    useLayoutStore.getState().setChatLayout('flat')
    expect(useLayoutStore.getState().chatLayout).toBe('flat')
    expect(localStorage.getItem('sr-chat-layout')).toBe('flat')
  })
})
