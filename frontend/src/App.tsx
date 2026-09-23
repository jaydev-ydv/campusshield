import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider } from './auth/AuthContext'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ProtectedRoute, PublicOnlyRoute } from './components/ProtectedRoute'
import { AuthLayout } from './components/layout/AppShell'
import { Button } from './components/ui/Button'
import { AccountPage } from './pages/AccountPage'
import { DashboardPage } from './pages/DashboardPage'
import { EmergencyConfirmationPage } from './pages/EmergencyConfirmationPage'
import { IncidentsPage } from './pages/IncidentsPage'
import { LoginPage } from './pages/LoginPage'
import { MyReportsPage } from './pages/MyReportsPage'
import { RegisterPage } from './pages/RegisterPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { ReportConfirmationPage } from './pages/ReportConfirmationPage'
import { ReportDetailPage } from './pages/ReportDetailPage'
import { ReportPage } from './pages/ReportPage'

function NotFoundPage() {
  return (
    <AuthLayout
      title="Page not found"
      subtitle="That address does not exist in CampusShield."
      footer={
        <Button variant="ghost" onClick={() => window.history.back()}>
          Go back
        </Button>
      }
    >
      <p className="text-ink-600 text-sm">
        Check the address, or return to your overview from the link below.
      </p>
      <div className="mt-4">
        <a href="/dashboard" className="text-brand-700 text-sm font-medium hover:underline">
          Go to my overview
        </a>
      </div>
    </AuthLayout>
  )
}

/** Routes, with the auth-state gate applied per route rather than per page. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/login"
        element={
          <PublicOnlyRoute>
            <LoginPage />
          </PublicOnlyRoute>
        }
      />
      <Route
        path="/register"
        element={
          <PublicOnlyRoute>
            <RegisterPage />
          </PublicOnlyRoute>
        }
      />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <DashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/report"
        element={
          <ProtectedRoute>
            <ReportPage />
          </ProtectedRoute>
        }
      />
      {/* Responder-only in practice, but not gated here: the page explains
          itself to a student, and every endpoint behind it refuses them
          server-side. A route guard is usability, never enforcement. */}
      <Route
        path="/incidents"
        element={
          <ProtectedRoute>
            <IncidentsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/reports/:ref"
        element={
          <ProtectedRoute>
            <ReportDetailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/report/submitted"
        element={
          <ProtectedRoute>
            <ReportConfirmationPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/emergency/submitted"
        element={
          <ProtectedRoute>
            <EmergencyConfirmationPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/reports"
        element={
          <ProtectedRoute>
            <MyReportsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/notifications"
        element={
          <ProtectedRoute>
            <NotificationsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/account"
        element={
          <ProtectedRoute>
            <AccountPage />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </AuthProvider>
    </ErrorBoundary>
  )
}
