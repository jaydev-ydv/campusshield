import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { firebaseErrorMessage } from '../auth/firebaseErrors'
import { AuthLayout } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Input } from '../components/ui/Input'
import { validateLogin, type LoginErrors } from '../lib/validation'

export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fieldErrors, setFieldErrors] = useState<LoginErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const returnTo = (location.state as { from?: string } | null)?.from ?? '/dashboard'

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)

    const errors = validateLogin(email, password)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) return

    setSubmitting(true)
    try {
      await signIn(email, password)
      navigate(returnTo, { replace: true })
    } catch (error) {
      setFormError(firebaseErrorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Sign in"
      subtitle="Report safely. Help improve campus safety."
      footer={
        <>
          New to CampusShield?{' '}
          <Link to="/register" className="text-brand-700 font-medium hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      {/* noValidate so our own messages are used rather than the browser's,
          which vary by vendor and are not announced consistently. */}
      <form onSubmit={handleSubmit} noValidate className="space-y-5">
        {formError && (
          <Alert tone="error" title="Could not sign in">
            {formError}
          </Alert>
        )}

        <Input
          label="Email address"
          type="email"
          name="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          autoCapitalize="none"
          spellCheck={false}
          inputMode="email"
          required
          error={fieldErrors.email}
          disabled={submitting}
        />

        <Input
          label="Password"
          type="password"
          name="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          // Lets a password manager fill and save. Discouraging managers pushes
          // people toward passwords they can remember, which are worse.
          autoComplete="current-password"
          required
          error={fieldErrors.password}
          disabled={submitting}
        />

        <Button
          type="submit"
          fullWidth
          size="lg"
          loading={submitting}
          loadingLabel="Signing in…"
        >
          Sign in
        </Button>
      </form>

      <p className="text-ink-500 mt-5 text-xs leading-relaxed">
        Your password is handled by Firebase Authentication and is never seen or stored by
        CampusShield.
      </p>
    </AuthLayout>
  )
}
