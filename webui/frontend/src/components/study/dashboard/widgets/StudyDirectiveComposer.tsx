/**
 * StudyDirectiveComposer — enhanced directive input for the study chat widget.
 * Features: textarea, Cmd/Ctrl+Enter submit, status hint, directive history.
 */
import { useState, useCallback, useRef } from 'react'
import { Send, Clock, Check } from 'lucide-react'
import { api } from '../../../../api/client'

interface StudyDirectiveComposerProps {
  studyId: string
  /** Placeholder text */
  placeholder?: string
  /** Called after successful submit */
  onSubmit?: () => void
}

export function StudyDirectiveComposer({
  studyId,
  placeholder = '输入研究指令（下轮 researcher 生效）...',
  onSubmit,
}: StudyDirectiveComposerProps) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [hint, setHint] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null)
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const clearHint = useCallback(() => {
    if (hintTimer.current) clearTimeout(hintTimer.current)
    hintTimer.current = null
  }, [])

  const submit = useCallback(async () => {
    const value = text.trim()
    if (!value || sending) return
    setSending(true)
    setHint(null)
    clearHint()
    try {
      await api.study.directive(studyId, value, 'webui')
      setText('')
      setHint({ type: 'ok', msg: '已提交，下轮生效' })
      hintTimer.current = setTimeout(() => setHint(null), 3000)
      onSubmit?.()
    } catch (err) {
      setHint({ type: 'err', msg: `提交失败: ${(err as Error).message || '未知错误'}` })
      hintTimer.current = setTimeout(() => setHint(null), 5000)
    } finally {
      setSending(false)
      taRef.current?.focus()
    }
  }, [text, sending, studyId, onSubmit, clearHint])

  return (
    <div className="flex-shrink-0 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
      <div className="flex items-end gap-2">
        <textarea
          ref={taRef}
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void submit()
            }
          }}
          placeholder={placeholder}
          className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none transition-shadow placeholder:text-slate-600 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={sending || !text.trim()}
          className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" />
          {sending ? '提交中…' : '提交'}
        </button>
      </div>

      {/* Status hint */}
      {hint && (
        <div className={`mt-2 flex items-center gap-1.5 text-[10px] ${
          hint.type === 'ok' ? 'text-emerald-400' : 'text-rose-400'
        }`}>
          {hint.type === 'ok' ? <Check className="h-3 w-3" /> : <Clock className="h-3 w-3" />}
          {hint.msg}
        </div>
      )}

      {/* Keyboard shortcut hint */}
      <div className="mt-1.5 text-[10px] text-slate-600">
        Cmd/Ctrl+Enter 提交 · 指令将在下一轮 researcher 中生效
      </div>
    </div>
  )
}
