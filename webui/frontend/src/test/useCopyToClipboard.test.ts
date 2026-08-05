// useCopyToClipboard — verifies navigator.clipboard.writeText is
// invoked with the given text and the `copied` flag auto-resets after 2s.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useCopyToClipboard } from '../hooks/useCopyToClipboard'

describe('useCopyToClipboard', () => {
  let writeText: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts with copied=false', () => {
    const { result } = renderHook(() => useCopyToClipboard())
    expect(result.current[0]).toBe(false)
  })

  it('calls writeText and flips copied to true', () => {
    const { result } = renderHook(() => useCopyToClipboard())
    act(() => result.current[1]('hello'))
    expect(writeText).toHaveBeenCalledWith('hello')
    expect(result.current[0]).toBe(true)
  })

  it('resets copied to false after 2 seconds', () => {
    const { result } = renderHook(() => useCopyToClipboard())
    act(() => result.current[1]('hi'))
    expect(result.current[0]).toBe(true)
    act(() => {
      vi.advanceTimersByTime(1999)
    })
    expect(result.current[0]).toBe(true)
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current[0]).toBe(false)
  })

  it('silently degrades when clipboard.writeText rejects', () => {
    writeText.mockRejectedValueOnce(new Error('not allowed') as never)
    const { result } = renderHook(() => useCopyToClipboard())
    expect(() => act(() => result.current[1]('x'))).not.toThrow()
    expect(result.current[0]).toBe(true)
  })

  it('returns a stable copy reference', () => {
    const { result, rerender } = renderHook(() => useCopyToClipboard())
    const firstCopy = result.current[1]
    rerender()
    expect(result.current[1]).toBe(firstCopy)
  })
})