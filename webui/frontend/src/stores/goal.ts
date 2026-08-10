import { create } from 'zustand'
import type { Criterion } from '../api/client'

export type { Criterion }

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
  clearGoal: () => void
}

export const useGoalStore = create<GoalState>()((set) => ({
  currentGoal: null,
  setGoal: (goal) => set({ currentGoal: goal }),
  clearGoal: () => set({ currentGoal: null }),
}))
