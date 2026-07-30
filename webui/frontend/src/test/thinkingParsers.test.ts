import { describe, it, expect } from 'vitest'
import { parseMinimaxThinking, passthroughParser, getThinkingParser } from '../utils/thinkingParsers'

describe('passthroughParser', () => {
  it('returns text as-is with empty thinking', () => {
    expect(passthroughParser('hello world')).toEqual({
      thinking: '',
      content: 'hello world',
    })
  })

  it('handles empty string', () => {
    expect(passthroughParser('')).toEqual({ thinking: '', content: '' })
  })
})

describe('parseMinimaxThinking', () => {
  it('extracts single think block', () => {
    const result = parseMinimaxThinking('<think>plan</think>Hello')
    expect(result.thinking).toBe('plan')
    expect(result.content).toBe('Hello')
  })

  it('returns empty thinking when no tag present', () => {
    const result = parseMinimaxThinking('just plain text')
    expect(result.thinking).toBe('')
    expect(result.content).toBe('just plain text')
  })

  it('handles multiline thinking content', () => {
    const input = '<think>\nplan step 1\nplan step 2\n</think>\nanswer'
    const result = parseMinimaxThinking(input)
    expect(result.thinking).toBe('plan step 1\nplan step 2')
    expect(result.content).toBe('answer')
  })

  it('handles thinking with no trailing content', () => {
    const result = parseMinimaxThinking('<think>only thinking</think>')
    expect(result.thinking).toBe('only thinking')
    expect(result.content).toBe('')
  })

  it('handles multiple think blocks', () => {
    const input = '<think>first</think>middle<think>second</think>tail'
    const result = parseMinimaxThinking(input)
    expect(result.thinking).toBe('firstsecond')
    expect(result.content).toBe('middletail')
  })

  it('handles thinking with content before and after', () => {
    const input = 'before<think>middle</think>after'
    const result = parseMinimaxThinking(input)
    expect(result.thinking).toBe('middle')
    expect(result.content).toBe('beforeafter')
  })

  it('handles empty string', () => {
    const result = parseMinimaxThinking('')
    expect(result.thinking).toBe('')
    expect(result.content).toBe('')
  })

  it('preserves whitespace inside thinking block', () => {
    const input = '<think>  spaces around </think>content'
    // Inside content, surrounding whitespace is trimmed
    expect(parseMinimaxThinking(input).thinking).toBe('spaces around')
  })

  it('handles Chinese text in thinking and content', () => {
    const input = '<think>用户思考</think>你好助手'
    const result = parseMinimaxThinking(input)
    expect(result.thinking).toBe('用户思考')
    expect(result.content).toBe('你好助手')
  })

  it('does not crash on malformed tags (only open)', () => {
    const input = '<think>unclosed thinking'
    // No close tag → no extraction, treat as content
    const result = parseMinimaxThinking(input)
    expect(result.thinking).toBe('')
    expect(result.content).toBe(input)
  })
})

describe('getThinkingParser', () => {
  it('returns MiniMax parser for minimax', () => {
    const parser = getThinkingParser('minimax')
    const result = parser('<think>plan</think>content')
    expect(result.thinking).toBe('plan')
    expect(result.content).toBe('content')
  })

  it('returns passthrough for unknown provider', () => {
    const parser = getThinkingParser('unknown_provider')
    const result = parser('<think>plan</think>content')
    expect(result.thinking).toBe('')
    expect(result.content).toBe('<think>plan</think>content')
  })

  it('returns passthrough for null', () => {
    const parser = getThinkingParser(null)
    const result = parser('<think>x</think>y')
    expect(result.thinking).toBe('')
    expect(result.content).toBe('<think>x</think>y')
  })

  it('returns passthrough for undefined', () => {
    const parser = getThinkingParser(undefined)
    const result = parser('<think>x</think>y')
    expect(result.thinking).toBe('')
    expect(result.content).toBe('<think>x</think>y')
  })

  it('returns passthrough for empty string', () => {
    const parser = getThinkingParser('')
    const result = parser('<think>x</think>y')
    expect(result.thinking).toBe('')
    expect(result.content).toBe('<think>x</think>y')
  })
})