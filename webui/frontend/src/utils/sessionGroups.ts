export type SessionGroup = 'today' | 'week' | 'older'

export const SESSION_GROUP_LABELS: { key: SessionGroup; label: string }[] = [
  { key: 'today', label: '今天' },
  { key: 'week', label: '7 天内' },
  { key: 'older', label: '更早' },
]

/** Bucket sessions by recency (assumes input sorted newest-first so
 *  each group keeps relative order). */
export function groupSessions<T extends { updated_at: number }>(
  list: T[]
): Record<SessionGroup, T[]> {
  const now = Date.now() / 1000
  const day = 86400
  const groups: Record<SessionGroup, T[]> = { today: [], week: [], older: [] }
  for (const s of list) {
    const age = now - s.updated_at
    if (age < day) groups.today.push(s)
    else if (age <= 7 * day) groups.week.push(s)
    else groups.older.push(s)
  }
  return groups
}
