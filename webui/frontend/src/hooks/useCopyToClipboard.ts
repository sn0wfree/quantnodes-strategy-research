import { useCallback, useState } from 'react'

/**
 * Copy-to-clipboard helper with a 2s "copied" indicator.
 *
 * Previously each consumer (MarkdownRenderer, ToolCallBlock,
 * ThinkingBlock, FileEditBlock) re-implemented the same
 * `navigator.clipboard.writeText + setCopied + setTimeout(2000)`
 * trio. Returns [copied, copy] where `copy` is stable.
 */
export function useCopyToClipboard(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false)

  const copy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).catch(() => {
      // Clipboard can be unavailable (non-secure context, permissions);
      // silently degrade — the button is cosmetic.
    })
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [])

  return [copied, copy]
}
