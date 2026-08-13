import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { ReportConfirmationPage } from './ReportConfirmationPage'
import { AuthProvider } from '../auth/AuthContext'
import { ApiClient } from '../lib/apiClient'
import { ACCOUNT, createFetchStub, makeUser, signedInRoutes } from '../test/harness'
import { authState, setCurrentUser } from '../test/firebaseMock'

const IDENTIFIED = {
  public_ref: 'CS-2026-7QK4M2',
  report_kind: 'incident' as const,
  submission_mode: 'identified' as const,
  reporter_relationship: 'affected' as const,
  category: { category_id: 1, code: 'HARASS_VERBAL', label: 'Verbal harassment' },
  location: { location_id: 1, code: 'LKRC-MAIN', name: 'Library' },
  location_hint: null,
  occurred_at: '2026-08-10T18:00:00+00:00',
  submitted_at: '2026-08-10T20:00:00+00:00',
  is_emergency: false,
  is_ongoing: false,
  reporter_contactable: true,
  status: 'submitted',
}

const ANONYMOUS = {
  ...IDENTIFIED,
  public_ref: 'CS-2026-ANON01',
  submission_mode: 'anonymous' as const,
  reporter_contactable: false,
  access_token: 'b'.repeat(32),
  access_token_notice: 'Save this code now.',
}

function renderConfirmation(state: unknown) {
  setCurrentUser(makeUser())
  const client = new ApiClient({
    baseUrl: 'http://api.test/api/v1',
    getToken: async () => (authState.currentUser ? authState.currentUser.getIdToken() : null),
    fetchImpl: createFetchStub(signedInRoutes()),
  })
  return render(
    <AuthProvider authInstance={{} as never} apiClient={client}>
      <MemoryRouter initialEntries={[{ pathname: '/report/submitted', state }]}>
        <Routes>
          <Route path="/report/submitted" element={<ReportConfirmationPage />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

describe('ReportConfirmationPage — identified', () => {
  it('shows the reference and what happens next', async () => {
    renderConfirmation({ result: IDENTIFIED })

    expect(
      await screen.findByRole('heading', { name: /your report has been received/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(IDENTIFIED.public_ref)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /what happens next/i })).toBeInTheDocument()
  })

  it('issues no access token for an identified report', async () => {
    renderConfirmation({ result: IDENTIFIED })
    await screen.findByText(IDENTIFIED.public_ref)

    expect(screen.queryByText(/save this code now/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /copy code/i })).not.toBeInTheDocument()
  })

  it('links to the reports list', async () => {
    renderConfirmation({ result: IDENTIFIED })
    expect(await screen.findByRole('link', { name: /view my reports/i })).toHaveAttribute(
      'href',
      '/reports',
    )
  })

  it('says the team may contact the reporter when consent was given', async () => {
    renderConfirmation({ result: IDENTIFIED })
    expect(await screen.findByText(/the team may also contact you/i)).toBeInTheDocument()
  })

  it('says nobody will contact the reporter when consent was withheld', async () => {
    renderConfirmation({ result: { ...IDENTIFIED, reporter_contactable: false } })
    expect(await screen.findByText(/nobody will get in touch/i)).toBeInTheDocument()
  })

  it('tells an identified reporter to expect in-app notifications', async () => {
    renderConfirmation({ result: IDENTIFIED })
    expect(
      await screen.findByText(/status updates in your CampusShield notifications/i),
    ).toBeInTheDocument()
  })
})

describe('ReportConfirmationPage — anonymous', () => {
  it('shows the one-time access token', async () => {
    renderConfirmation({ result: ANONYMOUS })

    expect(await screen.findByText(/save this code now/i)).toBeInTheDocument()
    expect(screen.getByText(ANONYMOUS.access_token)).toBeInTheDocument()
  })

  it('states plainly that the code cannot be recovered', async () => {
    renderConfirmation({ result: ANONYMOUS })
    expect(await screen.findByText(/cannot be recovered/i)).toBeInTheDocument()
  })

  it('explains why the report will not appear in the reports list', async () => {
    renderConfirmation({ result: ANONYMOUS })
    expect(await screen.findByText(/will not appear in your reports/i)).toBeInTheDocument()
  })

  it('does not link to the reports list, which cannot contain it', async () => {
    renderConfirmation({ result: ANONYMOUS })
    await screen.findByText(ANONYMOUS.access_token)
    expect(screen.queryByRole('link', { name: /view my reports/i })).not.toBeInTheDocument()
  })

  it('mentions the emergency dispatch only when it applies', async () => {
    renderConfirmation({ result: { ...ANONYMOUS, is_emergency: true } })
    expect(await screen.findByText(/campus security has been alerted/i)).toBeInTheDocument()
  })

  it('tells an anonymous reporter that notifications are not possible', async () => {
    renderConfirmation({ result: ANONYMOUS })
    expect(
      await screen.findByText(/anonymous reports cannot receive notifications/i),
    ).toBeInTheDocument()
  })
})

describe('ReportConfirmationPage — the token is shown once', () => {
  it('shows nothing useful without router state', async () => {
    // A refresh loses it, and that is correct: only the SHA-256 reaches the
    // database, so a recoverable copy would defeat the mechanism.
    renderConfirmation(null)

    expect(
      await screen.findByRole('heading', { name: /no longer available/i }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/save this code now/i)).not.toBeInTheDocument()
  })

  it('never writes the token to storage', async () => {
    renderConfirmation({ result: ANONYMOUS })
    await screen.findByText(ANONYMOUS.access_token)

    expect(JSON.stringify(localStorage)).not.toContain(ANONYMOUS.access_token)
    expect(JSON.stringify(sessionStorage)).not.toContain(ANONYMOUS.access_token)
  })

  it('never puts the token in the URL', async () => {
    renderConfirmation({ result: ANONYMOUS })
    await screen.findByText(ANONYMOUS.access_token)
    expect(window.location.href).not.toContain(ANONYMOUS.access_token)
  })
})

describe('ReportConfirmationPage — promises', () => {
  it('makes no guarantee this system does not keep', async () => {
    renderConfirmation({ result: IDENTIFIED })
    await screen.findByText(IDENTIFIED.public_ref)

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/police/i)
    expect(text).not.toMatch(/guarantee/i)
    expect(text).not.toMatch(/within \d+ (hours|minutes|days)/i)
    expect(text).toMatch(/does not decide whether a report is true/i)
  })

  it('carries no reporter identity', async () => {
    renderConfirmation({ result: IDENTIFIED })
    await screen.findByText(IDENTIFIED.public_ref)
    expect(document.body.textContent ?? '').not.toContain(ACCOUNT.user_id)
  })
})
