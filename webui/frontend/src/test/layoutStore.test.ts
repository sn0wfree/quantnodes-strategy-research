import { describe, it, expect, beforeEach } from 'vitest'
import { useLayoutStore } from '../stores/layout'

describe('useLayoutStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useLayoutStore.setState({
      navWidth: 64,
      rightPanelVisible: true,
      rightPanelTab: 'dag',
      workMode: 'monitor',
      leftRatio: 0.5,
      settingsOpen: false,
      chatLayout: 'bubble',
    })
  })

  it('toggles right panel visibility', () => {
    const initial = useLayoutStore.getState().rightPanelVisible
    useLayoutStore.getState().toggleRightPanel()
    expect(useLayoutStore.getState().rightPanelVisible).toBe(!initial)
  })

  it('sets right panel tab', () => {
    useLayoutStore.getState().setRightPanelTab('goal')
    expect(useLayoutStore.getState().rightPanelTab).toBe('goal')
    expect(useLayoutStore.getState().rightPanelVisible).toBe(true)
  })

  it('switches work mode', () => {
    useLayoutStore.getState().setWorkMode('chat')
    expect(useLayoutStore.getState().workMode).toBe('chat')
    expect(useLayoutStore.getState().rightPanelVisible).toBe(false)
  })

  it('clamps left ratio to [0.2, 0.8]', () => {
    useLayoutStore.getState().setLeftRatio(0.9)
    expect(useLayoutStore.getState().leftRatio).toBe(0.8)

    useLayoutStore.getState().setLeftRatio(0.1)
    expect(useLayoutStore.getState().leftRatio).toBe(0.2)

    useLayoutStore.getState().setLeftRatio(0.5)
    expect(useLayoutStore.getState().leftRatio).toBe(0.5)
  })

  it('defaults chatLayout to bubble', () => {
    expect(useLayoutStore.getState().chatLayout).toBe('bubble')
  })

  it('switches chatLayout to flat and persists to localStorage', () => {
    useLayoutStore.getState().setChatLayout('flat')
    expect(useLayoutStore.getState().chatLayout).toBe('flat')
    expect(localStorage.getItem('sr-chat-layout')).toBe('flat')
  })

  it('switches chatLayout back to bubble and updates localStorage', () => {
    useLayoutStore.getState().setChatLayout('flat')
    useLayoutStore.getState().setChatLayout('bubble')
    expect(useLayoutStore.getState().chatLayout).toBe('bubble')
    expect(localStorage.getItem('sr-chat-layout')).toBe('bubble')
  })
})