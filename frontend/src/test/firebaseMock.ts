/**
 * A controllable stand-in for the `firebase/auth` module.
 *
 * `onAuthStateChanged` and friends are module-level functions that read Firebase
 * internals, so an injected `Auth` object is not enough — the module itself has
 * to be replaced. `setup.ts` does that globally and delegates here.
 *
 * What is replaced is Firebase and the network. Everything this application owns
 * — the context, the route guards, the forms, the API client — runs for real.
 */
import { vi } from 'vitest'
import { act } from '@testing-library/react'
import type { User } from 'firebase/auth'

type Listener = (user: User | null) => void

interface AuthState {
  currentUser: User | null
  listeners: Set<Listener>
  signInImpl: (email: string, password: string) => Promise<User>
  signUpImpl: (email: string, password: string) => Promise<User>
}

export const TEST_TOKEN = 'test-id-token'

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    uid: 'test-uid',
    email: 'student@example.edu',
    emailVerified: true,
    getIdToken: vi.fn(async () => TEST_TOKEN),
    ...overrides,
  } as unknown as User
}

export const authState: AuthState = {
  currentUser: null,
  listeners: new Set(),
  signInImpl: async (email) => makeUser({ email } as Partial<User>),
  signUpImpl: async (email) => makeUser({ email } as Partial<User>),
}

export function resetAuthState() {
  authState.currentUser = null
  authState.listeners.clear()
  authState.signInImpl = async (email) => makeUser({ email } as Partial<User>)
  authState.signUpImpl = async (email) => makeUser({ email } as Partial<User>)
}

export function setCurrentUser(user: User | null) {
  authState.currentUser = user
  authState.listeners.forEach((listener) => listener(user))
}

/** A Firebase-shaped error, so error mapping is exercised as it will be live. */
export function firebaseError(code: string): Error & { code: string } {
  const error = new Error(code) as Error & { code: string }
  error.code = code
  return error
}

export const firebaseAuthMock = {
  getAuth: vi.fn(() => ({ name: 'mock-auth' })),
  setPersistence: vi.fn(async () => undefined),
  browserLocalPersistence: { type: 'LOCAL' },

  onAuthStateChanged: vi.fn((_auth: unknown, next: Listener) => {
    authState.listeners.add(next)
    // Asynchronous, exactly like the real SDK. Firing synchronously would hide
    // the `initialising` state every consumer has to handle.
    queueMicrotask(() => {
      act(() => {
        next(authState.currentUser)
      })
    })
    return () => authState.listeners.delete(next)
  }),

  signInWithEmailAndPassword: vi.fn(
    async (_auth: unknown, email: string, password: string) => {
      const user = await authState.signInImpl(email, password)
      setCurrentUser(user)
      return { user }
    },
  ),

  createUserWithEmailAndPassword: vi.fn(
    async (_auth: unknown, email: string, password: string) => {
      const user = await authState.signUpImpl(email, password)
      setCurrentUser(user)
      return { user }
    },
  ),

  signOut: vi.fn(async () => {
    setCurrentUser(null)
  }),

  updateProfile: vi.fn(async (user: User, profile: { displayName?: string }) => {
    if (profile.displayName !== undefined) {
      Object.assign(user, { displayName: profile.displayName })
    }
  }),
}
