import { create } from 'zustand'

export interface Criterion {
  criterion_id: string
  text: string
  status: string
  required: boolean
  evidence_count?: number
}

export interface Goal {
  goal_id: string
  session_id: string
  status: string
  objective: string
  progress_percent: number
  criteria: Criterion[]
  evidence_count: number
  created_at?: string
  updated_at?: string
  recap?: string
}

interface GoalState {
  currentGoal: Goal | null
  setGoal: (goal: Goal | null) => void
  updateGoal: (updater: (g: Goal) => void) => void
  clearGoal: () => void
}

export const useGoalStore = create<GoalState>()((set) => ({
  currentGoal: null,
  setGoal: (goal) => set({ currentGoal: goal }),
  updateGoal: (updater) =>
    set((state) => {
      if (!state.currentGoal) return state
      // structuredClone: nested fields (criteria, evidence) must not be
      // shared with the previous state, or an updater mutating them
      // would leak into the old snapshot (B10).
      const copy = structuredClone(state.currentGoal)
      updater(copy)
      return { currentGoal: copy }
    }),
  clearGoal: () => set({ currentGoal: null }),
}))
