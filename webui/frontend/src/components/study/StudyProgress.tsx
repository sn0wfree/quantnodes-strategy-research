import { useEffect, useState, useCallback, useRef } from 'react'
import { Pause, Play, X, ArrowRightCircle, ExternalLink } from 'lucide-react'
import { useStudyStore } from '../../stores/study'
import { api, type StudySummaryResponse, type FlowNodeData } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { ObjectiveProgress } from './ObjectiveProgress'
import { FlowCard } from './FlowCard'
import { RoundHistory } from './RoundHistory'
import { ScoreboardMini } from './ScoreboardMini'

interface Props {
  // sessionId is no longer needed - SSE handlers update the store directly
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

export function StudyProgress(_props: Props) {
  // SSE-driven: current is updated by studyHandlers via useStudyStore
  const current = useStudyStore((s) => s.current)
  const setError = useStudyStore((s) => s.setError)
  const [directiveText, setDirectiveText] = useState('')
  const [submittingDirective, setSubmittingDirective] = useState(false)
  const [summary, setSummary] = useState<StudySummaryResponse | null>(null)
  const [flowNodes, setFlowNodes] = useState<FlowNodeData[]>([])
  const lastSummaryStudyId = useRef<string>('')
  const lastSummaryRound = useRef<number>(-1)

  // Fetch summary when study changes or round advances
  const fetchSummary = useCallback(async () => {
    const studyId = current?.study_id
    if (!studyId) return

    // Only re-fetch if study changed or round advanced
    const round = Math.max(1, current?.current_round ?? 1)
    if (studyId === lastSummaryStudyId.current && round === lastSummaryRound.current) {
      return
    }

    try {
      const r = await api.study.summary(studyId)
      if (r.status === 'ok') {
        setSummary(r)
        lastSummaryStudyId.current = studyId
        lastSummaryRound.current = round

        // Build flow nodes from real round data
        const nodes: FlowNodeData[] = WORKFLOW_NODES.map((n) => ({
          id: n.id,
          label: n.label,
          status: 'pending' as const,
        }))

        const rounds = r.recent_rounds ?? []

        if (current?.execution_status === 'complete') {
          nodes.forEach((n) => { n.status = 'done' })
        } else if (rounds.length > 0 && round > 0) {
          const lastRound = rounds[rounds.length - 1]
          const verdict = lastRound?.verdict

          if (verdict === 'keep' || verdict === 'discard') {
            nodes.forEach((n) => { n.status = 'done' })
          } else if (current?.execution_status === 'running') {
            const completedRounds = Math.max(0, round - 1)
            if (completedRounds > 0) {
              nodes.forEach((n) => { n.status = 'done' })
            }
            nodes[0].status = 'running'
          }
        } else if (current?.execution_status === 'running' && round === 1) {
          nodes[0].status = 'running'
        }

        setFlowNodes(nodes)
      }
    } catch {
      // Silent - summary is non-critical
    }
  }, [current?.study_id, current?.execution_status, current?.current_round])

  // React to SSE-driven store changes: fetch summary when current changes
  useEffect(() => {
    fetchSummary()
  }, [fetchSummary])

  // Fallback: minimal poll every 30s for robustness (in case SSE misses events)
  useEffect(() => {
    const timer = setInterval(fetchSummary, 30000)
    return () => clearInterval(timer)
  }, [fetchSummary])

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

  const onAction = async (action: 'pause' | 'resume' | 'resume_interrupted' | 'cancel') => {
    try {
      if (action === 'resume_interrupted') {
        await api.study.resumeInterrupted(studyId)
      } else {
        await api.study[action](studyId)
      }
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
          Round {Math.max(1, current.current_round ?? 1)}/{maxRounds}
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
            onClick={() => onAction('resume_interrupted')}
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
        currentRound={Math.max(1, current.current_round ?? 1)}
        totalRounds={maxRounds}
      />

      {/* Round History */}
      <RoundHistory
        rounds={summary?.recent_rounds ?? []}
        currentRound={Math.max(1, current.current_round ?? 1)}
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
          <div className="flex items-start justify-between gap-2">
            <span className="min-w-0">{current.last_error}</span>
            {current.trace_id && (
              <button
                type="button"
                title="复制 trace_id 去日志里查对应轮次"
                onClick={() => navigator.clipboard?.writeText(current.trace_id!)}
                className="flex-shrink-0 cursor-pointer rounded-md border border-rose-700/60 bg-rose-950/70 px-1.5 py-0.5 font-mono text-[9px] text-rose-400 transition-colors hover:bg-rose-900/50"
              >
                {current.trace_id.slice(0, 8)} ⧉
              </button>
            )}
          </div>
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
