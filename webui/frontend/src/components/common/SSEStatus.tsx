import { Wifi, WifiOff, Loader2 } from 'lucide-react'
import { useSSEStore } from '../../stores/sse'

export function SSEStatus() {
  const status = useSSEStore((s) => s.status)

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
    <div className={`flex items-center gap-1.5 text-[10px] ${config.color}`}>
      <Icon className={`h-3 w-3 ${config.pulse ? 'animate-spin' : ''}`} />
      <span>{config.label}</span>
    </div>
  )
}
