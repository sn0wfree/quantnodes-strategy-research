/**
 * Word-buffering stream smoother (Mastra `smoothStream` port, frontend).
 *
 * Source: https://github.com/mastra-ai/mastra/blob/main/packages/core/src/stream/smooth-stream.ts
 * License: Apache 2.0
 *
 * Problem it solves
 * ────────────────
 * DeepSeek-V4-Flash streams the assistant text as one SSE chunk per BPE
 * token. When tokens like ``'Let'``, ``'me'``, ``'explore'`` arrive as
 * separate events, the upstream tokenizer / SSE pipeline sometimes
 * drops the *leading* space of each token — the result is the user-
 * visible "Letmeexplore" with no spaces between English words.
 *
 * Solution
 * ────────
 * The Mastra pattern: buffer the incoming chunks, only emit a chunk
 * when it ends on a *complete* word boundary ``\S+\s+``. Everything
 * after the last complete boundary (a half-arrived word) is held back
 * in the buffer until the next chunk lands and completes it.
 *
 * Frontend adaptation
 * ────────────────────
 * Mastra's ``smoothStream`` returns a ``TransformStream`` that operates
 * on a continuous chunk stream. Our pipeline drives rendering off a
 * React state (``partTextAccumDelta[partId]``) — chunks are *already*
 * accumulated by the time the renderer reads them. So we expose a pure
 * function ``smoothBuffer(buffer) → { stable, tail }`` that applies
 * the same word-boundary rule on the snapshot.
 *
 * * ``stable``: the prefix consisting of whole words (and any
 *   inter-word whitespace). Safe to render.
 * * ``tail``: the partial remainder (an unclosed last word, or empty
 *   when the buffer ends on whitespace). NOT rendered; wait for the
 *   next chunk to complete it.
 *
 * Idempotent: calling ``smoothBuffer`` on its own ``stable + tail``
 * concatenation is a no-op.
 */

const WORD_BOUNDARY = /\S+\s+/g

export interface SmoothResult {
  /** Prefix consisting of whole words (with their trailing spaces). */
  stable: string
  /** Partial remainder — an in-flight word that hasn't completed. */
  tail: string
}

/**
 * Split an accumulated text buffer into a renderable prefix
 * (whole words only) and a held-back partial tail.
 *
 * Examples
 * ────────
 *   smoothBuffer('')              → { stable: '',   tail: '' }
 *   smoothBuffer('Let')           → { stable: '',   tail: 'Let' }   (partial word)
 *   smoothBuffer('Let me ')       → { stable: 'Let me ', tail: '' }  (full word)
 *   smoothBuffer('Let me expl')   → { stable: 'Let me ', tail: 'expl' }
 *   smoothBuffer('你好 World')    → { stable: '你好 ',  tail: 'World' }
 *   smoothBuffer('first line\n')  → { stable: 'first line\n', tail: '' }
 *   smoothBuffer('Done.')         → { stable: '',   tail: 'Done.' }   (caller's
 *                                                           isStreaming=false
 *                                                           bypasses holdback
 *                                                           and renders full)
 *
 * Notes
 * ─────
 * * CJK: ``\S+\s+`` requires an ASCII-whitespace trailing character,
 *   so a CJK sentence *with no trailing whitespace or CJK
 *   punctuation* stays entirely in the tail. In practice the model
 *   typically emits ``.`` or ``\n`` or a CJK full-width punct after
 *   a sentence, so the boundary fires.
 * * Newlines: ``\s`` matches ``\n`` so a newline correctly ends a
 *   word segment.
 * * This is the streaming holdback half of Mastra's ``smoothStream``
 *   (the upstream variant also adds a configurable emit delay; we
 *   don't need it because the reveal cadence is already handled by
 *   the renderer's rAF pacing).
 */
/**
 * Heuristic chunk-boundary space recovery (companion to ``smoothBuffer``).
 *
 * DeepSeek-V4-Flash streams one SSE chunk per BPE token, and the
 * upstream tokenizer drops the *leading* space of every token after
 * the first. Naive ``prev + delta`` accumulation yields
 * ``'Letmeexplorethe'`` — the spaces are lost forever.
 *
 * This helper detects the missing-space boundary between two
 * adjacent chunks: if the previous chunk's last char and the new
 * chunk's first char are BOTH ASCII letters, insert one space.
 *
 * Trade-offs:
 *   * True proper-noun concatenations like ``'New' + 'York'`` become
 *     ``'New York'`` — accepted because reasoning blocks rarely
 *     contain proper-noun tokens, and the persisted backend
 *     ``part.text`` is left raw so DB reads still reflect the
 *     model's exact output.
 *   * CJK / punctuation / whitespace are never affected (only
 *     letter–letter boundaries trigger the insert).
 */
export function shouldInsertSpaceBetween(prevChunk: string, nextChunk: string): boolean {
  if (!prevChunk || !nextChunk) return false
  return (
    /[A-Za-z]$/.test(prevChunk) && /^[A-Za-z]/.test(nextChunk)
  )
}

export function smoothBuffer(buffer: string): SmoothResult {
  if (!buffer) return { stable: '', tail: '' }

  // ``matchAll`` returns every non-overlapping ``\S+\s+`` occurrence.
  // We keep the position of the *last* match — everything before it is
  // renderable as whole words, everything from it on is a partial
  // tail. ``String.prototype.matchAll`` allocates a fresh iterator
  // each call, so no ``lastIndex`` bookkeeping is needed.
  let lastEnd = 0
  for (const m of buffer.matchAll(WORD_BOUNDARY)) {
    lastEnd = m.index! + m[0].length
  }

  return {
    stable: buffer.slice(0, lastEnd),
    tail: buffer.slice(lastEnd),
  }
}

/**
 * Pure append helper: apply Mastra's emit-on-boundary rule to a
 * running buffer. Returns the **delta** (the newly-completed words
 * since the previous buffer state) plus the new tail. Useful when
 * you want to drive a custom emit pipeline without rebuilding
 * ``smoothBuffer`` per chunk.
 *
 *   let last = ''
 *   for (const chunk of incomingChunks) {
 *     const { delta, tail } = appendSmooth(last, last + chunk)
 *     if (delta) emit(delta)
 *     last = tail
 *   }
 *   if (last) emit(last)   // flush on end
 */
export function appendSmooth(
  previousBuffer: string,
  newBuffer: string,
): { delta: string; tail: string } {
  if (newBuffer.length < previousBuffer.length) {
    // Defensive: SSE replay / restart scenarios where the buffer
    // shrinks. Re-evaluate from scratch.
    const fresh = smoothBuffer(newBuffer)
    return { delta: fresh.stable, tail: fresh.tail }
  }
  // The previous buffer may contain a *partial* tail that was never
  // emitted. We must emit all whole words that have completed since
  // the previous buffer — so we measure the previous *stable* prefix,
  // not the raw previousBuffer length.
  const { stable: prevStable } = smoothBuffer(previousBuffer)
  const { stable: newStable, tail: newTail } = smoothBuffer(newBuffer)
  return {
    delta: newStable.slice(prevStable.length),
    tail: newTail,
  }
}
