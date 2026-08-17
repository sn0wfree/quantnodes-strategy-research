import { StudyCreateForm } from './StudyCreateForm'

interface Props {
  sessionId: string | null | undefined
  workspacePath: string
  onCreated?: (studyId: string) => void
}

/**
 * StudyCreatePanel — thin wrapper around StudyCreateForm with compact variant.
 * Used in StudyPage (three-column layout).
 */
export function StudyCreatePanel({ sessionId, workspacePath, onCreated }: Props) {
  return (
    <StudyCreateForm
      sessionId={sessionId}
      workspacePath={workspacePath}
      onCreated={onCreated}
      compact
    />
  )
}
