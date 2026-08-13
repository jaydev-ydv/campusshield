import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from './ErrorBoundary'

function Bomb(): never {
  throw new Error('deliberate render failure')
}

describe('ErrorBoundary', () => {
  it('renders children normally when nothing throws', () => {
    render(
      <ErrorBoundary>
        <p>ordinary content</p>
      </ErrorBoundary>,
    )

    expect(screen.getByText('ordinary content')).toBeInTheDocument()
  })

  it('catches a render exception and shows a recovery screen instead of a blank page', () => {
    // React logs the error to the console by default even inside a boundary;
    // silence it so the test output stays about assertions, not the
    // deliberate failure this test triggers.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    )

    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()

    consoleError.mockRestore()
  })

  it('does not show the report content or narrative in the recovery screen', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    )

    expect(document.body.textContent).not.toMatch(/deliberate render failure/i)

    consoleError.mockRestore()
  })
})
