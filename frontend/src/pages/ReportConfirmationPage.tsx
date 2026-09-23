import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { AppShell } from '../components/layout/AppShell'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import type { ReportSubmissionResult } from '../lib/api'
import { saveAnonymousReport } from '../lib/anonymousReports'

/**
 * Confirmation, shown once.
 *
 * The submission result arrives through router state. Anonymous report access
 * remains unlinked on the server; the reference and token are optionally saved
 * in this browser only so the student can reopen the status checker later.
 */
export function ReportConfirmationPage() {
  const location = useLocation()
  const result = (location.state as { result?: ReportSubmissionResult } | null)?.result ?? null

  const anonymous = result?.submission_mode === 'anonymous'

  useEffect(() => {
    if (anonymous && result.access_token) {
      saveAnonymousReport({
        public_ref: result.public_ref,
        access_token: result.access_token,
        submitted_at: result.submitted_at,
      })
    }
  }, [anonymous, result])

  if (!result) {
    return (
      <AppShell>
        <div className="mx-auto max-w-xl">
          <Card as="section">
            <h1 className="text-ink-900 text-xl font-semibold">
              This page is no longer available
            </h1>
            <p className="text-ink-600 mt-2 text-sm leading-relaxed">
              Confirmation details are shown once and are not kept. If you submitted with your
              account, your report is in your reports list.
            </p>
            <div className="mt-5 flex flex-wrap gap-3">
              <Link to="/reports">
                <Button variant="secondary">View my reports</Button>
              </Link>
              <Link to="/dashboard">
                <Button variant="ghost">Back to overview</Button>
              </Link>
            </div>
          </Card>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-xl space-y-5">
        <div className="text-center">
          <span
            aria-hidden="true"
            className="bg-brand-50 text-brand-700 ring-brand-200 inline-flex h-12 w-12 items-center justify-center rounded-full text-xl ring-1"
          >
            ✓
          </span>
          <h1 className="text-ink-900 mt-4 text-2xl font-semibold tracking-tight">
            Your report has been received
          </h1>
          <p className="text-ink-600 mt-2 text-sm leading-relaxed">
            Thank you for taking the time. What you shared helps the university understand what
            is happening on campus.
          </p>
        </div>

        <Card as="section">
          <h2 className="text-ink-500 text-xs font-medium tracking-wide uppercase">
            Your reference
          </h2>
          <p className="text-ink-900 mt-2 font-mono text-2xl font-semibold tracking-tight">
            {result.public_ref}
          </p>
          <p className="text-ink-600 mt-2 text-sm">
            Quote this if you need to talk to anyone about the report.
          </p>
        </Card>

        {anonymous && result.access_token && <AccessTokenPanel token={result.access_token} />}

        <Card as="section">
          <h2 className="text-ink-900 text-base font-semibold">What happens next</h2>
          <ol className="text-ink-700 mt-3 space-y-3 text-sm leading-relaxed">
            <li className="flex gap-3">
              <span className="text-ink-400 shrink-0 font-mono text-xs">01</span>
              <span>
                Your report goes to the team responsible for this type of concern
                {result.is_emergency &&
                  ', and campus security has been alerted to the location'}
                .
              </span>
            </li>
            <li className="flex gap-3">
              <span className="text-ink-400 shrink-0 font-mono text-xs">02</span>
              <span>
                They review what you wrote and decide what action is appropriate. CampusShield
                does not decide whether a report is true — people do.
              </span>
            </li>
            <li className="flex gap-3">
              <span className="text-ink-400 shrink-0 font-mono text-xs">03</span>
              <span>
                {anonymous
                  ? 'The status is updated as things progress. Anonymous reports cannot receive notifications — there is no account to send one to — so check back with the code above.'
                  : result.reporter_contactable
                    ? "You'll see status updates in your CampusShield notifications, and the team may also contact you directly."
                    : "You'll see status updates in your CampusShield notifications. You asked not to be contacted directly, so nobody will get in touch outside the app."}
              </span>
            </li>
          </ol>

          {/* Deliberately no response-time promise, no mention of police, and no
              confidentiality guarantee beyond what the system actually does. */}
          <p className="text-ink-500 mt-4 text-xs leading-relaxed">
            Reports are reviewed by people, so there is no fixed response time. If you are in
            immediate danger, contact campus security or emergency services directly.
          </p>
        </Card>

        <div className="flex flex-wrap justify-center gap-3">
          {anonymous && (
            <Link to="/check-report">
              <Button variant="secondary">Check status later</Button>
            </Link>
          )}
          {!anonymous && (
            <Link to="/reports">
              <Button variant="secondary">View my reports</Button>
            </Link>
          )}
          <Link to="/dashboard">
            <Button variant={anonymous ? 'secondary' : 'ghost'}>Back to overview</Button>
          </Link>
        </div>
      </div>
    </AppShell>
  )
}

function AccessTokenPanel({ token }: { token: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(token)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      // Clipboard access can be denied or unavailable. The code is on screen
      // and selectable, so this is not worth an error message.
    }
  }

  return (
    <Card as="section" className="border-brand-200 bg-brand-50 p-5 sm:p-6">
      <h2 className="text-brand-900 text-base font-semibold">Save this code now</h2>
      <p className="text-brand-900 mt-1.5 text-sm leading-relaxed">
        This is the only way to check your report&rsquo;s status. It is shown once and cannot
        be recovered — not by you, and not by anyone at the university.
      </p>

      <p className="border-brand-200 text-ink-900 mt-4 rounded-lg border bg-white px-4 py-3 font-mono text-sm break-all">
        {token}
      </p>

      <div className="mt-3 flex items-center gap-3">
        <Button variant="secondary" size="sm" onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy code'}
        </Button>
        <span aria-live="polite" className="text-brand-800 text-xs">
          {copied ? 'Copied to your clipboard.' : ''}
        </span>
      </div>

      <p className="text-brand-800 mt-4 text-xs leading-relaxed">
        This report remains unlinked to your account on the server. A browser-local shortcut
        may appear in My reports so you can check its status, but nobody can contact you about
        it. That is what keeps it anonymous.
      </p>
    </Card>
  )
}
