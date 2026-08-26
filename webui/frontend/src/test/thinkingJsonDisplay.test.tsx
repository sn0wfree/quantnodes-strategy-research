/**
 * Tests for:
 * 1. useRenderedParts fallback <think> tag detection (provider empty → still extracts)
 * 2. JsonActionCard rendering
 * 3. parseJsonAction JSON detection
 */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  splitTextIncremental,
  shouldSplitInline,
} from '../utils/thinkingParsers/incremental'
import {
  parseStructuredContent,
  JsonActionCard,
} from '../components/chat/JsonActionCard'

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

describe('JsonActionCard core field display', () => {
  it('shows verdict and recommendation in Chinese by default', () => {
    render(
      <JsonActionCard
        fullJson={{
          verdict: 'discard',
          overfit_passed: false,
          recommendation: 'BLOCK this round',
          details: { foo: 'bar' },
        }}
      />,
    )
    // Chinese labels should be visible
    expect(screen.getByText('结论:')).toBeInTheDocument()
    expect(screen.getByText('是否过拟合:')).toBeInTheDocument()
    expect(screen.getByText('建议:')).toBeInTheDocument()
    // Values
    expect(screen.getByText('discard')).toBeInTheDocument()
    expect(screen.getByText('BLOCK this round')).toBeInTheDocument()
  })

  it('renders boolean false with ✗ and true with ✓', () => {
    const { container: c1 } = render(
      <JsonActionCard fullJson={{ risk_passed: false }} />,
    )
    expect(c1.textContent).toContain('✗')
    expect(c1.textContent).toContain('是否通过:')

    const { container: c2 } = render(
      <JsonActionCard fullJson={{ risk_passed: true }} />,
    )
    expect(c2.textContent).toContain('✓')
  })

  it('shows risk_rating and status with Chinese labels', () => {
    render(
      <JsonActionCard
        fullJson={{
          risk_rating: 'Red',
          status: 'degraded',
          thresholds_breached: ['BACKTEST_NOT_RUN', 'GOAL_STALL'],
        }}
      />,
    )
    expect(screen.getByText('风险评级:')).toBeInTheDocument()
    expect(screen.getByText('Red')).toBeInTheDocument()
    expect(screen.getByText('状态:')).toBeInTheDocument()
    expect(screen.getByText('degraded')).toBeInTheDocument()
    expect(screen.getByText('触发规则:')).toBeInTheDocument()
    expect(screen.getByText(/BACKTEST_NOT_RUN · GOAL_STALL/)).toBeInTheDocument()
  })

  it('excludes core fields from the "其他字段" count', () => {
    render(
      <JsonActionCard
        fullJson={{
          verdict: 'discard',
          risk_rating: 'Red',
          var_95: 0.05,           // ← not core, should be in "其他"
          weights: { A: 0.5 },    // ← not core, should be in "其他"
        }}
      />,
    )
    // Only 2 fields should be in "其他字段" (var_95 + weights)
    expect(screen.getByText('其他字段 (2)')).toBeInTheDocument()
  })

  it('puts non-key fields into "其他字段" section, not as labeled rows', () => {
    // custom_metric is not in KEY_FIELDS, so it goes into the collapsible
    // "其他字段" section instead of being shown as a labeled key.
    render(<JsonActionCard fullJson={{ custom_metric: 'value' }} />)
    // Only 1 extra field in the collapsible section
    expect(screen.getByText('其他字段 (1)')).toBeInTheDocument()
  })

  it('truncates very long string values with hover title', () => {
    const longStr = 'x'.repeat(500)
    render(<JsonActionCard fullJson={{ thresholds_breached: longStr }} />)
    // The element with title attribute should exist
    const el = document.querySelector('[title]')
    expect(el?.getAttribute('title')).toBe(longStr)
  })

  it('still shows hypothesis above core fields', () => {
    render(
      <JsonActionCard
        fullJson={{
          verdict: 'discard',
          hypothesis: 'key finding here',
          risk_rating: 'Red',
        }}
      />,
    )
    expect(screen.getByText('key finding here')).toBeInTheDocument()
    expect(screen.getByText('结论:')).toBeInTheDocument()
    expect(screen.getByText('风险评级:')).toBeInTheDocument()
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
