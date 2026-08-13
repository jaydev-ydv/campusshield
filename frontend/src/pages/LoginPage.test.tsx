import { describe, expect, it, vi } from 'vitest'
import type { User } from 'firebase/auth'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { LoginPage } from './LoginPage'
import { validateLogin } from '../lib/validation'
import {
  createFetchStub,
  firebaseError,
  makeUser,
  renderWithAuth,
  signedInRoutes,
} from '../test/harness'
import { authState } from '../test/firebaseMock'

describe('validateLogin', () => {
  it('requires an email address', () => {
    expect(validateLogin('', 'password123').email).toBe('Enter your email address.')
  })

  it.each(['not-an-email', 'missing@domain', '@example.edu', 'spaces in@email.com'])(
    'rejects %s',
    (value) => {
      expect(validateLogin(value, 'password123').email).toBeDefined()
    },
  )

  it('requires a password', () => {
    expect(validateLogin('student@example.edu', '').password).toBe('Enter your password.')
  })

  it('does not impose a length rule on sign-in', () => {
    // An existing account may predate any rule introduced later. "Too short" on
    // a password that works is a dead end.
    expect(validateLogin('student@example.edu', 'ab').password).toBeUndefined()
  })

  it('accepts a valid pair', () => {
    expect(validateLogin('student@example.edu', 'password123')).toEqual({})
  })
})

describe('LoginPage', () => {
  it('renders accessible labelled fields', () => {
    renderWithAuth(<LoginPage />, { route: '/login' })

    expect(screen.getByLabelText(/email address/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('marks fields for password managers and mobile keyboards', () => {
    renderWithAuth(<LoginPage />, { route: '/login' })

    expect(screen.getByLabelText(/email address/i)).toHaveAttribute('autocomplete', 'email')
    expect(screen.getByLabelText(/password/i)).toHaveAttribute(
      'autocomplete',
      'current-password',
    )
    expect(screen.getByLabelText(/email address/i)).toHaveAttribute('inputmode', 'email')
  })

  it('shows validation errors without contacting Firebase', async () => {
    const user = userEvent.setup()
    renderWithAuth(<LoginPage />, { route: '/login' })

    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    const { signInWithEmailAndPassword } = await import('firebase/auth')
    expect(signInWithEmailAndPassword).not.toHaveBeenCalled()
  })

  it('links each error to its field for assistive technology', async () => {
    const user = userEvent.setup()
    renderWithAuth(<LoginPage />, { route: '/login' })

    await user.click(screen.getByRole('button', { name: /sign in/i }))

    const email = await screen.findByLabelText(/email address/i)
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email.getAttribute('aria-describedby')).toBeTruthy()
    expect(screen.getAllByRole('alert').length).toBeGreaterThan(0)
  })

  it('signs in with the entered credentials', async () => {
    const user = userEvent.setup()
    renderWithAuth(<LoginPage />, {
      route: '/login',
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    await user.type(screen.getByLabelText(/email address/i), 'student@example.edu')
    await user.type(screen.getByLabelText(/password/i), 'correct-horse')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    const { signInWithEmailAndPassword } = await import('firebase/auth')
    await waitFor(() => {
      expect(signInWithEmailAndPassword).toHaveBeenCalledWith(
        expect.anything(),
        'student@example.edu',
        'correct-horse',
      )
    })
  })

  it('trims a stray trailing space in the email', async () => {
    const user = userEvent.setup()
    renderWithAuth(<LoginPage />, {
      route: '/login',
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    await user.type(screen.getByLabelText(/email address/i), 'student@example.edu ')
    await user.type(screen.getByLabelText(/password/i), 'correct-horse')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    const { signInWithEmailAndPassword } = await import('firebase/auth')
    await waitFor(() => {
      expect(signInWithEmailAndPassword).toHaveBeenCalledWith(
        expect.anything(),
        'student@example.edu',
        'correct-horse',
      )
    })
  })

  it('renders a readable message for a Firebase failure', async () => {
    const user = userEvent.setup()
    authState.signInImpl = vi.fn(async () => {
      throw firebaseError('auth/invalid-credential')
    })
    renderWithAuth(<LoginPage />, { route: '/login' })

    await user.type(screen.getByLabelText(/email address/i), 'student@example.edu')
    await user.type(screen.getByLabelText(/password/i), 'wrong-password')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(
      await screen.findByText('That email address and password do not match.'),
    ).toBeInTheDocument()
  })

  it('does not reveal whether an account exists', async () => {
    // A different message for "no such user" turns the sign-in form into a way
    // to discover which email addresses are registered.
    const user = userEvent.setup()
    authState.signInImpl = vi.fn(async () => {
      throw firebaseError('auth/user-not-found')
    })
    renderWithAuth(<LoginPage />, { route: '/login' })

    await user.type(screen.getByLabelText(/email address/i), 'nobody@example.edu')
    await user.type(screen.getByLabelText(/password/i), 'whatever123')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    const message = await screen.findByText('That email address and password do not match.')
    expect(message).toBeInTheDocument()
    expect(screen.queryByText(/not found/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/no account/i)).not.toBeInTheDocument()
  })

  it('disables the form while submitting', async () => {
    const user = userEvent.setup()
    authState.signInImpl = vi.fn(
      () => new Promise<User>((resolve) => setTimeout(() => resolve(makeUser()), 50)),
    )
    renderWithAuth(<LoginPage />, {
      route: '/login',
      fetchImpl: createFetchStub(signedInRoutes()),
    })

    await user.type(screen.getByLabelText(/email address/i), 'student@example.edu')
    await user.type(screen.getByLabelText(/password/i), 'correct-horse')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    const button = screen.getByRole('button', { name: /signing in/i })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
  })

  it('is reachable by keyboard alone', async () => {
    const user = userEvent.setup()
    renderWithAuth(<LoginPage />, { route: '/login' })

    await user.tab()
    await user.tab() // past the skip link
    expect(screen.getByLabelText(/email address/i)).toHaveFocus()
    await user.tab()
    expect(screen.getByLabelText(/password/i)).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: /sign in/i })).toHaveFocus()
  })

  it('states that passwords are never handled by CampusShield', () => {
    renderWithAuth(<LoginPage />, { route: '/login' })
    expect(screen.getByText(/never seen or stored by CampusShield/i)).toBeInTheDocument()
  })
})
