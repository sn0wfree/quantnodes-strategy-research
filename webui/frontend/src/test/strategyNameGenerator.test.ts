// Tests for strategyNameGenerator.ts — keyword extraction, compression, validation.

import { describe, it, expect } from 'vitest'
import {
  toBase62,
  fromBase62,
  compressTimestamp,
  decompressTimestamp,
  compressSessionTs,
  decompressSessionTs,
  extractKeywords,
  generateAbbreviatedName,
  generateRandom2,
  generateSessionHash7,
  generateStrategyName,
  regenerateWithRandom,
  validateStrategyName,
} from '../utils/strategyNameGenerator'

// ── Base62 round-trip ──────────────────────────────────────────────

describe('Base62 encoding', () => {
  it('toBase62 and fromBase62 round-trip', () => {
    const values = [0n, 1n, 61n, 62n, 12345n, 999999999n]
    for (const v of values) {
      const encoded = toBase62(v, 7)
      const decoded = fromBase62(encoded)
      expect(decoded).toBe(v)
    }
  })

  it('toBase62 produces fixed length', () => {
    const result = toBase62(42n, 10)
    expect(result).toHaveLength(10)
  })

  it('toBase62 zero-pads', () => {
    const result = toBase62(0n, 5)
    expect(result).toBe('00000')
  })
})

// ── Timestamp compression ──────────────────────────────────────────

describe('Timestamp compression', () => {
  it('round-trips a known date', () => {
    const date = new Date(2026, 7, 4, 13, 30, 45) // Aug 4, 2026 13:30:45
    const compressed = compressTimestamp(date)
    expect(compressed).toHaveLength(7)

    const decompressed = decompressTimestamp(compressed)
    expect(decompressed.YY).toBe(26)
    expect(decompressed.MM).toBe(8)
    expect(decompressed.DD).toBe(4)
    expect(decompressed.hh).toBe(13)
    expect(decompressed.mm).toBe(30)
    expect(decompressed.ss).toBe(45)
  })

  it('round-trips midnight', () => {
    const date = new Date(2025, 0, 1, 0, 0, 0)
    const compressed = compressTimestamp(date)
    const decompressed = decompressTimestamp(compressed)
    expect(decompressed.YY).toBe(25)
    expect(decompressed.MM).toBe(1)
    expect(decompressed.DD).toBe(1)
    expect(decompressed.hh).toBe(0)
    expect(decompressed.mm).toBe(0)
    expect(decompressed.ss).toBe(0)
  })

  it('round-trips end of day', () => {
    const date = new Date(2029, 11, 31, 23, 59, 59)
    const compressed = compressTimestamp(date)
    const decompressed = decompressTimestamp(compressed)
    expect(decompressed.YY).toBe(29)
    expect(decompressed.MM).toBe(12)
    expect(decompressed.DD).toBe(31)
    expect(decompressed.hh).toBe(23)
    expect(decompressed.mm).toBe(59)
    expect(decompressed.ss).toBe(59)
  })
})

// ── Session + Timestamp compression ────────────────────────────────

describe('Session+Timestamp compression', () => {
  it('round-trips session hash + timestamp', () => {
    const sessionHash7 = 'abc1234'
    const timestampTs = compressTimestamp(new Date(2026, 7, 4, 12, 0, 0))
    const compressed = compressSessionTs(sessionHash7, timestampTs)
    expect(compressed).toHaveLength(12)

    const decompressed = decompressSessionTs(compressed)
    expect(decompressed.sessionHash7).toBe(sessionHash7)
    expect(decompressed.timestamp).toBe(timestampTs)
  })
})

// ── Keyword extraction ─────────────────────────────────────────────

describe('extractKeywords', () => {
  it('extracts momentum keywords', () => {
    const keywords = extractKeywords('研究a股动量因子')
    expect(keywords).toContain('Rsrch')
    expect(keywords).toContain('Ashare')
    expect(keywords).toContain('Mom')
  })

  it('extracts volatility keywords', () => {
    const keywords = extractKeywords('测试波动率策略')
    expect(keywords).toContain('Test')
    expect(keywords).toContain('Vol')
  })

  it('extracts value keywords', () => {
    const keywords = extractKeywords('价值因子优化')
    expect(keywords).toContain('Val')
    expect(keywords).toContain('Opt')
  })

  it('extracts rotation keywords', () => {
    const keywords = extractKeywords('行业轮动策略')
    expect(keywords).toContain('SectorRot')
  })

  it('returns empty for unknown text', () => {
    const keywords = extractKeywords('hello world 123')
    expect(keywords).toEqual([])
  })

  it('handles overlapping keywords greedily', () => {
    // 动量因子 should extract Mom (动量) and skip 因子 (filter word)
    const keywords = extractKeywords('动量因子')
    expect(keywords).toContain('Mom')
    expect(keywords).not.toContain('')
  })

  it('extracts multiple factor types', () => {
    const keywords = extractKeywords('多因子动量反转')
    expect(keywords).toContain('Multi')
    expect(keywords).toContain('Mom')
    expect(keywords).toContain('Rev')
  })
})

