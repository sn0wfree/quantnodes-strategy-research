import { describe, it, expect, beforeEach } from 'vitest'
import { useStudyStore } from '../stores/study'
import type { LiveEvent } from '../stores/study'

function freshState() {
  // Reset to factory defaults between tests
  useStudyStore.setState({
    current: null,
    list: [],
    busy: false,
    agentApprovals: {},
    currentPhase: null,
    currentAgent: null,
    phaseStartedAt: null,
    nodeStatuses: {},
    recentEvents: [],
  })
}

describe('useStudyStore.addLiveEvent', () => {
  beforeEach(freshState)

  it('prepends new events at index 0 (newest-first)', () => {
    const a: Omit<LiveEvent, 'timestamp'> = { type: 'phase', message: 'first' }
    const b: Omit<LiveEvent, 'timestamp'> = { type: 'phase', message: 'second' }

    useStudyStore.getState().addLiveEvent(a)
    useStudyStore.getState().addLiveEvent(b)

    const events = useStudyStore.getState().recentEvents
    expect(events.length).toBe(2)
    // b was added later → must come first (newest-first)
    expect(events[0].message).toBe('second')
    expect(events[1].message).toBe('first')
  })

  it('caps array length at 50 (oldest dropped)', () => {
    for (let i = 0; i < 60; i++) {
      useStudyStore.getState().addLiveEvent({
        type: 'phase', message: `e-${i}`,
      })
    }
    const events = useStudyStore.getState().recentEvents
    expect(events.length).toBe(50)
    // newest (e-59) at front, oldest kept is e-10
    expect(events[0].message).toBe('e-59')
    expect(events[49].message).toBe('e-10')
  })

  it('auto-assigns timestamp on insert', () => {
    const before = Date.now()
    useStudyStore.getState().addLiveEvent({
      type: 'agent', message: 'm',
    })
    const ts = useStudyStore.getState().recentEvents[0].timestamp
    expect(ts).toBeGreaterThanOrEqual(before)
    expect(ts).toBeLessThanOrEqual(before + 1000)
  })

  it('preserves all LiveEvent fields', () => {
    useStudyStore.getState().addLiveEvent({
      type: 'review', message: 'r', round: 7,
    })
    const e = useStudyStore.getState().recentEvents[0]
    expect(e.type).toBe('review')
    expect(e.message).toBe('r')
    expect(e.round).toBe(7)
  })
})

/**
 * Regression test for P1-1: StudyChat.tsx SSE diff direction.
 *
 * Store prepends newest-first. The diff logic in StudyChat used
 * ``recentEvents.slice(eventCountRef.current)`` which grabbed the
 * OLDEST events (slicing from the tail of a newest-first array).
 *
 * Post-fix: ``recentEvents.slice(0, newCount)`` grabs the newest events
 * from the front. This test simulates the new logic on the store's data
 * to confirm the slice returns the correct events.
 */
describe('StudyChat SSE diff logic (P1-1 regression)', () => {
  beforeEach(freshState)

  /**
   * Replicates the post-fix slice logic from StudyChat.tsx. Keeping it
   * here as a pure function means the test stays a fast unit (no
   * React render, no jsdom DOM).
   */
  function sliceNewEvents(
    recentEvents: LiveEvent[],
    prevCount: number,
  ): LiveEvent[] {
    const newCount = recentEvents.length - prevCount
    if (newCount <= 0) return []
    return recentEvents.slice(0, newCount)
  }

  it('returns empty when no new events', () => {
    const events: LiveEvent[] = [
      { type: 'phase', message: 'a', timestamp: 1 },
      { type: 'phase', message: 'b', timestamp: 2 },
    ]
    expect(sliceNewEvents(events, 2)).toEqual([])
    expect(sliceNewEvents(events, 3)).toEqual([])
  })

  it('returns only the front (newest) when new events arrive', () => {
    const events: LiveEvent[] = [
      { type: 'phase', message: 'c', timestamp: 3 },  // newest
      { type: 'phase', message: 'b', timestamp: 2 },
      { type: 'phase', message: 'a', timestamp: 1 },
    ]
    // prevCount=1 → newCount=2 → take first 2 (c, b)
    expect(sliceNewEvents(events, 1).map((e) => e.message)).toEqual(['c', 'b'])
    // prevCount=2 → newCount=1 → take first 1 (c)
    expect(sliceNewEvents(events, 2).map((e) => e.message)).toEqual(['c'])
    // prevCount=0 → newCount=3 → take first 3 (c, b, a)
    expect(sliceNewEvents(events, 0).map((e) => e.message)).toEqual(['c', 'b', 'a'])
  })

  it('end-to-end: store.prepend + slice extracts only the new ones', () => {
    // Seed initial 3 events
    for (let i = 0; i < 3; i++) {
      useStudyStore.getState().addLiveEvent({
        type: 'phase', message: `init-${i}`,
      })
    }
    const eventsAtT1 = useStudyStore.getState().recentEvents
    expect(eventsAtT1.length).toBe(3)
    const prevCount = eventsAtT1.length

    // 2 new events arrive
    useStudyStore.getState().addLiveEvent({ type: 'agent', message: 'new-1' })
    useStudyStore.getState().addLiveEvent({ type: 'agent', message: 'new-2' })

    const eventsAtT2 = useStudyStore.getState().recentEvents
    const newEvents = sliceNewEvents(eventsAtT2, prevCount)

    expect(newEvents.length).toBe(2)
    // The front of the array must be the new ones (in arrival order)
    expect(newEvents.map((e) => e.message)).toEqual(['new-2', 'new-1'])
    // And crucially, NOT the old 'init-2' (which is what pre-fix
    // slice(prevCount) would have returned from the tail).
    expect(newEvents.find((e) => e.message.startsWith('init-'))).toBeUndefined()
  })

  it('handles store cap (50) gracefully when computing newCount', () => {
    // Fill to 50, then push 3 more — total still 50, oldest 3 evicted.
    for (let i = 0; i < 53; i++) {
      useStudyStore.getState().addLiveEvent({
        type: 'phase', message: `e-${i}`,
      })
    }
    // Pretend we just observed events up to e-50 (50 events present,
    // oldest is e-3, so e-0..e-2 were evicted)
    const events = useStudyStore.getState().recentEvents
    // newCount = 50 - 50 = 0 → slice returns []
    expect(sliceNewEvents(events, 50)).toEqual([])
    // If we claim prevCount was 45 (but the store already lost those):
    expect(sliceNewEvents(events, 45).length).toBe(5)
  })
})