import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { IncidentsPage } from './IncidentsPage'
import type { AccountIdentity, IncidentSummary } from '../lib/api'
import {
  ACCOUNT,
  createFetchStub,
  makeUser,
  renderWithAuth,
  signedInRoutes,
  type StubRoute,
} from '../test/harness'
import { setCurrentUser } from '../test/firebaseMock'

const ICC_ACCOUNT: AccountIdentity = { ...ACCOUNT, role: 'icc', email: 'icc@example.edu' }

/**
 * An incident at an UNMAPPED location — the state of every campus location
 * today, because `CAMPUS_LOCATIONS.md` records zero verified coordinates.
 * This is the default fixture on purpose: the common case should be the one the
 * suite exercises most.
 */
const UNMAPPED_INCIDENT: IncidentSummary = {
  public_ref: 'CS-2026-7QK4M2',
  report_kind: 'incident',
  submission_mode: 'identified',
  category: { category_id: 1, code: 'HARASS_VERBAL', label: 'Verbal harassment' },
  location: {
    location_id: 1,
    code: 'LKRC-MAIN',
    name: 'Library & Knowledge Resource Centre',
    latitude: null,
    longitude: null,
    is_mapped: false,
    is_synthetic: false,
  },
  location_hint: null,
  occurred_at: '2026-08-11T14:00:00+00:00',
  submitted_at: '2026-08-11T14:20:00+00:00',
  is_emergency: false,
  is_ongoing: false,
  status: 'submitted',
  reporter_contactable: true,
  evidence_count: 0,
  location_signal: null,
  dispatch: null,
  is_assigned: false,
}

/** Synthetic coordinates, used only to prove the mapped path renders. */
const MAPPED_INCIDENT: IncidentSummary = {
  ...UNMAPPED_INCIDENT,
  public_ref: 'CS-2026-MAPPED',
  location: {
    ...UNMAPPED_INCIDENT.location,
    latitude: 12.5,
    longitude: 77.5,
    is_mapped: true,
  },
}

/** A demo/development fixture (Phase 5B) — functional, but flagged. */
const DEMO_MAPPED_INCIDENT: IncidentSummary = {
  ...MAPPED_INCIDENT,
  public_ref: 'CS-2026-DEMO',
  location: {
    ...MAPPED_INCIDENT.location,
    code: 'DEMO-LIBRARY',
    name: 'DEMO — Sample Library (NOT A REAL LOCATION)',
    is_synthetic: true,
  },
}

const EMERGENCY_INCIDENT: IncidentSummary = {
  ...UNMAPPED_INCIDENT,
  public_ref: 'CS-2026-URGENT',
  is_emergency: true,
  is_ongoing: true,
  submission_mode: 'anonymous',
  reporter_contactable: false,
}

const DESTINATION = {
  location_id: 1,
  code: 'LKRC-MAIN',
  name: 'Library & Knowledge Resource Centre',
  latitude: null,
  longitude: null,
  is_mapped: false,
  navigable: false,
  location_type: 'library',
  is_indoor: true,
  dispatch_note: 'Enter via the service gate; lift to level 2.',
  zone_name: 'North Zone',
  location_hint: null,
  is_synthetic: false,
}

function detailFor(summary: IncidentSummary, overrides: Record<string, unknown> = {}) {
  return {
    ...summary,
    destination: DESTINATION,
    narrative: 'Someone followed me from the library to the car park.',
    narrative_available: true,
    evidence: [],
    reporter_contact_guidance:
      summary.submission_mode === 'anonymous'
        ? 'This report was submitted anonymously. There is no reporter to contact, and no identity is held in this system. Go to the location; do not attempt to work out who reported it.'
        : 'The reporter consented to contact about this report.',
    case_status_history: [
      {
        history_id: 1,
        from_status: null,
        to_status: 'submitted',
        changed_at: summary.submitted_at,
        remark: null,
        visible_to_reporter: true,
        resolution_reason: null,
      },
    ],
    assignment: null,
    ...overrides,
  }
}