// ── Abbreviated name generation ────────────────────────────────────

describe('generateAbbreviatedName', () => {
  it('uses first 2-3 keywords', () => {
    const name = generateAbbreviatedName(['Ashare', 'Mom', 'Rsrch'])
    expect(name).toBe('AshareMomRsrch')
  })

  it('truncates to 20 chars', () => {
    const longKeywords = ['VeryLongKeyword', 'Another', 'Third']
    const name = generateAbbreviatedName(longKeywords)
    expect(name.length).toBeLessThanOrEqual(20)
  })

  it('returns Strategy for empty keywords', () => {
    expect(generateAbbreviatedName([])).toBe('Strategy')
  })

  it('handles single keyword', () => {
    expect(generateAbbreviatedName(['Mom'])).toBe('Mom')
  })
})

// ── Random string generation ───────────────────────────────────────

describe('generateRandom2', () => {
  it('returns 2-char string', () => {
    const r = generateRandom2()
    expect(r).toHaveLength(2)
  })

  it('contains only alphanumeric chars', () => {
    for (let i = 0; i < 20; i++) {
      const r = generateRandom2()
      expect(r).toMatch(/^[0-9a-z]{2}$/)
    }
  })
})

// ── Session hash ───────────────────────────────────────────────────

describe('generateSessionHash7', () => {
  it('returns 7-char string', () => {
    const hash = generateSessionHash7('test-session-id')
    expect(hash).toHaveLength(7)
  })

  it('is deterministic', () => {
    const h1 = generateSessionHash7('sess-1')
    const h2 = generateSessionHash7('sess-1')
    expect(h1).toBe(h2)
  })

  it('different sessions produce different hashes', () => {
    const h1 = generateSessionHash7('sess-1')
    const h2 = generateSessionHash7('sess-2')
    expect(h1).not.toBe(h2)
  })
})

// ── Full name generation ───────────────────────────────────────────

describe('generateStrategyName', () => {
  it('generates a valid name', () => {
    const { name, parts } = generateStrategyName(
      '研究A股动量因子',
      'admin',
      'sess-123',
      new Date(2026, 7, 4, 12, 0, 0),
    )
    expect(name).toContain('admin')
    expect(name.split('_')).toHaveLength(4)
    expect(parts.abbreviatedName).toBeTruthy()
    expect(parts.userId).toBe('admin')
    expect(parts.compressedSessionTs).toHaveLength(12)
    expect(parts.random2).toHaveLength(2)
  })

  it('name format is {abbrev}_{user}_{compressed}_{random2}', () => {
    const { name } = generateStrategyName('测试', 'user1', 'sess-1')
    const parts = name.split('_')
    expect(parts.length).toBe(4)
    expect(parts[1]).toBe('user1')
  })

  it('uses provided timestamp', () => {
    const ts = new Date(2025, 0, 15, 8, 30, 0)
    const { parts } = generateStrategyName('动量', 'u', 's', ts)
    const decompressed = decompressSessionTs(parts.compressedSessionTs)
    const timeDecomp = decompressTimestamp(decompressed.timestamp)
    expect(timeDecomp.YY).toBe(25)
    expect(timeDecomp.MM).toBe(1)
    expect(timeDecomp.DD).toBe(15)
  })
})

// ── Regenerate with random ─────────────────────────────────────────

describe('regenerateWithRandom', () => {
  it('preserves other parts', () => {
    const { parts } = generateStrategyName('动量', 'admin', 'sess-1')
    const { name, random2 } = regenerateWithRandom({
      abbreviatedName: parts.abbreviatedName,
      userId: parts.userId,
      compressedSessionTs: parts.compressedSessionTs,
    })
    expect(name).toContain(parts.abbreviatedName)
    expect(name).toContain(parts.userId)
    expect(name).toContain(parts.compressedSessionTs)
    expect(random2).toHaveLength(2)
  })
})

// ── Validation ─────────────────────────────────────────────────────

describe('validateStrategyName', () => {
  it('accepts valid name', () => {
    expect(validateStrategyName('AshareMom_admin_abc1234def567_x9')).toEqual({ valid: true })
  })

  it('rejects empty name', () => {
    const result = validateStrategyName('')
    expect(result.valid).toBe(false)
    expect(result.error).toContain('不能为空')
  })

  it('rejects too long name', () => {
    const longName = 'A'.repeat(65)
    const result = validateStrategyName(longName)
    expect(result.valid).toBe(false)
    expect(result.error).toContain('过长')
  })

  it('rejects name with too few parts', () => {
    const result = validateStrategyName('only_two_parts')
    expect(result.valid).toBe(false)
    expect(result.error).toContain('格式')
  })

  it('rejects name with invalid characters', () => {
    const result = validateStrategyName('valid_name!@#_extra_parts')
    expect(result.valid).toBe(false)
    expect(result.error).toContain('字母、数字和下划线')
  })
})
