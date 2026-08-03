/**
 * Incremental thinking/text split for streaming parts.
 *
 * Inspired by opencode's readPartText/StreamingAccumulator pattern: rather
 * than waiting for the full text to arrive before extracting thinking,
 * split on every change so the thinking block can mount on the first
 * ``<think>`` byte and the content block can mount on the first
 * ``</think>`` byte.
 *
 * Algorithm:
 *   1. Scan the full text for all *closed* <think>…</think> blocks.
 *      Concatenate their inner content into `thinkingBefore`.
 *   2. Whatever remains after the last closed block is the "tail".
 *   3. If the tail contains an unclosed <think>, the part of the tail
 *      after the opening tag is `thinkingOpen`; the part before the
 *      opening tag is `contentAfter`.  Otherwise the entire tail is
 *      `contentAfter`.
 *
 * Pure function — call on every text change. Cheap (single linear scan,
 * one regex allocation per call; can be tightened later if needed).
 */

export interface IncrementalSplit {
  /** Concatenated content of all *closed* <think>…</think> blocks. */
  thinkingBefore: string
  /**
   * Content inside an *unclosed* <think> tag (i.e. the LLM is still
   * streaming reasoning tokens). `null` when the most recent <think>
   * has already been closed.
   */
  thinkingOpen: string | null
  /** Text after the last closed </think> (or all of `text` if no tags). */
  contentAfter: string
}

const CLOSED_RE = /<think>([\s\S]*?)<\/think>/g

export function splitTextIncremental(text: string): IncrementalSplit {
  if (!text) {
    return { thinkingBefore: '', thinkingOpen: null, contentAfter: '' }
  }

  // Pass 1: collect closed blocks.
  let thinkingBefore = ''
  let lastClosedEnd = 0
  CLOSED_RE.lastIndex = 0
  for (;;) {
    const m = CLOSED_RE.exec(text)
    if (!m) break
    thinkingBefore += m[1]
    lastClosedEnd = m.index + m[0].length
  }

  // Pass 2: inspect the remaining tail.
  const tail = text.slice(lastClosedEnd)
  const openIdx = tail.indexOf('<think>')
  if (openIdx === -1) {
    return { thinkingBefore, thinkingOpen: null, contentAfter: tail }
  }
  const contentAfter = tail.slice(0, openIdx)
  const thinkingOpen = tail.slice(openIdx + '<think>'.length)
  return { thinkingBefore, thinkingOpen, contentAfter }
}

/**
 * Whether the active provider's thinking tokens are embedded inline in
 * content (e.g. ``<think>…</think>`` tags for minimax) and therefore
 * need client-side splitting. Providers that already separate thinking
 * via a dedicated reasoning_content field (DeepSeek / Qwen / Kimi /
 * OpenAI) return false — the backend already emits a dedicated
 * ThinkingPart via the thinking_delta SSE event.
 */
export function shouldSplitInline(provider: string | null | undefined): boolean {
  return provider === 'minimax'
}
