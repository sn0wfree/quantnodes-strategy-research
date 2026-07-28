import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useReducedMotion } from '../hooks/useReducedMotion'

describe('useReducedMotion', () => {
  let mockMatchMedia: any

  beforeEach(() => {
    mockMatchMedia = {
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn(() => mockMatchMedia),
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('returns false when prefers-reduced-motion is not set', () => {
    mockMatchMedia.matches = false

    const { result } = renderHook(() => useReducedMotion())

    expect(result.current).toBe(false)
  })

  it('returns true when prefers-reduced-motion is set on mount', () => {
    mockMatchMedia.matches = true

    const { result } = renderHook(() => useReducedMotion())

    expect(result.current).toBe(true)
  })

  it('subscribes to change events on mount', () => {
    renderHook(() => useReducedMotion())

    expect(mockMatchMedia.addEventListener).toHaveBeenCalledWith('change', expect.any(Function))
  })

  it('updates state when media query changes', () => {
    let changeHandler: (e: { matches: boolean }) => void = () => {}
    mockMatchMedia.addEventListener.mockImplementation((type: string, fn: any) => {
      if (type === 'change') changeHandler = fn
    })

    mockMatchMedia.matches = false
    const { result } = renderHook(() => useReducedMotion())
    expect(result.current).toBe(false)

    act(() => {
      changeHandler({ matches: true })
    })

    expect(result.current).toBe(true)
  })

  it('removes listener on unmount', () => {
    const { unmount } = renderHook(() => useReducedMotion())

    unmount()

    expect(mockMatchMedia.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function))
  })
})