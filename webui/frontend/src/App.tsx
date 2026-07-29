import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/auth'
import { LoginPage } from './components/auth/LoginPage'
import { AppShell } from './components/layout/AppShell'
import { CatalogPage } from './catalog/CatalogPage'
import { CatalogItem } from './catalog/CatalogItem'

function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* Catalog — Storybook-style component index (no auth required). */}
      <Route path="/catalog" element={<CatalogPage />} />
      <Route path="/catalog/:name" element={<CatalogItem />} />
      <Route
        path="/*"
        element={
          <AuthGuard>
            <AppShell />
          </AuthGuard>
        }
      />
    </Routes>
  )
}
