import { useState, useRef, useCallback, useEffect } from 'react'
import { Send, Image as ImageIcon, X, Square } from 'lucide-react'
import { useChatStore } from '../../stores/chat'
import { useSessionStore } from '../../stores/session'
import { useToastStore } from '../../stores/toast'
import { api } from '../../api/client'
import { uuid } from '../../utils/uuid'

export function Composer() {
  const [text, setText] = useState('')
  const [images, setImages] = useState<string[]>([])
  const [sending, setSending] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const addMessage = useChatStore((s) => s.addMessage)
  const streamingMessageId = useChatStore((s) => s.streamingMessageId)
  const setActiveAttempt = useChatStore((s) => s.setActiveAttempt)
  const cancelAttempt = useChatStore((s) => s.cancelAttempt)
  const addToast = useToastStore((s) => s.addToast)

  const isStreaming = streamingMessageId !== null

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
    }
  }, [text])

  const handleSend = useCallback(async () => {
    if (!text.trim() && images.length === 0) return
    if (!currentSessionId) return

    const content = text.trim()
    const messageImages = [...images]
    setText('')
    setImages([])
    setSending(true)

    // Optimistic: add user message to store immediately with a local temp id.
    const tempUserId = uuid('msg')
    addMessage({
      id: tempUserId,
      session_id: currentSessionId,
      role: 'user',
      parts: [
        ...(content ? [{ type: 'text' as const, id: `seed-${tempUserId}`, text: content }] : []),
        ...messageImages.map((url) => ({ type: 'image' as const, url })),
      ],
      created_at: Date.now() / 1000,
    })

    try {
      const res = await api.post<{
        message_id: string
        user_message_id?: string
        assistant_message_id?: string
        attempt_id?: string
        status: string
      }>('/chat/send_async', {
        session_id: currentSessionId,
        content,
        images: messageImages.length > 0 ? messageImages : undefined,
      })

      // Store attempt_id for cancel support
      if (res.attempt_id) {
        setActiveAttempt(res.attempt_id)
      }

      // Rename optimistic user message → backend's user_message_id
      const userMsgId = res.user_message_id || res.message_id
      if (userMsgId && userMsgId !== tempUserId) {
        useChatStore.setState((state) => {
          const local = state.messages.get(tempUserId)
          if (local) {
            state.messages.delete(tempUserId)
            state.messages.set(userMsgId, { ...local, id: userMsgId })
          }
        })
      }
    } catch (err: any) {
      console.error('Send failed:', err)
      const status = err?.status
      const detail = err?.detail
      if (status === 429 && detail?.error === 'queue_full') {
        addToast(
          'error',
          `队列已满（上限 ${detail.limit} 条），请等待完成或取消当前轮次`,
        )
      } else if (err?.message) {
        addToast('error', `发送失败：${err.message}`)
      }
      useChatStore.setState((state) => {
        state.messages.delete(tempUserId)
      })
    } finally {
      setSending(false)
    }
  }, [text, images, currentSessionId, addMessage, setActiveAttempt])

  const handleCancel = useCallback(() => {
    cancelAttempt()
  }, [cancelAttempt])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (isStreaming) {
        handleCancel()
      } else {
        handleSend()
      }
    }
  }

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items)
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault()
        const blob = item.getAsFile()
        if (blob && blob.size <= 5 * 1024 * 1024) {
          const reader = new FileReader()
          reader.onload = () => {
            setImages((prev) => [...prev, reader.result as string])
          }
          reader.readAsDataURL(blob)
        }
      }
    }
  }, [])

  const handleFileSelect = useCallback(() => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/*'
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (file && file.size <= 5 * 1024 * 1024) {
        const reader = new FileReader()
        reader.onload = () => {
          setImages((prev) => [...prev, reader.result as string])
        }
        reader.readAsDataURL(file)
      }
    }
    input.click()
  }, [])

  return (
    <div className="border-t border-slate-800 bg-slate-900/80 p-4">
      {/* Image previews */}
      {images.length > 0 && (
        <div className="mb-2 flex gap-2">
          {images.map((src, i) => (
            <div key={i} className="relative h-16 w-16">
              <img src={src} alt="" className="h-full w-full rounded-lg object-cover" />
              <button
                onClick={() => setImages((prev) => prev.filter((_, j) => j !== i))}
                className="absolute -right-1.5 -top-1.5 rounded-full bg-slate-700 p-0.5 text-slate-300 hover:text-white"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Input area */}
      <div className="glass rounded-xl flex items-end gap-2 px-4 py-3">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={
            isStreaming
              ? '正在生成中... (Enter 停止)'
              : currentSessionId
                ? '输入消息... (Shift+Enter 换行)'
                : '选择或创建会话'
          }
          disabled={!currentSessionId}
          rows={1}
          className="flex-1 resize-none bg-transparent text-sm text-slate-100 placeholder-slate-500 outline-none disabled:opacity-50"
        />
        <div className="flex items-center gap-1">
          <button
            onClick={handleFileSelect}
            disabled={isStreaming}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-700/50 hover:text-slate-200 transition-colors disabled:opacity-30"
            title="上传图片"
          >
            <ImageIcon className="h-4 w-4" />
          </button>
          {isStreaming ? (
            <button
              onClick={handleCancel}
              className="rounded-lg bg-red-600 p-1.5 text-white hover:bg-red-700 transition-colors"
              title="停止生成"
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={sending || (!text.trim() && images.length === 0) || !currentSessionId}
              className="rounded-lg bg-primary-600 p-1.5 text-white hover:bg-primary-700 disabled:opacity-30 disabled:hover:bg-primary-600 transition-colors"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
