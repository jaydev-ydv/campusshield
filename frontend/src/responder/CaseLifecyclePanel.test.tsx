import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { CaseLifecyclePanel } from './CaseLifecyclePanel'
import type { CaseAssignment, CaseStatusEntry } from '../lib/api'

const HISTORY: CaseStatusEntry[] = [
  {
    history_id: 1,
    from_status: null,
    to_status: 'submitted',
    changed_at: '2026-08-11T14:00:00+00:00',
    remark: 'Report submitted.',
    visible_to_reporter: true,
    resolution_reason: null,
  },
]

const ASSIGNMENT: CaseAssignment = {
  assignment_id: 1,
  assigned_at: '2026-08-11T14:10:00+00:00',
  assignee: { role: 'icc', label: 'ICC Officer' },
  assigned_by: { role: 'icc', label: 'ICC Officer' },
  note: null,
}

function renderPanel(props: Partial<Parameters<typeof CaseLifecyclePanel>[0]> = {}) {
  const onTransition = vi.fn()
  const onAssign = vi.fn()
  const onUnassign = vi.fn()
  render(
    <CaseLifecyclePanel
      publicRef="CS-2026-7QK4M2"
      currentStatus="submitted"
      history={HISTORY}
      assignment={null}
      isAssigned={false}
      onTransition={onTransition}
      onAssign={onAssign}
      onUnassign={onUnassign}
      {...props}
    />,
  )
  return { onTransition, onAssign, onUnassign }
}

const user = () => userEvent.setup({ delay: null })

describe('CaseLifecyclePanel — distinctness from dispatch', () => {
  it('is headed "Case status", never "Dispatch"', () => {
    renderPanel()
    expect(screen.getByRole('heading', { name: 'Case status' })).toBeInTheDocument()
  })

  it('carries a stable test hook distinct from the dispatch section', () => {
    renderPanel()
    expect(screen.getByTestId('case-lifecycle')).toBeInTheDocument()
  })
})

describe('CaseLifecyclePanel — status and assignment', () => {
  it('shows the current status', () => {
    renderPanel({ currentStatus: 'under_review' })
    expect(screen.getByText('Under review')).toBeInTheDocument()
  })

  it('shows "Unassigned" when there is no owner', () => {
    renderPanel({ assignment: null, isAssigned: false })
    expect(screen.getByText('Unassigned')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /assign to me/i })).toBeInTheDocument()
  })

  it('shows who owns the case and who assigned it', () => {
    renderPanel({ assignment: ASSIGNMENT, isAssigned: true })
    const status = screen.getByTestId('case-lifecycle')
    expect(within(status).getAllByText(/ICC Officer/).length).toBeGreaterThan(0)
    expect(within(status).getByText(/assigned by ICC Officer/)).toBeInTheDocument()
  })

  it('offers to release an assignment once claimed', () => {
    renderPanel({ assignment: ASSIGNMENT, isAssigned: true })
    expect(screen.getByRole('button', { name: /release assignment/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /assign to me/i })).not.toBeInTheDocument()
  })

  it('claims a case via "Assign to me"', async () => {
    const u = user()
    const { onAssign } = renderPanel()
    await u.click(screen.getByRole('button', { name: /assign to me/i }))
    expect(onAssign).toHaveBeenCalledOnce()
  })

  it('releases a case via "Release assignment"', async () => {
    const u = user()
    const { onUnassign } = renderPanel({ assignment: ASSIGNMENT, isAssigned: true })
    await u.click(screen.getByRole('button', { name: /release assignment/i }))
    expect(onUnassign).toHaveBeenCalledOnce()
  })

  it('never renders a bare user id anywhere', () => {
    renderPanel({ assignment: ASSIGNMENT, isAssigned: true })
    // The label is a role + display name; nothing UUID-shaped should appear.
    expect(document.body.innerHTML).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    )
  })
})

