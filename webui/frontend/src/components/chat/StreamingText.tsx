import { useState, useEffect, useRef } from 'react'
import { MarkdownRenderer } from './MarkdownRenderer'
import { ThinkingBlock } from './ThinkingBlock'
import { parseContentTags, hasContentTags } from './contentTagParser'

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
  if (diff <= 4) return target
  const want = Math.max(1, Math.ceil(diff * 0.3))
  let newLen = lastLen + want
  if (newLen >= target) return target

  while (newLen < target && !/[\s\n\u3002\uff0c\uff01\uff1f]/.test(text[newLen])) {
    newLen++
  }
  if (newLen < target && /[\s\n]/.test(text[newLen])) newLen++

  return Math.min(newLen, target)
}

function StreamingContent({ text, isDone }: { text: string; isDone: boolean }) {
  // Parse <think> and <system-reminder> tags
  if (hasContentTags(text)) {
    const parsed = parseContentTags(text)
    return (
      <>
        {parsed.map((p, i) => {
          if (p.type === 'thinking') {
            return (
              <ThinkingBlock
                key={`think-${i}`}
                text={p.content}
                collapsed={true}
                streaming={!isDone}
              />
            )
          }
          if (p.type === 'system') {
            // Hide system-reminder tags
            return null
          }
          return <MarkdownRenderer key={`text-${i}`} content={p.content} streaming={!isDone} />
        })}
      </>
    )
  }
  return <MarkdownRenderer content={text} streaming={!isDone} />
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
      if (lastLenRef.current < currentLen) {
        rafRef.current = requestAnimationFrame(tick)
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [text, isDone])

  const isStreaming = !isDone && displayed.length < text.length

  if (isStreaming) {
    return (
      <div className="relative rounded-lg transition-shadow duration-500"
        style={{
          boxShadow: '0 0 24px -4px rgba(99, 102, 241, 0.12), 0 0 8px -2px rgba(99, 102, 241, 0.06)',
        }}
      >
        <StreamingContent text={displayed} isDone={false} />
        <span className="inline-block w-2 h-4 bg-primary-400 animate-pulse ml-0.5 align-middle" />
      </div>
    )
  }

  return <StreamingContent text={displayed || text} isDone={true} />
}
