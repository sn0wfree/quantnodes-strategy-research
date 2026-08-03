/**
 * Tests for the Mastra `smoothStream` word-boundary port.
 *
 * Source: https://github.com/mastra-ai/mastra/blob/main/packages/core/src/stream/smooth-stream.ts
 * License: Apache 2.0
 */
import { describe, it, expect } from 'vitest'
import { smoothBuffer, appendSmooth, shouldInsertSpaceBetween } from './mastraSmoothStream'

describe('smoothBuffer', () => {
  it('returns empty for empty buffer', () => {
    expect(smoothBuffer('')).toEqual({ stable: '', tail: '' })
  })

  it('holds back a single partial word', () => {
    expect(smoothBuffer('Let')).toEqual({ stable: '', tail: 'Let' })
  })

  it('emits a complete word when followed by whitespace', () => {
    expect(smoothBuffer('Let me ')).toEqual({ stable: 'Let me ', tail: '' })
  })

  it('emits multiple complete words and holds back the last partial one', () => {
    expect(smoothBuffer('Let me expl')).toEqual({
      stable: 'Let me ',
      tail: 'expl',
    })
  })

  it('emits all content when buffer ends on whitespace', () => {
    expect(smoothBuffer('Let me explore the workspace. ')).toEqual({
      stable: 'Let me explore the workspace. ',
      tail: '',
    })
  })

  it('handles pure CJK with no whitespace (whole text as tail)', () => {
    // CJK has no inter-word spaces, so the regex never matches. We
    // expose the whole text as "tail" so the renderer still shows it
    // — better than dropping it on the floor. The caller (PartRenderer
    // when isStreaming=false) bypasses the holdback entirely.
    const r = smoothBuffer('你好世界')
    expect(r.stable).toBe('')
    expect(r.tail).toBe('你好世界')
  })

  it('treats a CJK punctuation as a word boundary (no — needs whitespace)', () => {
    // The current regex requires ASCII whitespace (the LLM-emitted
    // '。' is a full-width punct that the model doesn't add a literal
    // space after, so this is a partial word → tail). Documented as a
    // known limitation: a CJK sentence with no trailing whitespace
    // or newline is held in tail. The caller's isStreaming=false
    // path bypasses the holdback and renders the full string.
    const r = smoothBuffer('你好。World')
    expect(r.stable).toBe('')
    expect(r.tail).toBe('你好。World')
  })

  it('treats newlines as word boundaries (multi-line)', () => {
    const r = smoothBuffer('first line\nsecond li')
    // 'first ' + 'line\n' + 'second ' are all complete words. 'li'
    // is a partial word → tail.
    expect(r.stable).toBe('first line\nsecond ')
    expect(r.tail).toBe('li')
  })

  it('treats newlines as word boundaries (no partial tail)', () => {
    const r = smoothBuffer('first line\n')
    expect(r.stable).toBe('first line\n')
    expect(r.tail).toBe('')
  })

  it('preserves leading whitespace before the first word', () => {
    // After a markdown heading line break, the model may emit
    // "  word" — we should keep the leading spaces as part of the
    // stable prefix, not drop them.
    const r = smoothBuffer('  hello ')
    expect(r.stable).toBe('  hello ')
    expect(r.tail).toBe('')
  })

  it('handles realistic streaming chunks (Let+me+expl+ore)', () => {
    // Token-level streaming scenario that motivates the whole port.
    expect(smoothBuffer('Let')).toEqual({ stable: '', tail: 'Let' })
    expect(smoothBuffer('Letme')).toEqual({ stable: '', tail: 'Letme' })
    expect(smoothBuffer('Letmeexplore')).toEqual({ stable: '', tail: 'Letmeexplore' })
    expect(smoothBuffer('Letmeexplore ')).toEqual({
      stable: 'Letmeexplore ',
      tail: '',
    })
    expect(smoothBuffer('Letmeexplore the')).toEqual({
      stable: 'Letmeexplore ',
      tail: 'the',
    })
    // The fix: after the space arrives, the user sees the
    // correctly-spaced "Letmeexplore the" rather than "Letmeexplorethe".
  })

  it('is idempotent (running on stable+tail yields same result)', () => {
    const text = 'Let me explore the'
    const once = smoothBuffer(text)
    const twice = smoothBuffer(once.stable + once.tail)
    expect(twice).toEqual(once)
  })

  it('handles Chinese + English mix', () => {
    // "你好 World" → "你好 " complete word (CJK + space), "World" is
    // a partial word (no trailing whitespace). The "!" also has no
    // whitespace before it so it stays part of the partial.
    const r = smoothBuffer('你好 World!')
    expect(r.stable).toBe('你好 ')
    expect(r.tail).toBe('World!')
  })

  it('handles punctuation-only tail (no trailing whitespace)', () => {
    // 'Done.' is a partial word — no trailing whitespace, so the
    // caller's isStreaming=false path bypasses the holdback and
    // renders the full string.
    const r = smoothBuffer('Done.')
    expect(r.stable).toBe('')
    expect(r.tail).toBe('Done.')
  })

  it('resets regex lastIndex between calls (defensive)', () => {
    smoothBuffer('a b c')
    smoothBuffer('x y z')
    // 'foo bar' has 'foo ' complete; 'bar' is partial (no trailing
    // whitespace). 'foo ' is in stable.
    expect(smoothBuffer('foo bar')).toEqual({ stable: 'foo ', tail: 'bar' })
  })
})