describe('CaseLifecyclePanel — legal transitions only', () => {
  it('offers only the transitions legal from the current status', () => {
    renderPanel({ currentStatus: 'submitted' })
    expect(screen.getByRole('button', { name: 'Triaged' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Withdrawn' })).toBeInTheDocument()
    // under_review is not reachable directly from submitted.
    expect(screen.queryByRole('button', { name: 'Under review' })).not.toBeInTheDocument()
  })

  it('offers nothing further once a case is terminal', () => {
    renderPanel({ currentStatus: 'resolved' })
    expect(screen.getByText(/this case is closed/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resolved' })).not.toBeInTheDocument()
  })

  it('hides the assign/release controls once a case is terminal', () => {
    renderPanel({ currentStatus: 'withdrawn', assignment: null, isAssigned: false })
    expect(screen.queryByRole('button', { name: /assign to me/i })).not.toBeInTheDocument()
  })

  it('disables a transition that requires an assignment when unassigned', () => {
    renderPanel({ currentStatus: 'triaged', isAssigned: false })
    expect(screen.getByRole('button', { name: 'Under review' })).toBeDisabled()
    expect(screen.getByText(/assign the case to yourself/i)).toBeInTheDocument()
  })

  it('enables the same transition once assigned', () => {
    renderPanel({ currentStatus: 'triaged', isAssigned: true })
    expect(screen.getByRole('button', { name: 'Under review' })).toBeEnabled()
  })
})

describe('CaseLifecyclePanel — the transition form', () => {
  it('requires a remark for every transition except the first acknowledgement', async () => {
    const u = user()
    const { onTransition } = renderPanel({ currentStatus: 'submitted' })
    await u.click(screen.getByRole('button', { name: 'Withdrawn' }))

    const confirm = screen.getByRole('button', { name: /confirm/i })
    expect(confirm).toBeDisabled()
    expect(onTransition).not.toHaveBeenCalled()
  })

  it('does not require a remark to acknowledge (submitted → triaged)', async () => {
    const u = user()
    renderPanel({ currentStatus: 'submitted' })
    await u.click(screen.getByRole('button', { name: 'Triaged' }))
    expect(screen.getByRole('button', { name: /confirm/i })).toBeEnabled()
  })

  it('requires a resolution reason for a terminal transition', async () => {
    const u = user()
    renderPanel({ currentStatus: 'submitted' })
    await u.click(screen.getByRole('button', { name: 'Withdrawn' }))
    await u.type(
      screen.getByLabelText(/reason for this update/i),
      'Reporter asked to withdraw.',
    )

    expect(screen.getByRole('button', { name: /confirm/i })).toBeDisabled()
    expect(screen.getByLabelText(/resolution reason/i)).toBeInTheDocument()
  })

  it('only offers resolution reasons that fit the target status', async () => {
    const u = user()
    renderPanel({ currentStatus: 'submitted' })
    await u.click(screen.getByRole('button', { name: 'Withdrawn' }))

    const select = screen.getByLabelText(/resolution reason/i)
    const optionLabels = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent)
    expect(optionLabels).toContain('Reporter withdrew the report')
    expect(optionLabels).not.toContain('Duplicate of an existing case')
  })

  it('submits the transition with remark, reason, and visibility', async () => {
    const u = user()
    const { onTransition } = renderPanel({ currentStatus: 'submitted' })
    await u.click(screen.getByRole('button', { name: 'Withdrawn' }))
    await u.type(
      screen.getByLabelText(/reason for this update/i),
      'Reporter called to withdraw.',
    )
    await u.selectOptions(screen.getByLabelText(/resolution reason/i), 'withdrawn_by_reporter')
    await u.click(screen.getByRole('button', { name: /confirm/i }))

    expect(onTransition).toHaveBeenCalledWith('withdrawn', {
      remark: 'Reporter called to withdraw.',
      resolutionReason: 'withdrawn_by_reporter',
      visibleToReporter: true,
    })
  })

  it('lets a responder mark an update as internal-only', async () => {
    const u = user()
    const { onTransition } = renderPanel({ currentStatus: 'submitted' })
    await u.click(screen.getByRole('button', { name: 'Triaged' }))
    await u.click(screen.getByLabelText(/show this update to the reporter/i))
    await u.click(screen.getByRole('button', { name: /confirm/i }))

    expect(onTransition).toHaveBeenCalledWith('triaged', {
      remark: undefined,
      resolutionReason: undefined,
      visibleToReporter: false,
    })
  })

  it('cancels without calling onTransition', async () => {
    const u = user()
    const { onTransition } = renderPanel({ currentStatus: 'submitted' })
    await u.click(screen.getByRole('button', { name: 'Triaged' }))
    await u.click(screen.getByRole('button', { name: /cancel/i }))

    expect(screen.queryByRole('button', { name: /confirm/i })).not.toBeInTheDocument()
    expect(onTransition).not.toHaveBeenCalled()
  })
})

describe('CaseLifecyclePanel — status history', () => {
  it('lists every transition with when and why', async () => {
    const u = user()
    renderPanel({
      history: [
        ...HISTORY,
        {
          history_id: 2,
          from_status: 'submitted',
          to_status: 'triaged',
          changed_at: '2026-08-11T15:00:00+00:00',
          remark: 'Looking into it.',
          visible_to_reporter: true,
          resolution_reason: null,
        },
      ],
    })
    await u.click(screen.getByText(/status history/i))

    expect(screen.getByText('Looking into it.')).toBeInTheDocument()
  })

  it('marks an internal-only entry as such', async () => {
    const u = user()
    renderPanel({
      history: [{ ...HISTORY[0], visible_to_reporter: false }],
    })
    await u.click(screen.getByText(/status history/i))
    expect(screen.getByText(/internal only/i)).toBeInTheDocument()
  })

  it('shows the resolution reason on a terminal entry', async () => {
    const u = user()
    renderPanel({
      currentStatus: 'resolved',
      history: [
        ...HISTORY,
        {
          history_id: 2,
          from_status: 'under_review',
          to_status: 'resolved',
          changed_at: '2026-08-12T09:00:00+00:00',
          remark: 'Addressed with both parties.',
          visible_to_reporter: true,
          resolution_reason: 'action_taken',
        },
      ],
    })
    await u.click(screen.getByText(/status history/i))
    expect(screen.getByText(/action was taken/i)).toBeInTheDocument()
  })
})
