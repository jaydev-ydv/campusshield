import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
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
  type StubRoute,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'
import { toLocalInputValue } from '../report/draft'

const SUBMITTED = {
  public_ref: 'CS-2026-7QK4M2',
  report_kind: 'incident',
  submission_mode: 'identified',
  reporter_relationship: 'affected',
  category: { category_id: 1, code: 'HARASS_VERBAL', label: 'Verbal harassment' },
  location: { location_id: 1, code: 'LKRC-MAIN', name: 'Library & Knowledge Resource Centre' },
  location_hint: null,
  occurred_at: '2026-08-10T18:00:00+00:00',
  submitted_at: '2026-08-10T20:00:00+00:00',
  is_emergency: false,
  is_ongoing: false,
  reporter_contactable: true,
  status: 'submitted',
}

const ANON_SUBMITTED = {
  ...SUBMITTED,
  public_ref: 'CS-2026-ANON01',
  submission_mode: 'anonymous',
  reporter_contactable: false,
  access_token: 'a'.repeat(32),
  access_token_notice: 'Save this code now.',
}

function reportRoutes(overrides: Record<string, StubRoute> = {}) {
  return signedInRoutes({
    '/reports': { status: 201, body: SUBMITTED },
    '/evidence/config': {
      body: {
        max_bytes: 10 * 1024 * 1024,
        accepted_types: ['image/jpeg', 'image/png', 'image/webp'],
        max_per_report: 5,
      },
    },
    ...overrides,
  })
}

async function renderReportForm(routes = reportRoutes()) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(routes)
  const view = renderWithAuth(<AppRoutes />, { route: '/report', fetchImpl })
  await screen.findByRole('heading', { name: /report a safety concern/i })
  await waitFor(() =>
    expect(fetchImpl.calls.some((c) => c.url.includes('/locations'))).toBe(true),
  )
  return { ...view, fetchImpl }
}

// `delay: null` removes the inter-keystroke wait. Without it, typing a
// paragraph into a form that re-renders on every character takes seconds.
const user = () => userEvent.setup({ delay: null })

/** Walks the wizard to the review step with a valid identified incident. */
async function fillToReview(
  u: ReturnType<typeof userEvent.setup>,
  options: {
    anonymous?: boolean
    emergency?: boolean
    ongoing?: boolean
    concern?: boolean
  } = {},
) {
  // Step 1 — kind. `findBy`, not `getBy`: the catalog fetch is still in flight
  // on first render and the step shows a loading state until it lands.
  await u.click(
    await screen.findByRole('radio', {
      name: options.concern ? /something feels unsafe/i : /something happened/i,
    }),
  )
  await u.click(screen.getByRole('button', { name: /continue/i }))

  // Step 2 — where
  await screen.findByRole('combobox', { name: /campus location/i })
  await u.selectOptions(
    screen.getByRole('combobox', { name: /campus location/i }),
    String(LOCATIONS[0].location_id),
  )
  await u.click(screen.getByRole('button', { name: /continue/i }))

  // Step 3 — when
  const when = await screen.findByLabelText(/date and time/i)
  await u.clear(when)
  await u.type(when, toLocalInputValue(new Date(Date.now() - 3_600_000)))
  await u.click(screen.getByRole('button', { name: /continue/i }))

  // Step 4 — what
  const category = await screen.findByRole('combobox', { name: /what kind of/i })
  const wanted = options.concern ? CATEGORIES[1] : CATEGORIES[0]
  await u.selectOptions(category, String(wanted.category_id))
  await u.type(
    screen.getByLabelText(/in your own words/i),
    'Someone followed me to the car park.',
  )
  await u.click(screen.getByRole('button', { name: /continue/i }))

  // Step 5 — evidence. Optional, so it is skipped unless a test adds one.
  await screen.findByRole('heading', { name: /add a photo/i })
  await u.click(screen.getByRole('button', { name: /continue/i }))

  // Step 6 — relationship
  await screen.findByRole('radio', { name: /this happened to me/i })
  await u.click(screen.getByRole('button', { name: /continue/i }))

  // Step 7 — privacy and urgency
  await screen.findByRole('radio', { name: /submit with my account/i })
  if (options.anonymous) {
    await u.click(screen.getByRole('radio', { name: /submit anonymously/i }))
  }
  if (options.emergency) {
    await u.click(screen.getByRole('radio', { name: /needs immediate attention/i }))
    if (options.ongoing) {
      await u.click(await screen.findByRole('radio', { name: /yes, right now/i }))
    }
  }
  await u.click(screen.getByRole('button', { name: /continue/i }))

  await screen.findByRole('heading', { name: /review your report/i })
}

