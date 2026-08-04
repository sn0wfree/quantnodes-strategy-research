import { useState } from 'react'
import { useStudyStore } from '../../stores/study'
import { api, type MetricTarget } from '../../api/client'
import { Plus, X } from 'lucide-react'

interface Props {
  workspacePath: string
  strategyName: string
  onCreated?: () => void
}

const DEFAULT_METRICS: MetricTarget[] = [
  { name: 'calmar', op: '>=', value: 0.5 },
  { name: 'sharpe', op: '>=', value: 0.3 },
  { name: 'max_dd', op: '>=', value: -0.15 },
]

export function StudyCreateForm({ workspacePath, strategyName, onCreated }: Props) {
  const [objective, setObjective] = useState('')
  const [metrics, setMetrics] = useState<MetricTarget[]>(DEFAULT_METRICS)
  const [budgetTurn, setBudgetTurn] = useState<number | ''>('')
  const [maxRounds, setMaxRounds] = useState<number | ''>('')
  const [monitorSec, setMonitorSec] = useState<number | ''>('')
  const [behavior, setBehavior] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const busy = useStudyStore((s) => s.busy)
  const setBusy = useStudyStore((s) => s.setBusy)
  const setErrorGlobal = useStudyStore((s) => s.setError)

  const updateMetric = (i: number, patch: Partial<MetricTarget>) => {
    setMetrics((prev) =>
      prev.map((m, idx) => (idx === i ? { ...m, ...patch } : m))
    )
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!objective.trim()) {
      setError('Objective is required.')
      return
    }
    setSubmitting(true)
    setBusy(true)
    setErrorGlobal('')
    try {
      const sessionId = (window as unknown as { __sessionId?: string })
        .__sessionId
        ?? ''
      await api.study.start({
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
      onCreated?.()
    } catch (err) {
      setError((err as Error).message || 'Study start failed')
    } finally {
      setSubmitting(false)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4 text-slate-100">
      <div>
        <label className="block text-xs font-medium text-slate-300 mb-1">
          研究目标
        </label>
        <textarea
          rows={3}
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder="例：研究 A 股动量因子，目标 Calmar ≥ 0.5"
          className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-300 mb-1">
          验收指标（默认对齐 AcceptanceConfig：calmar/sharpe/max_dd）
        </label>
        <div className="space-y-1">
          {metrics.map((m, i) => (
            <div key={i} className="flex items-center gap-2 text-sm">
              <input
                className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-0.5"
                value={m.name}
                onChange={(e) => updateMetric(i, { name: e.target.value })}
                placeholder="metric"
              />
              <select
                className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5"
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
                className="w-24 rounded border border-slate-700 bg-slate-900 px-2 py-0.5"
                value={m.value}
                onChange={(e) => updateMetric(i, { value: Number(e.target.value) })}
              />
              {metrics.length > 1 && (
                <button
                  type="button"
                  onClick={() =>
                    setMetrics((prev) => prev.filter((_, idx) => idx !== i))
                  }
                  className="text-slate-500 hover:text-rose-400"
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
          className="mt-1 inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
        >
          <Plus className="h-3 w-3" /> 添加指标
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-slate-400 mb-1">
            轮数预算 (turns)
          </label>
          <input
            type="number"
            min={1}
            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-sm"
            value={budgetTurn}
            onChange={(e) =>
              setBudgetTurn(e.target.value === '' ? '' : Number(e.target.value))
            }
            placeholder="不限"
          />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1">
            最大轮数
          </label>
          <input
            type="number"
            min={1}
            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-sm"
            value={maxRounds}
            onChange={(e) =>
              setMaxRounds(e.target.value === '' ? '' : Number(e.target.value))
            }
            placeholder="不限"
          />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1">
            监控间隔（秒，0=不监控）
          </label>
          <input
            type="number"
            min={0}
            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-sm"
            value={monitorSec}
            onChange={(e) =>
              setMonitorSec(e.target.value === '' ? '' : Number(e.target.value))
            }
            placeholder="例 3600"
          />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1">
            Behavior (CI)
          </label>
          <select
            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-sm"
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

      {error && <p className="text-xs text-rose-400">{error}</p>}

      <button
        type="submit"
        disabled={submitting || busy || !objective.trim()}
        className="w-full rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-50"
      >
        {submitting ? '启动中…' : '启动 study'}
      </button>
    </form>
  )
}