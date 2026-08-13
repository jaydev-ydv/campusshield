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
