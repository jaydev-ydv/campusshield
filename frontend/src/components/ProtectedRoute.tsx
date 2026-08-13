import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { AuthLayout } from './layout/AppShell'
import { Alert } from './ui/Alert'
import { Button } from './ui/Button'
import { LoadingState } from './ui/Spinner'

/**
 * Gates a route on authentication state.
 *
 * **This is not a security boundary and must never be treated as one.** Anything
 * shipped to a browser can be bypassed by editing it. The backend re-checks
 * authentication and authorisation on every single request, and the report
 * endpoints decide access from `identity.app_user` rather than from anything the
 * client asserts. This component decides what to *render*, not what is
 * *permitted* — it exists so a signed-out user sees a sign-in page instead of an
 * empty dashboard flashing 401 errors.
 *
 * The `needs-provisioning` branch is the one worth reading. A user can be
 * genuinely signed in to Firebase and have no application account: that is the
 * state between creating a credential and provisioning a row, and it is also
 * what someone lands in if registration was interrupted. Redirecting them to
 * sign in would be a loop — they are already signed in — so the state is
 * resolved here instead.
 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status, error, provision, logout } = useAuth()
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

  return <>{children}</>
}

function ProvisioningGate({
  onProvision,
  onSignOut,
}: {
  onProvision: () => Promise<unknown>
  onSignOut: () => Promise<void>
}) {
  return (
    <AuthLayout
      title="Finish setting up your account"
      subtitle="You are signed in, but your CampusShield account has not been created yet."
    >
      <div className="space-y-4">
        <Alert tone="info" title="One more step">
          This links your sign-in to CampusShield. Accounts are created as student accounts;
          staff access is arranged by your institution.
        </Alert>
        <Button fullWidth onClick={() => void onProvision()} loadingLabel="Setting up…">
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
