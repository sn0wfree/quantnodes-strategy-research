/**
 * MiniMax thinking parser.
 *
 * MiniMax streams thinking tokens as `<think>...</think>` tags inline
 * within the assistant content. The backend does NOT extract these tags
 * (because they may be split across SSE chunks during streaming), so the
 * frontend parses the complete content string once it has been received.
 *
 * Algorithm: stateless single-pass regex over the complete text. Since
 * the frontend only sees the final accumulated text, there's no cross-
 * chunk boundary problem.
 *
 * Multiple think blocks are concatenated into one thinking part.
 * If parsing throws (shouldn't happen with a well-formed regex), the
 * caller falls back to passthrough.
 */

import type { ParsedThinking } from './passthrough';

const THINK_PATTERN = /<think>([\s\S]*?)<\/think>/g;

export function parseMinimaxThinking(text: string): ParsedThinking {
  if (!text) {
    return { thinking: '', content: text };
  }

  const matches: RegExpExecArray[] = [];
  let m: RegExpExecArray | null;
  // Reset lastIndex by re-creating the regex instance per call (g flag
  // keeps lastIndex state across calls otherwise)
  while ((m = THINK_PATTERN.exec(text)) !== null) {
    matches.push(m);
  }

  if (matches.length === 0) {
    return { thinking: '', content: text };
  }

  let thinking = '';
  let contentParts: string[] = [];
  let lastEnd = 0;
  for (const match of matches) {
    // Content before this think tag (between previous end and this start)
    const before = text.slice(lastEnd, match.index);
    if (before) contentParts.push(before);
    thinking += match[1];
    lastEnd = match.index + match[0].length;
  }
  // Trailing content after the last think tag
  const trailing = text.slice(lastEnd);
  if (trailing) contentParts.push(trailing);

  return {
    thinking: thinking.trim(),
    content: contentParts.join('').trim(),
  };
}