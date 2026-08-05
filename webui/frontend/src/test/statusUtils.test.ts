// utils/status.ts — status label + badge class mapping.

import { describe, it, expect } from 'vitest'
import { statusLabel, statusBadgeClass } from '../utils/status'

describe('statusLabel', () => {
  it('maps known status keys to localized labels', () => {
    expect(statusLabel('pending')).toBe('等待中')
    expect(statusLabel('running')).toBe('运行中')
    expect(statusLabel('completed')).toBe('已完成')
    expect(statusLabel('failed')).toBe('失败')
    expect(statusLabel('skipped')).toBe('已跳过')
    expect(statusLabel('aborted')).toBe('已中止')
    expect(statusLabel('active')).toBe('进行中')
  })

  it('falls back to the raw status for unknown keys', () => {
    expect(statusLabel('totally-unknown')).toBe('totally-unknown')
  })

  it('collapses "done" and "error" aliases to canonical labels', () => {
    expect(statusLabel('done')).toBe('已完成')
    expect(statusLabel('error')).toBe('失败')
    expect(statusLabel('success')).toBe('成功')
  })
})

describe('statusBadgeClass', () => {
  it('returns the matching Tailwind pill for known statuses', () => {
    expect(statusBadgeClass('pending')).toMatch(/slate/)
    expect(statusBadgeClass('running')).toMatch(/blue/)
    expect(statusBadgeClass('completed')).toMatch(/emerald/)
    expect(statusBadgeClass('failed')).toMatch(/red/)
    expect(statusBadgeClass('aborted')).toMatch(/orange/)
    expect(statusBadgeClass('active')).toMatch(/emerald/)
  })

  it('falls back to a neutral slate class for unknown statuses', () => {
    expect(statusBadgeClass('weird-key')).toMatch(/slate/)
  })

  it('treats "done" and "success" the same as "completed" (emerald)', () => {
    expect(statusBadgeClass('done')).toBe(statusBadgeClass('completed'))
    expect(statusBadgeClass('success')).toBe(statusBadgeClass('completed'))
  })
})