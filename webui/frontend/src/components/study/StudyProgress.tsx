import { useEffect, useState, useCallback } from 'react'
import { Pause, Play, X, ArrowRightCircle, ExternalLink } from 'lucide-react'
import { useStudyStore } from '../../stores/study'
import { api, type StudySummaryResponse, type FlowNodeData } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { ObjectiveProgress } from './ObjectiveProgress'
import { FlowCard } from './FlowCard'
import { RoundHistory } from './RoundHistory'
import { ScoreboardMini } from './ScoreboardMini'

interface Props {
  sessionId: string
  pollIntervalMs?: number
}

// 9-agent workflow nodes
const WORKFLOW_NODES = [
  { id: 'researcher', label: 'Researcher' },
  { id: 'data_quality', label: 'DataQuality' },
  { id: 'factor_analyst', label: 'FactorAnalyst' },
  { id: 'strategist', label: 'Strategist' },
  { id: 'portfolio', label: 'Portfolio' },
  { id: 'backtest', label: 'Backtest' },
  { id: 'risk_ctrl', label: 'RiskController' },
  { id: 'attribution', label: 'Attribution' },
  { id: 'anti_overfit', label: 'AntiOverfit' },
]

export function StudyProgress({ sessionId, pollIntervalMs = 10000 }: Props) {
  const current = useStudyStore((s) => s.current)
  const setCurrent = useStudyStore((s) => s.setCurrent)
  const setError = useStudyStore((s) => s.setError)
  const [directiveText, setDirectiveText] = useState('')
  const [submittingDirective, setSubmittingDirective] = useState(false)
  const [summary, setSummary] = useState<StudySummaryResponse | null>(null)
  const [flowNodes, setFlowNodes] = useState<FlowNodeData[]>([])

  // Poll /study/status for basic state
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const r = await api.study.status(sessionId)
        if (!cancelled) setCurrent(r)
      } catch (err) {
        if (!cancelled) setError((err as Error).message)
      } finally {
        if (!cancelled) {
          timer = setTimeout(poll, pollIntervalMs)
        }
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [sessionId, setCurrent, setError, pollIntervalMs])

  // Poll /study/{id}/summary for detailed data
  const pollSummary = useCallback(async () => {
    const studyId = current?.study_id
    if (!studyId) return

    try {
      const r = await api.study.summary(studyId)
      if (r.status === 'ok') {
        setSummary(r)

        // Build flow nodes from summary
        const nodes: FlowNodeData[] = WORKFLOW_NODES.map((n) => ({
          id: n.id,
          label: n.label,
          status: 'pending' as const,
        }))

          // Mark nodes based on current round and execution status
        if (current?.execution_status === 'running') {
          // Mark first few nodes as done based on round progress
          const round = current.current_round ?? 1
          const doneCount = Math.min(round * 2, WORKFLOW_NODES.length - 1)

          nodes.forEach((n, i) => {
            if (i < doneCount) {
              n.status = 'done'
            } else if (i === doneCount) {
              n.status = 'running'
            }
          })
        } else if (current?.execution_status === 'complete') {
          nodes.forEach((n) => { n.status = 'done' })
        }

        setFlowNodes(nodes)
      }
    } catch {
      // Silent - summary is non-critical
    }
  }, [current?.study_id, current?.execution_status, current?.current_round])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const pollLoop = async () => {
      if (!cancelled) {
        await pollSummary()
        timer = setTimeout(pollLoop, pollIntervalMs)
      }
    }

    pollLoop()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [pollSummary, pollIntervalMs])

  if (!current || current.status === 'no_study') {
    return (
      <p className="text-xs text-slate-400">
        当前 session 暂无 study。
      </p>
    )
  }

  const studyId = current.study_id ?? ''
  const status = current.execution_status ?? 'unknown'
  const maxRounds = summary?.max_rounds ?? 5

  const onAction = async (action: 'pause' | 'resume' | 'cancel') => {
    try {
      await api.study[action](studyId)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const onDirective = async () => {
    const text = directiveText.trim()
    if (!text) return
    setSubmittingDirective(true)
    try {
      await api.study.directive(studyId, text, 'webui')
      setDirectiveText('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmittingDirective(false)
    }
  }

  return (
    <div className="space-y-3 text-slate-100">
      {/* Status Bar */}
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${
            STUDY_STATUS_COLORS[status] ?? 'bg-slate-700 text-slate-100'
          }`}
        >
          {STUDY_STATUS_LABELS[status] ?? status}
        </span>
        <span className="font-mono text-xs text-slate-400">
          Round {current.current_round ?? 0}/{maxRounds}
        </span>
        <div className="flex-1" />
        {(status === 'running' || status === 'monitoring') && (
          <button
            onClick={() => onAction('pause')}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-amber-600 px-2 py-1 text-xs transition-all hover:bg-amber-500 active:scale-95"
          >
            <Pause className="h-3 w-3" /> 暂停
          </button>
        )}
        {status === 'paused' && (
          <button
            onClick={() => onAction('resume')}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-emerald-600 px-2 py-1 text-xs transition-all hover:bg-emerald-500 active:scale-95"
          >
            <Play className="h-3 w-3" /> 恢复
          </button>
        )}
        {status === 'interrupted' && (
          <button
            onClick={() => onAction('resume')}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-emerald-600 px-2 py-1 text-xs transition-all hover:bg-emerald-500 active:scale-95"
          >
            <Play className="h-3 w-3" /> 继续运行
          </button>
        )}
        {status !== 'complete' && status !== 'cancelled' && status !== 'error' && status !== 'needs_refresh' && status !== 'interrupted' && (
          <button
            onClick={() => onAction('cancel')}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-rose-700 px-2 py-1 text-xs transition-all hover:bg-rose-600 active:scale-95"
          >
            <X className="h-3 w-3" /> 取消
          </button>
        )}
      </div>

      {/* Objective + Progress */}
      <ObjectiveProgress
        objective={current.objective ?? ''}
        progressPercent={current.goal_snapshot?.progress_percent ?? 0}
        evidenceCount={current.goal_snapshot?.evidence_count ?? 0}
        criteria={current.goal_snapshot?.criteria ?? []}
      />

      {/* Flow Card */}
      <FlowCard
        nodes={flowNodes}
        currentRound={current.current_round ?? 1}
        totalRounds={maxRounds}
      />

      {/* Round History */}
      <RoundHistory
        rounds={summary?.recent_rounds ?? []}
        currentRound={current.current_round ?? 1}
      />

      {/* Scoreboard */}
      <ScoreboardMini scoreboard={summary?.scoreboard ?? []} />

      {/* View Details */}
      {studyId && (
        <a
          href={`/study/${studyId}`}
          className="flex items-center gap-1 text-xs text-sky-400 hover:text-sky-300 transition-colors"
        >
          <ExternalLink className="h-3 w-3" />
          查看详细
        </a>
      )}

      {/* Error */}
      {current.last_error && (
        <div className="rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-xs text-rose-300 break-words">
          {current.last_error}
        </div>
      )}

      {/* Directive Input */}
      {(status === 'running' || status === 'monitoring') && (
        <div className="space-y-1.5 rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
          <label className="block text-[10px] font-medium uppercase tracking-wider text-slate-500">
            注入研究方向（下一轮 researcher 看到）
          </label>
          <textarea
            rows={2}
            value={directiveText}
            onChange={(e) => setDirectiveText(e.target.value)}
            placeholder="例：改成动量因子 + 减小 top_n"
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
          />
          <button
            type="button"
            onClick={onDirective}
            disabled={submittingDirective || !directiveText.trim()}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
          >
            <ArrowRightCircle className="h-3 w-3" /> 提交指令
          </button>
        </div>
      )}
    </div>
  )
}
