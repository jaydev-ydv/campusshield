import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { useAuth } from './useAuth'
import {
  ACCOUNT,
  createFetchStub,
  makeUser,
  renderWithAuth,
  signedInRoutes,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'

/** Surfaces the context so its transitions can be asserted directly. */
function AuthProbe() {
  const { status, account, logout } = useAuth()
  return (
    <div>
      <p data-testid="status">{status}</p>
      <p data-testid="email">{account?.email ?? 'none'}</p>
      <p data-testid="role">{account?.role ?? 'none'}</p>
      <button onClick={() => void logout()}>Sign out</button>
    </div>
  )
}

describe('AuthContext', () => {
  it('starts by resolving the session rather than assuming signed-out', async () => {
    // Rendering a sign-in page for a user who is already signed in is a visible
    // flash of the wrong screen on every page load.
    renderWithAuth(<AuthProbe />)
    expect(screen.getByTestId('status')).toHaveTextContent('initialising')
  })

  it('settles on signed-out when nobody is signed in', async () => {
    renderWithAuth(<AuthProbe />)
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-out'))
  })

  it('loads the application account for a signed-in user', async () => {
    setCurrentUser(makeUser())
    renderWithAuth(<AuthProbe />, { fetchImpl: createFetchStub(signedInRoutes()) })

    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated'),
    )
    expect(screen.getByTestId('email')).toHaveTextContent(ACCOUNT.email)
  })

  it('takes the role from the server, not from the Firebase user', async () => {
    // The Firebase user carries a made-up admin claim; the account endpoint says
    // student. The server wins, because it is the only trustworthy answer.
    setCurrentUser(makeUser({ role: 'admin' } as never))
    renderWithAuth(<AuthProbe />, {
      fetchImpl: createFetchStub(signedInRoutes({ '/auth/me': { body: ACCOUNT } })),
    })

    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('student'))
  })

  it('reflects a role change on the next load', async () => {
    setCurrentUser(makeUser())
    renderWithAuth(<AuthProbe />, {
      fetchImpl: createFetchStub(
        signedInRoutes({ '/auth/me': { body: { ...ACCOUNT, role: 'icc' } } }),
      ),
    })

    await waitFor(() => expect(screen.getByTestId('role')).toHaveTextContent('icc'))
  })

  it('reports needs-provisioning when Firebase knows the user but the API does not', async () => {
    // A real state, not an error: it is where someone lands between creating a
    // credential and having an application account.
    setCurrentUser(makeUser())
    renderWithAuth(<AuthProbe />, {
      fetchImpl: createFetchStub({
        '/auth/me': {
          status: 401,
          body: {
            error: {
              code: 'UNAUTHENTICATED',
              message: 'No account exists for these credentials.',
              request_id: 'r',
            },
          },
        },
      }),
    })

    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('needs-provisioning'),
    )
  })

  it('clears the account on sign out', async () => {
    const user = userEvent.setup()
    setCurrentUser(makeUser())
    renderWithAuth(<AuthProbe />, { fetchImpl: createFetchStub(signedInRoutes()) })

    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated'),
    )

    await user.click(screen.getByRole('button', { name: /sign out/i }))

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed-out'))
    // Cleared in the same tick, so no screen renders one user's details for even
    // a frame after another signs out.
    expect(screen.getByTestId('email')).toHaveTextContent('none')
    expect(screen.getByTestId('role')).toHaveTextContent('none')
  })

  it('calls Firebase signOut so the credential is actually released', async () => {
    const user = userEvent.setup()
    setCurrentUser(makeUser())
    renderWithAuth(<AuthProbe />, { fetchImpl: createFetchStub(signedInRoutes()) })

    await waitFor(() =>
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated'),
    )
    await user.click(screen.getByRole('button', { name: /sign out/i }))

    const { signOut } = await import('firebase/auth')
    expect(signOut).toHaveBeenCalled()
  })

  it('reports unavailable when Firebase is not configured', async () => {
    // Without an injected Auth instance and with no VITE_FIREBASE_* variables,
    // this is what a fresh checkout without a .env file looks like.
    const { render } = await import('@testing-library/react')
    const { AuthProvider } = await import('./AuthContext')
    const { MemoryRouter } = await import('react-router-dom')

    render(
      <AuthProvider>
        <MemoryRouter>
          <AuthProbe />
        </MemoryRouter>
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unavailable'))
  })
})