/* -------------------------------------------------------------------------- */

describe('ReportPage — navigation', () => {
  it('starts on step 1 of 8', async () => {
    await renderReportForm()
    expect(screen.getByText('Step 1 of 8')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /something happened/i })).toBeInTheDocument()
  })

  it('advances only when the step is valid', async () => {
    const u = user()
    await renderReportForm()

    await u.click(screen.getByRole('button', { name: /continue/i }))
    expect(
      await screen.findByText(/choose what you would like to report/i),
    ).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 8')).toBeInTheDocument()

    await u.click(screen.getByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))
    expect(await screen.findByText('Step 2 of 8')).toBeInTheDocument()
  })

  it('goes back without losing what was entered', async () => {
    const u = user()
    await renderReportForm()

    await u.click(screen.getByRole('radio', { name: /something feels unsafe/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))
    await screen.findByText('Step 2 of 8')

    await u.click(screen.getByRole('button', { name: /^back$/i }))
    await screen.findByText('Step 1 of 8')
    expect(screen.getByRole('radio', { name: /something feels unsafe/i })).toBeChecked()
  })

  it('offers Cancel rather than Back on the first step', async () => {
    await renderReportForm()
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^back$/i })).not.toBeInTheDocument()
  })

  it('announces progress accessibly', async () => {
    await renderReportForm()
    const bar = screen.getByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuenow', '1')
    expect(bar).toHaveAttribute('aria-valuemax', '8')
  })
})

describe('ReportPage — required fields', () => {
  it('requires a location', async () => {
    const u = user()
    await renderReportForm()
    await u.click(screen.getByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))

    await screen.findByRole('combobox', { name: /campus location/i })
    await u.click(screen.getByRole('button', { name: /continue/i }))
    expect(await screen.findByText(/choose where this happened/i)).toBeInTheDocument()
  })

  it('requires a time', async () => {
    const u = user()
    await renderReportForm()
    await u.click(screen.getByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))
    await u.selectOptions(
      await screen.findByRole('combobox', { name: /campus location/i }),
      String(LOCATIONS[0].location_id),
    )
    await u.click(screen.getByRole('button', { name: /continue/i }))

    await screen.findByLabelText(/date and time/i)
    await u.click(screen.getByRole('button', { name: /continue/i }))
    expect(await screen.findByText(/enter when this happened/i)).toBeInTheDocument()
  })

  it('rejects a future timestamp', async () => {
    const u = user()
    await renderReportForm()
    await u.click(screen.getByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))
    await u.selectOptions(
      await screen.findByRole('combobox', { name: /campus location/i }),
      String(LOCATIONS[0].location_id),
    )
    await u.click(screen.getByRole('button', { name: /continue/i }))

    const when = await screen.findByLabelText(/date and time/i)
    await u.clear(when)
    await u.type(when, toLocalInputValue(new Date(Date.now() + 86_400_000)))
    await u.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText(/in the future/i)).toBeInTheDocument()
  })

  it('requires a category and a long enough narrative', async () => {
    const u = user()
    await renderReportForm()
    await u.click(screen.getByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))
    await u.selectOptions(
      await screen.findByRole('combobox', { name: /campus location/i }),
      String(LOCATIONS[0].location_id),
    )
    await u.click(screen.getByRole('button', { name: /continue/i }))
    const when = await screen.findByLabelText(/date and time/i)
    await u.clear(when)
    await u.type(when, toLocalInputValue(new Date(Date.now() - 3_600_000)))
    await u.click(screen.getByRole('button', { name: /continue/i }))

    await screen.findByRole('combobox', { name: /what kind of/i })
    await u.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByText(/choose the option that fits best/i)).toBeInTheDocument()
    expect(screen.getByText(/at least 10 characters/i)).toBeInTheDocument()
  })
})

