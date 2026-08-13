import { describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiError } from './apiClient'
import { createFetchStub } from '../test/harness'

const BASE = 'http://api.test/api/v1'

function client(
  routes = {},
  getToken: () => Promise<string | null> = async () => 'token-abc',
) {
  const fetchImpl = createFetchStub(routes)
  return { api: new ApiClient({ baseUrl: BASE, getToken, fetchImpl }), fetchImpl }
}

describe('ApiClient — token injection', () => {
  it('sends the Firebase ID token as a bearer credential', async () => {
    const { api, fetchImpl } = client({ '/locations': { body: { items: [] } } })

    await api.get('/locations')

    expect(fetchImpl.headersFor('/locations')?.get('Authorization')).toBe('Bearer token-abc')
  })

  it('requests a fresh token for every call', async () => {
    // The SDK returns the cached token and refreshes it near expiry, so asking
    // each time is how the one-hour lifetime is handled without tracking it.
    const getToken = vi.fn(async () => 'token-abc')
    const { api } = client({ '/locations': { body: { items: [] } } }, getToken)

    await api.get('/locations')
    await api.get('/locations')

    expect(getToken).toHaveBeenCalledTimes(2)
  })

  it('fails locally rather than sending an unauthenticated request', async () => {
    // An empty-handed request comes back 401, which is indistinguishable from an
    // expired session and sends the user to sign in again for no reason.
    const { api, fetchImpl } = client(
      { '/locations': { body: { items: [] } } },
      async () => null,
    )

    await expect(api.get('/locations')).rejects.toMatchObject({ code: 'NO_CREDENTIAL' })
    expect(fetchImpl.calls).toHaveLength(0)
  })

  it('omits the header on an explicitly public call', async () => {
    const { api, fetchImpl } = client({ '/health': { body: { status: 'ok' } } })

    await api.get('/health', { auth: false })

    expect(fetchImpl.headersFor('/health')?.get('Authorization')).toBeNull()
  })

  it('never puts the token in the URL', async () => {
    // A token in a query string reaches server logs, browser history, and the
    // Referer header on any outbound link.
    const { api, fetchImpl } = client({ '/locations': { body: { items: [] } } })

    await api.get('/locations')

    expect(fetchImpl.calls[0].url).not.toContain('token-abc')
  })
})

describe('ApiClient — error handling', () => {
  it('preserves the backend error envelope', async () => {
    const { api } = client({
      '/reports': {
        status: 400,
        body: {
          error: {
            code: 'VALIDATION_ERROR',
            message: 'The request body failed validation.',
            request_id: 'req-123',
            details: { fields: { category_id: ['No such active category.'] } },
          },
        },
      },
    })

    const error = (await api.get('/reports').catch((e) => e)) as ApiError

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(400)
    expect(error.code).toBe('VALIDATION_ERROR')
    expect(error.requestId).toBe('req-123')
    expect(error.fieldErrors.category_id).toEqual(['No such active category.'])
  })

  it('flags 401 so the caller can distinguish an ended session', async () => {
    const { api } = client({
      '/auth/me': {
        status: 401,
        body: {
          error: {
            code: 'UNAUTHENTICATED',
            message: 'Authentication is required.',
            request_id: 'r',
          },
        },
      },
    })

    const error = (await api.get('/auth/me').catch((e) => e)) as ApiError
    expect(error.isUnauthenticated).toBe(true)
  })

  it('turns a network failure into an ApiError rather than a raw throw', async () => {
    const api = new ApiClient({
      baseUrl: BASE,
      getToken: async () => 'token-abc',
      fetchImpl: async () => {
        throw new TypeError('Failed to fetch')
      },
    })

    const error = (await api.get('/locations').catch((e) => e)) as ApiError

    expect(error).toBeInstanceOf(ApiError)
    expect(error.isOffline).toBe(true)
    expect(error.message).toMatch(/could not reach/i)
  })

  it('recovers a request id from the header when the body is not JSON', async () => {
    // A proxy 502 returns HTML. The reference still has to survive, because it
    // is the only link between what a user saw and what the server logged.
    const api = new ApiClient({
      baseUrl: BASE,
      getToken: async () => 'token-abc',
      fetchImpl: async () =>
        new Response('<html>Bad Gateway</html>', {
          status: 502,
          headers: { 'X-Request-ID': 'proxy-req-9' },
        }),
    })

    const error = (await api.get('/locations').catch((e) => e)) as ApiError

    expect(error.status).toBe(502)
    expect(error.requestId).toBe('proxy-req-9')
    expect(error.message).toMatch(/having trouble/i)
  })

  it('handles a 204 with no body', async () => {
    const api = new ApiClient({
      baseUrl: BASE,
      getToken: async () => 'token-abc',
      fetchImpl: async () => new Response(null, { status: 204 }),
    })

    await expect(api.get('/thing')).resolves.toBeUndefined()
  })

  it('sends JSON with the right content type on a POST', async () => {
    const { api, fetchImpl } = client({ '/auth/register': { status: 201, body: {} } })

    await api.post('/auth/register', { email: 'a@b.com' })

    const call = fetchImpl.calls[0]
    expect(new Headers(call.init?.headers).get('Content-Type')).toBe('application/json')
    expect(call.init?.body).toBe('{"email":"a@b.com"}')
  })
})
