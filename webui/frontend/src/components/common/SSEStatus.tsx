import { useEffect, useState } from 'react'
import { Wifi, WifiOff, Loader2 } from 'lucide-react'
import { useSessionStore } from '../../stores/session'

export function SSEStatus() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const [status, setStatus] = useState<'connected' | 'connecting' | 'disconnected'>('connecting')

  useEffect(() => {
    if (!currentSessionId) {
      setStatus('disconnected')
      return
    }

    setStatus('connecting')

    let retryCount = 0
    let es: EventSource | null = null
    let timeout: ReturnType<typeof setTimeout> | null = null

    const connect = () => {
      const token = localStorage.getItem('sr-auth')
      let parsedToken = ''
      try {
        parsedToken = token ? JSON.parse(token).state.token : ''
      } catch {}

      const params = new URLSearchParams({ session_id: currentSessionId })
      if (parsedToken) params.set('token', parsedToken)
      es = new EventSource(`/api/chat/events?${params}`)

      es.onopen = () => {
        setStatus('connected')
        retryCount = 0
      }

      es.onerror = () => {
        setStatus('disconnected')
        es?.close()
        retryCount++
        const delay = Math.min(1000 * Math.pow(2, retryCount - 1), 30000)
        timeout = setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      es?.close()
      if (timeout) clearTimeout(timeout)
    }
  }, [currentSessionId])

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