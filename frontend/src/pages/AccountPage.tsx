import { useState, type FormEvent } from 'react'

import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ErrorState } from '../components/ui/ErrorState'
import { Input } from '../components/ui/Input'
import { ROLE_LABELS } from '../lib/api'
import { ApiError } from '../lib/apiClient'

/**
 * Account identity and the one editable field this phase supports.
 *
 * Deliberately not a general profile editor. `display_name` exists on
 * `identity.app_user` for authority roles only — a CHECK constraint
 * (`ck_app_user_student_has_no_name`) forbids a student row from carrying one
 * at all, so a student sees why rather than a form field that would only
 * ever fail. There is no avatar or profile photo here: nothing in the
 * project stores one yet, and a student-facing photo in particular would cut
 * against the anonymous-reporting model this app exists to protect.
 */
export function AccountPage() {
  const { account, refreshAccount } = useAuth()

  return (
    <AppShell>
      <div className="mx-auto max-w-xl space-y-6">
        <div>
          <h1 className="text-ink-900 text-2xl font-semibold tracking-tight sm:text-3xl">
            Account
          </h1>
          <p className="text-ink-600 mt-1.5 text-sm">
            Your identity in CampusShield, as the server sees it.
          </p>
        </div>

        <Card as="section">
          <h2 className="text-ink-900 text-base font-semibold">Identity</h2>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">
                Signed in as
              </dt>
              <dd className="text-ink-900 mt-1 text-sm break-all">{account?.email ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">
                Role
              </dt>
              <dd className="mt-1">
                <span className="bg-brand-50 text-brand-800 ring-brand-200 inline-block rounded-full px-2.5 py-0.5 text-sm font-medium ring-1 ring-inset">
                  {account ? ROLE_LABELS[account.role] : '—'}
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-ink-500 text-xs font-medium tracking-wide uppercase">
                Status
              </dt>
              <dd className="mt-1 flex items-center gap-2 text-sm">
                <span
                  aria-hidden="true"
                  className={`h-2 w-2 rounded-full ${
                    account?.is_active ? 'bg-emerald-500' : 'bg-ink-300'
                  }`}
                />
                <span className="text-ink-900">
                  {account?.is_active ? 'Verified and active' : 'Inactive'}
                </span>
              </dd>
            </div>
          </dl>
          <p className="text-ink-500 mt-4 text-xs leading-relaxed">
            Your role is set by your institution and read from the CampusShield database on
            every request. It is not something this browser can change.
          </p>
        </Card>

        {account?.role === 'student' ? (
          <Alert tone="info" title="Student accounts do not store a display name">
            Nothing in CampusShield needs one, and not collecting it is what keeps a report you
            file with your account from carrying your name any further than it already does.
          </Alert>
        ) : (
          account && (
            <DisplayNameForm currentName={account.display_name} onSaved={refreshAccount} />
          )
        )}
      </div>
    </AppShell>
  )
}

function DisplayNameForm({
  currentName,
  onSaved,
}: {
  currentName: string | null
  onSaved: () => Promise<void>
}) {
  const { api } = useAuth()
  const [name, setName] = useState(currentName ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await api.updateDisplayName(name)
      await onSaved()
      setSaved(true)
    } catch (err) {
      setError(err)
    } finally {
      setSaving(false)
    }
  }

  const fieldError =
    error instanceof ApiError ? error.fieldErrors.display_name?.[0] : undefined

  return (
    <Card as="section">
      <h2 className="text-ink-900 text-base font-semibold">Display name</h2>
      <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
        Shown to students and colleagues wherever a case you handle displays who is working on
        it — case assignments, for instance.
      </p>

      <form onSubmit={(e) => void handleSubmit(e)} className="mt-4 space-y-4" noValidate>
        <Input
          label="Display name"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setSaved(false)
          }}
          maxLength={120}
          required
          error={fieldError}
        />

        {error != null && !fieldError && <ErrorState error={error} />}
        {saved && (
          <p role="status" className="text-sm font-medium text-emerald-700">
            Saved.
          </p>
        )}

        <Button type="submit" loading={saving} disabled={name.trim().length === 0}>
          Save name
        </Button>
      </form>
    </Card>
  )
}
