import { Send } from 'lucide-react'
import { type StudySummaryResponse } from '../../api/client'
import { ObjectiveProgress } from './ObjectiveProgress'
import { AgentFlowCanvas } from './AgentFlowCanvas'

interface StudyFlowTabProps {
  studyId: string
  summary: StudySummaryResponse
  directiveText: string
  submittingDirective: boolean
  canDirective: boolean
  onDirective: () => void
  onDirectiveTextChange: (text: string) => void
}

export function StudyFlowTab({
  studyId,
  summary,
  directiveText,
  submittingDirective,
  canDirective,
  onDirective,
  onDirectiveTextChange,
}: StudyFlowTabProps) {
  const progressPercent = summary.goal_snapshot?.progress_percent ?? 0
  const evidenceCount = summary.goal_snapshot?.evidence_count ?? 0

  return (
    <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
      {/* Row 1: Objective progress (2/3) + Directive input (1/3) */}
      <div className="lg:col-span-2">
        <ObjectiveProgress
          objective={summary.objective}
          progressPercent={progressPercent}
          evidenceCount={evidenceCount}
          criteria={summary.goal_snapshot?.criteria ?? []}
        />
      </div>
      <div>
        {canDirective && (
          <div className="h-full rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft flex flex-col">
            <label className="block text-[10px] font-medium uppercase tracking-wider text-slate-500 mb-2">
              注入研究方向
            </label>
            <textarea
              rows={2}
              value={directiveText}
              onChange={(e) => onDirectiveTextChange(e.target.value)}
              placeholder="例：改成动量因子 + 减小 top_n"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
            />
            <button
              type="button"
              onClick={onDirective}
              disabled={submittingDirective || !directiveText.trim()}
              className="mt-2 inline-flex cursor-pointer items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" /> 提交指令
            </button>
          </div>
        )}
      </div>

      {/* Row 2: Agent flow canvas (full width, aligned with top) */}
      <div className="lg:col-span-3">
        <AgentFlowCanvas
          studyId={studyId}
          currentRound={summary.current_round ?? 1}
          totalRounds={summary.max_rounds}
        />
      </div>
    </div>
  )
}
