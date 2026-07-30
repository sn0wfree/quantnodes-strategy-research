/**
 * Parse <think> and <system-reminder> tags from text content.
 *
 * - <think>...</think> → thinking parts (rendered as ThinkingBlock)
 * - <system-reminder>...</system-reminder> → hidden (not rendered)
 * - Other text → rendered as Markdown
 */

export interface ParsedContent {
  type: 'thinking' | 'system' | 'text'
  content: string
}

export function parseContentTags(text: string): ParsedContent[] {
  if (!text) return []

  const parts: ParsedContent[] = []
  // Match <system-reminder>...</system-reminder> and <think>...</think>
  const tagRegex = /<(system-reminder|think)>([\s\S]*?)<\/\1>/g
  let lastIndex = 0
  let match

  while ((match = tagRegex.exec(text)) !== null) {
    // Add text before the tag
    if (match.index > lastIndex) {
      const before = text.slice(lastIndex, match.index)
      if (before.trim()) {
        parts.push({ type: 'text', content: before })
      }
    }
    // Add the tag content
    const type = match[1] === 'think' ? 'thinking' : 'system'
    const content = match[2].trim()
    if (content) {
      parts.push({ type, content })
    }
    lastIndex = match.index + match[0].length
  }

  // Add remaining text
  if (lastIndex < text.length) {
    const remaining = text.slice(lastIndex)
    if (remaining.trim()) {
      parts.push({ type: 'text', content: remaining })
    }
  }

  return parts.length > 0 ? parts : [{ type: 'text', content: text }]
}

/**
 * Check if text contains any parseable tags.
 */
export function hasContentTags(text: string): boolean {
  return /<(system-reminder|think)>/.test(text)
}
