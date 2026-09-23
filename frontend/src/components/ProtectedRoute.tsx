import { useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { AuthLayout } from './layout/AppShell'
import { Alert } from './ui/Alert'
import { Button } from './ui/Button'
import { LoadingState } from './ui/Spinner'

import type { UserRole } from '../lib/api'

/**
 * Gates a route on authentication state and optional role permissions.
 *
 * **This is not a security boundary and must never be treated as one.** Anything
 * shipped to a browser can be bypassed by editing it. The backend re-checks
 * authentication and authorisation on every single request, and the report
 * endpoints decide access from `identity.app_user` rather than from anything the
 * client asserts. This component decides what to *render*, not what is
 * *permitted* — it exists so a signed-out user sees a sign-in page instead of an
 * empty dashboard flashing 401 errors.
 *
 * When `allowedRoles` is passed, users with other roles are smoothly redirected
 * to their primary operational portal rather than hitting a dead end:
 * - Staff members navigating to student reporting routes are redirected to `/incidents`.
 * - Students navigating to responder queues are redirected to `/dashboard`.
 *
 * The `needs-provisioning` branch is the one worth reading. A user can be
 * genuinely signed in to Firebase and have no application account: that is the
 * state between creating a credential and provisioning a row, and it is also
 * what someone lands in if registration was interrupted. Redirecting them to
 * sign in would be a loop — they are already signed in — so the state is
 * resolved here instead.
 */
export function ProtectedRoute({
  children,
  allowedRoles,
}: {
  children: ReactNode
  allowedRoles?: UserRole[]
}) {
  const { status, error, provision, logout, account } = useAuth()
  const location = useLocation()

  if (status === 'initialising') {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <LoadingState label="Checking your session…" />
      </div>
    )
  }

  if (status === 'unavailable') {
    return (
      <AuthLayout title="Sign-in is unavailable">
        <Alert tone="error" title="CampusShield is not configured">
          {error ?? 'The sign-in service could not be reached.'}
        </Alert>
      </AuthLayout>
    )
  }

  if (status === 'signed-out') {
    // `state.from` lets the login page return the user where they were headed,
    // rather than dumping everyone on the dashboard after signing in.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  if (status === 'needs-provisioning') {
    return <ProvisioningGate onProvision={provision} onSignOut={logout} />
  }

  if (allowedRoles && account && !allowedRoles.includes(account.role)) {
    const destination = account.role === 'student' ? '/dashboard' : '/incidents'
    return <Navigate to={destination} replace />
  }

  return <>{children}</>
}

function ProvisioningGate({
  onProvision,
  onSignOut,
}: {
  onProvision: () => Promise<unknown>
  onSignOut: () => Promise<void>
}) {
  const [provisioningError, setProvisioningError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleProvision() {
    setProvisioningError(null)
    setSubmitting(true)
    try {
      await onProvision()
    } catch (error) {
      setProvisioningError(
        error instanceof Error
          ? error.message
          : 'We could not finish creating your CampusShield account. Please try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Finish setting up your account"
      subtitle="You are signed in, but your CampusShield account has not been created yet."
    >
      <div className="space-y-4">
        {provisioningError && (
          <Alert tone="error" title="Account setup could not be completed">
            {provisioningError} Check that the API is running, then try again.
          </Alert>
        )}
        <Alert tone="info" title="One more step">
          Click the button below once to create your CampusShield account. Your Firebase
          sign-in is already complete. New accounts are created as student accounts; staff
          access is arranged by your institution.
        </Alert>
        <Button
          fullWidth
          onClick={() => void handleProvision()}
          loading={submitting}
          loadingLabel="Setting up…"
        >
          Create my CampusShield account
        </Button>
        <Button variant="ghost" fullWidth onClick={() => void onSignOut()}>
          Sign out instead
        </Button>
      </div>
    </AuthLayout>
  )
}

/** Keeps a signed-in user away from the sign-in and registration pages. */
export function PublicOnlyRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth()

  if (status === 'initialising') {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <LoadingState label="Checking your session…" />
      </div>
    )
  }
  if (status === 'authenticated') {
    return <Navigate to="/dashboard" replace />
  }
  return <>{children}</>
}
