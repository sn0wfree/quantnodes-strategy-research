import { useState, useCallback, useRef } from 'react'

export interface MentionItem {
  id: string
  name: string
  type: 'agent' | 'file'
  description?: string
}

export function useMention(items: MentionItem[]) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const startPos = useRef(0)

  const filtered = items.filter((item) =>
    item.name.toLowerCase().includes(query.toLowerCase())
  )

  const checkMention = useCallback(
    (text: string, cursorPos: number) => {
      const before = text.slice(0, cursorPos)
      const match = before.match(/@(\w*)$/)
      if (match) {
        setQuery(match[1])
        setActive(true)
        startPos.current = cursorPos - match[0].length
        setSelectedIndex(0)
        return true
      }
      setActive(false)
      return false
    },
    []
  )

  const selectItem = useCallback(
    (text: string, item: MentionItem) => {
      const before = text.slice(0, startPos.current)
      const after = text.slice(startPos.current + query.length + 1)
      const newText = `${before}@${item.name} ${after}`
      setActive(false)
      return newText
    },
    [query]
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent, text: string, onSelect: (item: MentionItem) => void) => {
      if (!active) return false
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((i) => (i + 1) % filtered.length)
        return true
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((i) => (i - 1 + filtered.length) % filtered.length)
        return true
      }
      if (e.key === 'Enter' && filtered.length > 0) {
        e.preventDefault()
        onSelect(filtered[selectedIndex])
        return true
      }
      if (e.key === 'Escape') {
        setActive(false)
        return true
      }
      return false
    },
    [active, filtered, selectedIndex]
  )

  return { active, filtered, selectedIndex, checkMention, selectItem, handleKeyDown }
}
