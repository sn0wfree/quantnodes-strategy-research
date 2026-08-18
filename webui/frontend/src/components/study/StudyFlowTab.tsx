import { Send, Clock, FileText } from 'lucide-react'
import { type StudySummaryResponse, type StudyDirectivesResponse, type StudyJournalResponse } from '../../api/client'
import { ObjectiveProgress } from './ObjectiveProgress'
import { AgentFlowCanvas } from './AgentFlowCanvas'
import { AgentChatLog } from './AgentChatLog'

function formatDateTime(iso?: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return '—'
  }
}

interface StudyFlowTabProps {
  studyId: string
  summary: StudySummaryResponse
  directives: StudyDirectivesResponse | null
  journal: StudyJournalResponse | null
  directiveText: string
  submittingDirective: boolean
  canDirective: boolean
  onDirective: () => void
  onDirectiveTextChange: (text: string) => void
}

export function StudyFlowTab({
  studyId,
  summary,
  directives,
  journal,
  directiveText,
  submittingDirective,
  canDirective,
  onDirective,
  onDirectiveTextChange,
}: StudyFlowTabProps) {
  const progressPercent = summary.goal_snapshot?.progress_percent ?? 0
  const evidenceCount = summary.goal_snapshot?.evidence_count ?? 0

  return (
    <div className="mt-4 flex flex-col gap-4">
      {/* Top: Objective progress + Directive input */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <ObjectiveProgress
            objective={summary.objective}
            progressPercent={progressPercent}
            evidenceCount={evidenceCount}
            criteria={summary.goal_snapshot?.criteria ?? []}
          />
        </div>
        <div>
          {canDirective && (
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft space-y-2">
              <label className="block text-[10px] font-medium uppercase tracking-wider text-slate-500">
                注入研究方向
              </label>
              <textarea
                rows={2}
                value={directiveText}
                onChange={(e) => onDirectiveTextChange(e.target.value)}
                placeholder="例：改成动量因子 + 减小 top_n"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
              />
              <button
                type="button"
                onClick={onDirective}
                disabled={submittingDirective || !directiveText.trim()}
                className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" /> 提交指令
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Middle: Agent flow canvas */}
      <AgentFlowCanvas
        studyId={studyId}
        currentRound={summary.current_round ?? 1}
        totalRounds={summary.max_rounds}
      />

      {/* Bottom: Chat log + Directives audit trail */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <AgentChatLog
          studyId={studyId}
          currentRound={summary.current_round ?? 1}
        />

        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
          <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
            <Clock className="h-3 w-3" /> 指令记录
          </div>
          {(directives?.directives?.length ?? 0) === 0 ? (
            <p className="text-xs text-slate-500">暂无指令</p>
          ) : (
            <ul className="space-y-1.5 max-h-64 overflow-y-auto">
              {directives!.directives.map((d) => (
                <li key={d.directive_id} className="rounded-lg border border-slate-800/60 bg-slate-950/60 p-2 text-[11px]">
                  <p className="text-slate-300">{d.content}</p>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-500">
                    <span>{formatDateTime(d.created_at)}</span>
                    {d.issued_by && <span>· {d.issued_by}</span>}
                    <span
                      className={
                        d.consumed_at
                          ? 'rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1.5 text-emerald-400'
                          : 'rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 text-amber-400'
                      }
                    >
                      {d.consumed_at ? '已消费' : '待消费'}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Journal */}
      {journal?.journal && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft">
          <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
            <FileText className="h-3 w-3" /> 研究日志 journal.md
          </div>
          <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950/60 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-slate-400">
            {journal.journal}
          </pre>
        </div>
      )}
    </div>
  )
}
