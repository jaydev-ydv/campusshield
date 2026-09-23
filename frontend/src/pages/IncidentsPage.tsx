import { useCallback, useEffect, useMemo, useState } from 'react'

import { useAuth } from '../auth/useAuth'
import { useCatalog } from '../hooks/useCatalog'
import { AppShell } from '../components/layout/AppShell'
import { Alert } from '../components/ui/Alert'
import { Card } from '../components/ui/Card'
import { ErrorState } from '../components/ui/ErrorState'
import { LoadingState } from '../components/ui/Spinner'
import {
  DISPATCH_LABELS,
  STATUS_LABELS,
  type CaseStatus,
  type DispatchState,
  type IncidentDetail,
  type IncidentSummary,
  type ResolutionReason,
} from '../lib/api'
import { ApiError } from '../lib/apiClient'
import { IncidentDetailPanel } from '../responder/IncidentDetail'
import { IncidentMap } from '../responder/IncidentMap'
import { defaultNavigationProvider } from '../responder/navigation'

/** Only statuses that can actually appear in an open queue. The terminal
 *  ones never do — offering them as filter chips would just be confusing. */
const FILTERABLE_STATUSES: CaseStatus[] = [
  'submitted',
  'triaged',
  'under_review',
  'action_taken',
]

/** No dispatch yet, or one raised but not yet acknowledged — the emergencies
 *  nobody has looked at, distinct from one already being actively handled. */
function needsAttention(incident: IncidentSummary): boolean {
  const state: DispatchState | null = incident.dispatch?.state ?? null
  return incident.is_emergency && (state === null || state === 'pending')
}

function relativeTime(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}

/**
 * The responder incident view: map, queue, and the incident a responder is
 * acting on.
 *
 * ## The list is not a fallback for the map
 *
 * It is the interface. Every location is unmapped today, and even once the
 * survey lands the list is what works on a phone, what a screen reader can use,
 * and what still functions for a location nobody has surveyed. The map is a
 * second view of the same data, not the only way in.
 *
 * ## Authorisation is not here
 *
 * Every endpoint this page calls refuses a student server-side. The route guard
 * below is for usability — so a student sees an explanation instead of a wall of
 * 403s — and is never the thing that keeps anyone out.
 */
