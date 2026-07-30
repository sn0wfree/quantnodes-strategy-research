/**
 * Per-provider thinking parser registry.
 *
 * Each provider has its own thinking-token format. The backend stores raw
 * text from the model (it doesn't know how to parse cross-chunk tags).
 * The frontend parses the complete content string with the appropriate
 * provider-specific parser before rendering.
 *
 * Adding a new provider:
 *   1. Create provider/<name>.ts with a parser function.
 *   2. Register it in the `parsers` map below.
 *
 * Unknown providers fall back to passthrough (no extraction).
 */

import { passthroughParser, type ParsedThinking } from './passthrough';
import { parseMinimaxThinking } from './minimax';

export type ThinkingParser = (text: string) => ParsedThinking;

const parsers: Record<string, ThinkingParser> = {
  minimax: parseMinimaxThinking,
  // Future providers: deepseek, qwen, kimi, openai, ...
};

export function getThinkingParser(provider: string | null | undefined): ThinkingParser {
  if (!provider) return passthroughParser;
  return parsers[provider] ?? passthroughParser;
}

export { passthroughParser, parseMinimaxThinking };
export type { ParsedThinking };