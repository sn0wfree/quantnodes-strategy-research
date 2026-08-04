import { create } from 'zustand'

export type RightPanelTab = 'dag' | 'goal' | 'study' | 'agent'
export type WorkMode = 'chat' | 'monitor' | 'focus'
export type ChatLayout = 'bubble' | 'flat'

const CHAT_LAYOUT_KEY = 'sr-chat-layout'

function loadInitialLayout(): ChatLayout {
  if (typeof window === 'undefined') return 'bubble'
  return localStorage.getItem(CHAT_LAYOUT_KEY) === 'flat' ? 'flat' : 'bubble'
}

interface LayoutState {
  navWidth: number
  rightPanelVisible: boolean
  rightPanelTab: RightPanelTab
  workMode: WorkMode
  leftRatio: number
  settingsOpen: boolean
  chatLayout: ChatLayout
  setNavWidth: (w: number) => void
  toggleRightPanel: () => void
  setRightPanelTab: (tab: RightPanelTab) => void
  setWorkMode: (mode: WorkMode) => void
  setLeftRatio: (r: number) => void
  setSettingsOpen: (open: boolean) => void
  setChatLayout: (layout: ChatLayout) => void
}

export const useLayoutStore = create<LayoutState>()((set) => ({
  navWidth: 64,
  rightPanelVisible: true,
  rightPanelTab: 'dag',
  workMode: 'monitor',
  leftRatio: 0.5,
  settingsOpen: false,
  chatLayout: loadInitialLayout(),
  setNavWidth: (w) => set({ navWidth: w }),
  toggleRightPanel: () => set((s) => ({ rightPanelVisible: !s.rightPanelVisible })),
  setRightPanelTab: (tab) => set({ rightPanelTab: tab, rightPanelVisible: true }),
  setWorkMode: (mode) =>
    set({
      workMode: mode,
      rightPanelVisible: mode === 'monitor',
    }),
  setLeftRatio: (r) => set({ leftRatio: Math.max(0.2, Math.min(0.8, r)) }),
  setSettingsOpen: (open) => set({ settingsOpen: open }),
  setChatLayout: (layout) => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(CHAT_LAYOUT_KEY, layout)
    }
    set({ chatLayout: layout })
  },
}))