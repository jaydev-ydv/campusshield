import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { AppRoutes } from '../../App'
import { createFetchStub, makeUser, renderWithAuth, signedInRoutes } from '../../test/harness'
import { setCurrentUser } from '../../test/firebaseMock'

function renderShell(unreadCount = 0) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(
    signedInRoutes({
      '/notifications': {
        body: {
          items: [],
          pagination: { total: 0, limit: 1, offset: 0, returned: 0 },
          unread_count: unreadCount,
        },
      },
    }),
  )
  const view = renderWithAuth(<AppRoutes />, { route: '/dashboard', fetchImpl })
  return { ...view, fetchImpl }
}

describe('Navigation — notification bell', () => {
  // Rendered twice — once for the desktop bar, once for the mobile header —
  // and CSS (`hidden`/`sm:hidden`) is what hides one of them for a real
  // viewport. jsdom does not evaluate media queries, so both are present in
  // the tree here; every assertion below accounts for that deliberately,
  // the same way this file's `sign out` button duplication is handled
  // elsewhere in the suite.

  it('links to the notification inbox', async () => {
    renderShell()
    const bells = await screen.findAllByRole('link', { name: /notifications/i })
    expect(bells.length).toBeGreaterThan(0)
    for (const bell of bells) expect(bell).toHaveAttribute('href', '/notifications')
  })

  it('polls the unread count with a bearer token', async () => {
    const { fetchImpl } = renderShell()
    await waitFor(() => {
      expect(fetchImpl.calls.some((c) => c.url.includes('/notifications'))).toBe(true)
    })
    expect(fetchImpl.headersFor('/notifications')?.get('Authorization')).toMatch(/^Bearer /)
  })

  it('shows no badge when nothing is unread', async () => {
    renderShell(0)
    await waitFor(() => {
      expect(
        screen.getAllByRole('link', { name: /notifications, none unread/i }).length,
      ).toBeGreaterThan(0)
    })
    expect(screen.queryByText(/^[1-9]/)).not.toBeInTheDocument()
  })

  it('shows the unread count as a badge', async () => {
    renderShell(3)
    await waitFor(() => {
      expect(
        screen.getAllByRole('link', { name: /notifications, 3 unread/i }).length,
      ).toBeGreaterThan(0)
    })
    expect(screen.getAllByText('3').length).toBeGreaterThan(0)
  })

  it('caps a large unread count at "9+" rather than growing the badge', async () => {
    renderShell(42)
    await waitFor(() => {
      expect(
        screen.getAllByRole('link', { name: /notifications, 42 unread/i }).length,
      ).toBeGreaterThan(0)
    })
    expect(screen.getAllByText('9+').length).toBeGreaterThan(0)
  })
})

describe('Navigation — role-based links', () => {
  it('renders student navigation with Overview, Report, My reports, Account, and SOS', async () => {
    setCurrentUser(makeUser())
    const fetchImpl = createFetchStub(
      signedInRoutes({
        '/auth/me': {
          body: {
            user_id: '11111111-1111-1111-1111-111111111111',
            email: 'student@example.edu',
            role: 'student',
            is_active: true,
            display_name: null,
          },
        },
      }),
    )
    renderWithAuth(<AppRoutes />, { route: '/dashboard', fetchImpl })

    expect(await screen.findByRole('heading', { name: /your overview/i })).toBeInTheDocument()

    // Links present for students
    expect(screen.getAllByRole('link', { name: /^overview$/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /^report$/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /^my reports$/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /^account$/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: /emergency sos/i }).length).toBeGreaterThan(0)

    // Incidents is not shown in student navigation
    expect(screen.queryByRole('link', { name: /^incidents$/i })).not.toBeInTheDocument()
  })

  it('renders staff navigation with Overview, Incidents, Account and hides student reporting & SOS for security', async () => {
    setCurrentUser(makeUser({ email: 'security@example.edu' }))
    const fetchImpl = createFetchStub(
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
            items: [],
            pagination: { total: 0, limit: 100, offset: 0, returned: 0 },
            mapping_available: false,
          },
        },
      }),
    )
    renderWithAuth(<AppRoutes />, { route: '/dashboard', fetchImpl })

    expect(
      await screen.findByRole('heading', { name: /security overview/i }),
    ).toBeInTheDocument()

    // Links present for staff
    expect(screen.getAllByRole('link', { name: /^overview$/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /^incidents$/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /^account$/i }).length).toBeGreaterThan(0)

    // Student-only links NOT present for staff
    expect(screen.queryByRole('link', { name: /^report$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^my reports$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /emergency sos/i })).not.toBeInTheDocument()
  })

  it('renders staff navigation and hides student options for ICC staff', async () => {
    setCurrentUser(makeUser({ email: 'icc@example.edu' }))
    const fetchImpl = createFetchStub(
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
    renderWithAuth(<AppRoutes />, { route: '/dashboard', fetchImpl })

    expect(await screen.findByRole('heading', { name: /icc overview/i })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /^incidents$/i }).length).toBeGreaterThan(0)
    expect(screen.queryByRole('link', { name: /^report$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^my reports$/i })).not.toBeInTheDocument()
  })

  it('renders staff navigation and hides student options for admin', async () => {
    setCurrentUser(makeUser({ email: 'admin@example.edu' }))
    const fetchImpl = createFetchStub(
      signedInRoutes({
        '/auth/me': {
          body: {
            user_id: '44444444-4444-4444-4444-444444444444',
            email: 'admin@example.edu',
            role: 'admin',
            is_active: true,
            display_name: 'Administrator',
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
    renderWithAuth(<AppRoutes />, { route: '/dashboard', fetchImpl })

    expect(await screen.findByRole('heading', { name: /admin overview/i })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /^incidents$/i }).length).toBeGreaterThan(0)
    expect(screen.queryByRole('link', { name: /^report$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /^my reports$/i })).not.toBeInTheDocument()
  })
})
