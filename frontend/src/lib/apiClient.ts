/**
 * The single door between this application and the Flask API.
 *
 * Every request goes through here, which means token injection and error
 * normalisation happen exactly once. A component that calls `fetch` directly is
 * a component that will eventually forget the Authorization header, or handle a
 * 401 differently from every other screen.
 *
 * Two responsibilities:
 *
 * 1. **Attach a fresh Firebase ID token.** `getIdToken()` returns the cached
 *    token and refreshes it automatically when it is close to expiring, so this
 *    never has to reason about the one-hour lifetime itself.
 * 2. **Turn every failure into one `ApiError` shape.** The backend already
 *    returns a consistent envelope; network failures and non-JSON responses do
 *    not, and callers should not have to tell them apart.
 */

export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
  details?: Record<string, unknown>
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string
  readonly details?: Record<string, unknown>

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.requestId = body.request_id
    this.details = body.details
  }

  /** Field-level messages from marshmallow, when the failure was validation. */
  get fieldErrors(): Record<string, string[]> {
    const fields = this.details?.fields
    return typeof fields === 'object' && fields !== null
      ? (fields as Record<string, string[]>)
      : {}
  }

  /** The caller is signed in to Firebase but has no application account yet. */
  get isUnauthenticated(): boolean {
    return this.status === 401
  }

  get isOffline(): boolean {
    return this.status === 0
  }
}

export type TokenProvider = () => Promise<string | null>

export interface ApiClientOptions {
  baseUrl?: string
  getToken?: TokenProvider
  fetchImpl?: typeof fetch
}

const DEFAULT_BASE_URL =
  (import.meta.env?.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:5000/api/v1'

export class ApiClient {
  private readonly baseUrl: string
  private readonly getToken: TokenProvider
  private readonly fetchImpl: typeof fetch

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, '')
    this.getToken = options.getToken ?? (async () => null)
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
  }

  async request<T>(path: string, init: RequestInit & { auth?: boolean } = {}): Promise<T> {
    const { auth = true, headers, ...rest } = init
    const requestHeaders = new Headers(headers)
    requestHeaders.set('Accept', 'application/json')
    // FormData must set its own Content-Type: the browser appends the multipart
    // boundary, and overriding it produces a body the server cannot parse.
    const isFormData = typeof FormData !== 'undefined' && rest.body instanceof FormData
    if (rest.body !== undefined && !isFormData && !requestHeaders.has('Content-Type')) {
      requestHeaders.set('Content-Type', 'application/json')
    }

    if (auth) {
      const token = await this.getToken()
      if (!token) {
        // Failed here rather than sent unauthenticated. A request without a
        // token comes back 401, which is indistinguishable from an expired
        // session and sends the user to sign in again for no reason.
        throw new ApiError(401, {
          code: 'NO_CREDENTIAL',
          message: 'You are not signed in.',
          request_id: 'client',
        })
      }
      requestHeaders.set('Authorization', `Bearer ${token}`)
    }

    let response: Response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...rest,
        headers: requestHeaders,
      })
    } catch (cause) {
      // A CORS rejection and a dead server are indistinguishable from here — the
      // browser deliberately withholds the difference — so the message names
      // both rather than guessing.
      throw new ApiError(0, {
        code: 'NETWORK_ERROR',
        message:
          'Could not reach the CampusShield service. Check your connection, or that the API is running.',
        request_id: 'client',
        details: { cause: String(cause) },
      })
    }

    if (response.status === 204) return undefined as T

    const raw = await response.text()
    let parsed: unknown = undefined
    if (raw) {
      try {
        parsed = JSON.parse(raw)
      } catch {
        parsed = undefined
      }
    }

    if (!response.ok) {
      const envelope = (parsed as { error?: ApiErrorBody } | undefined)?.error
      throw new ApiError(
        response.status,
        envelope ?? {
          code: 'UNEXPECTED_RESPONSE',
          message:
            response.status >= 500
              ? 'The service is having trouble. Please try again shortly.'
              : 'The service returned an unexpected response.',
          // Fall back to the header: the backend sets X-Request-ID on every
          // response, so a reference survives even when the body does not.
          request_id: response.headers.get('X-Request-ID') ?? 'unknown',
        },
      )
    }

    return parsed as T
  }

  get<T>(path: string, init?: RequestInit & { auth?: boolean }) {
    return this.request<T>(path, { ...init, method: 'GET' })
  }

  /**
   * Fetch binary content — currently only evidence images.
   *
   * A separate path because `request` parses every response as JSON. It exists
   * at all because evidence is served behind an `Authorization` header, and a
   * plain `<img src="...">` cannot send one. Fetching to a Blob and rendering
   * from an object URL is what keeps the image behind the same authorisation
   * check as everything else, rather than requiring a public or signed URL —
   * which the whole evidence design refuses to mint.
   */
  async getBlob(path: string): Promise<Blob> {
    const headers = new Headers()
    const token = await this.getToken()
    if (!token) {
      throw new ApiError(401, {
        code: 'NO_CREDENTIAL',
        message: 'You are signed out. Sign in again to view this.',
        request_id: 'client',
      })
    }
    headers.set('Authorization', `Bearer ${token}`)

    let response: Response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, { method: 'GET', headers })
    } catch (cause) {
      throw new ApiError(0, {
        code: 'NETWORK_ERROR',
        message: 'Could not reach the CampusShield service.',
        request_id: 'client',
        details: { cause: String(cause) },
      })
    }

    if (!response.ok) {
      throw new ApiError(response.status, {
        code: response.status === 404 ? 'NOT_FOUND' : 'UNEXPECTED_RESPONSE',
        // 404 is what the server returns both for "no such evidence" and for
        // "not yours", deliberately. The message must not distinguish them
        // either, or it would become the oracle the status code refuses to be.
        message:
          response.status === 404
            ? 'This image is not available to you.'
            : 'The image could not be loaded.',
        request_id: response.headers.get('X-Request-ID') ?? 'unknown',
      })
    }

    return response.blob()
  }

  post<T>(path: string, body?: unknown, init?: RequestInit & { auth?: boolean }) {
    const isFormData = typeof FormData !== 'undefined' && body instanceof FormData
    return this.request<T>(path, {
      ...init,
      method: 'POST',
      body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
    })
  }

  patch<T>(path: string, body?: unknown, init?: RequestInit & { auth?: boolean }) {
    return this.request<T>(path, {
      ...init,
      method: 'PATCH',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  delete<T>(path: string, init?: RequestInit & { auth?: boolean }) {
    return this.request<T>(path, { ...init, method: 'DELETE' })
  }
}
