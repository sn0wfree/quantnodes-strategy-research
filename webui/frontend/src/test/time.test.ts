// utils/time.ts — formatting helpers shared across components.

import { describe, it, expect } from 'vitest'
import { formatTime, formatTimestamp, formatDuration } from '../utils/time'

describe('formatTime', () => {
  it('renders HH:MM with leading zeros', () => {
    // 2026-08-06 09:05 in local time
    const ts = new Date(2026, 7, 6, 9, 5, 0).getTime() / 1000
    const out = formatTime(ts)
    expect(out).toMatch(/^09:05$/)
  })

  it('accepts a unix timestamp in seconds', () => {
    const ts = new Date(2026, 7, 6, 23, 59, 0).getTime() / 1000
    expect(formatTime(ts)).toMatch(/^23:59$/)
  })
})

describe('formatTimestamp', () => {
  it('returns "刚刚" for under 1 minute', () => {
    const now = Date.now() / 1000
    expect(formatTimestamp(now)).toBe('刚刚')
  })

  it('returns minutes for under an hour', () => {
    const now = Date.now() / 1000
    expect(formatTimestamp(now - 5 * 60)).toBe('5 分钟前')
  })

  it('returns hours for under a day', () => {
    const now = Date.now() / 1000
    expect(formatTimestamp(now - 3 * 3600)).toBe('3 小时前')
  })

  it('returns days for under a week', () => {
    const now = Date.now() / 1000
    expect(formatTimestamp(now - 2 * 86400)).toBe('2 天前')
  })

  it('falls back to a localized date for older timestamps', () => {
    const longAgo = new Date(2020, 0, 15).getTime() / 1000
    const out = formatTimestamp(longAgo)
    expect(out).toMatch(/2020/)
  })
})

describe('formatDuration', () => {
  it('formats sub-second durations as "<n>ms"', () => {
    expect(formatDuration(0)).toBe('0ms')
    expect(formatDuration(250)).toBe('250ms')
    expect(formatDuration(999)).toBe('999ms')
  })

  it('formats sub-minute durations as seconds with 1 decimal', () => {
    expect(formatDuration(1000)).toBe('1.0s')
    expect(formatDuration(5500)).toBe('5.5s')
    expect(formatDuration(59500)).toBe('59.5s')
  })

  it('formats >= 1 minute durations as "Xm Ys"', () => {
    expect(formatDuration(60000)).toBe('1m 0s')
    expect(formatDuration(125000)).toBe('2m 5s')
  })
})