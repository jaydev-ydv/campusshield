import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AppRoutes } from '../App'
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

describe('ProtectedRoute', () => {
  it('sends a signed-out visitor to sign in', async () => {
    renderWithAuth(<AppRoutes />, { route: '/dashboard' })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    })
    expect(screen.queryByText(/your overview/i)).not.toBeInTheDocument()
  })

  it('shows a session check rather than flashing the wrong screen', () => {
    renderWithAuth(<AppRoutes />, { route: '/dashboard' })
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('renders the protected page for an authenticated user', async () => {
    setCurrentUser(makeUser())
    renderWithAuth(<AppRoutes />, {
      route: '/dashboard',
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    expect(await screen.findByRole('heading', { name: /your overview/i })).toBeInTheDocument()
  })

  it('offers to finish setup instead of looping a signed-in user back to sign in', async () => {
    setCurrentUser(makeUser())
    renderWithAuth(<AppRoutes />, {
      route: '/dashboard',
      fetchImpl: createFetchStub({
        '/auth/me': {
          status: 401,
          body: {
            error: { code: 'UNAUTHENTICATED', message: 'No account.', request_id: 'r' },
          },
        },
      }),
    })

    expect(
      await screen.findByRole('heading', { name: /finish setting up your account/i }),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument()
  })

  it('provisions the account from the setup gate', async () => {
    const user = userEvent.setup()
    setCurrentUser(makeUser())
    const fetchStub = createFetchStub({
      '/auth/me': {
        status: 401,
        body: { error: { code: 'UNAUTHENTICATED', message: 'No account.', request_id: 'r' } },
      },
      '/auth/register': { status: 201, body: { ...ACCOUNT, created: true } },
      '/locations': { body: { items: LOCATIONS } },
      '/categories': { body: { items: CATEGORIES } },
    })
    renderWithAuth(<AppRoutes />, { route: '/dashboard', fetchImpl: fetchStub })

    await user.click(
      await screen.findByRole('button', { name: /create my campusshield account/i }),
    )

    expect(await screen.findByRole('heading', { name: /your overview/i })).toBeInTheDocument()
  })

  it('keeps a signed-in user away from the sign-in page', async () => {
    setCurrentUser(makeUser())
    renderWithAuth(<AppRoutes />, {
      route: '/login',
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    expect(await screen.findByRole('heading', { name: /your overview/i })).toBeInTheDocument()
  })

  it('keeps a signed-in user away from registration', async () => {
    setCurrentUser(makeUser())
    renderWithAuth(<AppRoutes />, {
      route: '/register',
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    expect(await screen.findByRole('heading', { name: /your overview/i })).toBeInTheDocument()
  })

  it('lets a signed-out visitor reach sign-in and registration', async () => {
    const { unmount } = renderWithAuth(<AppRoutes />, { route: '/login' })
    expect(await screen.findByRole('heading', { name: /^sign in$/i })).toBeInTheDocument()
    unmount()

    renderWithAuth(<AppRoutes />, { route: '/register' })
    expect(
      await screen.findByRole('heading', { name: /create your account/i }),
    ).toBeInTheDocument()
  })

  it('returns the user to where they were headed after signing in', async () => {
    const user = userEvent.setup()
    renderWithAuth(<AppRoutes />, {
      route: '/dashboard',
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    await waitFor(() => expect(screen.getByLabelText(/email address/i)).toBeInTheDocument())
    await user.type(screen.getByLabelText(/email address/i), ACCOUNT.email)
    await user.type(screen.getByLabelText(/^password$/i), 'password123')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('heading', { name: /your overview/i })).toBeInTheDocument()
  })

  it('shows an unknown address as not found', async () => {
    renderWithAuth(<AppRoutes />, { route: '/no-such-page' })
    expect(await screen.findByRole('heading', { name: /page not found/i })).toBeInTheDocument()
  })

  it('redirects staff navigating to student-only route /report to /incidents', async () => {
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
            items: [],
            pagination: { total: 0, limit: 100, offset: 0, returned: 0 },
            mapping_available: false,
          },
        },
      }),
    )

    renderWithAuth(<AppRoutes />, { route: '/report', fetchImpl: fetchStub })

    // Redirected away from student wizard to /incidents
    expect(
      await screen.findByRole('heading', { name: /active incidents/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /what happened\?/i })).not.toBeInTheDocument()
  })

  it('redirects students navigating to staff-only route /incidents to /dashboard', async () => {
    setCurrentUser(makeUser())
    const fetchStub = createFetchStub(signedInRoutes())

    renderWithAuth(<AppRoutes />, { route: '/incidents', fetchImpl: fetchStub })

    // Redirected away from incidents queue to /dashboard
    expect(await screen.findByRole('heading', { name: /your overview/i })).toBeInTheDocument()
  })
})
