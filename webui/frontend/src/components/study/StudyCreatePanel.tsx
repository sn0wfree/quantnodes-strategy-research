import { useState } from 'react'
import { useStudyStore } from '../../stores/study'
import { useAuthStore } from '../../stores/auth'
import { api, type MetricTarget } from '../../api/client'
import { Plus, X, Send, SlidersHorizontal, ChevronDown, ChevronRight } from 'lucide-react'
import { StrategyNameInput } from './StrategyNameInput'

interface Props {
  sessionId: string | null | undefined
  workspacePath: string
  /** Called with the new study id after a successful start. */
  onCreated?: (studyId: string) => void
}

const DEFAULT_METRICS: MetricTarget[] = [
  { name: 'calmar', op: '>=', value: 0.5 },
  { name: 'sharpe', op: '>=', value: 0.3 },
  { name: 'max_dd', op: '>=', value: -0.15 },
]

const INPUT_CLS =
  'w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40'

export function StudyCreatePanel({ sessionId, workspacePath, onCreated }: Props) {
  const [objective, setObjective] = useState('')
  const [strategyName, setStrategyName] = useState('')
  const [metrics, setMetrics] = useState<MetricTarget[]>(DEFAULT_METRICS)
  const [budgetTurn, setBudgetTurn] = useState<number | ''>('')
  const [maxRounds, setMaxRounds] = useState<number | ''>('')
  const [monitorSec, setMonitorSec] = useState<number | ''>('')
  const [behavior, setBehavior] = useState<string>('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

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
      setError('请先在聊天页选择一个 session。')
      return
    }
    if (!objective.trim()) {
      setError('请输入研究目标。')
      return
    }
    if (!strategyName.trim()) {
      setError('请输入策略名称。')
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

  if (!sessionId) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-center shadow-soft">
        <p className="text-xs text-slate-400">尚未选择 session</p>
        <p className="mt-1 text-[10px] text-slate-600">先在聊天页选择或创建一个 chat session</p>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2.5 text-slate-100">
      <div>
        <label className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-slate-500">
          研究目标
        </label>
        <textarea
          rows={3}
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder="例：研究 A 股动量因子，目标 Calmar ≥ 0.5"
          className="w-full resize-none rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs leading-relaxed text-slate-200 outline-none transition-shadow placeholder:text-slate-600 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
        />
      </div>

      <StrategyNameInput
        objective={objective}
        userId={userId}
        sessionId={sessionId}
        value={strategyName}
        onChange={setStrategyName}
      />

      {/* Advanced params (collapsed by default) */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="flex w-full cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-2 text-[10px] font-medium text-slate-400 transition-colors hover:text-slate-200"
        >
          {showAdvanced ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
          <SlidersHorizontal className="h-3 w-3" />
          高级参数
          <span className="ml-auto font-mono text-[9px] text-slate-600">
            {metrics.length} 指标
          </span>
        </button>

        {showAdvanced && (
          <div className="space-y-2.5 border-t border-slate-800/70 px-2.5 py-2.5">
            <div>
              <label className="mb-1 block text-[10px] text-slate-500">验收指标</label>
              <div className="space-y-1">
                {metrics.map((m, i) => (
                  <div key={i} className="flex items-center gap-1.5 text-xs">
                    <input
                      className="w-24 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
                      value={m.name}
                      onChange={(e) => updateMetric(i, { name: e.target.value })}
                      placeholder="metric"
                    />
                    <select
                      className="cursor-pointer rounded-lg border border-slate-700 bg-slate-900 px-1.5 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
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
                      className="w-20 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
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
                        <X className="h-3 w-3" />
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
                className="mt-1 inline-flex cursor-pointer items-center gap-1 text-[10px] text-slate-400 transition-colors hover:text-slate-200"
              >
                <Plus className="h-3 w-3" /> 添加指标
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="mb-1 block text-[10px] text-slate-500">轮数预算 (turns)</label>
                <input
                  type="number"
                  min={1}
                  className={INPUT_CLS}
                  value={budgetTurn}
                  onChange={(e) =>
                    setBudgetTurn(e.target.value === '' ? '' : Number(e.target.value))
                  }
                  placeholder="不限"
                />
              </div>
              <div>
                <label className="mb-1 block text-[10px] text-slate-500">最大轮数</label>
                <input
                  type="number"
                  min={1}
                  className={INPUT_CLS}
                  value={maxRounds}
                  onChange={(e) =>
                    setMaxRounds(e.target.value === '' ? '' : Number(e.target.value))
                  }
                  placeholder="不限"
                />
              </div>
              <div>
                <label className="mb-1 block text-[10px] text-slate-500">监控间隔（秒）</label>
                <input
                  type="number"
                  min={0}
                  className={INPUT_CLS}
                  value={monitorSec}
                  onChange={(e) =>
                    setMonitorSec(e.target.value === '' ? '' : Number(e.target.value))
                  }
                  placeholder="0=不监控"
                />
              </div>
              <div>
                <label className="mb-1 block text-[10px] text-slate-500">Behavior (CI)</label>
                <select
                  className="w-full cursor-pointer rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-primary-500"
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
          </div>
        )}
      </div>

      {error && (
        <p className="rounded-lg border border-rose-800 bg-rose-950/50 px-2.5 py-1.5 text-[10px] text-rose-300">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting || busy || !objective.trim() || !strategyName.trim()}
        className="flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-xl bg-gradient-to-r from-primary-600 to-accent-500 px-3 py-2 text-xs font-medium text-white shadow-glow transition-all hover:from-primary-500 hover:to-accent-400 active:scale-[0.98] disabled:opacity-50 disabled:shadow-none"
      >
        <Send className="h-3.5 w-3.5" />
        {submitting ? '启动中…' : '启动 study'}
      </button>
    </form>
  )
}
