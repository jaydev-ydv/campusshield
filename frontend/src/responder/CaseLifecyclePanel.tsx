import { useState } from 'react'

import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import { Select } from '../components/ui/Select'
import { Spinner } from '../components/ui/Spinner'
import { Textarea } from '../components/ui/Textarea'
import {
  CASE_TRANSITIONS,
  REQUIRES_ASSIGNMENT,
  RESOLUTION_REASONS_BY_STATUS,
  RESOLUTION_REASON_LABELS,
  STATUS_LABELS,
  TERMINAL_STATUSES,
  type CaseAssignment,
  type CaseStatus,
  type CaseStatusEntry,
  type ResolutionReason,
} from '../lib/api'

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/**
 * Case status and assignment — deliberately its own panel, separate from
 * dispatch.
 *
 * ## Case status vs. dispatch status
 *
 * These are related but **not the same state machine**. Dispatch
 * (`pending → … → closed`, in `IncidentDetailPanel` below this) tracks one
 * physical response to one emergency. Case status (`submitted → … →
 * resolved`, here) tracks the institutional handling of the report itself —
 * whether it has been looked at, who owns it, what came of it. A case can be
 * `resolved` with no dispatch ever raised, and a dispatch can be `closed`
 * while the case is still `under_review`. Closing one does not close the
 * other — there is no button here that touches dispatch, and none in the
 * dispatch section that touches this.
 *
 * ## Every transition is server-validated
 *
 * `CASE_TRANSITIONS` here only decides which buttons to *offer* — it is a
 * courtesy copy of `case_service.py`'s `CASE_TRANSITIONS`, not the
 * enforcement. The server re-checks the whole rule set on every request and
 * would refuse an illegal move even if this component offered it by mistake.
 */
export function CaseLifecyclePanel({
  publicRef,
  currentStatus,
  history,
  assignment,
  isAssigned,
  onTransition,
  onAssign,
  onUnassign,
  busy,
}: {
  publicRef: string
  currentStatus: CaseStatus
  history: CaseStatusEntry[]
  assignment: CaseAssignment | null
  isAssigned: boolean
  onTransition: (
    target: CaseStatus,
    options: {
      remark?: string
      resolutionReason?: ResolutionReason
      visibleToReporter: boolean
    },
  ) => void
  onAssign: () => void
  onUnassign: () => void
  busy?: boolean
}) {
  const [choosingTarget, setChoosingTarget] = useState<CaseStatus | null>(null)

  const nextStates = CASE_TRANSITIONS[currentStatus] ?? []
  const isTerminal = TERMINAL_STATUSES.has(currentStatus)

  return (
    <section aria-labelledby="case-heading" className="space-y-3" data-testid="case-lifecycle">
      <h3 id="case-heading" className="text-ink-900 text-sm font-medium">
        Case status
      </h3>

      <div className="border-ink-200 rounded-lg border bg-white p-3">
        <p className="text-ink-600 text-xs font-medium tracking-wide uppercase">Status</p>
        <p className="text-ink-900 mt-1 text-sm font-medium">{STATUS_LABELS[currentStatus]}</p>

        <p className="text-ink-600 mt-3 text-xs font-medium tracking-wide uppercase">
          Assigned to
        </p>
        {assignment ? (
          <p className="text-ink-900 mt-1 text-sm">
            {assignment.assignee.label}
            {assignment.assigned_by && (
              <span className="text-ink-500">
                {' '}
                · assigned by {assignment.assigned_by.label}
              </span>
            )}
          </p>
        ) : (
          <p className="text-ink-500 mt-1 text-sm">Unassigned</p>
        )}

        {!isTerminal && (
          <div className="mt-2 flex flex-wrap gap-2">
            {!isAssigned ? (
              <Button variant="secondary" size="sm" onClick={onAssign} disabled={busy}>
                Assign to me
              </Button>
            ) : (
              <Button variant="ghost" size="sm" onClick={onUnassign} disabled={busy}>
                Release assignment
              </Button>
            )}
          </div>
        )}
      </div>

      {isTerminal ? (
        <Alert tone="info" title="This case is closed">
          <p>Closed cases cannot be reopened here.</p>
        </Alert>
      ) : (
        <div className="space-y-2">
          <p className="text-ink-600 text-xs font-medium tracking-wide uppercase">
            Move this case
          </p>
          <div className="flex flex-wrap gap-2">
            {nextStates.map((target) => {
              const blocked = REQUIRES_ASSIGNMENT.has(target) && !isAssigned
              return (
                <Button
                  key={target}
                  variant="secondary"
                  size="sm"
                  onClick={() => setChoosingTarget(target)}
                  disabled={busy || blocked}
                  title={blocked ? 'Assign the case first.' : undefined}
                >
                  {STATUS_LABELS[target]}
                </Button>
              )
            })}
          </div>
          {nextStates.some((target) => REQUIRES_ASSIGNMENT.has(target)) && !isAssigned && (
            <p className="text-ink-500 text-xs">
              Assign the case to yourself before moving it into investigation or closing it as
              resolved.
            </p>
          )}
        </div>
      )}

      {choosingTarget && (
        <TransitionForm
          publicRef={publicRef}
          target={choosingTarget}
          busy={busy}
          onCancel={() => setChoosingTarget(null)}
          onSubmit={(options) => {
            onTransition(choosingTarget, options)
            setChoosingTarget(null)
          }}
        />
      )}

      <details>
        <summary className="text-ink-700 cursor-pointer text-sm">
          Status history ({history.length})
        </summary>
        <ol className="text-ink-700 mt-2 space-y-2 text-sm">
          {history.map((entry) => (
            <li key={entry.history_id} className="border-ink-100 border-l-2 pl-3">
              <p className="text-ink-900 font-medium">{STATUS_LABELS[entry.to_status]}</p>
              <p className="text-ink-500 text-xs">{formatWhen(entry.changed_at)}</p>
              {entry.remark && <p className="mt-0.5">{entry.remark}</p>}
              {entry.resolution_reason && (
                <p className="text-ink-500 text-xs">
                  Reason: {RESOLUTION_REASON_LABELS[entry.resolution_reason]}
                </p>
              )}
              {!entry.visible_to_reporter && (
                <p className="text-ink-400 text-xs italic">
                  Internal only — not shown to reporter
                </p>
              )}
            </li>
          ))}
        </ol>
      </details>
    </section>
  )
}