describe('ReportPage — the two report kinds', () => {
  it('offers only incident categories for an incident', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u)

    await u.click(screen.getByRole('button', { name: /^back$/i })) // privacy
    await u.click(screen.getByRole('button', { name: /^back$/i })) // relationship
    await u.click(screen.getByRole('button', { name: /^back$/i })) // evidence
    await u.click(screen.getByRole('button', { name: /^back$/i })) // what

    const select = await screen.findByRole('combobox', { name: /what kind of incident/i })
    const labels = within(select)
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(labels).toContain(CATEGORIES[0].label)
    expect(labels).not.toContain(CATEGORIES[1].label)
  })

  it('offers only concern categories for a safety concern', async () => {
    const u = user()
    await renderReportForm()
    await u.click(screen.getByRole('radio', { name: /something feels unsafe/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))
    await u.selectOptions(
      await screen.findByRole('combobox', { name: /campus location/i }),
      String(LOCATIONS[0].location_id),
    )
    await u.click(screen.getByRole('button', { name: /continue/i }))
    const when = await screen.findByLabelText(/date and time/i)
    await u.clear(when)
    await u.type(when, toLocalInputValue(new Date(Date.now() - 3_600_000)))
    await u.click(screen.getByRole('button', { name: /continue/i }))

    const select = await screen.findByRole('combobox', { name: /what kind of concern/i })
    const labels = within(select)
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(labels).toContain(CATEGORIES[1].label)
    expect(labels).not.toContain(CATEGORIES[0].label)
  })

  it('submits a safety concern as kind=concern', async () => {
    const u = user()
    const { fetchImpl } = await renderReportForm(
      reportRoutes({
        '/reports': { status: 201, body: { ...SUBMITTED, report_kind: 'concern' } },
      }),
    )
    await fillToReview(u, { concern: true })
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    await waitFor(() => {
      const call = fetchImpl.calls.find((c) => c.method === 'POST')
      expect(JSON.parse(String(call?.init?.body)).category_id).toBe(CATEGORIES[1].category_id)
    })
  })
})

describe('ReportPage — emergency constraints', () => {
  it('disables emergency for a category that does not allow it', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u, { concern: true })
    await u.click(screen.getByRole('button', { name: /^back$/i }))

    const emergency = await screen.findByRole('radio', { name: /needs immediate attention/i })
    expect(emergency).toBeDisabled()
    expect(screen.getByText(new RegExp(CATEGORIES[1].label, 'i'))).toBeInTheDocument()
  })

  it('offers emergency for an eligible category', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /^back$/i }))

    expect(
      await screen.findByRole('radio', { name: /needs immediate attention/i }),
    ).toBeEnabled()
  })

  it('only asks whether the situation is ongoing once emergency is chosen', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /^back$/i }))

    expect(screen.queryByRole('radio', { name: /yes, right now/i })).not.toBeInTheDocument()
    await u.click(screen.getByRole('radio', { name: /needs immediate attention/i }))
    expect(await screen.findByRole('radio', { name: /yes, right now/i })).toBeInTheDocument()
  })

  it('submits an emergency report with both flags set', async () => {
    const u = user()
    const { fetchImpl } = await renderReportForm(
      reportRoutes({
        '/reports': {
          status: 201,
          body: { ...SUBMITTED, is_emergency: true, is_ongoing: true },
        },
      }),
    )
    await fillToReview(u, { emergency: true, ongoing: true })
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    await waitFor(() => {
      const body = JSON.parse(
        String(fetchImpl.calls.find((c) => c.method === 'POST')?.init?.body),
      )
      expect(body.is_emergency).toBe(true)
      expect(body.is_ongoing).toBe(true)
    })
  })

  it('clears emergency when the category changes to an ineligible one', async () => {
    // Otherwise the student is rejected two steps later for a combination the
    // form let them build.
    const u = user()
    await renderReportForm()
    await fillToReview(u, { emergency: true })

    await u.click(screen.getByRole('button', { name: /^back$/i })) // privacy
    await u.click(screen.getByRole('button', { name: /^back$/i })) // relationship
    await u.click(screen.getByRole('button', { name: /^back$/i })) // evidence
    await u.click(screen.getByRole('button', { name: /^back$/i })) // what

    const select = await screen.findByRole('combobox', { name: /what kind of/i })
    await u.selectOptions(select, String(CATEGORIES[0].category_id))
    await u.click(screen.getByRole('button', { name: /continue/i })) // -> evidence
    await u.click(screen.getByRole('button', { name: /continue/i })) // -> relationship
    await u.click(screen.getByRole('button', { name: /continue/i })) // -> privacy

    expect(
      await screen.findByRole('radio', { name: /review in the usual way/i }),
    ).toBeChecked()
  })
})

