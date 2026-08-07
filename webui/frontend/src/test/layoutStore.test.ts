import { describe, it, expect, beforeEach } from 'vitest'
import { useLayoutStore } from '../stores/layout'

describe('useLayoutStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useLayoutStore.setState({
      navWidth: 64,
      rightPanelVisible: true,
      rightPanelTab: 'progress',
      workMode: 'monitor',
      leftRatio: 0.55,
      contextRatio: 0.22,
      rightRatio: 0.30,
      density: 'comfortable',
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
    useLayoutStore.getState().setRightPanelTab('progress')
    expect(useLayoutStore.getState().rightPanelTab).toBe('progress')
    expect(useLayoutStore.getState().rightPanelVisible).toBe(true)
  })

  it('switches work mode', () => {
    useLayoutStore.getState().setWorkMode('chat')
    expect(useLayoutStore.getState().workMode).toBe('chat')
    expect(useLayoutStore.getState().rightPanelVisible).toBe(false)
  })

  it('clamps left ratio to [0.2, 0.85]', () => {
    useLayoutStore.getState().setLeftRatio(0.9)
    expect(useLayoutStore.getState().leftRatio).toBe(0.85)

    useLayoutStore.getState().setLeftRatio(0.1)
    expect(useLayoutStore.getState().leftRatio).toBe(0.2)

    useLayoutStore.getState().setLeftRatio(0.5)
    expect(useLayoutStore.getState().leftRatio).toBe(0.5)
    expect(localStorage.getItem('sr-left-ratio')).toBe('0.5')
  })

  it('defaults rightRatio to comfortable preset (0.30)', () => {
    expect(useLayoutStore.getState().rightRatio).toBe(0.30)
  })

  it('defaults contextRatio to comfortable preset (0.22)', () => {
    expect(useLayoutStore.getState().contextRatio).toBe(0.22)
  })

  it('clamps contextRatio to [0.15, 0.35] and persists', () => {
    useLayoutStore.getState().setContextRatio(0.5)
    expect(useLayoutStore.getState().contextRatio).toBe(0.35)
    useLayoutStore.getState().setContextRatio(0.05)
    expect(useLayoutStore.getState().contextRatio).toBe(0.15)
    useLayoutStore.getState().setContextRatio(0.25)
    expect(useLayoutStore.getState().contextRatio).toBe(0.25)
    expect(localStorage.getItem('sr-context-ratio')).toBe('0.25')
  })

  it('clamps rightRatio to [0.25, 0.55] and persists', () => {
    useLayoutStore.getState().setRightRatio(0.9)
    expect(useLayoutStore.getState().rightRatio).toBe(0.55)

    useLayoutStore.getState().setRightRatio(0.1)
    expect(useLayoutStore.getState().rightRatio).toBe(0.25)

    useLayoutStore.getState().setRightRatio(0.4)
    expect(useLayoutStore.getState().rightRatio).toBe(0.4)
    expect(localStorage.getItem('sr-right-ratio')).toBe('0.4')
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

  it('defaults density to comfortable', () => {
    expect(useLayoutStore.getState().density).toBe('comfortable')
  })

  it('setDensity applies preset widths and persists', () => {
    useLayoutStore.getState().setDensity('compact')
    expect(useLayoutStore.getState().density).toBe('compact')
    expect(useLayoutStore.getState().leftRatio).toBe(0.52)
    expect(useLayoutStore.getState().contextRatio).toBe(0.20)
    expect(useLayoutStore.getState().rightRatio).toBe(0.28)
    expect(localStorage.getItem('sr-density')).toBe('compact')
  })

  it('setDensity("spacious") applies spacious preset', () => {
    useLayoutStore.getState().setDensity('spacious')
    expect(useLayoutStore.getState().density).toBe('spacious')
    expect(useLayoutStore.getState().leftRatio).toBe(0.40)
    expect(useLayoutStore.getState().contextRatio).toBe(0.26)
    expect(useLayoutStore.getState().rightRatio).toBe(0.34)
  })

  it('setDensity overwrites manual ratio overrides', () => {
    useLayoutStore.getState().setLeftRatio(0.7)
    useLayoutStore.getState().setContextRatio(0.30)
    useLayoutStore.getState().setDensity('compact')
    expect(useLayoutStore.getState().leftRatio).toBe(0.52)
    expect(useLayoutStore.getState().contextRatio).toBe(0.20)
    expect(localStorage.getItem('sr-left-ratio')).toBeNull()
    expect(localStorage.getItem('sr-context-ratio')).toBeNull()
  })
})