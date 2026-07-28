import { useState, useEffect, useRef } from 'react'
import { MarkdownRenderer } from './MarkdownRenderer'

interface StreamingTextProps {
  text: string
  isDone: boolean
}

export function StreamingText({ text, isDone }: StreamingTextProps) {
  const [displayed, setDisplayed] = useState('')
  const rafRef = useRef<number>(0)
  const lastLenRef = useRef(0)

  useEffect(() => {
    if (isDone) {
      setDisplayed(text)
      return
    }

    const tick = () => {
      const currentLen = text.length
      if (currentLen > lastLenRef.current) {
        // Reveal characters incrementally
        const diff = currentLen - lastLenRef.current
        const reveal = Math.max(1, Math.ceil(diff * 0.3))
        const newLen = Math.min(lastLenRef.current + reveal, currentLen)
        setDisplayed(text.slice(0, newLen))
        lastLenRef.current = newLen
      }
      rafRef.current = requestAnimationFrame(tick)
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [text, isDone])

  // Show cursor while streaming
  if (!isDone && displayed.length < text.length) {
    return (
      <div className="relative">
        <MarkdownRenderer content={displayed} />
        <span className="inline-block w-2 h-4 bg-primary-400 animate-pulse ml-0.5 align-middle" />
      </div>
    )
  }

  return <MarkdownRenderer content={displayed || text} />
}
