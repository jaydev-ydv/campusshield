import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ErrorState } from '../components/ui/ErrorState'
import { LoadingState } from '../components/ui/Spinner'
import { STATUS_LABELS, type Paginated, type ReportSummary } from '../lib/api'
import { loadSavedAnonymousReports, type SavedAnonymousReport } from '../lib/anonymousReports'

/**
 * The caller's own identified reports.
 *
 * Backed by `GET /api/v1/reports/mine`, which joins through
 * `identity.report_attribution` — a table that by construction contains no
 * anonymous report. Anonymous submissions are therefore absent, and that is the
 * guarantee working rather than a gap: they are linked to nobody, so no query
 * starting from a user id can reach them, including this one.
 */
export function MyReportsPage() {
  const { api, status } = useAuth()
  const [data, setData] = useState<Paginated<ReportSummary> | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [nonce, setNonce] = useState(0)
  const [anonymousReports] = useState<SavedAnonymousReport[]>(loadSavedAnonymousReports)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (status !== 'authenticated') return
    let cancelled = false

    void api
      .myReports()
      .then((result) => {
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err)
      })

    return () => {
      cancelled = true
    }
  }, [api, status, nonce])

  const loading = data === null && error == null

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-ink-900 text-2xl font-semibold tracking-tight sm:text-3xl">
              My reports
            </h1>
            <p className="text-ink-600 mt-1.5 text-sm">
              Reports you submitted with your account.
            </p>
          </div>
          <Link to="/report">
            <Button size="sm">New report</Button>
          </Link>
        </div>

        {error != null && <ErrorState error={error} onRetry={reload} />}

        {loading && <LoadingState label="Loading your reports…" />}

        {data && data.items.length === 0 && (
          <Card as="section" className="p-8 text-center">
            <p className="text-ink-800 text-sm font-medium">
              You have not submitted any reports
            </p>
            <p className="text-ink-600 mx-auto mt-2 max-w-md text-sm leading-relaxed">
              When you submit a report with your account, it appears here so you can follow
              what happens to it.
            </p>
            <div className="mt-5">
              <Link to="/report">
                <Button variant="secondary">Report a safety concern</Button>
              </Link>
            </div>
          </Card>
        )}

        {data && data.items.length > 0 && (
          <>
            <ul className="space-y-3">
              {data.items.map((report) => (
                <li key={report.public_ref}>
                  <ReportRow report={report} />
                </li>
              ))}
            </ul>
            {data.pagination.total > data.items.length && (
              <p className="text-ink-500 text-center text-xs">
                Showing {data.items.length} of {data.pagination.total}.
              </p>
            )}
          </>
        )}

        {data && (
          <Alert tone="info" title="Anonymous reports are not listed here">
            Anonymous reports remain unlinked to your account on the server. Saved entries below
            are stored only in this browser so you can reopen the status check.
          </Alert>
        )}

        {anonymousReports.length > 0 && (
          <section aria-labelledby="anonymous-reports-heading">
            <h2
              id="anonymous-reports-heading"
              className="text-ink-900 mb-3 text-lg font-semibold"
            >
              Saved anonymous reports
            </h2>
            <ul className="space-y-3">
              {anonymousReports.map((report) => (
                <li key={report.public_ref}>
                  <Link
                    to="/check-report"
                    state={{ reference: report.public_ref, accessToken: report.access_token }}
                    className="block"
                  >
                    <Card className="hover:border-brand-300 p-4 transition-colors sm:p-5">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <p className="text-ink-900 font-mono text-sm font-semibold">
                            {report.public_ref}
                          </p>
                          <p className="text-ink-600 mt-1 text-sm">Anonymous report</p>
                        </div>
                        <span className="text-brand-700 text-sm font-medium">Check status</span>
                      </div>
                    </Card>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </AppShell>
  )
}

function ReportRow({ report }: { report: ReportSummary }) {
  const submitted = new Date(report.submitted_at)
  const occurred = new Date(report.occurred_at)

  return (
    <Link to={`/reports/${report.public_ref}`} className="block">
      <Card className="hover:border-brand-300 p-4 transition-colors sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-ink-900 font-mono text-sm font-semibold">{report.public_ref}</p>
            <p className="text-ink-800 mt-1 text-sm">
              {report.category?.label ?? 'Uncategorised'}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {report.is_emergency && (
              // Amber, not red. This marks how the report was routed, not that
              // something is currently wrong on the student's screen.
              <span className="rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-900 ring-1 ring-amber-200 ring-inset">
                Immediate attention
              </span>
            )}
            <span className="bg-brand-50 text-brand-800 ring-brand-200 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset">
              {STATUS_LABELS[report.status] ?? report.status}
            </span>
          </div>
        </div>

        <dl className="text-ink-600 mt-3 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
          <div>
            <dt className="inline text-ink-500">Where: </dt>
            <dd className="inline">{report.location.name}</dd>
          </div>
          <div>
            <dt className="inline text-ink-500">Happened: </dt>
            <dd className="inline">
              {occurred.toLocaleDateString(undefined, { dateStyle: 'medium' })}
            </dd>
          </div>
          <div>
            <dt className="inline text-ink-500">Submitted: </dt>
            <dd className="inline">
              {submitted.toLocaleDateString(undefined, { dateStyle: 'medium' })}
            </dd>
          </div>
        </dl>
      </Card>
    </Link>
  )
}