export function IncidentsPage() {
  const { api, account } = useAuth()

  const [incidents, setIncidents] = useState<IncidentSummary[] | null>(null)
  const [mappingAvailable, setMappingAvailable] = useState(false)
  const [queueError, setQueueError] = useState<unknown>(null)
  const [queueNonce, setQueueNonce] = useState(0)
  // Empty = every open status. Narrows the queue; never widens past what the
  // server already scopes to this responder's routed role.
  const [statusFilter, setStatusFilter] = useState<CaseStatus[]>([])

  const [selectedRef, setSelectedRef] = useState<string | null>(null)
  const [detail, setDetail] = useState<IncidentDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const navigation = useMemo(() => defaultNavigationProvider(), [])
  // Reused rather than fetched again: the responder needs the category list only
  // to record a different judgement, and the dashboard already loads it.
  const { categories } = useCatalog()

  /** Reload is a nonce bump, and the fetch lives in the effect below.
   *
   *  The same shape as `useCatalog`, for the same reason: calling a setState-ing
   *  function from an effect body causes a cascading render, and the linter is
   *  right to refuse it. */
  const reloadQueue = useCallback(() => setQueueNonce((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    void api
      .incidents(100, 0, statusFilter.length > 0 ? statusFilter : undefined)
      .then((page) => {
        // The responder may have navigated away while this was in flight.
        if (cancelled) return
        setIncidents(page.items)
        setMappingAvailable(page.mapping_available)
        setQueueError(null)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setIncidents([])
        setQueueError(cause)
      })
    return () => {
      cancelled = true
    }
  }, [api, queueNonce, statusFilter])

  const openIncident = useCallback(
    async (publicRef: string) => {
      setSelectedRef(publicRef)
      setDetail(null)
      setDetailError(null)
      setActionError(null)
      try {
        setDetail(await api.incident(publicRef))
      } catch (cause) {
        setDetailError(
          cause instanceof ApiError ? cause.message : 'That incident could not be opened.',
        )
      }
    },
    [api],
  )

  /** Re-read from the server rather than patching local state.
   *  A dispatch action can change more than the field that was touched, and the
   *  responder is about to act on what this shows. */
  const afterAction = useCallback(
    async (publicRef: string) => {
      reloadQueue()
      await openIncident(publicRef)
    },
    [reloadQueue, openIncident],
  )

  const raiseDispatch = useCallback(async () => {
    if (!selectedRef) return
    setBusy(true)
    setActionError(null)
    try {
      await api.raiseDispatch(selectedRef)
      await afterAction(selectedRef)
    } catch (cause) {
      setActionError(
        cause instanceof ApiError ? cause.message : 'The dispatch could not be raised.',
      )
    } finally {
      setBusy(false)
    }
  }, [api, selectedRef, afterAction])

  const overrideCategory = useCallback(
    async (categoryId: number) => {
      if (!selectedRef) return
      setBusy(true)
      setActionError(null)
      try {
        await api.overrideCategory(selectedRef, categoryId)
        await afterAction(selectedRef)
      } catch (cause) {
        setActionError(
          cause instanceof ApiError ? cause.message : 'That category could not be recorded.',
        )
      } finally {
        setBusy(false)
      }
    },
    [api, selectedRef, afterAction],
  )

  const reviewLink = useCallback(
    async (linkId: number, confirmed: boolean) => {
      if (!selectedRef) return
      setBusy(true)
      setActionError(null)
      try {
        await api.reviewLink(selectedRef, linkId, confirmed)
        await afterAction(selectedRef)
      } catch (cause) {
        setActionError(
          cause instanceof ApiError ? cause.message : 'That link could not be updated.',
        )
      } finally {
        setBusy(false)
      }
    },
    [api, selectedRef, afterAction],
  )

  const changeCaseStatus = useCallback(
    async (
      target: CaseStatus,
      options: {
        remark?: string
        resolutionReason?: ResolutionReason
        visibleToReporter: boolean
      },
    ) => {
      if (!selectedRef) return
      setBusy(true)
      setActionError(null)
      try {
        await api.changeCaseStatus(selectedRef, target, options)
        await afterAction(selectedRef)
      } catch (cause) {
        setActionError(
          cause instanceof ApiError
            ? cause.message
            : 'That status change could not be recorded.',
        )
      } finally {
        setBusy(false)
      }
    },
    [api, selectedRef, afterAction],
  )

  const assignToMe = useCallback(async () => {
    if (!selectedRef) return
    setBusy(true)
    setActionError(null)
    try {
      await api.assignCase(selectedRef)
      await afterAction(selectedRef)
    } catch (cause) {
      setActionError(
        cause instanceof ApiError ? cause.message : 'The case could not be assigned.',
      )
    } finally {
      setBusy(false)
    }
  }, [api, selectedRef, afterAction])

  const releaseAssignment = useCallback(async () => {
    if (!selectedRef) return
    setBusy(true)
    setActionError(null)
    try {
      await api.unassignCase(selectedRef)
      await afterAction(selectedRef)
    } catch (cause) {
      setActionError(
        cause instanceof ApiError ? cause.message : 'The assignment could not be released.',
      )
    } finally {
      setBusy(false)
    }
  }, [api, selectedRef, afterAction])

  const advanceDispatch = useCallback(
    async (state: DispatchState) => {
      if (!selectedRef) return
      setBusy(true)
      setActionError(null)
      try {
        await api.advanceDispatch(selectedRef, state)
        await afterAction(selectedRef)
      } catch (cause) {
        setActionError(
          cause instanceof ApiError ? cause.message : 'The dispatch could not be updated.',
        )
      } finally {
        setBusy(false)
      }
    },
    [api, selectedRef, afterAction],
  )

  if (account && account.role === 'student') {
    return (
      <AppShell>
        <Alert tone="info" title="This area is for campus responders">
          <p>
            The incident map is used by campus security and the Internal Complaints Committee
            to respond to reports. Your own reports are on your reports page.
          </p>
        </Alert>
      </AppShell>
    )
  }

  const attentionCount = (incidents ?? []).filter(needsAttention).length

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-ink-900 text-xl font-medium">Active incidents</h1>
          <p className="text-ink-600 mt-1 text-sm leading-relaxed">
            Reports routed to your role that are still open. Ordered by urgency, then by how
            recently they arrived.
          </p>
        </div>

        {attentionCount > 0 && (
          <div data-testid="attention-banner">
            <Alert tone="warning" title="Emergency — needs attention">
              <p>
                {attentionCount === 1
                  ? 'One emergency report has not been acknowledged yet.'
                  : `${attentionCount} emergency reports have not been acknowledged yet.`}{' '}
                They are sorted to the top of the queue below.
              </p>
            </Alert>
          </div>
        )}

        <div
          role="group"
          aria-label="Filter by case status"
          className="flex flex-wrap items-center gap-1.5"
        >
          <span className="text-ink-500 text-xs font-medium">Status:</span>
          <button
            type="button"
            onClick={() => setStatusFilter([])}
            aria-pressed={statusFilter.length === 0}
            className={[
              'rounded-full px-2.5 py-1 text-xs font-medium transition-colors',
              statusFilter.length === 0
                ? 'bg-brand-700 text-white'
                : 'bg-ink-100 text-ink-700 hover:bg-ink-200',
            ].join(' ')}
          >
            All open
          </button>
          {FILTERABLE_STATUSES.map((option) => {
            const active = statusFilter.includes(option)
            return (
              <button
                key={option}
                type="button"
                onClick={() =>
                  setStatusFilter((current) =>
                    active ? current.filter((s) => s !== option) : [...current, option],
                  )
                }
                aria-pressed={active}
                className={[
                  'rounded-full px-2.5 py-1 text-xs font-medium transition-colors',
                  active
                    ? 'bg-brand-700 text-white'
                    : 'bg-ink-100 text-ink-700 hover:bg-ink-200',
                ].join(' ')}
              >
                {STATUS_LABELS[option]}
              </button>
            )
          })}
        </div>

        {queueError !== null && <ErrorState error={queueError} onRetry={reloadQueue} />}

        {incidents === null ? (
          <LoadingState label="Loading incidents…" />
        ) : incidents.length === 0 && queueError === null ? (
          <Card>
            <p className="text-ink-700 text-sm">
              There are no open incidents routed to your role right now.
            </p>
          </Card>
        ) : (
          <>
            <IncidentMap
              incidents={incidents}
              selectedRef={selectedRef}
              onSelect={(ref) => void openIncident(ref)}
            />
            {!mappingAvailable && incidents.length > 0 && (
              <p className="text-ink-500 text-xs" data-testid="mapping-pending-note">
                Campus mapping is pending the location survey. Incident locations below are the
                places reporters selected.
              </p>
            )}

            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
              <section aria-labelledby="queue-heading">
                <h2 id="queue-heading" className="text-ink-900 mb-2 text-sm font-medium">
                  Queue ({incidents.length})
                </h2>
                <ul className="space-y-2">
                  {incidents.map((incident) => (
                    <li key={incident.public_ref}>
                      <button
                        type="button"
                        onClick={() => void openIncident(incident.public_ref)}
                        aria-current={incident.public_ref === selectedRef ? 'true' : undefined}
                        className={[
                          'w-full rounded-lg border px-3 py-3 text-left transition-colors',
                          incident.public_ref === selectedRef
                            ? 'border-brand-400 bg-brand-50/60'
                            : 'border-ink-200 hover:bg-ink-50 bg-white',
                        ].join(' ')}
                      >
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="text-ink-900 text-sm font-medium">
                            {incident.public_ref}
                          </span>
                          {incident.is_emergency && (
                            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900">
                              Emergency
                            </span>
                          )}
                          {incident.is_ongoing && (
                            <span className="rounded-full bg-amber-200 px-2 py-0.5 text-xs font-medium text-amber-900">
                              Ongoing
                            </span>
                          )}
                        </span>
                        <span className="text-ink-700 mt-1 block text-sm">
                          {incident.category?.label ?? 'Category not recorded'}
                        </span>
                        <span className="text-ink-600 mt-0.5 block text-sm">
                          {incident.location.name}
                        </span>
                        <span className="mt-1 flex flex-wrap items-center gap-1.5">
                          <span className="bg-ink-100 text-ink-700 rounded-full px-2 py-0.5 text-xs font-medium">
                            {STATUS_LABELS[incident.status]}
                          </span>
                          <span
                            className={[
                              'rounded-full px-2 py-0.5 text-xs font-medium',
                              incident.is_assigned
                                ? 'bg-emerald-50 text-emerald-800'
                                : 'bg-ink-50 text-ink-500',
                            ].join(' ')}
                          >
                            {incident.is_assigned ? 'Assigned' : 'Unassigned'}
                          </span>
                        </span>
                        <span className="text-ink-500 mt-1 flex flex-wrap gap-x-3 text-xs">
                          <span>{relativeTime(incident.submitted_at)}</span>
                          {incident.evidence_count > 0 && (
                            <span>
                              {incident.evidence_count}{' '}
                              {incident.evidence_count === 1 ? 'photo' : 'photos'}
                            </span>
                          )}
                          {incident.location_signal?.resolution === 'conflicting' && (
                            <span className="text-amber-800">Location differs</span>
                          )}
                          {incident.dispatch && (
                            <span>{DISPATCH_LABELS[incident.dispatch.state]}</span>
                          )}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>

              <section aria-labelledby="detail-heading">
                <h2 id="detail-heading" className="sr-only">
                  Incident detail
                </h2>
                <Card>
                  {detailError ? (
                    <Alert tone="error" title="Incident unavailable">
                      <p>{detailError}</p>
                    </Alert>
                  ) : selectedRef === null ? (
                    <p className="text-ink-600 text-sm">
                      Select an incident to see the location, evidence, and dispatch actions.
                    </p>
                  ) : detail === null ? (
                    <LoadingState label="Opening incident…" />
                  ) : (
                    <IncidentDetailPanel
                      incident={detail}
                      navigation={navigation}
                      onDispatch={() => void raiseDispatch()}
                      onAdvance={(state) => void advanceDispatch(state)}
                      onOverrideCategory={(id) => void overrideCategory(id)}
                      onReviewLink={(id, confirmed) => void reviewLink(id, confirmed)}
                      onCaseTransition={(target, opts) => void changeCaseStatus(target, opts)}
                      onAssign={() => void assignToMe()}
                      onUnassign={() => void releaseAssignment()}
                      categories={categories}
                      busy={busy}
                      error={actionError}
                    />
                  )}
                </Card>
              </section>
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}
