import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/auth'

/**
 * Restricts a route to authenticated superusers (role === 'admin').
 * Non-admins are redirected home; unauthenticated users to /login.
 */
export function AdminGuard({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)

  if (!token) return <Navigate to="/login" replace />
  if (user?.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}