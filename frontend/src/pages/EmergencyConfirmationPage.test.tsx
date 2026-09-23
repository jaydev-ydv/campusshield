import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { EmergencyConfirmationPage } from './EmergencyConfirmationPage'
import { AuthProvider } from '../auth/AuthContext'
import { ApiClient } from '../lib/apiClient'
import { createFetchStub, makeUser, signedInRoutes, type StubRoute } from '../test/harness'
import { authState, setCurrentUser } from '../test/firebaseMock'

const SOS_RESULT = {
  public_ref: 'CS-2026-SOS001',
  report_kind: 'incident' as const,
  submission_mode: 'identified' as const,
  reporter_relationship: 'affected' as const,
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

const RESOLVED_RESULT = {
  ...SOS_RESULT,
  public_ref: 'CS-2026-SOS002',
  location: { location_id: 2, code: 'LKRC-MAIN', name: 'Library' },
}

const LIMITS = {
  max_bytes: 10 * 1024 * 1024,
  accepted_types: ['image/jpeg', 'image/png', 'image/webp'],
  max_per_report: 5,
}

function makeFile(name = 'photo.jpg', type = 'image/jpeg', size = 2048): File {
  const file = new File([new Uint8Array(size)], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

function uploadResponse(token = 'a'.repeat(32)) {
  return {
    upload_token: token,
    content_type: 'image/jpeg',
    byte_size: 1024,
    width: 320,
    height: 240,
    notice: 'Hidden location and device data have been removed from this image.',
  }
}

function renderConfirmation(state: unknown, overrides: Record<string, StubRoute> = {}) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(
    signedInRoutes({ '/evidence/config': { body: LIMITS }, ...overrides }),
  )
  const client = new ApiClient({
    baseUrl: 'http://api.test/api/v1',
    getToken: async () => (authState.currentUser ? authState.currentUser.getIdToken() : null),
    fetchImpl,
  })
  const view = render(
    <AuthProvider authInstance={{} as never} apiClient={client}>
      <MemoryRouter initialEntries={[{ pathname: '/emergency/submitted', state }]}>
        <Routes>
          <Route path="/emergency/submitted" element={<EmergencyConfirmationPage />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
  return { ...view, fetchImpl }
}

const user = () => userEvent.setup({ delay: null })

describe('EmergencyConfirmationPage', () => {
  it('shows a fallback when no result is in router state', async () => {
    renderConfirmation(null)
    expect(
      await screen.findByRole('heading', { name: /this page is no longer available/i }),
    ).toBeInTheDocument()
  })

  it('shows the reference and confirms security was alerted', async () => {
    renderConfirmation({ result: SOS_RESULT })

    expect(
      await screen.findByRole('heading', { name: /your emergency alert has been sent/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('CS-2026-SOS001')).toBeInTheDocument()
    expect(screen.getByText(/campus security has been alerted/i)).toBeInTheDocument()
  })

  it('is honest about an unresolved location rather than implying one was found', async () => {
    renderConfirmation({ result: SOS_RESULT })
    expect(await screen.findByText(/no location could be determined/i)).toBeInTheDocument()
  })

  it('names the resolved location when one was matched', async () => {
    renderConfirmation({ result: RESOLVED_RESULT })
    expect(await screen.findByText(/recorded near library/i)).toBeInTheDocument()
  })

  it('never mentions an access token — the emergency path is always identified', async () => {
    renderConfirmation({ result: SOS_RESULT })
    await screen.findByRole('heading', { name: /your emergency alert has been sent/i })
    expect(screen.queryByText(/save this code now/i)).not.toBeInTheDocument()
  })

  it('lets the reporter attach a photo taken after the trigger', async () => {
    const u = user()
    const { fetchImpl } = renderConfirmation(
      { result: SOS_RESULT },
      {
        '/evidence': { status: 201, body: uploadResponse() },
        '/reports/CS-2026-SOS001/evidence': { status: 201, body: { evidence_ids: ['ev-1'] } },
      },
    )
    await screen.findByRole('heading', { name: /your emergency alert has been sent/i })

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await u.upload(input, makeFile())

    const attachButton = await screen.findByRole('button', { name: /attach this photo/i })
    await u.click(attachButton)

    await waitFor(() => {
      expect(
        fetchImpl.calls.some(
          (c) => c.url.includes('/reports/CS-2026-SOS001/evidence') && c.method === 'POST',
        ),
      ).toBe(true)
    })
    expect(await screen.findByText(/attached to this report/i)).toBeInTheDocument()

    const body = fetchImpl.bodyFor('/reports/CS-2026-SOS001/evidence')
    expect(body).toEqual({ evidence_tokens: ['a'.repeat(32)] })
  })
})
