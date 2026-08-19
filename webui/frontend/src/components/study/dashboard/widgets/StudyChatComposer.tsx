/**
 * StudyChatComposer — unified input area for the study chat widget.
 *
 * Renders a pill toggle [指令 | 对话] + textarea.
 * Plan mode defaults to 指令, Build mode defaults to 对话.
 * 指令 sends via directive API (optimistic).
 * 对话 delegates to the existing full Composer component.
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { Send, Check } from 'lucide-react'
import { api } from '../../../../api/client'
import { Composer } from '../../../chat/Composer'

type SendAs = 'directive' | 'chat'

interface StudyChatComposerProps {
  studyId: string
  mode: 'plan' | 'build'
}

export function StudyChatComposer({
  studyId,
  mode,
}: StudyChatComposerProps) {
  const [sendAs, setSendAs] = useState<SendAs>(
    mode === 'plan' ? 'directive' : 'chat',
  )
  const [directiveText, setDirectiveText] = useState('')
  const [sending, setSending] = useState(false)
  const [hint, setHint] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null)
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Sync default with mode when mode changes
  useEffect(() => {
    setSendAs(mode === 'plan' ? 'directive' : 'chat')
  }, [mode])

  const clearHint = useCallback(() => {
    if (hintTimer.current) clearTimeout(hintTimer.current)
    hintTimer.current = null
  }, [])

  const submitDirective = useCallback(async () => {
    const value = directiveText.trim()
    if (!value || sending) return
    setSending(true)
    setHint(null)
    clearHint()
    try {
      await api.study.directive(studyId, value, 'webui')
      setDirectiveText('')
      setHint({ type: 'ok', msg: '已提交，下轮生效' })
      hintTimer.current = setTimeout(() => setHint(null), 3000)
    } catch (err) {
      setHint({ type: 'err', msg: `提交失败: ${(err as Error).message || '未知错误'}` })
      hintTimer.current = setTimeout(() => setHint(null), 5000)
    } finally {
      setSending(false)
      taRef.current?.focus()
    }
  }, [directiveText, sending, studyId, clearHint])

  // ── Directive input ──────────────────────────────────────────
  if (sendAs === 'directive') {
    return (
      <div className="border-t border-slate-800 p-3">
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1.5">
            <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-900/60 p-1">
              <button
                onClick={() => setSendAs('directive')}
                className="rounded-md bg-slate-700 px-2.5 py-1 text-[11px] font-medium text-slate-200"
              >
                指令
              </button>
              <button
                onClick={() => setSendAs('chat')}
                className="rounded-md px-2.5 py-1 text-[11px] font-medium text-slate-500 hover:text-slate-300"
              >
                对话
              </button>
            </div>
          </div>
          <textarea
            ref={taRef}
            rows={2}
            value={directiveText}
            onChange={(e) => setDirectiveText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                void submitDirective()
              }
            }}
            placeholder="输入研究指令（下轮 researcher 生效）..."
            className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none transition-shadow placeholder:text-slate-600 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
          />
          <button
            type="button"
            onClick={() => void submitDirective()}
            disabled={sending || !directiveText.trim()}
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
            {hint.type === 'ok' ? <Check className="h-3 w-3" /> : <span className="h-3 w-3">✗</span>}
            {hint.msg}
          </div>
        )}

        <div className="mt-1.5 text-[10px] text-slate-600">
          Cmd/Ctrl+Enter 提交 · 指令将在下一轮 researcher 中生效
        </div>
      </div>
    )
  }

  // ── Chat input (wraps existing Composer) ──────────────────────
  return (
    <div className="border-t border-slate-800">
      <div className="px-3 pt-2">
        <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-900/60 p-1">
          <button
            onClick={() => setSendAs('directive')}
            className="rounded-md px-2.5 py-1 text-[11px] font-medium text-slate-500 hover:text-slate-300"
          >
            指令
          </button>
          <button
            onClick={() => setSendAs('chat')}
            className="rounded-md bg-slate-700 px-2.5 py-1 text-[11px] font-medium text-slate-200"
          >
            对话
          </button>
        </div>
      </div>
      <Composer />
    </div>
  )
}