function responderRoutes(
  items: IncidentSummary[] = [UNMAPPED_INCIDENT],
  overrides: Record<string, StubRoute> = {},
) {
  const mappingAvailable = items.some((item) => item.location.is_mapped)
  return signedInRoutes({
    '/auth/me': { body: ICC_ACCOUNT },
    '/incidents': {
      body: {
        items,
        pagination: { total: items.length, limit: 100, offset: 0, returned: items.length },
        mapping_available: mappingAvailable,
      },
    },
    ...overrides,
  })
}

function renderPage(routes = responderRoutes(), account: AccountIdentity = ICC_ACCOUNT) {
  setCurrentUser(makeUser())
  const fetchImpl = createFetchStub(routes)
  const view = renderWithAuth(<IncidentsPage />, { fetchImpl })
  return { ...view, fetchImpl, account }
}

const user = () => userEvent.setup({ delay: null })

/* -------------------------------------------------------------------------- */

describe('IncidentsPage — access', () => {
  it('tells a student this area is not theirs, without showing incidents', async () => {
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], { '/auth/me': { body: ACCOUNT } }),
      ACCOUNT,
    )

    expect(await screen.findByText(/this area is for campus responders/i)).toBeInTheDocument()
    expect(screen.queryByText('CS-2026-7QK4M2')).not.toBeInTheDocument()
  })

  it('surfaces a server refusal rather than pretending the queue is empty', async () => {
    // The frontend is never the boundary. If the server says no, this is what a
    // student who reached the route directly would see.
    renderPage(
      responderRoutes([], {
        '/incidents': {
          status: 403,
          body: {
            error: {
              code: 'AUTHORIZATION_ERROR',
              message: 'This account cannot view the responder map.',
              request_id: 'req-1',
            },
          },
        },
      }),
    )

    // ErrorState deliberately generalises a 403 rather than echoing the
    // server's wording. What matters is that the refusal is shown, not
    // swallowed into an empty-looking queue.
    expect(await screen.findByText(/not available to you/i)).toBeInTheDocument()
    expect(screen.queryByText('CS-2026-7QK4M2')).not.toBeInTheDocument()
  })
})

describe('IncidentsPage — the unmapped campus', () => {
  it('says mapping is pending instead of drawing an empty campus', async () => {
    renderPage()

    expect(await screen.findByTestId('map-empty-state')).toBeInTheDocument()
    expect(
      screen.getByText(/campus locations have not yet been verified/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/will become available after the campus location survey/i),
    ).toBeInTheDocument()
  })

  it('still lists every incident when nothing can be mapped', async () => {
    renderPage()

    expect(await screen.findByText('CS-2026-7QK4M2')).toBeInTheDocument()
    expect(screen.getAllByText(/library & knowledge resource centre/i).length).toBeGreaterThan(
      0,
    )
  })

  it('renders a map once a location has a verified coordinate', async () => {
    renderPage(responderRoutes([MAPPED_INCIDENT]))

    expect(
      await screen.findByRole('application', { name: /campus incident map/i }),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('map-empty-state')).not.toBeInTheDocument()
  })

  it('shows no demo-data notice for a real-style verified location', async () => {
    renderPage(responderRoutes([MAPPED_INCIDENT]))
    await screen.findByRole('application', { name: /campus incident map/i })
    expect(screen.queryByTestId('demo-data-notice')).not.toBeInTheDocument()
  })

  it('flags a demo-fixture incident on the map with a visible notice', async () => {
    renderPage(responderRoutes([DEMO_MAPPED_INCIDENT]))

    await screen.findByRole('application', { name: /campus incident map/i })
    expect(await screen.findByTestId('demo-data-notice')).toHaveTextContent(/demo/i)
  })
})

