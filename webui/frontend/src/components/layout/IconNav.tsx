import { MessageSquare, Settings, LogOut } from 'lucide-react'
import { useLayoutStore } from '../../stores/layout'
import { useAuthStore } from '../../stores/auth'
import { useNavigate } from 'react-router-dom'

const NAV_ITEMS: {
  icon: typeof MessageSquare
  action: 'chat' | 'settings' | 'logout'
}[] = [
  { icon: MessageSquare, action: 'chat' },
  { icon: Settings, action: 'settings' },
  { icon: LogOut, action: 'logout' },
]

export function IconNav() {
  const setSettingsOpen = useLayoutStore((s) => s.setSettingsOpen)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const handleClick = (action: (typeof NAV_ITEMS)[number]['action']) => {
    if (action === 'logout') {
      logout()
      navigate('/login')
      return
    }
    if (action === 'settings') {
      setSettingsOpen(true)
      return
    }
    // 'chat' — focus composer & scroll to bottom
    const textarea = document.querySelector<HTMLTextAreaElement>('textarea')
    textarea?.focus()
    window.dispatchEvent(new CustomEvent('sr:focus-chat'))
  }

  return (
    <nav className="flex h-screen w-16 flex-col items-center gap-1 border-r border-slate-800 bg-slate-900/50 py-3">
      {NAV_ITEMS.map((item, i) => {
        const Icon = item.icon
        const isChat = item.action === 'chat'
        return (
          <button
            key={i}
            onClick={() => handleClick(item.action)}
            className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors
              ${isChat
                ? 'bg-primary-600/20 text-primary-400'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }
            `}
            title={item.action === 'chat' ? '聊天' : item.action === 'settings' ? '设置' : '登出'}
          >
            <Icon className="h-5 w-5" />
          </button>
        )
      })}
    </nav>
  )
}