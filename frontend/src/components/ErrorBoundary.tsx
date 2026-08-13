import { Component, type ErrorInfo, type ReactNode } from 'react'

import { AuthLayout } from './layout/AppShell'
import { Button } from './ui/Button'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * The last resort, for the one class of failure every other screen's
 * loading/empty/error states cannot cover: a genuine, unexpected render
 * exception. Everything reachable through a normal API call already renders
 * `ErrorState`/`EmptyState` explicitly (see `useCatalog`, `IncidentsPage`,
 * `MyReportsPage`) — this exists only so a real bug produces a page telling
 * a student or responder to reload, instead of a blank white screen with no
 * way forward.
 *
 * Must be a class component: React has no hook equivalent for
 * `componentDidCatch`/`getDerivedStateFromError`.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // No narrative, no report content, no identity ever reaches this layer —
    // a render exception's own message and component stack are the only
    // things logged, matching every other client-side log statement in this
    // codebase.
    console.error('Unhandled render error', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <AuthLayout
          title="Something went wrong"
          subtitle="This page hit an unexpected error. Reloading usually fixes it."
          footer={
            <Button variant="primary" onClick={() => window.location.reload()}>
              Reload
            </Button>
          }
        >
          <p className="text-ink-600 text-sm">
            If this keeps happening, it is worth reporting — nothing you entered was lost on
            the server.
          </p>
        </AuthLayout>
      )
    }
    return this.props.children
  }
}
