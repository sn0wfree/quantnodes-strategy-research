import {
  MessageSquare,
  Workflow,
  Target,
  Bot,
  Settings,
  LogOut,
} from 'lucide-react'
import { useLayoutStore, type RightPanelTab } from '../../stores/layout'
import { useAuthStore } from '../../stores/auth'
import { useNavigate } from 'react-router-dom'

const NAV_ITEMS: { icon: typeof MessageSquare; tab?: RightPanelTab; action?: 'logout' }[] = [
  { icon: MessageSquare },
  { icon: Workflow, tab: 'dag' },
  { icon: Target, tab: 'goal' },
  { icon: Bot, tab: 'agent' },
  { icon: Settings },
  { icon: LogOut, action: 'logout' },
]

export function IconNav() {
  const setRightPanelTab = useLayoutStore((s) => s.setRightPanelTab)
  const rightPanelTab = useLayoutStore((s) => s.rightPanelTab)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const handleClick = (item: (typeof NAV_ITEMS)[0]) => {
    if (item.action === 'logout') {
      logout()
      navigate('/login')
      return
    }
    if (item.tab) {
      setRightPanelTab(item.tab)
    }
  }

  return (
    <nav className="flex h-screen w-16 flex-col items-center gap-1 border-r border-slate-800 bg-slate-900/50 py-3">
      {NAV_ITEMS.map((item, i) => {
        const Icon = item.icon
        const isActive = item.tab && rightPanelVisible && rightPanelTab === item.tab
        return (
          <button
            key={i}
            onClick={() => handleClick(item)}
            className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors
              ${isActive
                ? 'bg-primary-600/20 text-primary-400'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }
            `}
            title={item.tab || item.action}
          >
            <Icon className="h-5 w-5" />
          </button>
        )
      })}
    </nav>
  )
}
