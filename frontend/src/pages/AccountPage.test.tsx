import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { AppRoutes } from '../App'
import type { AccountIdentity } from '../lib/api'
import {
  ACCOUNT,
  createFetchStub,
  makeUser,
  renderWithAuth,
  signedInRoutes,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'

const ICC_ACCOUNT: AccountIdentity = {
  ...ACCOUNT,
  role: 'icc',
  email: 'icc@example.edu',
  display_name: 'ICC Member',
}

function renderAccount(account: AccountIdentity = ACCOUNT) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(signedInRoutes({ '/auth/me': { body: account } }))
  const view = renderWithAuth(<AppRoutes />, { route: '/account', fetchImpl })
  return { ...view, fetchImpl }
}

describe('AccountPage — student', () => {
  it('shows identity read-only', async () => {
    renderAccount()
    // Appears twice by design: in the navigation and on the identity card.
    expect((await screen.findAllByText(ACCOUNT.email)).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Student').length).toBeGreaterThan(0)
    expect(screen.getByText(/verified and active/i)).toBeInTheDocument()
  })

  it('explains that student accounts do not store a display name', async () => {
    renderAccount()
    expect(
      await screen.findByText(/student accounts do not store a display name/i),
    ).toBeInTheDocument()
  })

  it('offers no editable name field', async () => {
    renderAccount()
    await screen.findAllByText(ACCOUNT.email)
    expect(screen.queryByLabelText(/display name/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save name/i })).not.toBeInTheDocument()
  })
})

describe('AccountPage — staff', () => {
  it('shows the current display name in an editable field', async () => {
    renderAccount(ICC_ACCOUNT)
    const field = await screen.findByLabelText(/display name/i)
    expect(field).toHaveValue('ICC Member')
  })

  it('saves a new display name and confirms it', async () => {
    const user = userEvent.setup({ delay: null })
    const { fetchImpl } = renderAccount(ICC_ACCOUNT)
    const field = await screen.findByLabelText(/display name/i)

    await user.clear(field)
    await user.type(field, 'New ICC Name')
    await user.click(screen.getByRole('button', { name: /save name/i }))

    await waitFor(() => {
      expect(fetchImpl.bodyFor('/auth/me', 'PATCH')).toEqual({ display_name: 'New ICC Name' })
    })
    expect(await screen.findByText(/^saved\.$/i)).toBeInTheDocument()
  })

  it('sends a bearer token with the update', async () => {
    const user = userEvent.setup({ delay: null })
    const { fetchImpl } = renderAccount(ICC_ACCOUNT)
    const field = await screen.findByLabelText(/display name/i)
    await user.clear(field)
    await user.type(field, 'Another Name')
    await user.click(screen.getByRole('button', { name: /save name/i }))

    await waitFor(() => {
      expect(fetchImpl.calls.some((c) => c.method === 'PATCH')).toBe(true)
    })
    expect(fetchImpl.headersFor('/auth/me')?.get('Authorization')).toMatch(/^Bearer /)
  })

  it('shows a server error inline and does not claim success', async () => {
    // `createFetchStub` keys routes on URL only, and GET/PATCH `/auth/me`
    // need different responses here, so this test wires a small fetch of its
    // own rather than the shared stub.
    const user = userEvent.setup({ delay: null })
    setCurrentUser(makeUser())
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })

    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/auth/me') && method === 'PATCH') {
        return json(
          {
            error: {
              code: 'SERVICE_UNAVAILABLE',
              message: 'The database is unavailable.',
              request_id: 'req-save-fail',
            },
          },
          503,
        )
      }
      if (url.includes('/auth/me')) return json(ICC_ACCOUNT)
      if (url.includes('/notifications')) {
        return json({
          items: [],
          pagination: { total: 0, limit: 1, offset: 0, returned: 0 },
          unread_count: 0,
        })
      }
      return json(
        { error: { code: 'NOT_FOUND', message: 'No stub route', request_id: 'stub' } },
        404,
      )
    }) as typeof fetch

    renderWithAuth(<AppRoutes />, { route: '/account', fetchImpl })
    const field = await screen.findByLabelText(/display name/i)
    await user.clear(field)
    await user.type(field, 'New Name')
    await user.click(screen.getByRole('button', { name: /save name/i }))

    expect(await screen.findByText(/the service is having trouble/i)).toBeInTheDocument()
    expect(screen.getByText(/req-save-fail/)).toBeInTheDocument()
    expect(screen.queryByText(/^saved\.$/i)).not.toBeInTheDocument()
  })

  it('does not offer the form to a student even if rendered mid-transition', async () => {
    renderAccount(ACCOUNT)
    await screen.findAllByText(ACCOUNT.email)
    expect(screen.queryByLabelText(/display name/i)).not.toBeInTheDocument()
  })

  it('disables saving a blank name', async () => {
    const user = userEvent.setup({ delay: null })
    renderAccount(ICC_ACCOUNT)
    const field = await screen.findByLabelText(/display name/i)
    await user.clear(field)
    expect(screen.getByRole('button', { name: /save name/i })).toBeDisabled()
  })
})

describe('AccountPage — identity', () => {
  it('requires authentication', async () => {
    renderWithAuth(<AppRoutes />, { route: '/account' })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    })
  })

  it('never renders the raw user_id', async () => {
    renderAccount()
    await screen.findAllByText(ACCOUNT.email)
    expect(document.body.textContent ?? '').not.toContain(ACCOUNT.user_id)
  })
})
