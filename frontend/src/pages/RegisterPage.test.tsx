import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { RegisterPage } from './RegisterPage'
import { MIN_PASSWORD_LENGTH, validateRegistration } from '../lib/validation'
import { ACCOUNT, createFetchStub, firebaseError, renderWithAuth } from '../test/harness'
import { authState } from '../test/firebaseMock'

const valid = { email: 'student@example.edu', password: 'password123' }

describe('validateRegistration', () => {
  it('requires an email address', () => {
    expect(validateRegistration('', 'password123', 'password123').email).toBeDefined()
  })

  it('rejects a malformed email address', () => {
    expect(validateRegistration('nope', 'password123', 'password123').email).toBeDefined()
  })

  it(`requires at least ${MIN_PASSWORD_LENGTH} characters`, () => {
    const errors = validateRegistration('a@b.com', 'short', 'short')
    expect(errors.password).toBe(`Use at least ${MIN_PASSWORD_LENGTH} characters.`)
  })

  it('requires the confirmation to match', () => {
    const errors = validateRegistration('a@b.com', 'password123', 'password124')
    expect(errors.confirmPassword).toBe('Passwords do not match.')
  })

  it('requires the confirmation to be filled in', () => {
    expect(validateRegistration('a@b.com', 'password123', '').confirmPassword).toBeDefined()
  })

  it('does not report a mismatch when the password is empty', () => {
    // Two errors for one omission reads as two separate mistakes.
    const errors = validateRegistration('a@b.com', '', '')
    expect(errors.password).toBeDefined()
    expect(errors.confirmPassword).toBe('Re-enter your password.')
  })

  it('accepts a valid submission', () => {
    expect(validateRegistration(valid.email, valid.password, valid.password)).toEqual({})
  })
})

describe('RegisterPage', () => {
  function fillForm() {
    return {
      email: screen.getByLabelText(/email address/i),
      password: screen.getByLabelText(/^password$/i),
      confirm: screen.getByLabelText(/confirm password/i),
      submit: screen.getByRole('button', { name: /create account/i }),
    }
  }

  it('renders three accessible labelled fields', () => {
    renderWithAuth(<RegisterPage />, { route: '/register' })
    const fields = fillForm()

    expect(fields.email).toBeInTheDocument()
    expect(fields.password).toHaveAttribute('autocomplete', 'new-password')
    expect(fields.confirm).toHaveAttribute('autocomplete', 'new-password')
  })

  it('blocks submission and shows errors when empty', async () => {
    const user = userEvent.setup()
    renderWithAuth(<RegisterPage />, { route: '/register' })

    await user.click(fillForm().submit)

    expect(await screen.findByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Choose a password.')).toBeInTheDocument()
    const { createUserWithEmailAndPassword } = await import('firebase/auth')
    expect(createUserWithEmailAndPassword).not.toHaveBeenCalled()
  })

  it('reports a password mismatch before contacting Firebase', async () => {
    const user = userEvent.setup()
    renderWithAuth(<RegisterPage />, { route: '/register' })
    const fields = fillForm()

    await user.type(fields.email, valid.email)
    await user.type(fields.password, 'password123')
    await user.type(fields.confirm, 'password124')
    await user.click(fields.submit)

    expect(await screen.findByText('Passwords do not match.')).toBeInTheDocument()
    const { createUserWithEmailAndPassword } = await import('firebase/auth')
    expect(createUserWithEmailAndPassword).not.toHaveBeenCalled()
  })

  it('creates the credential and then provisions the application account', async () => {
    const user = userEvent.setup()
    const fetchStub = createFetchStub({
      '/auth/register': { status: 201, body: { ...ACCOUNT, created: true } },
      '/auth/me': { body: ACCOUNT },
    })
    renderWithAuth(<RegisterPage />, { route: '/register', fetchImpl: fetchStub })
    const fields = fillForm()

    await user.type(fields.email, valid.email)
    await user.type(fields.password, valid.password)
    await user.type(fields.confirm, valid.password)
    await user.click(fields.submit)

    const { createUserWithEmailAndPassword } = await import('firebase/auth')
    await waitFor(() => {
      expect(createUserWithEmailAndPassword).toHaveBeenCalledWith(
        expect.anything(),
        valid.email,
        valid.password,
      )
    })
    // The second step: Firebase alone leaves the user with a token and no
    // application account.
    await waitFor(() => {
      expect(fetchStub.calls.some((c) => c.url.includes('/auth/register'))).toBe(true)
    })
  })

  it('never sends a role to the server', async () => {
    const user = userEvent.setup()
    const fetchStub = createFetchStub({
      '/auth/register': { status: 201, body: { ...ACCOUNT, created: true } },
      '/auth/me': { body: ACCOUNT },
    })
    renderWithAuth(<RegisterPage />, { route: '/register', fetchImpl: fetchStub })
    const fields = fillForm()

    await user.type(fields.email, valid.email)
    await user.type(fields.password, valid.password)
    await user.type(fields.confirm, valid.password)
    await user.click(fields.submit)

    await waitFor(() => {
      const call = fetchStub.calls.find((c) => c.url.includes('/auth/register'))
      expect(call).toBeDefined()
      const body = String(call?.init?.body ?? '')
      expect(body).not.toMatch(/role/i)
      expect(body).not.toMatch(/admin/i)
    })
  })

  it('never puts the password in an API request', async () => {
    const user = userEvent.setup()
    const fetchStub = createFetchStub({
      '/auth/register': { status: 201, body: { ...ACCOUNT, created: true } },
      '/auth/me': { body: ACCOUNT },
    })
    renderWithAuth(<RegisterPage />, { route: '/register', fetchImpl: fetchStub })
    const fields = fillForm()

    await user.type(fields.email, valid.email)
    await user.type(fields.password, valid.password)
    await user.type(fields.confirm, valid.password)
    await user.click(fields.submit)

    await waitFor(() => expect(fetchStub.calls.length).toBeGreaterThan(0))
    for (const call of fetchStub.calls) {
      expect(String(call.init?.body ?? '')).not.toContain(valid.password)
    }
  })

  it('reports an email already in use', async () => {
    const user = userEvent.setup()
    authState.signUpImpl = vi.fn(async () => {
      throw firebaseError('auth/email-already-in-use')
    })
    renderWithAuth(<RegisterPage />, { route: '/register' })
    const fields = fillForm()

    await user.type(fields.email, valid.email)
    await user.type(fields.password, valid.password)
    await user.type(fields.confirm, valid.password)
    await user.click(fields.submit)

    expect(
      await screen.findByText('An account already exists for this email address.'),
    ).toBeInTheDocument()
  })

  it('reports a weak password rejected by Firebase', async () => {
    const user = userEvent.setup()
    authState.signUpImpl = vi.fn(async () => {
      throw firebaseError('auth/weak-password')
    })
    renderWithAuth(<RegisterPage />, { route: '/register' })
    const fields = fillForm()

    await user.type(fields.email, valid.email)
    await user.type(fields.password, valid.password)
    await user.type(fields.confirm, valid.password)
    await user.click(fields.submit)

    // The field hint says the same thing, so target the error alert itself.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/at least 8 characters/i)
  })

  it('tells the user what is and is not stored', () => {
    renderWithAuth(<RegisterPage />, { route: '/register' })

    expect(screen.getByText(/Not your name/i)).toBeInTheDocument()
    expect(screen.getByText(/held by Firebase Authentication/i)).toBeInTheDocument()
    expect(screen.getByText(/anonymously/i)).toBeInTheDocument()
  })
})