describe('IncidentsPage — the queue', () => {
  it('shows an empty state when nothing is routed to this role', async () => {
    renderPage(responderRoutes([]))

    expect(
      await screen.findByText(/no open incidents routed to your role/i),
    ).toBeInTheDocument()
  })

  it('marks emergency and ongoing incidents without alarming the whole page', async () => {
    renderPage(responderRoutes([EMERGENCY_INCIDENT, UNMAPPED_INCIDENT]))

    const queue = await screen.findByRole('region', { name: /queue/i })
    expect(within(queue).getByText('Emergency')).toBeInTheDocument()
    expect(within(queue).getByText('Ongoing')).toBeInTheDocument()
    // No siren language anywhere on the page.
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/alert!|urgent!|🚨/i)
  })

  it('banners an unacknowledged emergency, calmly', async () => {
    renderPage(responderRoutes([EMERGENCY_INCIDENT, UNMAPPED_INCIDENT]))

    const banner = await screen.findByTestId('attention-banner')
    expect(within(banner).getByText(/has not been acknowledged yet/i)).toBeInTheDocument()
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/alert!|urgent!|🚨/i)
  })

  it('does not banner once the emergency has a dispatch under way', async () => {
    renderPage(
      responderRoutes([
        {
          ...EMERGENCY_INCIDENT,
          dispatch: {
            dispatch_id: 'd-1',
            state: 'acknowledged',
            raised_at: '2026-08-11T14:05:00+00:00',
            acknowledged_at: '2026-08-11T14:06:00+00:00',
            dispatched_at: null,
            on_scene_at: null,
            closed_at: null,
            responder_note: null,
          },
        },
      ]),
    )

    await screen.findByRole('region', { name: /queue/i })
    expect(screen.queryByTestId('attention-banner')).not.toBeInTheDocument()
  })

  it('does not banner when nothing is an emergency', async () => {
    renderPage(responderRoutes([UNMAPPED_INCIDENT]))

    await screen.findByRole('region', { name: /queue/i })
    expect(screen.queryByTestId('attention-banner')).not.toBeInTheDocument()
  })

  it('shows how many photographs an incident carries', async () => {
    renderPage(responderRoutes([{ ...UNMAPPED_INCIDENT, evidence_count: 2 }]))
    expect(await screen.findByText('2 photos')).toBeInTheDocument()
  })

  it('flags a location conflict in the queue', async () => {
    renderPage(
      responderRoutes([
        {
          ...UNMAPPED_INCIDENT,
          location_signal: {
            resolution: 'conflicting',
            source: 'photo_exif',
            distance_m: 2400,
            signal_captured_at: null,
            note: 'The photograph is about 2400 m away.',
          },
        },
      ]),
    )
    expect(await screen.findByText(/location differs/i)).toBeInTheDocument()
  })
})

