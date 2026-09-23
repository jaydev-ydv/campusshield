import { useState, type FormEvent } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { AuthLayout } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Input } from '../components/ui/Input'
import { LoadingState } from '../components/ui/Spinner'
import { STATUS_LABELS, type ReportDetail } from '../lib/api'
import { ApiError } from '../lib/apiClient'

export function AnonymousReportPage() {
  const { api } = useAuth()
  const location = useLocation()
  const savedState = location.state as { reference?: string; accessToken?: string } | null
  const [reference, setReference] = useState(savedState?.reference ?? '')
  const [accessToken, setAccessToken] = useState(savedState?.accessToken ?? '')
  const [report, setReport] = useState<ReportDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setReport(null)
    setLoading(true)

    try {
      const result = await api.anonymousReport(reference.trim(), accessToken.trim())
      setReport(result)
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.status === 404
          ? 'We could not find that report. Check the reference and code and try again.'
          : cause instanceof Error
            ? cause.message
            : 'The report status could not be loaded. Try again.',
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthLayout
      title="Check an anonymous report"
      subtitle="Use the reference and private access code you received after submitting."
      footer={
        <Link to="/login" className="text-brand-700 font-medium hover:underline">
          Sign in to CampusShield
        </Link>
      }
    >
      <form onSubmit={handleSubmit} noValidate className="space-y-5">
        {error && (
          <Alert tone="error" title="Could not load report">
            {error}
          </Alert>
        )}

        <Input
          label="Report reference"
          name="reference"
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          placeholder="CS-2026-XXXXXX"
          required
          disabled={loading}
        />
        <Input
          label="Private access code"
          name="access-token"
          value={accessToken}
          onChange={(event) => setAccessToken(event.target.value)}
          autoComplete="off"
          required
          disabled={loading}
        />
        <Button type="submit" fullWidth size="lg" loading={loading} loadingLabel="Checking…">
          Check status
        </Button>
      </form>

      {loading && <LoadingState label="Loading report status…" />}

      {report && (
        <Card as="section" className="mt-6">
          <p className="text-ink-500 font-mono text-sm">{report.public_ref}</p>
          <h2 className="text-ink-900 mt-2 text-xl font-semibold">
            {report.category?.label ?? 'Campus report'}
          </h2>
          <p className="text-ink-600 mt-3 text-sm">Current status</p>
          <p className="text-brand-800 mt-1 text-lg font-semibold">
            {STATUS_LABELS[report.status]}
          </p>
          <p className="text-ink-500 mt-4 text-xs leading-relaxed">
            Status updates are shown here when the response team records them. Anonymous
            reports do not send notifications.
          </p>
        </Card>
      )}
    </AuthLayout>
  )
}
