/**
 * LiveActivity — shows real-time phase, agent, and elapsed time.
 * Reads from useStudyStore (SSE-driven state).
 */
import { useState, useEffect } from 'react'
import { useStudyStore } from '../../../../stores/study'
import type { WidgetProps } from '../types'

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const remain = s % 60
  return `${m}m ${remain}s`
}

const PHASE_LABELS: Record<string, string> = {
  researcher: '研究',
  execution: '回测执行',
  evaluation: '评估',
  review: '审核',
  knowledge: '知识收集',
}

const STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  queued: '排队中',
  complete: '已完成',
  paused: '已暂停',
  cancelled: '已取消',
  error: '错误',
  budget_limited: '预算用尽',
  early_stopped: '提前停止',
  needs_refresh: '需要刷新',
  interrupted: '已中断',
  monitoring: '监控中',
  archived: '已归档',
}

export function LiveActivity({ summary }: WidgetProps) {
  const s = summary as unknown as Record<string, unknown>
  const status = (s.execution_status as string) ?? 'unknown'
  const round = (s.current_round as number) ?? 0
  const maxRounds = s.max_rounds as number | undefined

  const currentPhase = useStudyStore((st) => st.currentPhase)
  const currentAgent = useStudyStore((st) => st.currentAgent)
  const phaseStartedAt = useStudyStore((st) => st.phaseStartedAt)

  // Elapsed timer — updates every second when a phase is active
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!phaseStartedAt) {
      setElapsed(0)
      return
    }
    const tick = () => setElapsed(Date.now() - phaseStartedAt)
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [phaseStartedAt])

  const isRunning = status === 'running'
  const phaseLabel = currentPhase ? (PHASE_LABELS[currentPhase] ?? currentPhase) : null
  const agentLabel = currentAgent
    ? (AGENT_LABELS[currentAgent] ?? currentAgent)
    : null

  return (
    <div className="flex flex-wrap items-center gap-4 text-sm">
      {/* Status */}
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${
          isRunning ? 'bg-emerald-400 animate-pulse' :
          status === 'queued' ? 'bg-amber-400' :
          status === 'complete' ? 'bg-emerald-500' :
          status === 'error' ? 'bg-rose-500' :
          'bg-slate-500'
        }`} />
        <span className="text-slate-300">
          {STATUS_LABELS[status] ?? status}
        </span>
      </div>

      {/* Round */}
      <div className="text-slate-400">
        Round <span className="text-slate-200 font-medium">{round}</span>
        {maxRounds != null && ` / ${maxRounds}`}
      </div>

      {/* Current phase */}
      {isRunning && phaseLabel && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-slate-500">阶段:</span>
          <span className="rounded-md bg-sky-500/15 px-2 py-0.5 text-xs font-medium text-sky-300">
            {phaseLabel}
          </span>
        </div>
      )}

      {/* Current agent */}
      {isRunning && agentLabel && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-slate-500">Agent:</span>
          <span className="rounded-md bg-violet-500/15 px-2 py-0.5 text-xs font-medium text-violet-300">
            {agentLabel}
          </span>
        </div>
      )}

      {/* Elapsed time */}
      {isRunning && phaseStartedAt && (
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-slate-500">⏱</span>
          <span className="font-mono text-xs text-slate-400">
            {formatElapsed(elapsed)}
          </span>
        </div>
      )}
    </div>
  )
}

const AGENT_LABELS: Record<string, string> = {
  researcher: 'Researcher',
  data_quality: 'DataQuality',
  factor_analyst: 'FactorAnalyst',
  strategist: 'Strategist',
  portfolio_construction: 'Portfolio',
  risk_controller: 'RiskCtrl',
  attribution_analyst: 'Attribution',
  anti_overfit_analyst: 'AntiOverfit',
  backtest_diagnostics: 'BacktestDiag',
}
