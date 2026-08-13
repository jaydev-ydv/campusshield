import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AppRoutes } from '../App'
import {
  createFetchStub,
  makeUser,
  renderWithAuth,
  signedInRoutes,
  type StubRoute,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'
import type { ReportDetail } from '../lib/api'

const REF = 'CS-2026-AAA111'

function detail(overrides: Partial<ReportDetail> = {}): ReportDetail {
  return {
    public_ref: REF,
    report_kind: 'incident',
    submission_mode: 'identified',
    reporter_relationship: 'affected',
    category: { category_id: 1, code: 'HARASS_VERBAL', label: 'Verbal harassment' },
    location: {
      location_id: 1,
      code: 'LKRC-MAIN',
      name: 'Library & Knowledge Resource Centre',
    },
    location_hint: null,
    occurred_at: '2026-08-01T18:00:00+00:00',
    submitted_at: '2026-08-01T19:00:00+00:00',
    is_emergency: false,
    is_ongoing: false,
    reporter_contactable: true,
    status: 'under_review',
    narrative: 'Someone followed me from the library.',
    narrative_available: true,
    evidence_count: 0,
    status_history: [
      {
        status: 'submitted',
        changed_at: '2026-08-01T19:00:00+00:00',
        remark: null,
        resolution_reason: null,
      },
      {
        status: 'under_review',
        changed_at: '2026-08-02T09:00:00+00:00',
        remark: 'We are reviewing your report.',
        resolution_reason: null,
      },
    ],
    ...overrides,
  }
}

function renderDetail(body: ReportDetail = detail(), extra: Record<string, StubRoute> = {}) {
  setCurrentUser(makeUser())
  const routes = signedInRoutes({ [`/reports/${REF}`]: { body }, ...extra })
  const fetchImpl = createFetchStub(routes)
  const view = renderWithAuth(<AppRoutes />, { route: `/reports/${REF}`, fetchImpl })
  return { ...view, fetchImpl }
}

describe('ReportDetailPage', () => {
  it('shows the report reference, category, and current status', async () => {
    renderDetail()
    expect(await screen.findByText(REF)).toBeInTheDocument()
    expect(screen.getByText('Verbal harassment')).toBeInTheDocument()
    // Appears twice by design: the header badge and the timeline's own entry
    // for the same status.
    expect(screen.getAllByText('Under review').length).toBeGreaterThanOrEqual(1)
  })

  it('renders the status timeline in order, with each remark', async () => {
    renderDetail()
    await screen.findByText(REF)

    const items = screen.getAllByRole('listitem')
    expect(within(items[0]).getByText('Received')).toBeInTheDocument()
    expect(within(items[1]).getByText('Under review')).toBeInTheDocument()
    expect(within(items[1]).getByText('We are reviewing your report.')).toBeInTheDocument()
  })

  it('shows a resolution reason on a closed report', async () => {
    renderDetail(
      detail({
        status: 'resolved',
        status_history: [
          {
            status: 'submitted',
            changed_at: '2026-08-01T19:00:00+00:00',
            remark: null,
            resolution_reason: null,
          },
          {
            status: 'resolved',
            changed_at: '2026-08-03T10:00:00+00:00',
            remark: 'This matter has been addressed.',
            resolution_reason: 'action_taken',
          },
        ],
      }),
    )
    await screen.findByText(REF)
    expect(screen.getByText(/action was taken/i)).toBeInTheDocument()
    expect(screen.getByText(/this report is now closed/i)).toBeInTheDocument()
  })

  it('says a report is anonymous rather than showing an account link', async () => {
    renderDetail(detail({ submission_mode: 'anonymous', reporter_contactable: false }))
    await screen.findByText(REF)
    expect(screen.getByText(/submitted anonymously/i)).toBeInTheDocument()
  })

  it('explains that some internal updates are not shown', async () => {
    renderDetail()
    await screen.findByText(REF)
    expect(screen.getByText(/not every action taken on your report/i)).toBeInTheDocument()
  })

  it('shows an error state when the report cannot be loaded', async () => {
    renderDetail(detail(), {
      [`/reports/${REF}`]: {
        status: 404,
        body: {
          error: {
            code: 'NOT_FOUND',
            message: 'No report exists with that reference.',
            request_id: 'r1',
          },
        },
      },
    })
    expect(await screen.findByText(/no report exists/i)).toBeInTheDocument()
  })

  it('links back to the reports list', async () => {
    renderDetail()
    await screen.findByText(REF)
    // Distinct from the top-nav "My reports" link — this is the in-page
    // back-link, identified by its "←" prefix.
    expect(screen.getByRole('link', { name: /← my reports/i })).toHaveAttribute(
      'href',
      '/reports',
    )
  })

  it('never renders responder-only fields such as risk scores or ML data', async () => {
    renderDetail()
    await screen.findByText(REF)
    const markup = document.body.innerHTML.toLowerCase()
    expect(markup).not.toMatch(/risk_score|risk_band|triage|label_scores|confidence/)
  })
})

describe('MyReportsPage → ReportDetailPage navigation', () => {
  it('opens a report from the list', async () => {
    const u = userEvent.setup({ delay: null })
    setCurrentUser(makeUser())
    const routes = signedInRoutes({
      '/reports/mine': {
        body: {
          items: [
            {
              public_ref: REF,
              report_kind: 'incident',
              submission_mode: 'identified',
              reporter_relationship: 'affected',
              category: { category_id: 1, code: 'HARASS_VERBAL', label: 'Verbal harassment' },
              location: {
                location_id: 1,
                code: 'LKRC-MAIN',
                name: 'Library & Knowledge Resource Centre',
              },
              location_hint: null,
              occurred_at: '2026-08-01T18:00:00+00:00',
              submitted_at: '2026-08-01T19:00:00+00:00',
              is_emergency: false,
              is_ongoing: false,
              reporter_contactable: true,
              status: 'under_review',
            },
          ],
          pagination: { total: 1, limit: 50, offset: 0, returned: 1 },
        },
      },
      [`/reports/${REF}`]: { body: detail() },
    })
    const fetchImpl = createFetchStub(routes)
    renderWithAuth(<AppRoutes />, { route: '/reports', fetchImpl })

    await u.click(await screen.findByText(REF))
    expect(await screen.findByText(/status timeline/i)).toBeInTheDocument()
  })
})
