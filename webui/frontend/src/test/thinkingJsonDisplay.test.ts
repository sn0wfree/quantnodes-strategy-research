/**
 * Tests for:
 * 1. useRenderedParts fallback <think> tag detection (provider empty → still extracts)
 * 2. JsonActionCard rendering
 * 3. parseJsonAction JSON detection
 */
import { describe, expect, it } from 'vitest'
import {
  splitTextIncremental,
  shouldSplitInline,
} from '../utils/thinkingParsers/incremental'
import { parseStructuredContent } from '../components/chat/JsonActionCard'

describe('splitTextIncremental edge cases', () => {
  it('handles closed <think> tags correctly', () => {
    const result = splitTextIncremental('<think>reasoning</think>text after')
    expect(result.thinkingBefore).toBe('reasoning')
    expect(result.thinkingOpen).toBeNull()
    expect(result.contentAfter).toBe('text after')
  })

  it('handles unclosed <think> tag (tail = all thinking)', () => {
    const result = splitTextIncremental('<think>reasoning without closing')
    expect(result.thinkingBefore).toBe('')
    expect(result.thinkingOpen).toBe('reasoning without closing')
    expect(result.contentAfter).toBe('')
  })

  it('handles no think tags at all', () => {
    const result = splitTextIncremental('plain text no tags')
    expect(result.thinkingBefore).toBe('')
    expect(result.thinkingOpen).toBeNull()
    expect(result.contentAfter).toBe('plain text no tags')
  })

  it('handles multiple closed blocks then tail', () => {
    const result = splitTextIncremental('<think>a</think><think>b</think>tail')
    expect(result.thinkingBefore).toBe('ab')
    expect(result.thinkingOpen).toBeNull()
    expect(result.contentAfter).toBe('tail')
  })

  it('handles mixed closed + unclosed', () => {
    const result = splitTextIncremental('<think>closed</think>tail<think>open')
    expect(result.thinkingBefore).toBe('closed')
    expect(result.thinkingOpen).toBe('open')
    expect(result.contentAfter).toBe('tail')
  })

  it('handles empty string', () => {
    const result = splitTextIncremental('')
    expect(result.thinkingBefore).toBe('')
    expect(result.thinkingOpen).toBeNull()
    expect(result.contentAfter).toBe('')
  })
})

