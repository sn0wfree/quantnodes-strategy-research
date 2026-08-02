import { useEffect, useState } from 'react'

// TODO(feature): no component uses this today (only its own test).
// Intended for respecting prefers-reduced-motion in scroll/streaming
// animation (StreamingText reveal, Virtuoso follow). Wire it in when
// those animations get accessibility treatment.

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  return reduced
}