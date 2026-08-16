import { useState } from 'react'
import { BookOpen } from 'lucide-react'
import { StudyCreateForm } from './StudyCreateForm'
import { StudyProgress } from './StudyProgress'
import { EmptyState } from '../common/EmptyState'
import { useStudyStore } from '../../stores/study'

interface StudyTabProps {
  /** Active session id; undefined means no session yet. */
  sessionId: string | undefined
  /** Path to the workspace (passed to the create form). */
  workspacePath: string
}

export function StudyTab({ sessionId, workspacePath }: StudyTabProps) {
  const current = useStudyStore((s) => s.current)
  const setCurrent = useStudyStore((s) => s.setCurrent)
  const [creating, setCreating] = useState(false)

  const hasActiveStudy =
    current && current.status === 'ok' && current.study_id

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center gap-2 flex-shrink-0">
        <BookOpen className="h-4 w-4 text-slate-400" />
        <h3 className="text-sm font-semibold text-slate-100">Study</h3>
        {hasActiveStudy && (
          <button
            onClick={() => setCurrent(null)}
            className="ml-auto text-[10px] text-slate-500 hover:text-slate-300"
          >
            清空显示
          </button>
        )}
      </div>

      {!sessionId && (
        <div className="flex-1 flex flex-col min-h-0">
          <EmptyState
            icon={<BookOpen className="h-10 w-10" />}
            title="尚未选择 session"
            description="先在左侧选择或创建一个 chat session"
          />
        </div>
      )}

      {sessionId && (creating || !hasActiveStudy) && (
        <div className="flex-1 flex flex-col min-h-0">
          <StudyCreateForm
            sessionId={sessionId}
            workspacePath={workspacePath}
            onCreated={() => setCreating(false)}
          />
        </div>
      )}

      {sessionId && hasActiveStudy && !creating && (
        <div className="flex flex-1 flex-col gap-3 min-h-0">
          <button
            onClick={() => setCreating(true)}
            className="w-full flex-shrink-0 cursor-pointer rounded-lg border border-dashed border-slate-700 px-3 py-1.5 text-xs text-slate-400 transition-colors hover:border-slate-500 hover:bg-slate-800/30 hover:text-slate-200"
          >
            + 启动新的 study
          </button>
          <div className="flex-1 flex flex-col min-h-0">
            <StudyProgress sessionId={sessionId} />
          </div>
        </div>
      )}
    </div>
  )
}