describe('IncidentsPage — incident detail', () => {
  async function openFirst(routes = responderRoutes()) {
    const u = user()
    const view = renderPage(routes)
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    return { ...view, u }
  }

  it('opens the incident a responder selects', async () => {
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
      }),
    )

    expect(
      await screen.findByRole('heading', { name: 'CS-2026-7QK4M2', level: 2 }),
    ).toBeInTheDocument()
    expect(screen.getByText(/someone followed me/i)).toBeInTheDocument()
  })

  it('gives directions a responder can use without a map', async () => {
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
      }),
    )

    expect(await screen.findByText(/enter via the service gate/i)).toBeInTheDocument()
    expect(screen.getByText('North Zone')).toBeInTheDocument()
    expect(
      screen.getByText(/no verified coordinate yet, so it cannot be opened in a map/i),
    ).toBeInTheDocument()
  })

  it('disables navigation when there is no coordinate rather than inventing one', async () => {
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
      }),
    )

    const navigate = await screen.findByRole('button', { name: /navigate to incident/i })
    expect(navigate).toBeDisabled()
  })

  it('offers a navigation link once the destination is mapped', async () => {
    const mappedDestination = {
      ...DESTINATION,
      latitude: 12.5,
      longitude: 77.5,
      is_mapped: true,
      navigable: true,
    }
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, { destination: mappedDestination }),
        },
      }),
    )

    const link = await screen.findByRole('link', { name: /navigate to/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('12.5'))
  })

  it('shows no demo notice or navigation caveat for a real-style destination', async () => {
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
      }),
    )
    await screen.findByRole('heading', { name: 'CS-2026-7QK4M2', level: 2 })
    expect(screen.queryByTestId('destination-demo-notice')).not.toBeInTheDocument()
  })

  it('marks a synthetic destination as DEMO MODE in the "Where" section', async () => {
    const demoDestination = { ...DESTINATION, is_synthetic: true }
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, { destination: demoDestination }),
        },
      }),
    )

    const notice = await screen.findByTestId('destination-demo-notice')
    expect(notice).toHaveTextContent(/demo mode/i)
    expect(notice).toHaveTextContent(/not a real, surveyed campus location/i)
  })

  it('appends "(DEMO)" to the navigate link for a synthetic destination', async () => {
    const demoDestination = {
      ...DESTINATION,
      latitude: 12.5,
      longitude: 77.5,
      is_mapped: true,
      navigable: true,
      is_synthetic: true,
    }
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, { destination: demoDestination }),
        },
      }),
    )

    const link = await screen.findByRole('link', { name: /navigate to.*\(demo\)/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('12.5'))
  })

  it('tells the responder plainly that an anonymous reporter cannot be contacted', async () => {
    const u = user()
    renderPage(
      responderRoutes([EMERGENCY_INCIDENT], {
        '/incidents/CS-2026-URGENT': { body: detailFor(EMERGENCY_INCIDENT) },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-URGENT/i }))

    expect(await screen.findByText(/the reporter cannot be contacted/i)).toBeInTheDocument()
    expect(screen.getByText(/do not attempt to work out who reported it/i)).toBeInTheDocument()
    // The limitation is stated, not glossed over.
    expect(
      screen.getByText(/cannot prevent someone at the scene from seeing who is present/i),
    ).toBeInTheDocument()
  })

  it('shows a location conflict with its innocent explanations', async () => {
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, {
            location_signal: {
              resolution: 'conflicting',
              source: 'photo_exif',
              distance_m: 2400,
              signal_captured_at: null,
              note: 'The reporter may have moved before reporting. Photo location data can also be edited.',
            },
          }),
        },
      }),
    )

    // The heading and the alert title both say it — deliberately, so it is
    // visible whether or not the alert is read.
    expect((await screen.findAllByText(/photo location differs/i)).length).toBeGreaterThan(0)
    expect(screen.getByText(/may have moved before reporting/i)).toBeInTheDocument()
  })

  it('says nothing about the location check when it is unremarkable', async () => {
    // `approximate` is the ordinary outcome and must not read as a finding.
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, {
            location_signal: {
              resolution: 'approximate',
              source: 'location_default',
              distance_m: null,
              signal_captured_at: null,
              note: null,
            },
          }),
        },
      }),
    )

    await screen.findByRole('heading', { name: 'CS-2026-7QK4M2', level: 2 })
    expect(screen.queryByText(/location check/i)).not.toBeInTheDocument()
  })

  it('explains a withheld narrative rather than showing an empty box', async () => {
    await openFirst(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, {
            narrative: null,
            narrative_available: false,
            narrative_withheld_reason: 'This category is handled confidentially by the ICC.',
          }),
        },
      }),
    )

    expect(await screen.findByText(/handled confidentially by the icc/i)).toBeInTheDocument()
  })
})

describe('IncidentsPage — evidence', () => {
  it('says so plainly when no photograph was attached', async () => {
    const u = user()
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(await screen.findByText(/no photographs were attached/i)).toBeInTheDocument()
  })

  it('fetches the image through the API rather than a URL in the markup', async () => {
    const u = user()
    const { fetchImpl } = renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, {
            evidence: [{ evidence_id: 'ev-1' }],
            evidence_count: 1,
          }),
        },
        '/evidence/ev-1': { body: {} },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    const image = await screen.findByRole('img', { name: /photograph 1 of 1/i })
    // An object URL, created in this tab from bytes fetched with the bearer
    // token. Never a bucket path and never a signed URL.
    expect(image.getAttribute('src')).toMatch(/^blob:/)

    const evidenceCall = fetchImpl.calls.find((call) => call.url.includes('/evidence/ev-1'))
    expect(evidenceCall).toBeDefined()
    expect(new Headers(evidenceCall?.init?.headers).get('Authorization')).toMatch(/^Bearer /)
  })

  it('states what sanitisation did and did not do', async () => {
    const u = user()
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, { evidence: [{ evidence_id: 'ev-1' }] }),
        },
        '/evidence/ev-1': { body: {} },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(
      await screen.findByText(/hidden location and device data have been removed/i),
    ).toBeInTheDocument()
    // The claim the system must not make.
    expect(screen.getByText(/does not on its own establish it/i)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/proves the incident/i)
  })

  it('reports an unavailable image without guessing why', async () => {
    const u = user()
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, { evidence: [{ evidence_id: 'ev-1' }] }),
        },
        '/evidence/ev-1': { status: 404, body: {} },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(await screen.findByText(/not available to you/i)).toBeInTheDocument()
  })
})

