/**
 * KeyPointsPanel — right sidebar showing all study rounds as compact cards.
 * Data source: GET /study/{id}/summary (recent_rounds).
 * Manifests are lazy-loaded per card (inside RoundCard).
 * Panel collapse state persisted to localStorage.
 */
import { useEffect, useState } from 'react'
import { api, type StudyRoundSummary } from '../../../../api/client'
import { RoundCard } from './RoundCard'

interface KeyPointsPanelProps {
  studyId: string
  selectedRound: number
  onSelectRound: (round: number) => void
  /** Trigger refresh when new round SSE events arrive */
  refreshKey?: number
}

export function KeyPointsPanel({
  studyId,
  selectedRound,
  onSelectRound,
  refreshKey,
}: KeyPointsPanelProps) {
  const [rounds, setRounds] = useState<StudyRoundSummary[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.study
      .summary(studyId)
      .then((r) => {
        if (!cancelled) {
          setRounds(r.recent_rounds ?? [])
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [studyId, refreshKey])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2.5">
        <span className="text-[11px] font-medium text-slate-400">📌 关键点</span>
        <span className="text-[10px] text-slate-600">{rounds.length} 轮</span>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 py-2">
        {loading && rounds.length === 0 && (
          <div className="py-8 text-center text-[11px] text-slate-500">加载轮次中...</div>
        )}

        {!loading && rounds.length === 0 && (
          <div className="py-8 text-center text-[11px] text-slate-500">暂无轮次</div>
        )}

        {/* Newest rounds first */}
        {[...rounds]
          .sort((a, b) => b.round_num - a.round_num)
          .map((r) => (
            <RoundCard
              key={r.round_num}
              studyId={studyId}
              roundNum={r.round_num}
              verdict={r.verdict}
              metrics={r.metrics}
              isSelected={r.round_num === selectedRound}
              onClick={() => onSelectRound(r.round_num)}
            />
          ))}
      </div>
    </div>
  )
}
