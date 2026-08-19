/**
 * RoundCard — compact card for a study round in the right panel.
 * Shows: round number, verdict badge, hypothesis one-liner, ΔCalmar.
 */
import { useEffect, useState } from 'react'
import { api } from '../../../../api/client'

interface RoundCardProps {
  studyId: string
  roundNum: number
  verdict: string | null
  metrics: Record<string, number> | null
  isSelected: boolean
  onClick: () => void
}

interface Manifest {
  hypothesis?: { text?: string; levers?: string[] }
  metrics?: { vs_prev?: { calmar?: number } }
  verdict?: { decision?: string }
}

export function RoundCard({
  studyId,
  roundNum,
  verdict,
  metrics: _metrics,
  isSelected,
  onClick,
}: RoundCardProps) {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.study
      .roundManifest(studyId, roundNum)
      .then((r) => {
        if (!cancelled) {
          setManifest(r.manifest as Manifest)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [studyId, roundNum])

  const keepOrDiscard = verdict === 'keep' ? 'keep' : verdict === 'discard' ? 'discard' : 'unknown'
  const hypothesisText = manifest?.hypothesis?.text ?? (loading ? '加载中...' : '')
  const hypothesisTruncated =
    hypothesisText.length > 48 ? hypothesisText.slice(0, 48) + '...' : hypothesisText

  // ΔCalmar from manifest vs_prev
  const calmarDelta = manifest?.metrics?.vs_prev?.calmar
  const calmarStr =
    calmarDelta != null
      ? `${calmarDelta >= 0 ? '▲' : '▼'} ${calmarDelta >= 0 ? '+' : ''}${calmarDelta.toFixed(2)}`
      : null

  return (
    <button
      onClick={onClick}
      className={`w-full rounded-lg border px-3 py-2.5 text-left transition-all ${
        isSelected
          ? 'border-primary-500/50 bg-primary-500/10'
          : 'border-slate-800 bg-slate-900/40 hover:border-slate-700 hover:bg-slate-800/50'
      }`}
    >
      {/* Header: Round N + verdict badge */}
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-slate-300">Round {roundNum}</span>
        <span
          className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
            keepOrDiscard === 'keep'
              ? 'bg-emerald-500/15 text-emerald-400'
              : keepOrDiscard === 'discard'
              ? 'bg-rose-500/15 text-rose-400'
              : 'bg-slate-500/15 text-slate-400'
          }`}
        >
          {keepOrDiscard === 'keep' ? '✅ keep' : keepOrDiscard === 'discard' ? '❌ discard' : '—'}
        </span>
      </div>

      {/* Hypothesis one-liner */}
      {hypothesisText && (
        <div className="mt-1.5 text-[11px] leading-snug text-slate-400 line-clamp-1">
          {hypothesisTruncated}
        </div>
      )}

      {/* Calmar delta */}
      {calmarStr && (
        <div
          className={`mt-1 text-[11px] font-medium ${
            calmarDelta != null && calmarDelta >= 0 ? 'text-emerald-400' : 'text-rose-400'
          }`}
        >
          Calmar {calmarStr}
        </div>
      )}
    </button>
  )
}