describe('ReportPage — review', () => {
  it('shows exactly what will be sent', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u)

    expect(screen.getByText(CATEGORIES[0].label)).toBeInTheDocument()
    expect(screen.getByText(LOCATIONS[0].name)).toBeInTheDocument()
    expect(screen.getByText(/someone followed me to the car park/i)).toBeInTheDocument()
    expect(screen.getByText(/with your account/i)).toBeInTheDocument()
  })

  it('does not display anything that will not be sent', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u)

    const review = screen.getByRole('heading', { name: /review your report/i }).closest('div')!
    expect(review.textContent).not.toContain(ACCOUNT.user_id)
    expect(review.textContent).not.toContain(ACCOUNT.email)
  })

  it('describes an anonymous submission accurately', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u, { anonymous: true })

    expect(screen.getByText(/^Anonymously$/)).toBeInTheDocument()
    expect(screen.getByText(/not linked to your account/i)).toBeInTheDocument()
  })
})

describe('ReportPage — submission', () => {
  it('posts to /reports and shows the confirmation', async () => {
    const u = user()
    const { fetchImpl } = await renderReportForm()
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    expect(
      await screen.findByRole('heading', { name: /your report has been received/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(SUBMITTED.public_ref)).toBeInTheDocument()

    const post = fetchImpl.calls.find((c) => c.method === 'POST')
    expect(post?.url).toContain('/reports')
    expect(new Headers(post?.init?.headers).get('Authorization')).toMatch(/^Bearer /)
  })

  it('sends no identity information in the body', async () => {
    const u = user()
    const { fetchImpl } = await renderReportForm()
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    await waitFor(() => expect(fetchImpl.calls.some((c) => c.method === 'POST')).toBe(true))
    const body = String(fetchImpl.calls.find((c) => c.method === 'POST')?.init?.body)
    for (const forbidden of [
      'user_id',
      'email',
      'firebase',
      'uid',
      'role',
      'submission_mode',
    ]) {
      expect(body).not.toContain(forbidden)
    }
  })

  it('omits contact_consent on an anonymous submission', async () => {
    const u = user()
    const { fetchImpl } = await renderReportForm(
      reportRoutes({ '/reports': { status: 201, body: ANON_SUBMITTED } }),
    )
    await fillToReview(u, { anonymous: true })
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    await waitFor(() => expect(fetchImpl.calls.some((c) => c.method === 'POST')).toBe(true))
    const body = JSON.parse(
      String(fetchImpl.calls.find((c) => c.method === 'POST')?.init?.body),
    )
    expect(body).not.toHaveProperty('contact_consent')
    expect(body.anonymous).toBe(true)
  })

  it('prevents a duplicate submission while one is in flight', async () => {
    // A duplicate is not cosmetic: it is a second row in the database and a
    // second alert to security.
    const u = user()
    const { fetchImpl } = await renderReportForm(
      reportRoutes({ '/reports': { status: 201, body: SUBMITTED, delayMs: 120 } }),
    )
    await fillToReview(u)

    const submit = screen.getByRole('button', { name: /submit report/i })
    await u.click(submit)
    const busy = screen.getByRole('button', { name: /submitting/i })
    expect(busy).toBeDisabled()
    await u.click(busy).catch(() => {})

    await screen.findByRole('heading', { name: /your report has been received/i })
    expect(fetchImpl.calls.filter((c) => c.method === 'POST')).toHaveLength(1)
  })

  it('shows a busy state while submitting', async () => {
    const u = user()
    await renderReportForm(
      reportRoutes({ '/reports': { status: 201, body: SUBMITTED, delayMs: 80 } }),
    )
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    expect(screen.getByRole('button', { name: /submitting/i })).toHaveAttribute(
      'aria-busy',
      'true',
    )
  })
})

describe('ReportPage — API errors', () => {
  it('surfaces field-level validation errors from the backend', async () => {
    const u = user()
    await renderReportForm(
      reportRoutes({
        '/reports': {
          status: 400,
          body: {
            error: {
              code: 'VALIDATION_ERROR',
              message: 'The request body failed validation.',
              request_id: 'req-55',
              details: { fields: { category_id: ['No such active category.'] } },
            },
          },
        },
      }),
    )
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    expect(await screen.findByText('No such active category.')).toBeInTheDocument()
    expect(screen.getByText(/req-55/)).toBeInTheDocument()
  })

  it('lets the student retry after a failure', async () => {
    const u = user()
    await renderReportForm(
      reportRoutes({
        '/reports': {
          status: 500,
          body: { error: { code: 'INTERNAL_ERROR', message: 'boom', request_id: 'r' } },
        },
      }),
    )
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    // The heading names what failed; the body says what it means and what to do.
    expect(await screen.findByText(/your report could not be submitted/i)).toBeInTheDocument()
    expect(screen.getByText(/not something you did/i)).toBeInTheDocument()
    // Not stuck: the button must come back so a transient failure is recoverable.
    expect(screen.getByRole('button', { name: /submit report/i })).toBeEnabled()
  })

  it('explains a quota rejection without alarm', async () => {
    const u = user()
    await renderReportForm(
      reportRoutes({
        '/reports': {
          status: 429,
          body: {
            error: {
              code: 'QUOTA_EXCEEDED',
              message: "You have reached today's limit of 5 reports.",
              request_id: 'r',
            },
          },
        },
      }),
    )
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    expect(await screen.findByText(/daily limit reached/i)).toBeInTheDocument()
  })

  it('reports a network failure as unreachable rather than rejected', async () => {
    const u = user()
    setCurrentUser(makeUser())
    const failing = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/reports') && init?.method === 'POST') {
        throw new TypeError('Failed to fetch')
      }
      const url = String(input)
      const body = url.includes('/evidence/config')
        ? {
            max_bytes: 10 * 1024 * 1024,
            accepted_types: ['image/jpeg', 'image/png', 'image/webp'],
            max_per_report: 5,
          }
        : url.includes('/locations')
          ? { items: LOCATIONS }
          : url.includes('/categories')
            ? { items: CATEGORIES }
            : ACCOUNT
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    renderWithAuth(<AppRoutes />, {
      route: '/report',
      fetchImpl: failing as unknown as typeof fetch,
    })
    await screen.findByRole('heading', { name: /report a safety concern/i })

    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))

    expect(await screen.findByText(/your report could not be submitted/i)).toBeInTheDocument()
    expect(screen.getByText(/check your connection/i)).toBeInTheDocument()
  })
})

