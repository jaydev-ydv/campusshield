/**
 * The context object and its type, separate from the provider component.
 *
 * Split out so `AuthContext.tsx` exports only a component — which keeps fast
 * refresh working, and keeps the "what is auth state" type readable without the
 * provider's implementation around it.
 */
import { createContext } from 'react'
import type { User } from 'firebase/auth'

import type { AccountIdentity, createApi } from '../lib/api'

export type AuthStatus =
  | 'initialising'
  /** Firebase has resolved and nobody is signed in. */
  | 'signed-out'
  /** Signed in to Firebase, with a matching application account. */
  | 'authenticated'
  /** Signed in to Firebase, but no `identity.app_user` row exists yet. */
  | 'needs-provisioning'
  /** Firebase itself could not start — almost always missing configuration. */
  | 'unavailable'

export interface AuthContextValue {
  status: AuthStatus
  firebaseUser: User | null
  /**
   * The application account, from `identity.app_user`.
   *
   * This is where the role comes from, and it is never inferred locally or read
   * from a token claim. Nothing here is a security boundary — the backend
   * re-checks every request — it decides what to render, not what is permitted.
   */
  account: AccountIdentity | null
  /** Set when the shell itself cannot work, not when a form was filled in wrong. */
  error: string | null
  signIn: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** Provision the application account for the current Firebase user. */
  provision: () => Promise<AccountIdentity>
  refreshAccount: () => Promise<void>
  api: ReturnType<typeof createApi>
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined)
