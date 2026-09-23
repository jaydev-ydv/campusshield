import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { DashboardPage } from './DashboardPage'
import {
  ACCOUNT,
  CATEGORIES,
  LOCATIONS,
  createFetchStub,
  makeUser,
  renderWithAuth,
  signedInRoutes,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'

function renderDashboard(routes = signedInRoutes()) {
  setCurrentUser(makeUser())
  return renderWithAuth(<DashboardPage />, {
    route: '/dashboard',
    fetchImpl: createFetchStub(routes),
  })
}

describe('DashboardPage', () => {
  it('shows the signed-in email and the role from the server', async () => {
    renderDashboard()

    // Appears twice by design: in the navigation and on the account card.
    expect((await screen.findAllByText(ACCOUNT.email)).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Student').length).toBeGreaterThan(0)
  })

  it('shows authentication status', async () => {
    renderDashboard()
    expect(await screen.findByText(/verified and active/i)).toBeInTheDocument()
  })

  it('shows counts from the real catalogue endpoints', async () => {
    renderDashboard()

    await waitFor(() => {
      const locations = screen.getByText('Campus locations').closest('div')!
      expect(within(locations).getByText(String(LOCATIONS.length))).toBeInTheDocument()
    })
    const incidents = CATEGORIES.filter((c) => c.kind === 'incident').length
    const concerns = CATEGORIES.filter((c) => c.kind === 'concern').length
    expect(screen.getByText('Incident types').closest('div')).toHaveTextContent(
      String(incidents),
    )
    expect(screen.getByText('Safety concerns').closest('div')).toHaveTextContent(
      String(concerns),
    )
  })

  it('calls both catalogue endpoints with a bearer token', async () => {
    setCurrentUser(makeUser())
    const fetchStub = createFetchStub(signedInRoutes())
    renderWithAuth(<DashboardPage />, { route: '/dashboard', fetchImpl: fetchStub })

    await waitFor(() => {
      expect(fetchStub.calls.some((c) => c.url.includes('/locations'))).toBe(true)
      expect(fetchStub.calls.some((c) => c.url.includes('/categories'))).toBe(true)
    })
    expect(fetchStub.headersFor('/locations')?.get('Authorization')).toMatch(/^Bearer /)
    expect(fetchStub.headersFor('/categories')?.get('Authorization')).toMatch(/^Bearer /)
  })

  it('treats an empty location list as a state, not a failure', async () => {
    // `GET /locations` legitimately returns nothing: no campus coordinates have
    // been verified yet. Rendering that as an error would send someone debugging
    // a system working exactly as intended.
    renderDashboard(signedInRoutes({ '/locations': { body: { items: [] } } }))

    expect(
      await screen.findByText(/campus locations are not published yet/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument()
  })

  it('shows an error with a retry when a catalogue call fails', async () => {
    const user = userEvent.setup()
    renderDashboard(
      signedInRoutes({
        '/locations': {
          status: 503,
          body: {
            error: {
              code: 'SERVICE_UNAVAILABLE',
              message: 'The database is unavailable.',
              request_id: 'req-77',
            },
          },
        },
      }),
    )

    expect(await screen.findByText(/the service is having trouble/i)).toBeInTheDocument()
    expect(screen.getByText(/req-77/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /try again/i }))
  })

  it('still shows categories when only locations fail', async () => {
    // One failing endpoint must not blank the other panel.
    renderDashboard(
      signedInRoutes({
        '/locations': {
          status: 500,
          body: { error: { code: 'INTERNAL_ERROR', message: 'boom', request_id: 'r' } },
        },
      }),
    )

    await waitFor(() => {
      expect(screen.getByText('Incident types').closest('div')).toHaveTextContent('1')
    })
  })

  it('explains that the role is not something the browser controls', async () => {
    renderDashboard()
    expect(
      await screen.findByText(/not something this browser can change/i),
    ).toBeInTheDocument()
  })

  it('states that student names are not stored', async () => {
    renderDashboard()
    expect(await screen.findByText(/student names are not stored/i)).toBeInTheDocument()
  })

  it('offers sign out from the navigation', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /sign out/i }).length).toBeGreaterThan(0)
    })
  })

  it('signs the user out when asked', async () => {
    const user = userEvent.setup()
    renderDashboard()

    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /sign out/i })[0]).toBeVisible(),
    )
    await user.click(screen.getAllByRole('button', { name: /sign out/i })[0])

    const { signOut } = await import('firebase/auth')
    await waitFor(() => expect(signOut).toHaveBeenCalled())
  })

  it('provides a skip link for keyboard users', async () => {
    renderDashboard()
    expect(
      await screen.findByRole('link', { name: /skip to main content/i }),
    ).toBeInTheDocument()
  })

  it('does not shout at the user', async () => {
    // The product is meant to feel calm: an interface that shouts raises the
    // cost of opening it at exactly the wrong moment.
    //
    // What is checked is alarm *styling*, not the word "danger" — the footer's
    // "In immediate danger, contact campus security" is calm, factual safety
    // information and belongs there. The anti-patterns are exclamation marks,
    // shouted capitals, and siren iconography.
    renderDashboard()
    await screen.findByRole('heading', { name: /your overview/i })

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/!/)
    expect(text).not.toMatch(/\b(URGENT|WARNING|ALERT|DANGER)\b/)
    expect(text).not.toMatch(/🚨|⚠️|🆘/)
  })

  it('reserves red for genuine errors', async () => {
    // Emergency reporting is a calm, clearly-labelled choice, not a red panic
    // button. Red appearing anywhere else erodes what it means when it does.
    renderDashboard()
    await screen.findByRole('heading', { name: /your overview/i })

    const red = document.body.querySelectorAll('[class*="red-"]')
    expect(red.length).toBe(0)
  })

  it('renders operational responder dashboard for security role', async () => {
    setCurrentUser(makeUser({ email: 'security@example.edu' }))
    const fetchStub = createFetchStub(
      signedInRoutes({
        '/auth/me': {
          body: {
            user_id: '22222222-2222-2222-2222-222222222222',
            email: 'security@example.edu',
            role: 'security',
            is_active: true,
            display_name: 'Officer Smith',
          },
        },
        '/incidents': {
          body: {
            items: [
              {
                public_ref: 'SEC-101',
                report_kind: 'incident',
                submission_mode: 'identified',
                category: { category_id: 1, code: 'SEC_TRESPASS', label: 'Trespassing' },
                location: {
                  location_id: 1,
                  code: 'MAIN-GATE',
                  name: 'Main Gate',
                  latitude: null,
                  longitude: null,
                  is_mapped: false,
                  is_synthetic: false,
                },
                location_hint: null,
                occurred_at: new Date().toISOString(),
                submitted_at: new Date().toISOString(),
                is_emergency: true,
                is_ongoing: true,
                status: 'submitted',
                reporter_contactable: true,
                evidence_count: 0,
                location_signal: null,
                dispatch: {
                  dispatch_id: 'disp-1',
                  state: 'pending',
                  raised_at: new Date().toISOString(),
                  acknowledged_at: null,
                  dispatched_at: null,
                  on_scene_at: null,
                  closed_at: null,
                  responder_note: null,
                },
                is_assigned: false,
              },
            ],
            pagination: { total: 1, limit: 100, offset: 0, returned: 1 },
            mapping_available: false,
          },
        },
      }),
    )

    renderWithAuth(<DashboardPage />, { route: '/dashboard', fetchImpl: fetchStub })

    expect(
      await screen.findByRole('heading', { name: /security overview/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /take action toward incidents/i }),
    ).toBeInTheDocument()
    expect(await screen.findByText('#SEC-101')).toBeInTheDocument()
    expect(screen.getByText('Active incidents')).toBeInTheDocument()
    expect(screen.getByText('Emergency alerts')).toBeInTheDocument()
    expect(screen.getByText('Trespassing')).toBeInTheDocument()

    // Student CTA is absent
    expect(screen.queryByText(/report a safety concern/i)).not.toBeInTheDocument()
  })

  it('renders confidential case dashboard for ICC role', async () => {
    setCurrentUser(makeUser({ email: 'icc@example.edu' }))
    const fetchStub = createFetchStub(
      signedInRoutes({
        '/auth/me': {
          body: {
            user_id: '33333333-3333-3333-3333-333333333333',
            email: 'icc@example.edu',
            role: 'icc',
            is_active: true,
            display_name: 'Dr. Committee',
          },
        },
        '/incidents': {
          body: {
            items: [],
            pagination: { total: 0, limit: 100, offset: 0, returned: 0 },
            mapping_available: false,
          },
        },
      }),
    )

    renderWithAuth(<DashboardPage />, { route: '/dashboard', fetchImpl: fetchStub })

    expect(await screen.findByRole('heading', { name: /icc overview/i })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /take action toward incidents/i }),
    ).toBeInTheDocument()
    expect(await screen.findByText(/no open incidents in your queue/i)).toBeInTheDocument()
    expect(screen.getByText('Active cases')).toBeInTheDocument()
    expect(screen.getByText('Under review')).toBeInTheDocument()

    // Student CTA is absent
    expect(screen.queryByText(/report a safety concern/i)).not.toBeInTheDocument()
  })

  it('renders operational oversight dashboard for admin role', async () => {
    setCurrentUser(makeUser({ email: 'admin@example.edu' }))
    const fetchStub = createFetchStub(
      signedInRoutes({
        '/auth/me': {
          body: {
            user_id: '44444444-4444-4444-4444-444444444444',
            email: 'admin@example.edu',
            role: 'admin',
            is_active: true,
            display_name: 'Admin User',
          },
        },
        '/incidents': {
          body: {
            items: [],
            pagination: { total: 0, limit: 100, offset: 0, returned: 0 },
            mapping_available: false,
          },
        },
      }),
    )

    renderWithAuth(<DashboardPage />, { route: '/dashboard', fetchImpl: fetchStub })

    expect(await screen.findByRole('heading', { name: /admin overview/i })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /take action toward incidents/i }),
    ).toBeInTheDocument()
    expect(await screen.findByText(/no open incidents in your queue/i)).toBeInTheDocument()
    expect(screen.getByText('Cases under review')).toBeInTheDocument()

    // Student CTA is absent
    expect(screen.queryByText(/report a safety concern/i)).not.toBeInTheDocument()
  })
})
