import { create } from 'zustand'

export type RightPanelTab = 'dag' | 'goal' | 'agent'
export type WorkMode = 'chat' | 'monitor' | 'focus'

interface LayoutState {
  navWidth: number
  rightPanelVisible: boolean
  rightPanelTab: RightPanelTab
  workMode: WorkMode
  leftRatio: number
  setNavWidth: (w: number) => void
  toggleRightPanel: () => void
  setRightPanelTab: (tab: RightPanelTab) => void
  setWorkMode: (mode: WorkMode) => void
  setLeftRatio: (r: number) => void
}

export const useLayoutStore = create<LayoutState>()((set) => ({
  navWidth: 64,
  rightPanelVisible: true,
  rightPanelTab: 'dag',
  workMode: 'monitor',
  leftRatio: 0.5,
  setNavWidth: (w) => set({ navWidth: w }),
  toggleRightPanel: () => set((s) => ({ rightPanelVisible: !s.rightPanelVisible })),
  setRightPanelTab: (tab) => set({ rightPanelTab: tab, rightPanelVisible: true }),
  setWorkMode: (mode) =>
    set({
      workMode: mode,
      rightPanelVisible: mode === 'monitor',
    }),
  setLeftRatio: (r) => set({ leftRatio: r }),
}))
