import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { firebaseErrorMessage } from '../auth/firebaseErrors'
import { AuthLayout } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Input } from '../components/ui/Input'
import {
  MIN_PASSWORD_LENGTH,
  validateRegistration,
  type RegistrationErrors,
} from '../lib/validation'

export function RegisterPage() {
  const { signUp, provision } = useAuth()
  const navigate = useNavigate()

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [fieldErrors, setFieldErrors] = useState<RegistrationErrors>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)

    const errors = validateRegistration(email, password, confirmPassword)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) return

    setSubmitting(true)
    try {
      // Two steps, and the order matters. Firebase creates the credential; the
      // API then creates the application account behind the resulting verified
      // token. This app never handles the password, and it never gets to say
      // what role the new account has — the server assigns `student`.
      await signUp(email, password, name)
      await provision()
      navigate('/dashboard', { replace: true })
    } catch (error) {
      // If Firebase succeeded and provisioning failed, the user is signed in
      // without an account. ProtectedRoute recognises that state and offers to
      // finish, so the half-completed case resolves itself rather than stranding
      // anyone.
      setFormError(firebaseErrorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Report safely. Help improve campus safety."
      footer={
        <>
          Already have an account?{' '}
          <Link to="/login" className="text-brand-700 font-medium hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} noValidate className="space-y-5">
        {formError && (
          <Alert tone="error" title="Could not create your account">
            {formError}
          </Alert>
        )}

        <Input
          label="Full name"
          type="text"
          name="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoComplete="name"
          disabled={submitting}
          hint="Optional. Configures your Firebase profile identity."
        />

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
          hint="Use your university email address."
        />

        <Input
          label="Password"
          type="password"
          name="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          required
          error={fieldErrors.password}
          disabled={submitting}
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
        />

        <Input
          label="Confirm password"
          type="password"
          name="confirm-password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          autoComplete="new-password"
          required
          error={fieldErrors.confirmPassword}
          disabled={submitting}
        />

        <Button
          type="submit"
          fullWidth
          size="lg"
          loading={submitting}
          loadingLabel="Creating your account…"
        >
          Create account
        </Button>
      </form>

      <div className="border-ink-200 mt-6 border-t pt-5">
        <h2 className="text-ink-800 text-sm font-medium">What CampusShield stores</h2>
        <ul className="text-ink-600 mt-2 space-y-1.5 text-xs leading-relaxed">
          <li>
            Your email address and role. <strong>Not your name</strong> — student names are not
            stored at all.
          </li>
          <li>Your password is held by Firebase Authentication, never by CampusShield.</li>
          <li>
            When you report, you may choose to do so anonymously. An anonymous report is not
            linked to your account in any way that can be reversed.
          </li>
        </ul>
      </div>
    </AuthLayout>
  )
}
