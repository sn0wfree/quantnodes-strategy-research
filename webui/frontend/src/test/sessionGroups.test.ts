import { describe, it, expect, vi } from 'vitest'
import { groupSessions } from '../utils/sessionGroups'

describe('groupSessions', () => {
  const now = Date.now() / 1000
  const day = 86400

  it('buckets sessions into today / week / older', () => {
    const list = [
      { id: 'old', updated_at: now - 30 * day },
      { id: 'week', updated_at: now - 3 * day },
      { id: 'today', updated_at: now - 3600 },
    ]
    const groups = groupSessions(list)
    expect(groups.today.map((s) => s.id)).toEqual(['today'])
    expect(groups.week.map((s) => s.id)).toEqual(['week'])
    expect(groups.older.map((s) => s.id)).toEqual(['old'])
  })

  it('keeps relative order within each group', () => {
    const list = [
      { id: 'a', updated_at: now - 5 * day },
      { id: 'b', updated_at: now - 2 * day },
      { id: 'c', updated_at: now - 3 * day },
    ]
    const groups = groupSessions(list)
    expect(groups.week.map((s) => s.id)).toEqual(['a', 'b', 'c'])
  })

  it('treats exactly-7-day-old session as week bucket', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-06T12:00:00Z'))
    const t = Date.now() / 1000
    const list = [{ id: 'edge', updated_at: t - 7 * day }]
    expect(groupSessions(list).week.map((s) => s.id)).toEqual(['edge'])
    vi.useRealTimers()
  })

  it('handles empty input', () => {
    const groups = groupSessions([])
    expect(groups.today).toEqual([])
    expect(groups.week).toEqual([])
    expect(groups.older).toEqual([])
  })

  it('uses the actual clock for bucketing', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-06T12:00:00Z'))
    const t = Date.now() / 1000
    const groups = groupSessions([
      { id: 'recent', updated_at: t - 600 },
      { id: 'ancient', updated_at: t - 90 * day },
    ])
    expect(groups.today.map((s) => s.id)).toEqual(['recent'])
    expect(groups.older.map((s) => s.id)).toEqual(['ancient'])
    vi.useRealTimers()
  })
})
