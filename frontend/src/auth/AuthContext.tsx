/**
 * Authentication state.
 *
 * There are two identities in this system and keeping them distinct is the whole
 * job of this file:
 *
 * - the **Firebase user**, which proves someone signed in, and
 * - the **application account** (`identity.app_user`), which says who they are
 *   here and what role they hold.
 *
 * They are not the same thing, and the second is the one that matters. A user
 * can be perfectly signed in to Firebase and have no account here at all — that
 * is exactly the state between creating a credential and provisioning an
 * account, which is why `status` has a `needs-provisioning` value rather than
 * being a boolean.
 *
 * **The role always comes from the server.** It is fetched from `/auth/me` and
 * never inferred, cached across sign-ins, or read from a token claim. A role
 * held in browser state is a claim the browser is making about itself, and the
 * backend re-checks every request regardless — so nothing here is a security
 * boundary. It decides what to render, not what is permitted.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signOut,
  type Auth,
  type User,
} from 'firebase/auth'

import { ApiClient } from '../lib/apiClient'
import { createApi, type AccountIdentity } from '../lib/api'
import { getFirebaseAuth, isFirebaseConfigured } from '../config/firebase'
import { AuthContext, type AuthContextValue, type AuthStatus } from './context'

const UNCONFIGURED_MESSAGE =
  'CampusShield is not configured to sign in yet. Copy frontend/.env.example to ' +
  'frontend/.env and add your Firebase web configuration.'

interface AuthProviderProps {
  children: ReactNode
  /** Injected in tests; production reads the real Firebase Auth instance. */
  authInstance?: Auth
  apiClient?: ApiClient
}

export function AuthProvider({ children, authInstance, apiClient }: AuthProviderProps) {
  const auth = useMemo<Auth | null>(() => {
    if (authInstance) return authInstance
    if (!isFirebaseConfigured()) return null
    try {
      return getFirebaseAuth()
    } catch {
      return null
    }
  }, [authInstance])

  // Seeded from what is already knowable rather than corrected inside an effect.
  // Setting state synchronously in an effect causes a cascading render, and here
  // it would also mean rendering `initialising` for a frame on a machine that
  // has no Firebase configuration at all.
  const [status, setStatus] = useState<AuthStatus>(() =>
    auth ? 'initialising' : 'unavailable',
  )
  const [firebaseUser, setFirebaseUser] = useState<User | null>(null)
  const [account, setAccount] = useState<AccountIdentity | null>(null)
  const [error, setError] = useState<string | null>(() => (auth ? null : UNCONFIGURED_MESSAGE))

  const client = useMemo(
    () =>
      apiClient ??
      new ApiClient({
        // Read from `auth.currentUser`, not from React state. The Firebase Auth
        // object is always current, whereas state is a snapshot that can lag a
        // render behind — and a token fetched from a stale user is a request
        // sent as the wrong person.
        //
        // `getIdToken()` returns the cached token and refreshes it when it is
        // close to expiring, so nothing here has to reason about the one-hour
        // lifetime.
        getToken: async () => {
          const user = auth?.currentUser
          return user ? user.getIdToken() : null
        },
      }),
    [apiClient, auth],
  )

  const api = useMemo(() => createApi(client), [client])

  const loadAccount = useCallback(
    async (user: User | null) => {
      if (!user) {
        setAccount(null)
        setStatus('signed-out')
        return
      }
      try {
        const identity = await api.me()
        setAccount(identity)
        setError(null)
        setStatus('authenticated')
      } catch (err) {
        // A 401 here does not mean the credential is bad — it verified, or the
        // request would have failed differently. It means no application account
        // exists yet, which is a state the UI can resolve by provisioning one.
        setAccount(null)
        setStatus('needs-provisioning')
        const status = (err as { status?: number } | null)?.status
        setError(
          status === 401
            ? null
            : err instanceof Error
              ? err.message
              : 'Could not load your account.',
        )
      }
    },
    [api],
  )

  useEffect(() => {
    if (!auth) return
    return onAuthStateChanged(auth, (user) => {
      setFirebaseUser(user)
      void loadAccount(user)
    })
  }, [auth, loadAccount])

  const signIn = useCallback(
    async (email: string, password: string) => {
      if (!auth) throw new Error('Sign-in is unavailable.')
      // Firebase takes the password directly. This application never sees,
      // holds, or transmits it.
      await signInWithEmailAndPassword(auth, email.trim(), password)
    },
    [auth],
  )

  const signUp = useCallback(
    async (email: string, password: string) => {
      if (!auth) throw new Error('Registration is unavailable.')
      await createUserWithEmailAndPassword(auth, email.trim(), password)
    },
    [auth],
  )

  const provision = useCallback(async () => {
    const identity = await api.register()
    setAccount(identity)
    setError(null)
    setStatus('authenticated')
    return identity
  }, [api])

  const refreshAccount = useCallback(async () => {
    await loadAccount(auth?.currentUser ?? null)
  }, [auth, loadAccount])

  const logout = useCallback(async () => {
    if (auth) await signOut(auth)
    // Cleared explicitly rather than waiting for the auth listener, so no screen
    // renders one user's details for even a frame after another signs out.
    setAccount(null)
    setFirebaseUser(null)
    setError(null)
    setStatus('signed-out')
  }, [auth])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      firebaseUser,
      account,
      error,
      signIn,
      signUp,
      logout,
      provision,
      refreshAccount,
      api,
    }),
    [
      status,
      firebaseUser,
      account,
      error,
      signIn,
      signUp,
      logout,
      provision,
      refreshAccount,
      api,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
