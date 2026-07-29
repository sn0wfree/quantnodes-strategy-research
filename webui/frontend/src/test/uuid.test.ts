import { describe, it, expect } from 'vitest'
import { uuid } from '../utils/uuid'

describe('uuid()', () => {
  it('returns a non-empty string', () => {
    const id = uuid()
    expect(typeof id).toBe('string')
    expect(id.length).toBeGreaterThan(0)
  })

  it('returns a 12-character suffix without prefix', () => {
    const id = uuid()
    expect(id.length).toBe(12)
  })

  it('returns prefixed IDs in correct format', () => {
    expect(uuid('msg')).toMatch(/^msg_[A-Za-z0-9_-]{12}$/)
    expect(uuid('session')).toMatch(/^session_[A-Za-z0-9_-]{12}$/)
    expect(uuid('agent')).toMatch(/^agent_[A-Za-z0-9_-]{12}$/)
  })

  it('produces unique IDs across many calls', () => {
    const N = 10_000
    const seen = new Set<string>()
    for (let i = 0; i < N; i++) {
      seen.add(uuid('msg'))
    }
    expect(seen.size).toBe(N)
  })

  it('produces unique IDs without prefix', () => {
    const N = 10_000
    const seen = new Set<string>()
    for (let i = 0; i < N; i++) {
      seen.add(uuid())
    }
    expect(seen.size).toBe(N)
  })

  it('works in non-secure context (no crypto.randomUUID required)', () => {
    // Simulate non-secure context by removing crypto.randomUUID.
    // crypto.getRandomValues is still available in real browsers; the test
    // ensures uuid() does not depend on randomUUID.
    const original = (globalThis as any).crypto?.randomUUID
    try {
      if ((globalThis as any).crypto) {
        delete (globalThis as any).crypto.randomUUID
      }
      const id = uuid('msg')
      expect(id).toMatch(/^msg_[A-Za-z0-9_-]{12}$/)
    } finally {
      if (original && (globalThis as any).crypto) {
        (globalThis as any).crypto.randomUUID = original
      }
    }
  })

  it('contains only URL-safe characters', () => {
    const N = 1000
    for (let i = 0; i < N; i++) {
      const id = uuid()
      expect(id).toMatch(/^[A-Za-z0-9_-]+$/)
    }
  })
})