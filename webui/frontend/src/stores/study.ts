import { create } from 'zustand'
import type { StudySummary, StudyStatusResponse } from '../api/client'

export interface StudyState {
  /** Most recent status response for the active session. */
  current: StudyStatusResponse | null
  /** Cached list (used by the GoalsPanel history tab). */
  list: StudySummary[]
  /** True while a write is in flight (start/pause/...). */
  busy: boolean
  /** Last error surfaced to the UI (empty when none). */
  error: string

  setCurrent: (s: StudyStatusResponse | null) => void
  setList: (rows: StudySummary[]) => void
  setBusy: (b: boolean) => void
  setError: (e: string) => void
  reset: () => void
}

export const useStudyStore = create<StudyState>()((set) => ({
  current: null,
  list: [],
  busy: false,
  error: '',
  setCurrent: (current) => set({ current }),
  setList: (list) => set({ list }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  reset: () => set({ current: null, list: [], busy: false, error: '' }),
}))

// Convenience helpers (kept here so components don't re-import api).
export type {
  MetricTarget,
  StudySummary,
  StudyStatusResponse,
} from '../api/client'