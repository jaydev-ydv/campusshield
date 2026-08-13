import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ErrorState } from '../components/ui/ErrorState'
import { LoadingState } from '../components/ui/Spinner'
import {
  RESOLUTION_REASON_LABELS,
  STATUS_LABELS,
  TERMINAL_STATUSES,
  type ReportDetail,
} from '../lib/api'

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/**
 * One of the student's own reports, in full — reached from `MyReportsPage`.
 *
 * Everything here comes from `GET /reports/<ref>`, which has existed and been
 * authorised since Phase 1; this page is the first thing in the frontend that
 * actually reads its `status_history`. The list on `MyReportsPage` stays a
 * lightweight scan (current status, submitted date); this is where "what has
 * happened since I submitted this" lives, because that answer is a timeline,
 * not a single field.
 *
 * `status_history` here is already filtered server-side to
 * `visible_to_reporter` rows — nothing this page renders was hidden by the
 * frontend, and there is no way for it to ask for more.
 */
export function ReportDetailPage() {
  const { ref } = useParams<{ ref: string }>()
  const { api } = useAuth()

  const [report, setReport] = useState<ReportDetail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!ref) return
    let cancelled = false

    // No synchronous reset here — setting state directly in an effect body
    // triggers a cascading render, and the linter is right to refuse it (the
    // same pattern `useCatalog` and `IncidentsPage`'s queue effect already
    // follow). Final state is written only once the request settles; the
    // `cancelled` guard is what keeps a superseded request from ever winning.
    void api
      .report(ref)
      .then((result) => {
        if (cancelled) return
        setReport(result)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setReport(null)
        setError(cause)
      })

    return () => {
      cancelled = true
    }
  }, [api, ref, nonce])

  return (
    <AppShell>
      <div className="mx-auto max-w-2xl space-y-6">
        <div>
          <Link to="/reports" className="text-brand-700 text-sm font-medium hover:underline">
            ← My reports
          </Link>
        </div>

        {error != null && <ErrorState error={error} onRetry={reload} />}
        {error == null && report === null && <LoadingState label="Loading your report…" />}

        {report && (
          <>
            <header>
              <p className="text-ink-500 font-mono text-sm">{report.public_ref}</p>
              <h1 className="text-ink-900 mt-1 text-2xl font-semibold tracking-tight">
                {report.category?.label ?? 'Uncategorised'}
              </h1>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="bg-brand-50 text-brand-800 ring-brand-200 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset">
                  {STATUS_LABELS[report.status]}
                </span>
                {report.is_emergency && (
                  <span className="rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-900 ring-1 ring-amber-200 ring-inset">
                    Immediate attention
                  </span>
                )}
              </div>
            </header>

            {report.submission_mode === 'anonymous' && (
              <Alert tone="info" title="Submitted anonymously">
                <p>
                  This report is not linked to your account. You are seeing it now because you
                  followed the link with your access code.
                </p>
              </Alert>
            )}

            <Card as="section" aria-labelledby="details-heading">
              <h2 id="details-heading" className="text-ink-900 text-sm font-medium">
                Details
              </h2>
              <dl className="text-ink-700 mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                <dt className="text-ink-500">Where</dt>
                <dd>{report.location.name}</dd>
                <dt className="text-ink-500">Occurred</dt>
                <dd>{formatWhen(report.occurred_at)}</dd>
                <dt className="text-ink-500">Submitted</dt>
                <dd>{formatWhen(report.submitted_at)}</dd>
                {report.evidence_count > 0 && (
                  <>
                    <dt className="text-ink-500">Evidence</dt>
                    <dd>
                      {report.evidence_count}{' '}
                      {report.evidence_count === 1 ? 'photo' : 'photos'} received
                    </dd>
                  </>
                )}
              </dl>
            </Card>

            {report.narrative_available && report.narrative && (
              <Card as="section" aria-labelledby="narrative-heading">
                <h2 id="narrative-heading" className="text-ink-900 text-sm font-medium">
                  What you told us
                </h2>
                <p className="text-ink-700 mt-2 text-sm leading-relaxed whitespace-pre-wrap">
                  {report.narrative}
                </p>
              </Card>
            )}

            <Card as="section" aria-labelledby="timeline-heading">
              <h2 id="timeline-heading" className="text-ink-900 text-sm font-medium">
                Status timeline
              </h2>
              <ol className="mt-3 space-y-3">
                {report.status_history.map((entry, index) => (
                  <li
                    key={`${entry.status}-${entry.changed_at}`}
                    className="border-ink-200 border-l-2 pl-4"
                  >
                    <p className="text-ink-900 text-sm font-medium">
                      {STATUS_LABELS[entry.status]}
                    </p>
                    <p className="text-ink-500 text-xs">{formatWhen(entry.changed_at)}</p>
                    {entry.remark && (
                      <p className="text-ink-700 mt-1 text-sm leading-relaxed">
                        {entry.remark}
                      </p>
                    )}
                    {entry.resolution_reason && (
                      <p className="text-ink-600 mt-1 text-xs">
                        {RESOLUTION_REASON_LABELS[entry.resolution_reason]}
                      </p>
                    )}
                    {index === report.status_history.length - 1 &&
                      TERMINAL_STATUSES.has(entry.status) && (
                        <p className="text-ink-500 mt-1 text-xs">This report is now closed.</p>
                      )}
                  </li>
                ))}
              </ol>
              <p className="text-ink-500 mt-3 text-xs">
                Some internal updates are not shown here. This is not every action taken on
                your report — it is what is safe to share while it is being handled.
              </p>
            </Card>

            <div>
              <Link to="/reports">
                <Button variant="secondary">Back to my reports</Button>
              </Link>
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}
