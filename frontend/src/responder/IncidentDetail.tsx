import { useCallback } from 'react'

import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Spinner } from '../components/ui/Spinner'
import {
  DISPATCH_LABELS,
  DISPATCH_NEXT,
  RESOLUTION_LABELS,
  STATUS_LABELS,
  type CaseStatus,
  type DispatchState,
  type IncidentDetail as Incident,
  type ReportCategory,
  type ResolutionReason,
} from '../lib/api'
import { CaseLifecyclePanel } from './CaseLifecyclePanel'
import { destinationDirections, type NavigationProvider } from './navigation'
import { EvidenceViewer } from './EvidenceViewer'
import { TriagePanel } from './TriagePanel'

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

/**
 * One incident, as the thing a responder acts on.
 *
 * Ordered the way someone deciding whether to go reads it: what, where, when,
 * what is happening now, then the evidence, then the actions. The contact
 * guidance sits above the actions because it changes what a responder does when
 * they arrive.
 */
export function IncidentDetailPanel({
  incident,
  navigation,
  onDispatch,
  onAdvance,
  onOverrideCategory,
  onReviewLink,
  onCaseTransition,
  onAssign,
  onUnassign,
  categories,
  busy,
  error,
}: {
  incident: Incident
  navigation: NavigationProvider
  onDispatch: () => void
  onAdvance: (state: DispatchState) => void
  onOverrideCategory: (categoryId: number) => void
  onReviewLink: (linkId: number, confirmed: boolean) => void
  onCaseTransition: (
    target: CaseStatus,
    options: {
      remark?: string
      resolutionReason?: ResolutionReason
      visibleToReporter: boolean
    },
  ) => void
  onAssign: () => void
  onUnassign: () => void
  categories: ReportCategory[] | null
  busy?: boolean
  error?: string | null
}) {
  const target = navigation.target(incident.destination)
  const directions = destinationDirections(incident.destination)

  const advance = useCallback(
    (state: DispatchState) => {
      onAdvance(state)
    },
    [onAdvance],
  )

  return (
    <article className="space-y-5" aria-labelledby="incident-heading">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="incident-heading" className="text-ink-900 text-lg font-medium">
            {incident.public_ref}
          </h2>
          {incident.is_emergency && (
            <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-900">
              Emergency
            </span>
          )}
          {incident.is_ongoing && (
            <span className="rounded-full bg-amber-200 px-2.5 py-0.5 text-xs font-medium text-amber-900">
              Ongoing
            </span>
          )}
          <span className="bg-ink-100 text-ink-700 rounded-full px-2.5 py-0.5 text-xs font-medium">
            {STATUS_LABELS[incident.status] ?? incident.status}
          </span>
        </div>
        <p className="text-ink-700 text-sm">
          {incident.category?.label ?? 'Category not recorded'}
        </p>
      </header>

      {error && (
        <Alert tone="error" title="That action did not complete">
          <p>{error}</p>
        </Alert>
      )}

      <section aria-labelledby="where-heading" className="space-y-1.5">
        <h3 id="where-heading" className="text-ink-900 text-sm font-medium">
          Where
        </h3>
        <ul className="text-ink-700 space-y-1 text-sm leading-relaxed">
          {directions.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {!incident.destination.is_mapped && (
          <p className="text-ink-500 text-xs">
            This location has no verified coordinate yet, so it cannot be opened in a map. Use
            the name and access notes above.
          </p>
        )}
        {incident.destination.is_synthetic && (
          <p
            data-testid="destination-demo-notice"
            className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-medium text-amber-900"
          >
            DEMO MODE — this is a development fixture, not a real, surveyed campus location. Do
            not act on it as if it were.
          </p>
        )}
      </section>

      <section aria-labelledby="when-heading" className="space-y-1">
        <h3 id="when-heading" className="text-ink-900 text-sm font-medium">
          When
        </h3>
        <p className="text-ink-700 text-sm">
          Occurred {formatWhen(incident.occurred_at)} · Reported{' '}
          {formatWhen(incident.submitted_at)}
        </p>
      </section>

      {incident.location_signal && incident.location_signal.resolution !== 'approximate' && (
        <section aria-labelledby="signal-heading" className="space-y-1">
          <h3 id="signal-heading" className="text-ink-900 text-sm font-medium">
            Location check
          </h3>
          <p className="text-ink-700 text-sm">
            {RESOLUTION_LABELS[incident.location_signal.resolution]}
          </p>
          {incident.location_signal.note && (
            /* A conflict is shown to a human and never resolved automatically.
               The note carries the innocent explanations alongside the fact. */
            <Alert
              tone={incident.location_signal.resolution === 'conflicting' ? 'warning' : 'info'}
              title={
                incident.location_signal.resolution === 'conflicting'
                  ? 'Photo location differs from the selected place'
                  : 'Location note'
              }
            >
              <p>{incident.location_signal.note}</p>
            </Alert>
          )}
        </section>
      )}

      <section aria-labelledby="account-heading" className="space-y-1">
        <h3 id="account-heading" className="text-ink-900 text-sm font-medium">
          What was reported
        </h3>
        {incident.narrative_available ? (
          <p className="text-ink-700 text-sm leading-relaxed whitespace-pre-wrap">
            {incident.narrative}
          </p>
        ) : (
          <p className="text-ink-500 text-sm">
            {incident.narrative_withheld_reason ?? 'Not available to your role.'}
          </p>
        )}
      </section>

      <section aria-labelledby="evidence-heading" className="space-y-2">
        <h3 id="evidence-heading" className="text-ink-900 text-sm font-medium">
          Evidence
        </h3>
        <EvidenceViewer
          evidenceIds={incident.evidence.map((item) => item.evidence_id)}
          publicRef={incident.public_ref}
        />
      </section>

      <CaseLifecyclePanel
        publicRef={incident.public_ref}
        currentStatus={incident.status}
        history={incident.case_status_history}
        assignment={incident.assignment}
        isAssigned={incident.is_assigned}
        onTransition={onCaseTransition}
        onAssign={onAssign}
        onUnassign={onUnassign}
        busy={busy}
      />

      {incident.triage && (
        <TriagePanel
          triage={incident.triage}
          declaredCategoryLabel={incident.category?.label ?? null}
          categories={categories}
          onOverride={onOverrideCategory}
          onReviewLink={onReviewLink}
          busy={busy}
        />
      )}

      {/* Stated positively rather than left as a missing phone number. A
          responder needs to know before they set off that there is nobody to
          call — and, for an anonymous report, that working out who reported it
          is not part of the job. */}
      <Alert
        tone={incident.reporter_contactable ? 'info' : 'warning'}
        title={
          incident.reporter_contactable
            ? 'The reporter can be contacted'
            : 'The reporter cannot be contacted'
        }
      >
        <p>{incident.reporter_contact_guidance}</p>
        {incident.submission_mode === 'anonymous' && (
          <p className="mt-2">
            This protects the reporter's identity in this system. It cannot prevent someone at
            the scene from seeing who is present.
          </p>
        )}
      </Alert>

      <section aria-labelledby="actions-heading" className="space-y-3">
        <h3 id="actions-heading" className="text-ink-900 text-sm font-medium">
          Actions
        </h3>

        <div className="flex flex-wrap gap-2">
          {target.url ? (
            /* A link rather than a Button: this navigates away to a map
               application, and `Button` renders a <button>. Middle-click and
               "open in new tab" should work the way they do for any link.
               The label itself is computed by `NavigationProvider` — left
               untouched — and "(DEMO)" is appended here, at the display
               layer, for a synthetic destination so a demo pin can never
               read as an instruction to actually go somewhere. */
            <a
              href={target.url}
              target="_blank"
              rel="noopener noreferrer"
              className="bg-brand-700 hover:bg-brand-800 active:bg-brand-900 inline-flex min-h-11 items-center rounded-lg px-4 py-2.5 text-sm font-medium text-white shadow-sm"
            >
              {incident.destination.is_synthetic ? `${target.label} (DEMO)` : target.label}
            </a>
          ) : (
            <Button variant="primary" disabled title={target.label}>
              Navigate to incident
            </Button>
          )}

          {incident.dispatch === null ? (
            <Button
              variant="secondary"
              onClick={onDispatch}
              disabled={busy || !incident.is_emergency}
              title={
                incident.is_emergency
                  ? undefined
                  : 'Only an emergency report can be dispatched.'
              }
            >
              {busy ? <Spinner size="sm" /> : 'Raise dispatch'}
            </Button>
          ) : (
            DISPATCH_NEXT[incident.dispatch.state].map((next) => (
              <Button
                key={next.state}
                variant="secondary"
                onClick={() => advance(next.state)}
                disabled={busy}
              >
                {next.label}
              </Button>
            ))
          )}
        </div>

        {incident.dispatch && (
          <dl className="text-ink-600 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="font-medium">Dispatch</dt>
            <dd>{DISPATCH_LABELS[incident.dispatch.state]}</dd>
            {incident.dispatch.acknowledged_at && (
              <>
                <dt className="font-medium">Acknowledged</dt>
                <dd>{formatWhen(incident.dispatch.acknowledged_at)}</dd>
              </>
            )}
            {incident.dispatch.on_scene_at && (
              <>
                <dt className="font-medium">On scene</dt>
                <dd>{formatWhen(incident.dispatch.on_scene_at)}</dd>
              </>
            )}
            {incident.dispatch.closed_at && (
              <>
                <dt className="font-medium">Closed</dt>
                <dd>{formatWhen(incident.dispatch.closed_at)}</dd>
              </>
            )}
          </dl>
        )}
      </section>
    </article>
  )
}
