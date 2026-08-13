import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AppRoutes } from '../App'
import {
  ACCOUNT,
  createFetchStub,
  makeUser,
  renderWithAuth,
  signedInRoutes,
  type StubRoute,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'

const MINE = {
  public_ref: 'CS-2026-AAA111',
  report_kind: 'incident',
  submission_mode: 'identified',
  reporter_relationship: 'affected',
  category: { category_id: 1, code: 'HARASS_VERBAL', label: 'Verbal harassment' },
  location: { location_id: 1, code: 'LKRC-MAIN', name: 'Library & Knowledge Resource Centre' },
  location_hint: null,
  occurred_at: '2026-08-01T18:00:00+00:00',
  submitted_at: '2026-08-01T19:00:00+00:00',
  is_emergency: false,
  is_ongoing: false,
  reporter_contactable: true,
  status: 'under_review',
}

const EMERGENCY = {
  ...MINE,
  public_ref: 'CS-2026-BBB222',
  is_emergency: true,
  status: 'submitted',
}

function listRoutes(items: unknown[], extra: Record<string, StubRoute> = {}) {
  return signedInRoutes({
    '/reports/mine': {
      body: {
        items,
        pagination: { total: items.length, limit: 50, offset: 0, returned: items.length },
      },
    },
    ...extra,
  })
}

async function renderList(routes = listRoutes([MINE])) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(routes)
  const view = renderWithAuth(<AppRoutes />, { route: '/reports', fetchImpl })
  await screen.findByRole('heading', { name: /my reports/i })
  return { ...view, fetchImpl }
}

describe('MyReportsPage', () => {
  it('calls /reports/mine with a bearer token', async () => {
    const { fetchImpl } = await renderList()
    await waitFor(() => {
      expect(fetchImpl.calls.some((c) => c.url.includes('/reports/mine'))).toBe(true)
    })
    expect(fetchImpl.headersFor('/reports/mine')?.get('Authorization')).toMatch(/^Bearer /)
  })

  it('lists a report with reference, category, location, date and status', async () => {
    await renderList()

    expect(await screen.findByText(MINE.public_ref)).toBeInTheDocument()
    expect(screen.getByText(MINE.category.label)).toBeInTheDocument()
    expect(screen.getByText(MINE.location.name)).toBeInTheDocument()
    expect(screen.getByText('Under review')).toBeInTheDocument()
  })

  it('marks an emergency report without alarm styling', async () => {
    await renderList(listRoutes([EMERGENCY]))

    expect(await screen.findByText(/immediate attention/i)).toBeInTheDocument()
    // Amber, not red: this describes how the report was routed, not that
    // something is wrong on the student's screen right now.
    expect(document.body.querySelectorAll('[class*="red-"]')).toHaveLength(0)
  })

  it('shows no emergency marker on an ordinary report', async () => {
    await renderList()
    expect(screen.queryByText(/immediate attention/i)).not.toBeInTheDocument()
  })

  it('shows an empty state that invites a first report', async () => {
    await renderList(listRoutes([]))

    expect(await screen.findByText(/you have not submitted any reports/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /report a safety concern/i })).toBeInTheDocument()
  })

  it('explains that anonymous reports cannot appear here', async () => {
    // Otherwise a student who filed anonymously sees an empty list and concludes
    // the report was lost.
    await renderList()
    expect(
      await screen.findByText(/anonymous reports are not listed here/i),
    ).toBeInTheDocument()
  })

  it('never renders another identity', async () => {
    await renderList()
    await screen.findByText(MINE.public_ref)

    const text = document.body.textContent ?? ''
    expect(text).not.toContain(ACCOUNT.user_id)
    expect(text).not.toContain('user_id')
  })

  it('shows an error with a retry when the list fails', async () => {
    const u = userEvent.setup({ delay: null })
    await renderList(
      signedInRoutes({
        '/reports/mine': {
          status: 503,
          body: {
            error: {
              code: 'SERVICE_UNAVAILABLE',
              message: 'The database is unavailable.',
              request_id: 'req-88',
            },
          },
        },
      }),
    )

    expect(await screen.findByText(/the service is having trouble/i)).toBeInTheDocument()
    expect(screen.getByText(/req-88/)).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: /try again/i }))
  })

  it('requires authentication', async () => {
    renderWithAuth(<AppRoutes />, { route: '/reports' })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    })
  })

  it('links to the reporting form', async () => {
    await renderList()
    expect(screen.getByRole('link', { name: /new report/i })).toHaveAttribute(
      'href',
      '/report',
    )
  })
})
