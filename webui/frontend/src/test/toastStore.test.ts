import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useToastStore } from '../stores/toast'

describe('useToastStore', () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] })
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts empty', () => {
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it('addToast appends with auto-incrementing id', () => {
    useToastStore.getState().addToast('info', 'hello')
    useToastStore.getState().addToast('error', 'oops')

    const toasts = useToastStore.getState().toasts
    expect(toasts.length).toBe(2)
    expect(toasts[0].message).toBe('hello')
    expect(toasts[1].message).toBe('oops')
    expect(toasts[0].id).not.toBe(toasts[1].id)
  })

  it('addToast keeps only last 3 toasts (FIFO)', () => {
    useToastStore.getState().addToast('info', '1')
    useToastStore.getState().addToast('info', '2')
    useToastStore.getState().addToast('info', '3')
    useToastStore.getState().addToast('info', '4')

    const messages = useToastStore.getState().toasts.map((t) => t.message)
    expect(messages).toEqual(['2', '3', '4'])
  })

  it('addToast auto-removes after duration', () => {
    useToastStore.getState().addToast('info', 'transient', 1000)

    expect(useToastStore.getState().toasts.length).toBe(1)
    vi.advanceTimersByTime(1100)
    expect(useToastStore.getState().toasts.length).toBe(0)
  })

  it('addToast with duration=0 does not auto-remove', () => {
    useToastStore.getState().addToast('info', 'sticky', 0)

    expect(useToastStore.getState().toasts.length).toBe(1)
    vi.advanceTimersByTime(10000)
    expect(useToastStore.getState().toasts.length).toBe(1)
  })

  it('removeToast removes by id', () => {
    useToastStore.getState().addToast('info', 'keep')
    useToastStore.getState().addToast('info', 'remove me')

    const toasts = useToastStore.getState().toasts
    const targetId = toasts.find((t) => t.message === 'remove me')!.id

    useToastStore.getState().removeToast(targetId)

    const remaining = useToastStore.getState().toasts
    expect(remaining.length).toBe(1)
    expect(remaining[0].message).toBe('keep')
  })

  it('supports all toast types', () => {
    // Store keeps last 3 → add 4 to verify all 4 types round-trip
    useToastStore.getState().addToast('info', 'i')
    useToastStore.getState().addToast('success', 's')
    useToastStore.getState().addToast('error', 'e')
    useToastStore.getState().addToast('warning', 'w')

    const stored = useToastStore.getState().toasts.map((t) => t.message)
    // Only last 3 should remain (info was evicted)
    expect(stored).toEqual(['s', 'e', 'w'])
    expect(useToastStore.getState().toasts.find((t) => t.type === 'warning')).toBeDefined()
    expect(useToastStore.getState().toasts.find((t) => t.type === 'info')).toBeUndefined()
  })
})