import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ApiError } from '../lib/apiClient'
import type { EvidenceLimits, ReportSubmissionResult } from '../lib/api'
import { EvidenceUpload, type EvidenceItem } from '../report/EvidenceUpload'

/**
 * Shown right after an SOS trigger.
 *
 * Mirrors `ReportConfirmationPage`'s "shown once, via router state" shape,
 * but for the emergency path specifically: no access token (an emergency is
 * always identified — see `ReportService.submit_sos`), and an evidence
 * uploader, since photos were never asked for at the moment of the alert and
 * this is where the reporter can add them once it's safe to.
 */
export function EmergencyConfirmationPage() {
  const location = useLocation()
  const { api } = useAuth()
  const result = (location.state as { result?: ReportSubmissionResult } | null)?.result ?? null

  const [evidence, setEvidence] = useState<EvidenceItem[]>([])
  const [limits, setLimits] = useState<EvidenceLimits | null>(null)
  const [attaching, setAttaching] = useState(false)
  const [attachError, setAttachError] = useState<string | null>(null)
  const [attached, setAttached] = useState(false)

  useEffect(() => {
    let cancelled = false
    void api
      .evidenceLimits()
      .then((value) => {
        if (!cancelled) setLimits(value)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [api])

  if (!result) {
    return (
      <AppShell>
        <div className="mx-auto max-w-xl">
          <Card as="section">
            <h1 className="text-ink-900 text-xl font-semibold">
              This page is no longer available
            </h1>
            <p className="text-ink-600 mt-2 text-sm leading-relaxed">
              Confirmation details are shown once and are not kept. Your alert is in your
              reports list.
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

  const uploadedTokens = evidence.filter((item) => item.status === 'uploaded' && item.token)

  async function attachEvidence() {
    if (!result || uploadedTokens.length === 0) return
    setAttaching(true)
    setAttachError(null)
    try {
      await api.attachEvidence(
        result.public_ref,
        uploadedTokens.map((item) => item.token as string),
      )
      setAttached(true)
      setEvidence([])
    } catch (cause) {
      setAttachError(
        cause instanceof ApiError ? cause.message : 'The images could not be attached.',
      )
    } finally {
      setAttaching(false)
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-xl space-y-5">
        <div className="text-center">
          <span
            aria-hidden="true"
            className="bg-amber-50 text-amber-700 ring-amber-200 inline-flex h-12 w-12 items-center justify-center rounded-full text-xl ring-1"
          >
            ✓
          </span>
          <h1 className="text-ink-900 mt-4 text-2xl font-semibold tracking-tight">
            Your emergency alert has been sent
          </h1>
          <p className="text-ink-600 mt-2 text-sm leading-relaxed">
            Campus security has been alerted and can see this in their queue now.
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
            You'll see status updates in your CampusShield notifications, and security may
            contact you directly.
          </p>
        </Card>

        <Card as="section">
          <h2 className="text-ink-900 text-base font-semibold">Location</h2>
          <p className="text-ink-700 mt-2 text-sm leading-relaxed">
            {result.location.code === 'SYS-UNSPECIFIED'
              ? 'No location could be determined automatically. Security has been alerted without one — call them directly with your location if you can.'
              : `Recorded near ${result.location.name}.`}
          </p>
        </Card>

        <Card as="section">
          <h2 className="text-ink-900 text-base font-semibold">Add photos, if it's safe to</h2>
          <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
            Never required, and only if it's safe to do this now. You can also come back to
            this report later from your reports list.
          </p>

          {attached ? (
            <Alert tone="success" title="Added" className="mt-4">
              <p>Your photos were attached to this report.</p>
            </Alert>
          ) : (
            <>
              <div className="mt-4">
                <EvidenceUpload
                  items={evidence}
                  setItems={setEvidence}
                  limits={limits}
                  disabled={attaching}
                />
              </div>
              {attachError && (
                <Alert tone="error" title="That did not complete" className="mt-4">
                  <p>{attachError}</p>
                </Alert>
              )}
              {uploadedTokens.length > 0 && (
                <div className="mt-4">
                  <Button onClick={() => void attachEvidence()} loading={attaching}>
                    Attach {uploadedTokens.length === 1 ? 'this photo' : 'these photos'}
                  </Button>
                </div>
              )}
            </>
          )}
        </Card>

        <p className="text-ink-500 text-center text-xs leading-relaxed">
          If you are in immediate danger, contact campus security or emergency services
          directly.
        </p>

        <div className="flex flex-wrap justify-center gap-3">
          <Link to="/reports">
            <Button variant="secondary">View my reports</Button>
          </Link>
          <Link to="/dashboard">
            <Button variant="ghost">Back to overview</Button>
          </Link>
        </div>
      </div>
    </AppShell>
  )
}
