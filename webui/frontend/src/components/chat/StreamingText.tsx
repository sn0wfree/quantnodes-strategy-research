import { useState, useEffect, useRef } from 'react'
import { MarkdownRenderer } from './MarkdownRenderer'

interface StreamingTextProps {
  text: string
  isDone: boolean
}

/**
 * Compute next reveal length, preferring word boundaries.
 * Keeps the 30%-per-tick rate but snaps to the next whitespace
 * so we don't cut through a word.
 */
function nextRevealLen(text: string, lastLen: number): number {
  const target = text.length
  if (target <= lastLen) return lastLen

  const diff = target - lastLen
  // Short chunks: reveal immediately (single tick)
  if (diff <= 4) return target
  // Medium chunks: 30% per tick, snapped to word boundary
  const want = Math.max(1, Math.ceil(diff * 0.3))
  let newLen = lastLen + want
  if (newLen >= target) return target

  // Snap to next whitespace if we're inside a word
  while (newLen < target && !/[\s\n\u3002\uff0c\uff01\uff1f]/.test(text[newLen])) {
    newLen++
  }
  // Include the separator itself
  if (newLen < target && /[\s\n]/.test(text[newLen])) newLen++

  // Safety: don't exceed target
  return Math.min(newLen, target)
}

export function StreamingText({ text, isDone }: StreamingTextProps) {
  const [displayed, setDisplayed] = useState('')
  const rafRef = useRef<number>(0)
  const lastLenRef = useRef(0)

  useEffect(() => {
    if (isDone) {
      setDisplayed(text)
      lastLenRef.current = text.length
      return
    }

    const tick = () => {
      const currentLen = text.length
      const newLen = nextRevealLen(text, lastLenRef.current)
      if (newLen > lastLenRef.current) {
        setDisplayed(text.slice(0, newLen))
        lastLenRef.current = newLen
      }
      // Keep ticking until we catch up
      if (lastLenRef.current < currentLen) {
        rafRef.current = requestAnimationFrame(tick)
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [text, isDone])

  if (!isDone && displayed.length < text.length) {
    return (
      <div className="relative">
        <MarkdownRenderer content={displayed} streaming />
        <span className="inline-block w-2 h-4 bg-primary-400 animate-pulse ml-0.5 align-middle" />
      </div>
    )
  }

  return <MarkdownRenderer content={displayed || text} />
}