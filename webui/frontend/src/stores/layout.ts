import { create } from 'zustand'

export type RightPanelTab = 'dag' | 'goal' | 'study' | 'agent'
export type WorkMode = 'chat' | 'monitor' | 'focus'
export type ChatLayout = 'bubble' | 'flat'

const CHAT_LAYOUT_KEY = 'sr-chat-layout'
const SIDEBAR_KEY = 'sr-sidebar-open'

function loadInitialLayout(): ChatLayout {
  if (typeof window === 'undefined') return 'bubble'
  return localStorage.getItem(CHAT_LAYOUT_KEY) === 'flat' ? 'flat' : 'bubble'
}

function loadInitialSidebar(): boolean {
  if (typeof window === 'undefined') return true
  return localStorage.getItem(SIDEBAR_KEY) !== 'false'
}

interface LayoutState {
  navWidth: number
  sidebarOpen: boolean
  rightPanelVisible: boolean
  rightPanelTab: RightPanelTab
  workMode: WorkMode
  leftRatio: number
  settingsOpen: boolean
  chatLayout: ChatLayout
  setNavWidth: (w: number) => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  toggleRightPanel: () => void
  setRightPanelTab: (tab: RightPanelTab) => void
  setWorkMode: (mode: WorkMode) => void
  setLeftRatio: (r: number) => void
  setSettingsOpen: (open: boolean) => void
  setChatLayout: (layout: ChatLayout) => void
}

export const useLayoutStore = create<LayoutState>()((set) => ({
  navWidth: 64,
  sidebarOpen: loadInitialSidebar(),
  rightPanelVisible: true,
  rightPanelTab: 'dag',
  workMode: 'monitor',
  leftRatio: 0.5,
  settingsOpen: false,
  chatLayout: loadInitialLayout(),
  setNavWidth: (w) => set({ navWidth: w }),
  toggleSidebar: () =>
    set((s) => {
      const next = !s.sidebarOpen
      if (typeof window !== 'undefined') {
        localStorage.setItem(SIDEBAR_KEY, String(next))
      }
      return { sidebarOpen: next }
    }),
  setSidebarOpen: (open) => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(SIDEBAR_KEY, String(open))
    }
    set({ sidebarOpen: open })
  },
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