describe('ReportPage — no verified locations', () => {
  it('blocks honestly instead of inventing options', async () => {
    const u = user()
    await renderReportForm(reportRoutes({ '/locations': { body: { items: [] } } }))

    await u.click(screen.getByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))

    expect(
      await screen.findByText(/campus locations are not available yet/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('combobox', { name: /campus location/i }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
  })
})

describe('ReportPage — tone', () => {
  it('avoids alarm styling and sensational language', async () => {
    await renderReportForm()
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/🚨|⚠️|🆘/)
    expect(text).not.toMatch(/\b(URGENT|DANGER|ALERT)\b/)
    expect(document.body.querySelectorAll('[class*="red-"]')).toHaveLength(0)
  })

  it('does not promise a response time or police involvement', async () => {
    const u = user()
    await renderReportForm()
    await fillToReview(u)
    await u.click(screen.getByRole('button', { name: /submit report/i }))
    await screen.findByRole('heading', { name: /your report has been received/i })

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/police/i)
    expect(text).not.toMatch(/within \d+ (hours|minutes|days)/i)
    expect(text).not.toMatch(/guarantee/i)
    expect(text).toMatch(/no fixed response time/i)
  })
})

/* -------------------------------------------------------------------------- */
/* The campus map on step 2 (Phase 5)                                         */
/* -------------------------------------------------------------------------- */

