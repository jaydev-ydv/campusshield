import type { ReactNode } from 'react'

import { ApiError } from '../../lib/apiClient'
import { Alert } from './Alert'
import { Button } from './Button'

interface ErrorStateProps {
  error: unknown
  onRetry?: () => void
  /** Overrides the derived message when the caller has better context. */
  title?: string
}

/**
 * Renders a failure so a person can act on it.
 *
 * An error the user cannot do anything about is noise, so each case says what to
 * try. The `request_id` is surfaced whenever the backend supplied one: it is the
 * only thing that ties what a student saw to what the server logged, and asking
 * someone to describe a screen from memory is much worse than asking them to
 * read out a reference.
 */
export function ErrorState({ error, onRetry, title }: ErrorStateProps) {
  let heading = title ?? 'Something went wrong'
  let body: ReactNode = 'Please try again.'
  let requestId: string | undefined

  if (error instanceof ApiError) {
    requestId = error.requestId
    body = error.message
    if (error.isOffline) {
      heading = title ?? 'Cannot reach CampusShield'
      body = 'Check your connection. If you are on campus Wi-Fi, try switching networks.'
    } else if (error.status === 401) {
      heading = title ?? 'Your session has ended'
      body = 'Sign in again to continue.'
    } else if (error.status === 403) {
      heading = title ?? 'Not available to you'
      body = 'Your account does not have access to this.'
    } else if (error.status >= 500) {
      heading = title ?? 'The service is having trouble'
      body = 'This is not something you did. Please try again shortly.'
    }
  } else if (error instanceof Error) {
    body = error.message
  }

  return (
    <Alert tone="error" title={heading} requestId={requestId}>
      <p>{body}</p>
      {onRetry && (
        <div className="mt-3">
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Try again
          </Button>
        </div>
      )}
    </Alert>
  )
}

interface EmptyStateProps {
  title: string
  children?: ReactNode
}

/**
 * Nothing to show, said plainly.
 *
 * Distinct from an error on purpose. Right now `GET /locations` legitimately
 * returns nothing — no campus coordinates have been verified yet — and a screen
 * that renders that as a failure would send someone debugging a system working
 * exactly as intended.
 */
export function EmptyState({ title, children }: EmptyStateProps) {
  return (
    <div className="border-ink-200 rounded-lg border border-dashed px-4 py-8 text-center">
      <p className="text-ink-700 text-sm font-medium">{title}</p>
      {children && (
        <div className="text-ink-500 mx-auto mt-1 max-w-sm text-sm">{children}</div>
      )}
    </div>
  )
}
