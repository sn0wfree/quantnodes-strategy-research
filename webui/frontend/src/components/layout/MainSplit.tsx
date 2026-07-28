import { useState, useCallback, useRef } from 'react'
import { useLayoutStore } from '../../stores/layout'
import { MessageList } from '../chat/MessageList'
import { Composer } from '../chat/Composer'

export function MainSplit() {
  const leftRatio = useLayoutStore((s) => s.leftRatio)
  const setLeftRatio = useLayoutStore((s) => s.setLeftRatio)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
  const [dragging, setDragging] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setDragging(true)
  }, [])

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const ratio = (e.clientX - rect.left) / rect.width
      setLeftRatio(Math.max(0.2, Math.min(0.8, ratio)))
    },
    [dragging, setLeftRatio]
  )

  const handleMouseUp = useCallback(() => setDragging(false), [])

  return (
    <div
      ref={containerRef}
      className="flex flex-1 overflow-hidden"
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* 左主区：聊天 */}
      <div
        className="flex flex-col overflow-hidden bg-slate-900"
        style={{ flex: rightPanelVisible ? `0 0 ${leftRatio * 100}%` : '1 1 0' }}
      >
        {/* Messages */}
        <div className="flex-1 overflow-hidden">
          <MessageList />
        </div>
        {/* Composer */}
        <Composer />
      </div>

      {/* 拖拽分隔条 */}
      {rightPanelVisible && (
        <div
          className={`w-1 cursor-col-resize transition-colors
            ${dragging ? 'bg-primary-500' : 'bg-slate-800 hover:bg-slate-700'}
          `}
          onMouseDown={handleMouseDown}
        />
      )}
    </div>
  )
}
