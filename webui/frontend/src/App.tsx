import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/auth'
import { LoginPage } from './components/auth/LoginPage'
import { RegisterPage } from './components/auth/RegisterPage'
import { StudyDetailPage } from './components/study/StudyDetailPage'
import { RunDetailPage } from './components/run/RunDetailPage'
import { WorkflowPage } from './components/workflow/WorkflowPage'
import { AppShell } from './components/layout/AppShell'
import { CatalogPage } from './catalog/CatalogPage'
import { CatalogItem } from './catalog/CatalogItem'
import { SettingsModal } from './components/common/SettingsModal'

function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/catalog" element={<CatalogPage />} />
        <Route path="/catalog/:name" element={<CatalogItem />} />
        <Route
          path="/study/:studyId"
          element={
            <AuthGuard>
              <StudyDetailPage />
            </AuthGuard>
          }
        />
        <Route
          path="/run/:strategyName/:runName"
          element={
            <AuthGuard>
              <RunDetailPage />
            </AuthGuard>
          }
        />
        <Route
          path="/workflow"
          element={
            <AuthGuard>
              <WorkflowPage />
            </AuthGuard>
          }
        />
        <Route
          path="/*"
          element={
            <AuthGuard>
              <AppShell />
            </AuthGuard>
          }
        />
      </Routes>
      <SettingsModal />
    </>
  )
}
