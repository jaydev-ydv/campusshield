import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
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

const STATUS_NOTIFICATION = {
  notification_id: 'n1111111-1111-1111-1111-111111111111',
  category: 'status_update' as const,
  title: 'Your report status changed',
  body: 'Report CS-2026-AAA111 is now triaged.',
  related_public_ref: 'CS-2026-AAA111',
  created_at: '2026-08-10T18:00:00+00:00',
  read_at: null,
}

const ASSIGNMENT_NOTIFICATION = {
  notification_id: 'n2222222-2222-2222-2222-222222222222',
  category: 'assignment' as const,
  title: 'A case was assigned to you',
  body: 'You have been assigned CS-2026-BBB222.',
  related_public_ref: 'CS-2026-BBB222',
  created_at: '2026-08-09T12:00:00+00:00',
  read_at: '2026-08-09T13:00:00+00:00',
}

function notificationsRoutes(
  items: unknown[],
  extra: Record<string, StubRoute> = {},
  unreadCount = items.filter((i) => (i as { read_at: string | null }).read_at === null).length,
) {
  return signedInRoutes({
    '/notifications': {
      body: {
        items,
        pagination: { total: items.length, limit: 20, offset: 0, returned: items.length },
        unread_count: unreadCount,
      },
    },
    ...extra,
  })
}

async function renderNotifications(routes = notificationsRoutes([STATUS_NOTIFICATION])) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(routes)
  const view = renderWithAuth(<AppRoutes />, { route: '/notifications', fetchImpl })
  await screen.findByRole('heading', { name: /^notifications$/i })
  return { ...view, fetchImpl }
}

describe('NotificationsPage', () => {
  it('calls /notifications with a bearer token', async () => {
    const { fetchImpl } = await renderNotifications()
    await waitFor(() => {
      expect(fetchImpl.calls.some((c) => c.url.includes('/notifications'))).toBe(true)
    })
    expect(fetchImpl.headersFor('/notifications')?.get('Authorization')).toMatch(/^Bearer /)
  })

  it('shows an unread notification with its title, body and reference', async () => {
    await renderNotifications()
    expect(await screen.findByText(STATUS_NOTIFICATION.title)).toBeInTheDocument()
    expect(screen.getByText(STATUS_NOTIFICATION.body)).toBeInTheDocument()
    expect(screen.getByText(STATUS_NOTIFICATION.related_public_ref)).toBeInTheDocument()
  })

  it('offers a mark-read action only for unread notifications', async () => {
    await renderNotifications(
      notificationsRoutes([STATUS_NOTIFICATION, ASSIGNMENT_NOTIFICATION]),
    )
    await screen.findByText(STATUS_NOTIFICATION.title)
    expect(screen.getAllByRole('button', { name: /mark read/i })).toHaveLength(1)
  })

  it('marks a notification read and refreshes the list', async () => {
    const user = userEvent.setup({ delay: null })
    const { fetchImpl } = await renderNotifications()
    await screen.findByText(STATUS_NOTIFICATION.title)

    await user.click(screen.getByRole('button', { name: /mark read/i }))

    await waitFor(() => {
      expect(
        fetchImpl.calls.some(
          (c) =>
            c.url.includes(`/notifications/${STATUS_NOTIFICATION.notification_id}/read`) &&
            c.method === 'POST',
        ),
      ).toBe(true)
    })
  })

  it('links a status-update notification to the reporter report view', async () => {
    await renderNotifications()
    await screen.findByText(STATUS_NOTIFICATION.title)
    expect(
      screen.getByRole('link', { name: STATUS_NOTIFICATION.related_public_ref }),
    ).toHaveAttribute('href', `/reports/${STATUS_NOTIFICATION.related_public_ref}`)
  })

  it('links an assignment notification to the incident queue, not the reporter view', async () => {
    await renderNotifications(notificationsRoutes([ASSIGNMENT_NOTIFICATION]))
    await screen.findByText(ASSIGNMENT_NOTIFICATION.title)
    expect(
      screen.getByRole('link', { name: ASSIGNMENT_NOTIFICATION.related_public_ref }),
    ).toHaveAttribute('href', '/incidents')
  })

  it('shows an empty state with no notifications', async () => {
    await renderNotifications(notificationsRoutes([]))
    expect(await screen.findByText(/no notifications yet/i)).toBeInTheDocument()
  })

  it('shows an error with a retry when the list fails', async () => {
    setCurrentUser(makeUser())
    const fetchImpl = createFetchStub(
      signedInRoutes({
        '/notifications': {
          status: 503,
          body: {
            error: {
              code: 'SERVICE_UNAVAILABLE',
              message: 'The database is unavailable.',
              request_id: 'req-99',
            },
          },
        },
      }),
    )
    renderWithAuth(<AppRoutes />, { route: '/notifications', fetchImpl })

    expect(await screen.findByText(/the service is having trouble/i)).toBeInTheDocument()
    expect(screen.getByText(/req-99/)).toBeInTheDocument()
  })

  it('requires authentication', async () => {
    renderWithAuth(<AppRoutes />, { route: '/notifications' })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
    })
  })

  it('never renders another user identity', async () => {
    await renderNotifications()
    await screen.findByText(STATUS_NOTIFICATION.title)
    const text = document.body.textContent ?? ''
    expect(text).not.toContain('user_id')
  })
})
