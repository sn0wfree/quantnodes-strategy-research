import { create } from 'zustand'

export type RightPanelTab = 'goal' | 'study'
export type WorkMode = 'chat' | 'monitor' | 'focus'
export type ChatLayout = 'bubble' | 'flat'
export type Density = 'compact' | 'comfortable' | 'spacious'

/**
 * Per-density presets for the main split layout. Each preset defines
 * the default widths of the left chat area, the middle context panel,
 * and the right Goal/Study panel — all as ratios of the parent flex
 * container. Together they sum to ≤ 1; the remainder is the auto-filled
 * gap between panels, which adapts to the screen width.
 */
const DENSITY_PRESETS: Record<Density, {
  leftRatio: number
  contextRatio: number
  rightRatio: number
}> = {
  compact:    { leftRatio: 0.50, contextRatio: 0.18, rightRatio: 0.20 },
  comfortable: { leftRatio: 0.45, contextRatio: 0.22, rightRatio: 0.25 },
  spacious:   { leftRatio: 0.40, contextRatio: 0.25, rightRatio: 0.30 },
}

const CHAT_LAYOUT_KEY = 'sr-chat-layout'
const SIDEBAR_KEY = 'sr-sidebar-open'
const LEFT_RATIO_KEY = 'sr-left-ratio'
const CONTEXT_RATIO_KEY = 'sr-context-ratio'
const RIGHT_RATIO_KEY = 'sr-right-ratio'
const DENSITY_KEY = 'sr-density'

const RATIO_MIN = 0.2
const RATIO_MAX = 0.85
const CONTEXT_RATIO_MIN = 0.15
const CONTEXT_RATIO_MAX = 0.35
const RIGHT_RATIO_MIN = 0.25
const RIGHT_RATIO_MAX = 0.55

function loadInitialLayout(): ChatLayout {
  if (typeof window === 'undefined') return 'bubble'
  return localStorage.getItem(CHAT_LAYOUT_KEY) === 'flat' ? 'flat' : 'bubble'
}

function loadInitialSidebar(): boolean {
  if (typeof window === 'undefined') return true
  return localStorage.getItem(SIDEBAR_KEY) !== 'false'
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function loadInitialDensity(): Density {
  if (typeof window === 'undefined') return 'comfortable'
  const raw = localStorage.getItem(DENSITY_KEY)
  if (raw === 'compact' || raw === 'comfortable' || raw === 'spacious') return raw
  return 'comfortable'
}

function loadInitialLeftRatio(density: Density): number {
  if (typeof window === 'undefined') return DENSITY_PRESETS[density].leftRatio
  // getItem returns null when absent; Number(null) === 0 which would be
  // clamped up — treat missing as the preset default.
  const raw = localStorage.getItem(LEFT_RATIO_KEY)
  if (raw === null) return DENSITY_PRESETS[density].leftRatio
  const n = Number(raw)
  if (!Number.isFinite(n)) return DENSITY_PRESETS[density].leftRatio
  return clamp(n, RATIO_MIN, RATIO_MAX)
}

function loadInitialContextRatio(density: Density): number {
  if (typeof window === 'undefined') return DENSITY_PRESETS[density].contextRatio
  const raw = localStorage.getItem(CONTEXT_RATIO_KEY)
  if (raw === null) return DENSITY_PRESETS[density].contextRatio
  const n = Number(raw)
  if (!Number.isFinite(n)) return DENSITY_PRESETS[density].contextRatio
  return clamp(n, CONTEXT_RATIO_MIN, CONTEXT_RATIO_MAX)
}

function loadInitialRightRatio(density: Density): number {
  if (typeof window === 'undefined') return DENSITY_PRESETS[density].rightRatio
  const raw = localStorage.getItem(RIGHT_RATIO_KEY)
  if (raw === null) return DENSITY_PRESETS[density].rightRatio
  const n = Number(raw)
  if (!Number.isFinite(n)) return DENSITY_PRESETS[density].rightRatio
  return clamp(n, RIGHT_RATIO_MIN, RIGHT_RATIO_MAX)
}

interface LayoutState {
  navWidth: number
  sidebarOpen: boolean
  rightPanelVisible: boolean
  rightPanelTab: RightPanelTab
  workMode: WorkMode
  leftRatio: number
  contextRatio: number
  rightRatio: number
  density: Density
  settingsOpen: boolean
  chatLayout: ChatLayout
  setNavWidth: (w: number) => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
  toggleRightPanel: () => void
  setRightPanelTab: (tab: RightPanelTab) => void
  setWorkMode: (mode: WorkMode) => void
  setLeftRatio: (r: number) => void
  setContextRatio: (r: number) => void
  setRightRatio: (r: number) => void
  setDensity: (d: Density) => void
  setSettingsOpen: (open: boolean) => void
  setChatLayout: (layout: ChatLayout) => void
}

export const useLayoutStore = create<LayoutState>()((set) => {
  const density = loadInitialDensity()
  return {
    navWidth: 64,
    sidebarOpen: loadInitialSidebar(),
    rightPanelVisible: true,
    rightPanelTab: 'goal',
    workMode: 'monitor',
    leftRatio: loadInitialLeftRatio(density),
    contextRatio: loadInitialContextRatio(density),
    rightRatio: loadInitialRightRatio(density),
    density,
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
    setLeftRatio: (r) => {
      const clamped = clamp(r, RATIO_MIN, RATIO_MAX)
      if (typeof window !== 'undefined') {
        localStorage.setItem(LEFT_RATIO_KEY, String(clamped))
      }
      set({ leftRatio: clamped })
    },
    setContextRatio: (r) => {
      const clamped = clamp(r, CONTEXT_RATIO_MIN, CONTEXT_RATIO_MAX)
      if (typeof window !== 'undefined') {
        localStorage.setItem(CONTEXT_RATIO_KEY, String(clamped))
      }
      set({ contextRatio: clamped })
    },
    setRightRatio: (r) => {
      const clamped = clamp(r, RIGHT_RATIO_MIN, RIGHT_RATIO_MAX)
      if (typeof window !== 'undefined') {
        localStorage.setItem(RIGHT_RATIO_KEY, String(clamped))
      }
      set({ rightRatio: clamped })
    },
    setDensity: (d) => {
      const preset = DENSITY_PRESETS[d]
      if (typeof window !== 'undefined') {
        localStorage.setItem(DENSITY_KEY, d)
        // Switching density restores its preset widths (user's manual
        // overrides are stored separately under their own keys).
        localStorage.removeItem(LEFT_RATIO_KEY)
        localStorage.removeItem(CONTEXT_RATIO_KEY)
        localStorage.removeItem(RIGHT_RATIO_KEY)
      }
      set({
        density: d,
        leftRatio: preset.leftRatio,
        contextRatio: preset.contextRatio,
        rightRatio: preset.rightRatio,
      })
    },
    setSettingsOpen: (open) => set({ settingsOpen: open }),
    setChatLayout: (layout) => {
      if (typeof window !== 'undefined') {
        localStorage.setItem(CHAT_LAYOUT_KEY, layout)
      }
      set({ chatLayout: layout })
    },
  }
})