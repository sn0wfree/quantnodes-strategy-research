import { useState } from 'react'
import { useStudyStore } from '../../stores/study'
import { useAuthStore } from '../../stores/auth'
import { api, type MetricTarget } from '../../api/client'
import { Plus, X, Target, SlidersHorizontal, ChevronDown, ChevronRight } from 'lucide-react'
import { StrategyNameInput } from './StrategyNameInput'

interface Props {
  sessionId: string | null | undefined
  workspacePath: string
  onCreated?: (studyId: string) => void
  /** Compact variant for inline use (smaller text, collapsible advanced params) */
  compact?: boolean
}

const DEFAULT_METRICS: MetricTarget[] = [
  { name: 'calmar', op: '>=', value: 0.5 },
  { name: 'sharpe', op: '>=', value: 0.3 },
  { name: 'max_dd', op: '>=', value: -0.15 },
]

const INPUT_CLS = (compact?: boolean) =>
  `w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 ${
    compact ? 'text-xs' : 'text-sm'
  } text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40`

function Section({
  icon,
  title,
  children,
  compact,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
  compact?: boolean
}) {
  return (
    <div className={`rounded-xl border border-slate-800 bg-slate-900/60 ${compact ? 'p-2.5' : 'p-3.5'} shadow-soft`}>
      <div className={`mb-2.5 flex items-center gap-1.5 ${compact ? 'text-[9px]' : 'text-[10px]'} font-medium uppercase tracking-wider text-slate-500`}>
        {icon}
        {title}
      </div>
      {children}
    </div>
  )
}

