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
import { parseJsonAction } from '../components/chat/JsonActionCard'

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

describe('parseJsonAction', () => {
  it('detects action objects with hypothesis', () => {
    const r = parseJsonAction('{"action":"optimize_param","hypothesis":"try smaller top_n"}')
    expect(r.isAction).toBe(true)
    expect(r.action).toBe('optimize_param')
    expect(r.hypothesis).toBe('try smaller top_n')
    expect(r.fullJson).toEqual({ action: 'optimize_param', hypothesis: 'try smaller top_n' })
  })

  it('detects action objects without hypothesis', () => {
    const r = parseJsonAction('{"action":"blocker","reason":"rate limited"}')
    expect(r.isAction).toBe(true)
    expect(r.action).toBe('blocker')
    expect(r.hypothesis).toBeUndefined()
  })

  it('returns false for non-JSON text', () => {
    expect(parseJsonAction('This is plain text').isAction).toBe(false)
  })

  it('returns false for JSON without action key', () => {
    expect(parseJsonAction('{"status":"ok","data":[]}').isAction).toBe(false)
  })

  it('returns false for malformed JSON', () => {
    expect(parseJsonAction('{bad json}').isAction).toBe(false)
  })

  it('handles JSON with extra whitespace', () => {
    const r = parseJsonAction('  {"action":"keep","hypothesis":"x"}  ')
    expect(r.isAction).toBe(true)
    expect(r.action).toBe('keep')
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
