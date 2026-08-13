import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { useCatalog } from '../hooks/useCatalog'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ErrorState } from '../components/ui/ErrorState'
import { LoadingState } from '../components/ui/Spinner'
import { Stepper } from '../components/ui/Stepper'
import { ApiError } from '../lib/apiClient'
import {
  EMPTY_DRAFT,
  STEPS,
  buildPayload,
  validateStep,
  type ReportDraft,
  type StepErrors,
  type StepIndex,
} from '../report/draft'
import {
  StepKind,
  StepLocation,
  StepPrivacy,
  StepRelationship,
  StepReview,
  StepWhat,
  StepWhen,
} from '../report/steps'
import { EvidenceUpload, type EvidenceItem } from '../report/EvidenceUpload'
import type { EvidenceLimits } from '../lib/api'

const LAST_STEP = (STEPS.length - 1) as StepIndex

/**
 * The reporting workflow.
 *
 * Holds the draft and decides when a step may be left; the steps themselves are
 * presentational and `draft.ts` owns the rules. Nothing here is a security
 * control — the backend validates every field again and is the only thing that
 * decides what is stored.
 */
export function ReportPage() {
  const { api } = useAuth()
  const navigate = useNavigate()
  const { locations, categories, loading, error: catalogError, reload } = useCatalog()

  const [step, setStep] = useState<StepIndex>(0)
  const [draft, setDraft] = useState<ReportDraft>(EMPTY_DRAFT)
  const [errors, setErrors] = useState<StepErrors>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<unknown>(null)
  const [evidence, setEvidence] = useState<EvidenceItem[]>([])
  const [limits, setLimits] = useState<EvidenceLimits | null>(null)

  // Guards against a double submit that React state cannot: two clicks in the
  // same tick both read `submitting === false` before either re-render lands.
  // A duplicate report is not a cosmetic bug — it is a second row in the
  // database and a second alert to security.
  const inFlight = useRef(false)

  const headingRef = useRef<HTMLDivElement>(null)

  const update = useCallback((patch: Partial<ReportDraft>) => {
    setDraft((current) => ({ ...current, ...patch }))
    // Clear only the fields being changed, so untouched errors stay visible.
    setErrors((current) => {
      const next = { ...current }
      for (const key of Object.keys(patch)) delete next[key as keyof ReportDraft]
      return next
    })
  }, [])

  const noLocations = locations !== null && locations.length === 0

  const goTo = useCallback((next: StepIndex) => {
    setStep(next)
    setErrors({})
    // Move focus to the top of the new step. Without this a keyboard or screen
    // reader user stays where the Continue button was and has no idea the page
    // changed underneath them.
    requestAnimationFrame(() => headingRef.current?.focus())
  }, [])

  const handleNext = useCallback(() => {
    const found = validateStep(step, draft, categories)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    if (step < LAST_STEP) goTo((step + 1) as StepIndex)
  }, [step, draft, categories, goTo])

  const handleBack = useCallback(() => {
    if (step > 0) goTo((step - 1) as StepIndex)
  }, [step, goTo])

  const handleSubmit = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    setSubmitting(true)
    setSubmitError(null)

    try {
      // Tokens are derived here rather than mirrored into the draft: upload
      // state belongs to the upload component, and the draft holds only what
      // the student answered.
      const tokens = evidence
        .filter((item) => item.status === 'uploaded' && item.token)
        .map((item) => item.token as string)
      const result = await api.submitReport(buildPayload(draft, tokens))
      // Passed through router state rather than stored. The anonymous access
      // token is shown once by design; persisting it anywhere — localStorage,
      // a query string — would create the recoverable copy the whole mechanism
      // exists to avoid.
      navigate('/report/submitted', { replace: true, state: { result } })
    } catch (err) {
      setSubmitError(err)
      inFlight.current = false
      setSubmitting(false)
    }
  }, [api, draft, evidence, navigate])

  // Limits come from the server so the client cannot disagree with it about the
  // size cap. A failure here is not fatal: the component falls back to defaults
  // and the server rejects anything oversized anyway.
  useEffect(() => {
    let cancelled = false
    void api
      .evidenceLimits()
      .then((result) => {
        if (!cancelled) setLimits(result)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [api])

  const uploadedCount = evidence.filter((item) => item.status === 'uploaded').length

  const stepProps = useMemo(
    () => ({ draft, errors, update, locations, categories, evidenceCount: uploadedCount }),
    [draft, errors, update, locations, categories, uploadedCount],
  )

  const STEP_COMPONENTS = [
    StepKind,
    StepLocation,
    StepWhen,
    StepWhat,
    null, // step 5 is evidence, which needs its own props
    StepRelationship,
    StepPrivacy,
    StepReview,
  ] as const
  const StepComponent = STEP_COMPONENTS[step]

  return (
    <AppShell>
      <div className="mx-auto max-w-2xl">
        <div className="mb-6">
          <h1 className="text-ink-900 text-2xl font-semibold tracking-tight sm:text-3xl">
            Report a safety concern
          </h1>
          <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
            Whatever you share helps the university understand what is happening on campus. You
            can stop at any point — nothing is sent until you submit.
          </p>
        </div>

        <Stepper steps={STEPS} current={step} />

        {catalogError != null && (
          <div className="mb-5">
            <ErrorState error={catalogError} onRetry={reload} />
          </div>
        )}

        <Card as="section">
          {/* tabIndex -1 so focus can be moved here programmatically without
              adding it to the tab order. */}
          <div ref={headingRef} tabIndex={-1} className="outline-none">
            {loading && locations === null ? (
              <LoadingState label="Preparing the form…" />
            ) : StepComponent === null ? (
              <EvidenceUpload
                items={evidence}
                setItems={setEvidence}
                limits={limits}
                disabled={submitting}
              />
            ) : (
              <StepComponent {...stepProps} />
            )}
          </div>

          {submitError != null && (
            <div className="mt-5">
              <SubmitError error={submitError} onBackToStart={() => goTo(0)} />
            </div>
          )}

          <div className="border-ink-200 mt-7 flex flex-col-reverse gap-3 border-t pt-5 sm:flex-row sm:justify-between">
            <Button
              variant="ghost"
              onClick={step === 0 ? () => navigate('/dashboard') : handleBack}
              disabled={submitting}
            >
              {step === 0 ? 'Cancel' : 'Back'}
            </Button>

            {step < LAST_STEP ? (
              <Button onClick={handleNext} disabled={noLocations && step >= 1} size="lg">
                Continue
              </Button>
            ) : (
              <Button
                onClick={() => void handleSubmit()}
                loading={submitting}
                loadingLabel="Submitting…"
                size="lg"
              >
                Submit report
              </Button>
            )}
          </div>
        </Card>

        <p className="text-ink-500 mt-5 text-center text-xs">
          In immediate danger? Contact campus security or emergency services directly.
        </p>
      </div>
    </AppShell>
  )
}

/**
 * Submission failures, said in terms of what the student can do next.
 *
 * Field-level validation errors from the backend are surfaced rather than
 * flattened into "something went wrong": if the server rejected the category,
 * the student needs to know it was the category.
 */
function SubmitError({ error, onBackToStart }: { error: unknown; onBackToStart: () => void }) {
  if (error instanceof ApiError && error.status === 400) {
    const fields = Object.entries(error.fieldErrors)
    return (
      <Alert
        tone="error"
        title="Your report could not be submitted"
        requestId={error.requestId}
      >
        <p>{error.message}</p>
        {fields.length > 0 && (
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {fields.map(([field, messages]) => (
              <li key={field}>
                {Array.isArray(messages) ? messages.join(' ') : String(messages)}
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3">
          <Button variant="secondary" size="sm" onClick={onBackToStart}>
            Go back and check
          </Button>
        </div>
      </Alert>
    )
  }

  if (error instanceof ApiError && error.status === 429) {
    return (
      <Alert tone="warning" title="Daily limit reached" requestId={error.requestId}>
        {error.message} If this is urgent, contact campus security directly.
      </Alert>
    )
  }

  return <ErrorState error={error} title="Your report could not be submitted" />
}
