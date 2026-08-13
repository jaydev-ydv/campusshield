/**
 * Test harness.
 *
 * Firebase is replaced at the module boundary (see `firebaseMock.ts`) and the
 * network is replaced by a route-table fetch stub. Nothing this application owns
 * is stubbed: the auth context, route guards, forms, and API client all run for
 * real against those two seams.
 */
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'

import { AuthProvider } from '../auth/AuthContext'
import { ApiClient } from '../lib/apiClient'
import { authState } from './firebaseMock'

export { TEST_TOKEN, makeUser, setCurrentUser, firebaseError } from './firebaseMock'

export interface StubRoute {
  status?: number
  body?: unknown
  delayMs?: number
}

export interface StubCall {
  url: string
  init?: RequestInit
  method: string
}

export interface FetchStub {
  (input: RequestInfo | URL, init?: RequestInit): Promise<Response>
  calls: StubCall[]
  headersFor(fragment: string): Headers | undefined
  /** Parsed JSON body of the first matching request, for asserting payloads. */
  bodyFor(fragment: string, method?: string): Record<string, unknown> | undefined
}

/** A fetch implementation driven by a route table keyed on URL fragments. */
export function createFetchStub(routes: Record<string, StubRoute>): FetchStub {
  const calls: StubCall[] = []

  const stub = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init, method: (init?.method ?? 'GET').toUpperCase() })

    // Longest match first, so "/auth/me" is not shadowed by a broader "/auth".
    const key = Object.keys(routes)
      .filter((route) => url.includes(route))
      .sort((a, b) => b.length - a.length)[0]
    const route = key ? routes[key] : undefined

    if (!route) {
      return new Response(
        JSON.stringify({
          error: { code: 'NOT_FOUND', message: 'No stub route', request_id: 'stub' },
        }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (route.delayMs) await new Promise((resolve) => setTimeout(resolve, route.delayMs))

    return new Response(route.body === undefined ? '' : JSON.stringify(route.body), {
      status: route.status ?? 200,
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'stub-request-id' },
    })
  }) as FetchStub

  stub.calls = calls
  stub.headersFor = (fragment: string) => {
    const call = calls.find((c) => c.url.includes(fragment))
    return call ? new Headers(call.init?.headers) : undefined
  }
  stub.bodyFor = (fragment: string, method = 'POST') => {
    const call = calls.find((c) => c.url.includes(fragment) && c.method === method)
    if (!call?.init?.body) return undefined
    return JSON.parse(String(call.init.body)) as Record<string, unknown>
  }
  return stub
}

export const ACCOUNT = {
  user_id: '11111111-1111-1111-1111-111111111111',
  email: 'student@example.edu',
  role: 'student' as const,
  is_active: true,
  display_name: null,
}

export const LOCATIONS = [
  {
    location_id: 1,
    code: 'LKRC-MAIN',
    name: 'Library & Knowledge Resource Centre',
    location_type: 'library',
    zone: null,
    latitude: null,
    longitude: null,
    is_indoor: true,
    dispatch_note: null,
    is_synthetic: false,
  },
]

export const CATEGORIES = [
  {
    category_id: 1,
    code: 'HARASS_VERBAL',
    label: 'Verbal harassment',
    kind: 'incident' as const,
    emergency_eligible: true,
    requires_confidentiality: true,
  },
  {
    category_id: 2,
    code: 'LIGHTING_POOR',
    label: 'Poor lighting',
    kind: 'concern' as const,
    emergency_eligible: false,
    requires_confidentiality: false,
  },
]

/** The common signed-in case: identity plus both catalogue endpoints. */
export function signedInRoutes(overrides: Record<string, StubRoute> = {}) {
  return {
    '/auth/me': { body: ACCOUNT },
    '/locations': { body: { items: LOCATIONS } },
    '/categories': { body: { items: CATEGORIES } },
    ...overrides,
  }
}

interface RenderOptions {
  fetchImpl?: typeof fetch
  route?: string
}

export function renderWithAuth(ui: ReactNode, options: RenderOptions = {}) {
  const client = new ApiClient({
    baseUrl: 'http://api.test/api/v1',
    getToken: async () => (authState.currentUser ? authState.currentUser.getIdToken() : null),
    fetchImpl: options.fetchImpl ?? createFetchStub({}),
  })

  // A placeholder Auth object. The `firebase/auth` module is mocked, so the
  // functions that would read its internals never do — but passing one keeps
  // AuthProvider off the `getFirebaseAuth()` path, which would otherwise report
  // "unavailable" because tests have no VITE_FIREBASE_* variables.
  //
  // `currentUser` is a live getter onto `authState`, not a static `null`:
  // `AuthContext.refreshAccount` reads `auth.currentUser` directly (by design —
  // state can lag a render behind), so a static placeholder would make any
  // caller of `refreshAccount` appear signed out mid-test.
  const authInstance = {
    get currentUser() {
      return authState.currentUser
    },
  }

  const result = render(
    <AuthProvider authInstance={authInstance as never} apiClient={client}>
      <MemoryRouter initialEntries={[options.route ?? '/']}>{ui}</MemoryRouter>
    </AuthProvider>,
  )

  return { ...result, client }
}