export function StudyCreateForm({ sessionId, workspacePath, onCreated, compact }: Props) {
  const [objective, setObjective] = useState('')
  const [strategyName, setStrategyName] = useState('')
  const [metrics, setMetrics] = useState<MetricTarget[]>(DEFAULT_METRICS)
  const [budgetTurn, setBudgetTurn] = useState<number | ''>('')
  const [maxRounds, setMaxRounds] = useState<number | ''>('')
  const [monitorSec, setMonitorSec] = useState<number | ''>('')
  const [behavior, setBehavior] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const busy = useStudyStore((s) => s.busy)
  const setBusy = useStudyStore((s) => s.setBusy)
  const setErrorGlobal = useStudyStore((s) => s.setError)
  const user = useAuthStore((s) => s.user)

  const userId = user?.username ?? 'user'

  const updateMetric = (i: number, patch: Partial<MetricTarget>) => {
    setMetrics((prev) =>
      prev.map((m, idx) => (idx === i ? { ...m, ...patch } : m))
    )
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!sessionId) {
      setError('Session ID is required.')
      return
    }
    if (!objective.trim()) {
      setError('Objective is required.')
      return
    }
    if (!strategyName.trim()) {
      setError('Strategy name is required.')
      return
    }
    setSubmitting(true)
    setBusy(true)
    setErrorGlobal('')
    try {
      const r = await api.study.start({
        session_id: sessionId,
        objective: objective.trim(),
        workspace_path: workspacePath,
        strategy_name: strategyName,
        metric_targets: metrics,
        budget_turn: budgetTurn === '' ? undefined : Number(budgetTurn),
        max_rounds: maxRounds === '' ? undefined : Number(maxRounds),
        monitor_interval_seconds: monitorSec === '' ? undefined : Number(monitorSec),
        behavior: behavior || undefined,
      })
      setObjective('')
      setStrategyName('')
      onCreated?.(r.study_id)
    } catch (err) {
      setError((err as Error).message || 'Study start failed')
    } finally {
      setSubmitting(false)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className={`space-y-3 text-slate-100 ${compact ? 'text-xs' : ''}`}>
      <Section icon={<Target className="h-3 w-3 text-primary-400" />} title="研究目标" compact={compact}>
        <textarea
          rows={compact ? 2 : 3}
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder="例：研究 A 股动量因子，目标 Calmar ≥ 0.5"
          className={INPUT_CLS(compact)}
        />
        <div className="mt-2.5">
          <StrategyNameInput
            objective={objective}
            userId={userId}
            sessionId={sessionId ?? ''}
            value={strategyName}
            onChange={setStrategyName}
          />
        </div>
      </Section>

      <Section
        icon={<Target className="h-3 w-3 text-primary-400" />}
        title="验收指标（默认对齐 AcceptanceConfig：calmar/sharpe/max_dd）"
        compact={compact}
      >
        <div className="space-y-1.5">
          {metrics.map((m, i) => (
            <div key={i} className={`flex items-center gap-2 ${compact ? 'text-xs' : 'text-sm'}`}>
              <input
                className={`w-28 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200 outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40 ${compact ? 'text-xs' : ''}`}
                value={m.name}
                onChange={(e) => updateMetric(i, { name: e.target.value })}
                placeholder="metric"
              />
              <select
                className="cursor-pointer rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200 outline-none focus:border-primary-500"
                value={m.op}
                onChange={(e) =>
                  updateMetric(i, { op: e.target.value as MetricTarget['op'] })
                }
              >
                <option value=">=">&gt;=</option>
                <option value="<=">&lt;=</option>
                <option value=">">&gt;</option>
                <option value="<">&lt;</option>
                <option value="==">==</option>
              </select>
              <input
                type="number"
                step="0.01"
                className={`w-24 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-slate-200 outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40 ${compact ? 'text-xs' : ''}`}
                value={m.value}
                onChange={(e) => updateMetric(i, { value: Number(e.target.value) })}
              />
              {metrics.length > 1 && (
                <button
                  type="button"
                  onClick={() =>
                    setMetrics((prev) => prev.filter((_, idx) => idx !== i))
                  }
                  className="cursor-pointer text-slate-500 transition-colors hover:text-rose-400"
                  aria-label="Remove metric"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={() =>
            setMetrics((prev) => [...prev, { name: '', op: '>=', value: 0 }])
          }
          className="mt-1.5 inline-flex cursor-pointer items-center gap-1 text-xs text-slate-400 transition-colors hover:text-slate-200"
        >
          <Plus className="h-3 w-3" /> 添加指标
        </button>
      </Section>

      {/* Advanced params - collapsible in compact mode, always shown otherwise */}
      {compact ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 shadow-soft">
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex w-full items-center gap-2 p-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-slate-500 transition-colors hover:text-slate-400"
          >
            <SlidersHorizontal className="h-3 w-3 text-primary-400" />
            高级参数
            {showAdvanced ? (
              <ChevronDown className="ml-auto h-3 w-3" />
            ) : (
              <ChevronRight className="ml-auto h-3 w-3" />
            )}
          </button>
          {showAdvanced && (
            <div className="border-t border-slate-800 px-2.5 pb-2.5 pt-2">
              <AdvancedParams
                budgetTurn={budgetTurn}
                setBudgetTurn={setBudgetTurn}
                maxRounds={maxRounds}
                setMaxRounds={setMaxRounds}
                monitorSec={monitorSec}
                setMonitorSec={setMonitorSec}
                behavior={behavior}
                setBehavior={setBehavior}
                compact
              />
            </div>
          )}
        </div>
      ) : (
        <Section icon={<SlidersHorizontal className="h-3 w-3 text-primary-400" />} title="高级参数">
          <AdvancedParams
            budgetTurn={budgetTurn}
            setBudgetTurn={setBudgetTurn}
            maxRounds={maxRounds}
            setMaxRounds={setMaxRounds}
            monitorSec={monitorSec}
            setMonitorSec={setMonitorSec}
            behavior={behavior}
            setBehavior={setBehavior}
          />
        </Section>
      )}

      {error && (
        <p className="rounded-lg border border-rose-800 bg-rose-950/50 px-2.5 py-1.5 text-xs text-rose-300">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting || busy || !objective.trim() || !strategyName.trim()}
        className="w-full cursor-pointer rounded-xl bg-gradient-to-r from-primary-600 to-accent-500 px-3 py-2 text-sm font-medium text-white shadow-glow transition-all hover:from-primary-500 hover:to-accent-400 active:scale-[0.98] disabled:opacity-50 disabled:shadow-none"
      >
        {submitting ? '启动中…' : '启动 study'}
      </button>
    </form>
  )
}

// Extracted advanced params to avoid duplication
function AdvancedParams({
  budgetTurn, setBudgetTurn,
  maxRounds, setMaxRounds,
  monitorSec, setMonitorSec,
  behavior, setBehavior,
  compact,
}: {
  budgetTurn: number | ''
  setBudgetTurn: (v: number | '') => void
  maxRounds: number | '' | ''
  setMaxRounds: (v: number | '' | '') => void
  monitorSec: number | ''
  setMonitorSec: (v: number | '') => void
  behavior: string
  setBehavior: (v: string) => void
  compact?: boolean
}) {
  const inputCls = INPUT_CLS(compact)
  return (
    <div className={`grid ${compact ? 'grid-cols-1 gap-2' : 'grid-cols-2 gap-2.5'}`}>
      <div>
        <label className={`mb-1 block ${compact ? 'text-[9px]' : 'text-[10px]'} text-slate-500`}>轮数预算 (turns)</label>
        <input
          type="number"
          min={1}
          className={inputCls}
          value={budgetTurn}
          onChange={(e) =>
            setBudgetTurn(e.target.value === '' ? '' : Number(e.target.value))
          }
          placeholder="不限"
        />
      </div>
      <div>
        <label className={`mb-1 block ${compact ? 'text-[9px]' : 'text-[10px]'} text-slate-500`}>最大轮数</label>
        <input
          type="number"
          min={1}
          className={inputCls}
          value={maxRounds}
          onChange={(e) =>
            setMaxRounds(e.target.value === '' ? '' : Number(e.target.value))
          }
          placeholder="不限"
        />
      </div>
      <div>
        <label className={`mb-1 block ${compact ? 'text-[9px]' : 'text-[10px]'} text-slate-500`}>监控间隔（秒，0=不监控）</label>
        <input
          type="number"
          min={0}
          className={inputCls}
          value={monitorSec}
          onChange={(e) =>
            setMonitorSec(e.target.value === '' ? '' : Number(e.target.value))
          }
          placeholder="例 3600"
        />
      </div>
      <div>
        <label className={`mb-1 block ${compact ? 'text-[9px]' : 'text-[10px]'} text-slate-500`}>Behavior (CI)</label>
        <select
          className={`w-full cursor-pointer rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 ${compact ? 'text-xs' : 'text-sm'} text-slate-200 outline-none focus:border-primary-500`}
          value={behavior}
          onChange={(e) => setBehavior(e.target.value)}
        >
          <option value="">real LLM</option>
          <option value="static">static</option>
          <option value="varying">varying</option>
          <option value="improving">improving</option>
        </select>
      </div>
    </div>
  )
}
