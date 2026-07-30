/**
 * Default thinking parser: returns the input as content, no thinking.
 *
 * Used for providers whose thinking tokens are NOT embedded in content
 * (e.g. DeepSeek/Qwen/Kimi use separate reasoning_content fields, OpenAI
 * uses reasoning field). For these providers, the backend already separates
 * thinking into a dedicated ThinkingPart, so no client-side parsing is
 * needed.
 */

export interface ParsedThinking {
  thinking: string;
  content: string;
}

export function passthroughParser(text: string): ParsedThinking {
  return { thinking: '', content: text };
}