function TransitionForm({
  target,
  busy,
  onCancel,
  onSubmit,
}: {
  publicRef: string
  target: CaseStatus
  busy?: boolean
  onCancel: () => void
  onSubmit: (options: {
    remark?: string
    resolutionReason?: ResolutionReason
    visibleToReporter: boolean
  }) => void
}) {
  const [remark, setRemark] = useState('')
  const [reason, setReason] = useState<ResolutionReason | ''>('')
  const [visibleToReporter, setVisibleToReporter] = useState(true)

  const isTerminal = TERMINAL_STATUSES.has(target)
  const remarkRequired = target !== 'triaged'
  const reasonOptions = RESOLUTION_REASONS_BY_STATUS[target] ?? []

  const canSubmit =
    (!remarkRequired || remark.trim().length > 0) && (!isTerminal || reason !== '')

  return (
    <form
      className="border-brand-200 bg-brand-50/40 space-y-3 rounded-lg border p-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (!canSubmit) return
        onSubmit({
          remark: remark.trim() || undefined,
          resolutionReason: reason || undefined,
          visibleToReporter,
        })
      }}
    >
      <p className="text-ink-900 text-sm font-medium">Move to {STATUS_LABELS[target]}</p>

      <Textarea
        label={remarkRequired ? 'Reason for this update' : 'Reason for this update (optional)'}
        value={remark}
        onChange={(event) => setRemark(event.target.value)}
        rows={3}
        required={remarkRequired}
      />

      {isTerminal && (
        <Select
          label="Resolution reason"
          value={reason}
          onChange={(event) => setReason(event.target.value as ResolutionReason)}
          placeholder="Choose a reason…"
          required
        >
          {reasonOptions.map((option) => (
            <option key={option} value={option}>
              {RESOLUTION_REASON_LABELS[option]}
            </option>
          ))}
        </Select>
      )}

      <label className="text-ink-700 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={visibleToReporter}
          onChange={(event) => setVisibleToReporter(event.target.checked)}
        />
        Show this update to the reporter
      </label>

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !canSubmit}>
          {busy ? <Spinner size="sm" /> : 'Confirm'}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
