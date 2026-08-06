import { create } from 'zustand'

export type Theme = 'dark' | 'light'

const THEME_KEY = 'sr-theme'

function loadTheme(): Theme {
  if (typeof window === 'undefined') return 'dark'
  return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'
}

export function applyTheme(theme: Theme) {
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = theme
  }
}

interface ThemeState {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

const initial = loadTheme()
applyTheme(initial)

export const useThemeStore = create<ThemeState>()((set, get) => ({
  theme: initial,
  setTheme: (theme) => {
    applyTheme(theme)
    if (typeof window !== 'undefined') {
      localStorage.setItem(THEME_KEY, theme)
    }
    set({ theme })
  },
  toggleTheme: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark'),
}))