/**
 * A location with a synthetic, clearly-labelled coordinate — never a real
 * campus coordinate. `LOCATIONS` (from `../test/harness`) intentionally
 * carries `latitude: null, longitude: null`, matching the real system's
 * current state of zero verified locations; these tests need the *other*
 * state — a verified location — to exercise the map itself.
 */
const MAPPED_LOCATION = {
  location_id: 9,
  code: 'TEST-MAPPED',
  name: 'Test Mapped Location',
  location_type: 'library',
  zone: null,
  latitude: 12.5,
  longitude: 77.5,
  is_indoor: true,
  dispatch_note: null,
  is_synthetic: false,
}

const DEMO_LOCATION = {
  ...MAPPED_LOCATION,
  location_id: 10,
  code: 'DEMO-TEST-MAPPED',
  name: 'DEMO — Test Location (NOT A REAL LOCATION)',
  is_synthetic: true,
}

describe('ReportPage — the campus map', () => {
  it('shows the map alongside the list once locations carry a coordinate', async () => {
    await renderReportForm(
      reportRoutes({ '/locations': { body: { items: [MAPPED_LOCATION] } } }),
    )
    await user().click(await screen.findByRole('radio', { name: /something happened/i }))
    await userEvent
      .setup({ delay: null })
      .click(screen.getByRole('button', { name: /continue/i }))

    expect(
      await screen.findByRole('application', { name: /campus location map/i }),
    ).toBeInTheDocument()
    // The list remains present and usable — the map is a second way in, not
    // a replacement for the first.
    expect(screen.getByRole('combobox', { name: /campus location/i })).toBeInTheDocument()
  })

  it('renders no map when no location has a verified coordinate, and the list still works', async () => {
    // The default `LOCATIONS` fixture — real-system-accurate: null coordinates.
    const u = user()
    await renderReportForm()
    await u.click(await screen.findByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))

    await screen.findByRole('combobox', { name: /campus location/i })
    expect(
      screen.queryByRole('application', { name: /campus location map/i }),
    ).not.toBeInTheDocument()

    await u.selectOptions(
      screen.getByRole('combobox', { name: /campus location/i }),
      String(LOCATIONS[0].location_id),
    )
    expect(screen.getByRole('combobox', { name: /campus location/i })).toHaveValue(
      String(LOCATIONS[0].location_id),
    )
  })

  it('offers no map when no campus locations are published at all', async () => {
    const u = user()
    await renderReportForm(reportRoutes({ '/locations': { body: { items: [] } } }))
    await u.click(await screen.findByRole('radio', { name: /something happened/i }))
    await u.click(screen.getByRole('button', { name: /continue/i }))

    expect(
      await screen.findByText(/campus locations are not available yet/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('application', { name: /campus location map/i }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('combobox', { name: /campus location/i }),
    ).not.toBeInTheDocument()
  })

  it('shows no demo-data notice for a real-style verified location', async () => {
    await renderReportForm(
      reportRoutes({ '/locations': { body: { items: [MAPPED_LOCATION] } } }),
    )
    await user().click(await screen.findByRole('radio', { name: /something happened/i }))
    await userEvent
      .setup({ delay: null })
      .click(screen.getByRole('button', { name: /continue/i }))

    await screen.findByRole('application', { name: /campus location map/i })
    expect(screen.queryByTestId('demo-data-notice')).not.toBeInTheDocument()
  })

  it('warns the student when campus locations shown are demo/development data', async () => {
    await renderReportForm(
      reportRoutes({ '/locations': { body: { items: [DEMO_LOCATION] } } }),
    )
    await user().click(await screen.findByRole('radio', { name: /something happened/i }))
    await userEvent
      .setup({ delay: null })
      .click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByTestId('demo-data-notice')).toHaveTextContent(
      /demo campus locations/i,
    )
    // The option itself is self-describing too, independent of the badge.
    expect(
      screen.getByRole('option', { name: /demo.*not a real location/i }),
    ).toBeInTheDocument()
  })
})