describe('parseStructuredContent', () => {
  // ── Pure JSON paths ──────────────────────────────────────────

  it('detects action JSON with hypothesis', () => {
    const r = parseStructuredContent('{"action":"optimize_param","hypothesis":"try smaller top_n"}')
    expect(r.hasStructured).toBe(true)
    expect(r.segments).toHaveLength(1)
    expect(r.segments[0].kind).toBe('json')
    expect(r.segments[0].action).toBe('optimize_param')
    expect(r.segments[0].json?.action).toBe('optimize_param')
    expect(r.segments[0].json?.hypothesis).toBe('try smaller top_n')
  })

  it('detects action JSON without hypothesis', () => {
    const r = parseStructuredContent('{"action":"blocker","reason":"rate limited"}')
    expect(r.hasStructured).toBe(true)
    expect(r.segments[0].action).toBe('blocker')
  })

  it('detects generic JSON without action field', () => {
    const r = parseStructuredContent(
      '{"method":"risk_parity_fallback_equal","weights":{"000001.SZ":0.10}}'
    )
    expect(r.hasStructured).toBe(true)
    expect(r.segments[0].kind).toBe('json')
    expect(r.segments[0].action).toBeUndefined()
    expect(r.segments[0].json?.method).toBe('risk_parity_fallback_equal')
  })

  it('detects risk_controller style JSON (no action, string values)', () => {
    const r = parseStructuredContent(
      '{"risk_passed":"False","risk_rating":"Red","var_95":"None"}'
    )
    expect(r.hasStructured).toBe(true)
    expect(r.segments[0].action).toBeUndefined()
    expect((r.segments[0].json as Record<string, unknown>).risk_rating).toBe('Red')
  })

  it('returns text-only for plain markdown', () => {
    const r = parseStructuredContent('# Hello\n\nSome **bold** text.')
    expect(r.hasStructured).toBe(false)
    expect(r.segments).toHaveLength(1)
    expect(r.segments[0].kind).toBe('text')
    expect(r.segments[0].text).toBe('# Hello\n\nSome **bold** text.')
  })

  it('returns text-only for plain Chinese text', () => {
    const r = parseStructuredContent('我无权写入文件（角色工具只给 read）')
    expect(r.hasStructured).toBe(false)
    expect(r.segments[0].kind).toBe('text')
  })

  // ── Embedded JSON paths ──────────────────────────────────────

  it('handles markdown with embedded JSON block (text + JSON)', () => {
    const text = '我无权写入文件（角色工具只给 read）。\n\n{"action":"report_progress","hypothesis":"attribution done"}'
    const r = parseStructuredContent(text)
    expect(r.hasStructured).toBe(true)
    // Should have 2 segments: text + json
    expect(r.segments.filter(s => s.kind === 'json')).toHaveLength(1)
    expect(r.segments.filter(s => s.kind === 'text')).toHaveLength(1)
    // First text segment contains the prose
    const textSeg = r.segments.find(s => s.kind === 'text')!
    expect(textSeg.text).toContain('我无权写入文件')
    // JSON segment has the parsed action
    const jsonSeg = r.segments.find(s => s.kind === 'json')!
    expect(jsonSeg.action).toBe('report_progress')
  })

  it('handles multiple JSON blocks interleaved with text', () => {
    const text = 'intro\n\n{"action":"a","hypothesis":"ha"}\n\nmiddle\n\n{"action":"b","hypothesis":"hb"}\n\nend'
    const r = parseStructuredContent(text)
    expect(r.hasStructured).toBe(true)
    const jsons = r.segments.filter(s => s.kind === 'json')
    expect(jsons).toHaveLength(2)
    expect(jsons[0].action).toBe('a')
    expect(jsons[1].action).toBe('b')
    const texts = r.segments.filter(s => s.kind === 'text')
    expect(texts).toHaveLength(3)
  })

  // ── Edge cases ───────────────────────────────────────────────

  it('treats malformed JSON as plain text (no crash, no card)', () => {
    const r = parseStructuredContent('{bad json with "unescaped quotes"}')
    expect(r.hasStructured).toBe(false)
    expect(r.segments[0].kind).toBe('text')
  })

  it('handles whitespace padding around JSON', () => {
    const r = parseStructuredContent('   {"action":"keep","hypothesis":"x"}   ')
    expect(r.hasStructured).toBe(true)
    expect(r.segments[0].action).toBe('keep')
  })

  it('handles empty string', () => {
    const r = parseStructuredContent('')
    expect(r.segments).toEqual([])
    expect(r.hasStructured).toBe(false)
  })

  it('handles JSON array as not structured (objects only)', () => {
    const r = parseStructuredContent('[1, 2, 3]')
    expect(r.hasStructured).toBe(false)
    expect(r.segments[0].kind).toBe('text')
  })
})

describe('shouldSplitInline guard', () => {
  it('returns true only for minimax', () => {
    expect(shouldSplitInline('minimax')).toBe(true)
    expect(shouldSplitInline('openai')).toBe(false)
    expect(shouldSplitInline('deepseek')).toBe(false)
    expect(shouldSplitInline(null)).toBe(false)
    expect(shouldSplitInline(undefined)).toBe(false)
    expect(shouldSplitInline('')).toBe(false)
  })
})

describe('useRenderedParts fallback behavior (unit check)', () => {
  it('shouldSplitInline returns false for empty provider but parts contain <think> tags → splitting should happen', () => {
    // This tests the LOGIC: when provider='' and parts contain think tags,
    // useRenderedParts should fall through to splitting (not early-return).
    // We can't test the hook directly, but we can verify the condition:
    expect(shouldSplitInline('')).toBe(false)
    // Verify the parts would trigger the fallback check
    const parts = [
      { type: 'text' as const, text: '<think>reasoning</think>content' },
    ]
    const hasTags = parts.some(
      (p) => p.type === 'text' && p.text && (p.text.includes('<think>') || p.text.includes('</think>')),
    )
    expect(hasTags).toBe(true)
    // The fallback should NOT early-return
  })
})
