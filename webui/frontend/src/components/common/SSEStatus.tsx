import { useEffect } from 'react'
import { Wifi, WifiOff, Loader2, Cpu } from 'lucide-react'
import { useSSEStore } from '../../stores/sse'
import { useSystemStore } from '../../stores/system'

export function SSEStatus() {
  const status = useSSEStore((s) => s.status)
  const llm = useSystemStore((s) => s.llm)
  const fetchSystemInfo = useSystemStore((s) => s.fetchSystemInfo)

  useEffect(() => {
    fetchSystemInfo()
  }, [fetchSystemInfo])

  const config = {
    connected: {
      icon: Wifi,
      color: 'text-emerald-400',
      label: '已连接',
      pulse: false,
    },
    connecting: {
      icon: Loader2,
      color: 'text-amber-400',
      label: '连接中',
      pulse: true,
    },
    disconnected: {
      icon: WifiOff,
      color: 'text-red-400',
      label: '已断开',
      pulse: false,
    },
  }[status]

  const Icon = config.icon

  return (
    <div className="flex items-center gap-3 text-[10px]">
      <div className={`flex items-center gap-1.5 ${config.color}`}>
        <Icon className={`h-3 w-3 ${config.pulse ? 'animate-spin' : ''}`} />
        <span>{config.label}</span>
      </div>
      {llm.configured && llm.provider && (
        <div className="flex items-center gap-1 text-slate-400 border-l border-slate-700 pl-2">
          <Cpu className="h-3 w-3" />
          <span className="font-mono">
            {llm.provider}/{llm.model || 'default'}
          </span>
        </div>
      )}
    </div>
  )
}