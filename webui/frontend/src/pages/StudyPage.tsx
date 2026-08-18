import { useEffect, useState, useCallback } from 'react'
import { BookOpen } from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { StudyCreatePanel } from '../components/study/StudyCreatePanel'
import { StudyTaskList } from '../components/study/StudyTaskList'
import { StudyTaskSummary } from '../components/study/StudyTaskSummary'
import { api, type StudySummary } from '../api/client'
import { useSessionStore } from '../stores/session'
import { useSystemStore } from '../stores/system'
import { useWorkflowStore } from '../stores/workflow'

/**
 * Three-column study dashboard (Linear-style task workbench):
 * left = create panel, middle = task list, right = selected task summary.
 * Clicking a task opens its full run page at /study/{id}.
 */
export function StudyPage() {
  const sessionId = useSessionStore((s) => s.currentSessionId)

  // Resolve workspace path (same precedence as the old right panel)
  const systemWorkspacePath = useSystemStore((s) => s.workspacePath)
  const presets = useWorkflowStore((s) => s.presets)
  const currentPresetId = useWorkflowStore((s) => s.currentPresetId)
  const currentPreset = presets.find((p) => p.id === currentPresetId)
  const workspacePath =
    systemWorkspacePath
    || (currentPreset as unknown as { workspace_path?: string })?.workspace_path
    || ''

  const [studies, setStudies] = useState<StudySummary[]>([])
  const [loadingList, setLoadingList] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [includeArchived, setIncludeArchived] = useState(false)

  const loadList = useCallback(async () => {
    setLoadingList(true)
    try {
      const res = await api.study.list({ limit: 50, include_archived: includeArchived })
      setStudies(res.studies ?? [])
    } catch {
      // Non-critical — history list can be empty
    } finally {
      setLoadingList(false)
    }
  }, [includeArchived])

  useEffect(() => {
    void loadList()
  }, [loadList])

  // Auto-select the first active study once the list loads (or when a
  // selected study disappears).
  useEffect(() => {
    if (studies.length === 0) {
      setSelectedId((cur) => (cur && cur !== null ? cur : null))
      return
    }
    if (selectedId && studies.some((s) => s.study_id === selectedId)) return
    const active = studies.find((s) =>
      ['running', 'queued', 'monitoring', 'paused'].includes(s.execution_status)
    )
    setSelectedId(active?.study_id ?? studies[0].study_id)
  }, [studies, selectedId])

  const handleCreated = (studyId: string) => {
    setSelectedId(studyId)
    void loadList()
  }

  const select = useCallback((study: StudySummary) => {
    setSelectedId(study.study_id)
  }, [])

  return (
    <PageShell
      title="Study 研究任务"
      subtitle="9-agent 多轮自主研究 · researcher → backtest → 验收"
      icon={<BookOpen className="h-4 w-4" />}
    >
      <div className="grid flex-1 gap-4 xl:grid-cols-[300px_minmax(0,1fr)_320px] lg:grid-cols-[300px_minmax(0,1fr)]">
        {/* Left: create panel */}
        <section className="min-w-0">
          <StudyCreatePanel
            sessionId={sessionId}
            workspacePath={workspacePath}
            onCreated={handleCreated}
          />
        </section>

        {/* Middle: task list */}
        <section className="min-h-[480px] min-w-0 lg:min-h-0">
          <StudyTaskList
            studies={studies}
            selectedId={selectedId}
            loading={loadingList}
            onSelect={select}
            onRefresh={() => void loadList()}
            includeArchived={includeArchived}
            onToggleArchived={(v) => setIncludeArchived(v)}
            onAction={() => void loadList()}
          />
        </section>

        {/* Right: selected task summary (below the list on < lg) */}
        <section className="min-w-0 lg:col-span-2 xl:col-span-1">
          <StudyTaskSummary studyId={selectedId} />
        </section>
      </div>
    </PageShell>
  )
}