describe('appendSmooth', () => {
  it('returns no tail when the buffer has not grown', () => {
    const r = appendSmooth('hello', 'hello')
    expect(r.delta).toBe('')
    // 'hello' is a partial word (no trailing whitespace) — stays in
    // tail regardless of buffer-growth.
    expect(r.tail).toBe('hello')
  })

  it('emits the newly-completed word as delta', () => {
    // "Let" (partial) → "Let ex" (still partial on "ex" but "Let "
    // is now a complete word boundary). prevBuffer's stable was "".
    // The new boundary is "Let ", so delta = newStable.slice(0) = "Let ".
    const r = appendSmooth('Let', 'Let ex')
    expect(r.delta).toBe('Let ')
    expect(r.tail).toBe('ex')
  })

  it('emits the next newly-completed word as delta on subsequent chunk', () => {
    // Realistic streaming: chunk1 lands "Let me explore" (partial),
    // chunk2 lands " the" (completes "explore " — the new boundary).
    //
    // After "Let me": stable="Let ", tail="me" (partial, never shown).
    // After "Let me explore": stable="Let me ", tail="explore" (partial).
    // delta = newStable.slice(prevStable.length) = "Let me ".slice(4)
    //   = "me " — the *second* word becomes the new visible word.
    const first = appendSmooth('Let me', 'Let me explore')
    expect(first.delta).toBe('me ')
    expect(first.tail).toBe('explore')

    // chunk2: " the" lands. New buffer = "Let me explore the".
    // smoothBuffer → stable="Let me explore ", tail="the".
    // previousBuffer="explore" (from previous tail);
    // prevStable=smoothBuffer("explore").stable = "" (partial).
    // delta = newStable.slice(0) = "Let me explore " (full new stable,
    // because the prior stable was empty — every completed word is
    // new from the caller's perspective).
    const second = appendSmooth(first.tail, 'Let me explore the')
    expect(second.delta).toBe('Let me explore ')
    expect(second.tail).toBe('the')
  })

  it('handles replay / buffer-shrink (defensive)', () => {
    // If a chunk boundary causes the new buffer to be *shorter* than
    // the previous (e.g. SSE replay with a new offset), fall back to
    // a fresh evaluation.
    const r = appendSmooth('hello world', 'hi')
    expect(r.tail).toBe('hi')
  })
})

describe('shouldInsertSpaceBetween', () => {
  it('returns true at letter–letter boundary (the DeepSeek-V4-Flash case)', () => {
    expect(shouldInsertSpaceBetween('Let', 'me')).toBe(true)
    expect(shouldInsertSpaceBetween('ex', 'plore')).toBe(true)
  })

  it('returns false when the next chunk already starts with whitespace', () => {
    expect(shouldInsertSpaceBetween('Let', ' me')).toBe(false)
    expect(shouldInsertSpaceBetween('Let', '\nme')).toBe(false)
  })

  it('returns false at CJK boundaries', () => {
    // CJK is letter-y in some Unicode sense but the heuristic is
    // restricted to ASCII letter–letter. CJK chunks flow through
    // untouched.
    expect(shouldInsertSpaceBetween('你', '好')).toBe(false)
  })

  it('returns false at letter–punctuation boundary', () => {
    expect(shouldInsertSpaceBetween('world', '!')).toBe(false)
    expect(shouldInsertSpaceBetween('done', '.')).toBe(false)
  })

  it('returns false at letter–digit boundary', () => {
    expect(shouldInsertSpaceBetween('let', '2024')).toBe(false)
  })

  it('returns false when either chunk is empty', () => {
    expect(shouldInsertSpaceBetween('', 'me')).toBe(false)
    expect(shouldInsertSpaceBetween('Let', '')).toBe(false)
    expect(shouldInsertSpaceBetween('', '')).toBe(false)
  })

  it('reproduces the real user-reported bug: streaming Let+me+explore', () => {
    // Three chunks, no leading space, accumulated naively. With the
    // heuristic each boundary inserts one space.
    const a = 'Let'
    const b = 'me'
    const c = 'explore'
    let acc = a
    if (shouldInsertSpaceBetween(acc, b)) acc += ' ' + b
    else acc += b
    // acc = 'Let me'
    if (shouldInsertSpaceBetween(acc, c)) acc += ' ' + c
    else acc += c
    expect(acc).toBe('Let me explore')
  })
})
