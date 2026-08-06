import { useEffect, useRef, useState, type ReactNode } from 'react'

interface Props {
  /** Which edge the drag handle sits on. 'right' → handle on the left edge
   *  of the panel (the panel sits to the right of its siblings); 'left'
   *  → handle on the right edge (panel sits to the left). */
  side: 'left' | 'right'
  /** Current width as a fraction of the parent flex container's width. */
  ratio: number
  setRatio: (r: number) => void
  minRatio?: number
  maxRatio?: number
  children: ReactNode
}

/**
 * A flex sibling that occupies `ratio` of its parent flex container's
 * width and can be resized via a drag handle on `side`.
 *
 * Width is set via inline `width: X%` plus `flex-shrink-0` so the
 * parent flex container balances remaining space against its other
 * flex children (no circular dependency between our width and the
 * width of our children).
 *
 * Drag is bound on `window` so the pointer can leave the panel mid
 * drag without interrupting the gesture.
 */
export function ResizablePanel({
  side,
  ratio,
  setRatio,
  minRatio = 0.2,
  maxRatio = 0.8,
  children,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!dragging) return
    const onMove = (e: MouseEvent) => {
      const container = containerRef.current
      const parent = container?.parentElement
      if (!container || !parent) return
      const rect = container.getBoundingClientRect()
      const parentRect = parent.getBoundingClientRect()
      // For side='right' the handle is on the left edge: dragging left
      // (clientX ↓) makes the panel wider. For side='left' the handle
      // is on the right edge: dragging right (clientX ↑) widens it.
      const widthPx = side === 'right'
        ? rect.right - e.clientX
        : e.clientX - rect.left
      const next = widthPx / parentRect.width
      setRatio(Math.max(minRatio, Math.min(maxRatio, next)))
    }
    const onUp = () => setDragging(false)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [dragging, side, setRatio, minRatio, maxRatio])

  const handleClass = `w-1.5 flex-shrink-0 cursor-col-resize transition-colors ${
    dragging ? 'bg-primary-500' : 'hover:bg-primary-500/40'
  }`

  return (
    <div
      ref={containerRef}
      className="flex h-full flex-shrink-0 overflow-hidden"
      style={{ width: `${ratio * 100}%` }}
    >
      {side === 'right' && (
        <div
          className={`${handleClass} border-l border-slate-800`}
          onMouseDown={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
        />
      )}
      <div className="flex flex-1 flex-col min-w-0">
        {children}
      </div>
      {side === 'left' && (
        <div
          className={`${handleClass} border-r border-slate-800`}
          onMouseDown={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
        />
      )}
    </div>
  )
}