describe('IncidentsPage — dispatch', () => {
  const dispatchRoutes = (state: string | null, isEmergency = true) => {
    const summary = {
      ...UNMAPPED_INCIDENT,
      is_emergency: isEmergency,
      dispatch:
        state === null
          ? null
          : {
              dispatch_id: 'd-1',
              state,
              raised_at: '2026-08-11T14:30:00+00:00',
              acknowledged_at: state === 'pending' ? null : '2026-08-11T14:31:00+00:00',
              dispatched_at: null,
              on_scene_at: null,
              closed_at: null,
              responder_note: null,
            },
    }
    return responderRoutes([summary as IncidentSummary], {
      '/incidents/CS-2026-7QK4M2': { body: detailFor(summary as IncidentSummary) },
      '/incidents/CS-2026-7QK4M2/dispatch': {
        status: 201,
        body: { dispatch_id: 'd-1', state: 'pending', raised_at: '2026-08-11T14:30:00+00:00' },
      },
    })
  }

  it('offers a dispatch on an emergency incident', async () => {
    const u = user()
    renderPage(dispatchRoutes(null))
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(await screen.findByRole('button', { name: /raise dispatch/i })).toBeEnabled()
  })

  it('does not offer a dispatch on a non-emergency report', async () => {
    // A database rule: `trg_dispatch_requires_emergency`. The UI reflects it
    // rather than letting a responder discover it through a 409.
    const u = user()
    renderPage(dispatchRoutes(null, false))
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(await screen.findByRole('button', { name: /raise dispatch/i })).toBeDisabled()
  })

  it('raises a dispatch and re-reads from the server', async () => {
    const u = user()
    const { fetchImpl } = renderPage(dispatchRoutes(null))
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    await u.click(await screen.findByRole('button', { name: /raise dispatch/i }))

    await waitFor(() => {
      expect(
        fetchImpl.calls.some(
          (call) => call.url.endsWith('/dispatch') && call.method === 'POST',
        ),
      ).toBe(true)
    })
  })

  it('offers only the transitions the state machine allows', async () => {
    const u = user()
    renderPage(dispatchRoutes('pending'))
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(await screen.findByRole('button', { name: /^acknowledge$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /stand down/i })).toBeInTheDocument()
    // A responder cannot arrive without setting off.
    expect(screen.queryByRole('button', { name: /arrived on scene/i })).not.toBeInTheDocument()
  })

  it('advances to on-scene from dispatched', async () => {
    const u = user()
    renderPage(dispatchRoutes('dispatched'))
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(
      await screen.findByRole('button', { name: /arrived on scene/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^acknowledge$/i })).not.toBeInTheDocument()
  })

  it('offers nothing further once a dispatch is closed', async () => {
    const u = user()
    renderPage(dispatchRoutes('closed'))
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    await screen.findByRole('heading', { name: 'CS-2026-7QK4M2', level: 2 })
    expect(
      screen.queryByRole('button', { name: /acknowledge|stand down|arrived/i }),
    ).toBeNull()
  })

  it('surfaces a rejected transition instead of silently doing nothing', async () => {
    const u = user()
    renderPage(
      responderRoutes([{ ...UNMAPPED_INCIDENT, is_emergency: true }], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor({ ...UNMAPPED_INCIDENT, is_emergency: true }),
        },
        '/incidents/CS-2026-7QK4M2/dispatch': {
          status: 409,
          body: {
            error: {
              code: 'CONFLICT',
              message: 'A dispatch is already open for this incident.',
              request_id: 'req-2',
            },
          },
        },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    await u.click(await screen.findByRole('button', { name: /raise dispatch/i }))

    expect(await screen.findByText(/a dispatch is already open/i)).toBeInTheDocument()
  })
})

describe('IncidentsPage — privacy', () => {
  it('never renders a reporter identity, path, or filename', async () => {
    const u = user()
    renderPage(
      responderRoutes([EMERGENCY_INCIDENT], {
        '/incidents/CS-2026-URGENT': {
          body: detailFor(EMERGENCY_INCIDENT, { evidence: [{ evidence_id: 'ev-1' }] }),
        },
        '/evidence/ev-1': { body: {} },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-URGENT/i }))
    await screen.findByRole('heading', { name: 'CS-2026-URGENT', level: 2 })

    const markup = document.body.innerHTML
    expect(markup).not.toMatch(/user_id/i)
    expect(markup).not.toMatch(/storage_path/i)
    expect(markup).not.toMatch(/\.jpg|\.jpeg|\.png/i)
    expect(markup).not.toMatch(/firebasestorage|googleapis/i)
  })
})

describe('IncidentsPage — the queue status filter', () => {
  it('defaults to every open status', async () => {
    const { fetchImpl } = renderPage(responderRoutes([UNMAPPED_INCIDENT]))
    await screen.findByText('CS-2026-7QK4M2')

    const call = fetchImpl.calls.find((c) => c.url.includes('/incidents?'))
    expect(call?.url).not.toMatch(/status=/)
    expect(screen.getByRole('button', { name: 'All open' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('requests only the selected status once a filter chip is chosen', async () => {
    const u = user()
    const { fetchImpl } = renderPage(responderRoutes([UNMAPPED_INCIDENT]))
    await screen.findByText('CS-2026-7QK4M2')

    await u.click(screen.getByRole('button', { name: 'Triaged' }))

    await waitFor(() => {
      expect(
        fetchImpl.calls.some(
          (c) => c.url.includes('/incidents?') && c.url.includes('status=triaged'),
        ),
      ).toBe(true)
    })
  })

  it('returns to every open status via "All open"', async () => {
    const u = user()
    const { fetchImpl } = renderPage(responderRoutes([UNMAPPED_INCIDENT]))
    await screen.findByText('CS-2026-7QK4M2')

    await u.click(screen.getByRole('button', { name: 'Triaged' }))
    await u.click(screen.getByRole('button', { name: 'All open' }))

    await waitFor(() => {
      const latest = fetchImpl.calls.filter((c) => c.url.includes('/incidents?')).at(-1)
      expect(latest?.url).not.toMatch(/status=/)
    })
  })

  it('does not offer terminal statuses as filter chips', async () => {
    renderPage(responderRoutes([UNMAPPED_INCIDENT]))
    await screen.findByText('CS-2026-7QK4M2')

    const filterGroup = screen.getByRole('group', { name: /filter by case status/i })
    expect(within(filterGroup).queryByText('Resolved')).not.toBeInTheDocument()
    expect(within(filterGroup).queryByText('Withdrawn')).not.toBeInTheDocument()
  })
})

describe('IncidentsPage — the queue shows case status and ownership', () => {
  it('shows the case status and whether it is assigned', async () => {
    renderPage(
      responderRoutes([{ ...UNMAPPED_INCIDENT, status: 'triaged', is_assigned: true }]),
    )

    const queue = await screen.findByRole('region', { name: /queue/i })
    expect(within(queue).getByText('Triaged')).toBeInTheDocument()
    expect(within(queue).getByText('Assigned')).toBeInTheDocument()
  })

  it('marks an unowned case as unassigned', async () => {
    renderPage(responderRoutes([{ ...UNMAPPED_INCIDENT, is_assigned: false }]))

    const queue = await screen.findByRole('region', { name: /queue/i })
    expect(within(queue).getByText('Unassigned')).toBeInTheDocument()
  })
})

describe('IncidentsPage — the case lifecycle panel', () => {
  it('renders inside the incident detail, distinct from dispatch', async () => {
    const u = user()
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))

    expect(await screen.findByTestId('case-lifecycle')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Case status' })).toBeInTheDocument()
  })

  it('claims an unassigned case and reloads the incident', async () => {
    const u = user()
    const { fetchImpl } = renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
        '/incidents/CS-2026-7QK4M2/assign': {
          status: 201,
          body: {
            assignment_id: 1,
            assigned_at: '2026-08-11T14:10:00+00:00',
            assignee: { role: 'icc', label: 'ICC Member' },
            assigned_by: null,
            note: null,
          },
        },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    await u.click(await screen.findByRole('button', { name: /assign to me/i }))

    await waitFor(() => {
      expect(
        fetchImpl.calls.some((c) => c.url.endsWith('/assign') && c.method === 'POST'),
      ).toBe(true)
    })
  })

  it('moves a case status with a remark', async () => {
    const u = user()
    const { fetchImpl } = renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
        '/incidents/CS-2026-7QK4M2/status': {
          status: 201,
          body: {
            history_id: 2,
            from_status: 'submitted',
            to_status: 'triaged',
            changed_at: '2026-08-11T14:05:00+00:00',
            remark: null,
            visible_to_reporter: true,
            resolution_reason: null,
          },
        },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    const casePanel = await screen.findByTestId('case-lifecycle')
    await u.click(within(casePanel).getByRole('button', { name: 'Triaged' }))
    await u.click(await screen.findByRole('button', { name: /confirm/i }))

    await waitFor(() => {
      const call = fetchImpl.calls.find(
        (c) => c.url.endsWith('/status') && c.method === 'POST',
      )
      expect(call).toBeDefined()
      const body = JSON.parse(String(call?.init?.body)) as { target: string }
      expect(body.target).toBe('triaged')
    })
  })

  it('surfaces a rejected transition instead of silently doing nothing', async () => {
    const u = user()
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': { body: detailFor(UNMAPPED_INCIDENT) },
        '/incidents/CS-2026-7QK4M2/status': {
          status: 409,
          body: {
            error: {
              code: 'CONFLICT',
              message: 'under_review requires the case to be assigned first.',
              request_id: 'req-3',
            },
          },
        },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    const casePanel = await screen.findByTestId('case-lifecycle')
    await u.click(within(casePanel).getByRole('button', { name: 'Triaged' }))
    await u.click(await screen.findByRole('button', { name: /confirm/i }))

    expect(
      await screen.findByText(/requires the case to be assigned first/i),
    ).toBeInTheDocument()
  })

  it('shows the full status history bundled in the incident detail', async () => {
    const u = user()
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, {
            case_status_history: [
              {
                history_id: 1,
                from_status: null,
                to_status: 'submitted',
                changed_at: '2026-08-11T14:00:00+00:00',
                remark: null,
                visible_to_reporter: true,
                resolution_reason: null,
              },
              {
                history_id: 2,
                from_status: 'submitted',
                to_status: 'triaged',
                changed_at: '2026-08-11T14:05:00+00:00',
                remark: 'Reviewed the account.',
                visible_to_reporter: true,
                resolution_reason: null,
              },
            ],
          }),
        },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    await u.click(await screen.findByText(/status history/i))

    expect(await screen.findByText('Reviewed the account.')).toBeInTheDocument()
  })
})

describe('IncidentsPage — case lifecycle privacy', () => {
  it('renders no bare user id in the assignment or status history', async () => {
    const u = user()
    renderPage(
      responderRoutes([UNMAPPED_INCIDENT], {
        '/incidents/CS-2026-7QK4M2': {
          body: detailFor(UNMAPPED_INCIDENT, {
            assignment: {
              assignment_id: 1,
              assigned_at: '2026-08-11T14:10:00+00:00',
              assignee: { role: 'icc', label: 'ICC Member' },
              assigned_by: { role: 'icc', label: 'ICC Member' },
              note: null,
            },
          }),
        },
      }),
    )
    await u.click(await screen.findByRole('button', { name: /CS-2026-7QK4M2/i }))
    await screen.findByTestId('case-lifecycle')

    const markup = document.body.innerHTML
    expect(markup).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i)
  })
})
