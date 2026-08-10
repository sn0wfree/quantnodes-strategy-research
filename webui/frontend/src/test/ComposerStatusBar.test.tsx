import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ComposerStatusBar } from '../components/chat/ComposerStatusBar'
import { useModeStore } from '../stores/mode'
import { useModelStore } from '../stores/model'
import { useThinkingPrefStore } from '../stores/thinkingPref'
import { useSystemStore } from '../stores/system'

beforeEach(() => {
  // Reset stores
  useModeStore.setState({ mode: 'build' })
  useModelStore.setState({ providers: [], sessionModels: {} })
  useThinkingPrefStore.setState({ thinkingMode: 'auto' })
  useSystemStore.setState({ modelInfo: { model: 'test-model', context_tokens: 128000 } as any })
})

describe('ComposerStatusBar', () => {
  it('renders mode toggle with default build mode', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('Build')).toBeDefined()
  })

  it('toggles mode on click', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    const btn = screen.getByText('Build')
    fireEvent.click(btn)
    expect(useModeStore.getState().mode).toBe('plan')
    expect(screen.getByText('Plan')).toBeDefined()
  })

  it('renders model selector', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('test-model')).toBeDefined()
  })

  it('renders thinking selector with auto default', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('Auto')).toBeDefined()
  })

  it('opens thinking dropdown on click', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    const thinkingBtn = screen.getByText('Auto')
    fireEvent.click(thinkingBtn)
    expect(screen.getByText('Off')).toBeDefined()
    expect(screen.getByText('On')).toBeDefined()
  })

  it('changes thinking mode', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    const thinkingBtn = screen.getByText('Auto')
    fireEvent.click(thinkingBtn)
    const offBtn = screen.getByText('Off')
    fireEvent.click(offBtn)
    expect(useThinkingPrefStore.getState().thinkingMode).toBe('off')
  })

  it('renders context tokens', () => {
    render(<ComposerStatusBar sessionId="s1" />)
    expect(screen.getByText('128K')).toBeDefined()
  })
})
