import { describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import { AppRoutes } from '../App'
import {
  createFetchStub,
  makeUser,
  renderWithAuth,
  signedInRoutes,
  type StubRoute,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'
import { SOS_HOLD_MS } from './SosButton'

const SOS_RESULT = {
  public_ref: 'CS-2026-SOS001',
  report_kind: 'incident',
  submission_mode: 'identified',
  reporter_relationship: 'affected',
  category: { category_id: 9, code: 'SOS_EMERGENCY', label: 'Emergency SOS' },
  location: { location_id: 1, code: 'SYS-UNSPECIFIED', name: 'Unspecified location' },
  location_hint: null,
  occurred_at: '2026-08-14T12:00:00+00:00',
  submitted_at: '2026-08-14T12:00:00+00:00',
  is_emergency: true,
  is_ongoing: false,
  reporter_contactable: true,
  status: 'submitted',
}

function sosRoutes(overrides: Record<string, StubRoute> = {}) {
  return signedInRoutes({
    '/reports/emergency': { status: 201, body: SOS_RESULT },
    ...overrides,
  })
}

function renderDashboard(routes = sosRoutes()) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(routes)
  return { ...renderWithAuth(<AppRoutes />, { route: '/dashboard', fetchImpl }), fetchImpl }
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

describe('SosButton', () => {
  it('is visible to a signed-in account', async () => {
    renderDashboard()
    expect(await screen.findByRole('button', { name: /press and hold/i })).toBeInTheDocument()
  })

  it('does nothing on a quick tap — release before the hold completes', async () => {
    const { fetchImpl } = renderDashboard()
    const button = await screen.findByRole('button', { name: /press and hold/i })

    fireEvent.pointerDown(button)
    fireEvent.pointerUp(button)
    await wait(SOS_HOLD_MS + 200)

    expect(fetchImpl.calls.some((c) => c.url.includes('/reports/emergency'))).toBe(false)
    expect(button).toHaveTextContent('SOS')
  }, 10000)

  it('cancels on pointer leave the same as on release', async () => {
    const { fetchImpl } = renderDashboard()
    const button = await screen.findByRole('button', { name: /press and hold/i })

    fireEvent.pointerDown(button)
    fireEvent.pointerLeave(button)
    await wait(SOS_HOLD_MS + 200)

    expect(fetchImpl.calls.some((c) => c.url.includes('/reports/emergency'))).toBe(false)
  }, 10000)

  it('triggers the alert and navigates to confirmation after a full hold', async () => {
    const { fetchImpl } = renderDashboard()
    const button = await screen.findByRole('button', { name: /press and hold/i })

    fireEvent.pointerDown(button)

    await waitFor(
      () =>
        expect(fetchImpl.calls.some((c) => c.url.includes('/reports/emergency'))).toBe(true),
      { timeout: SOS_HOLD_MS + 2000 },
    )
    expect(
      await screen.findByRole('heading', { name: /your emergency alert has been sent/i }),
    ).toBeInTheDocument()
    expect(await screen.findByText('CS-2026-SOS001')).toBeInTheDocument()
  }, 10000)

  it('shows an inline error and keeps the button available when the request fails', async () => {
    const { fetchImpl } = renderDashboard(
      sosRoutes({
        '/reports/emergency': {
          status: 500,
          body: {
            error: { code: 'INTERNAL', message: 'Something went wrong.', request_id: 'r' },
          },
        },
      }),
    )
    const button = await screen.findByRole('button', { name: /press and hold/i })

    fireEvent.pointerDown(button)

    await waitFor(
      () =>
        expect(fetchImpl.calls.some((c) => c.url.includes('/reports/emergency'))).toBe(true),
      { timeout: SOS_HOLD_MS + 2000 },
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(/something went wrong/i)
    expect(screen.getByRole('button', { name: /press and hold/i })).toBeInTheDocument()
  }, 10000)

  it('is not shown while signed out', () => {
    setCurrentUser(null)
    const fetchImpl = createFetchStub({})
    renderWithAuth(<AppRoutes />, { route: '/login', fetchImpl })
    expect(screen.queryByRole('button', { name: /press and hold/i })).not.toBeInTheDocument()
  })

  it('is keyboard-operable: Enter starts and releasing early cancels', async () => {
    const { fetchImpl } = renderDashboard()
    const button = await screen.findByRole('button', { name: /press and hold/i })
    button.focus()

    fireEvent.keyDown(button, { key: 'Enter' })
    fireEvent.keyUp(button, { key: 'Enter' })
    await wait(SOS_HOLD_MS + 200)

    expect(fetchImpl.calls.some((c) => c.url.includes('/reports/emergency'))).toBe(false)
  }, 10000)

  it('is keyboard-operable: holding Enter for the full duration triggers the alert', async () => {
    const { fetchImpl } = renderDashboard()
    const button = await screen.findByRole('button', { name: /press and hold/i })
    button.focus()

    fireEvent.keyDown(button, { key: 'Enter' })

    await waitFor(
      () =>
        expect(fetchImpl.calls.some((c) => c.url.includes('/reports/emergency'))).toBe(true),
      { timeout: SOS_HOLD_MS + 2000 },
    )
  }, 10000)
})
