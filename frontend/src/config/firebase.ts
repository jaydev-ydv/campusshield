/**
 * Firebase Web SDK initialisation.
 *
 * Everything here is Firebase's **public** web configuration. Vite inlines
 * `VITE_`-prefixed variables into the bundle, so all of it ships to the browser
 * and is visible in developer tools. That is expected: these values identify the
 * project, they do not authorise anything. `apiKey` is an identifier despite its
 * name.
 *
 * What actually protects the system is downstream — the ID token obtained after
 * a real sign-in, verified server-side by the Firebase Admin SDK, and the role
 * read from `identity.app_user` rather than from anything the browser claims.
 *
 * A service-account key must never appear in this directory. It can mint a token
 * for any user in the project, and anything under `frontend/src` is published to
 * every visitor.
 */
import { initializeApp, type FirebaseApp } from 'firebase/app'
import { browserLocalPersistence, getAuth, setPersistence, type Auth } from 'firebase/auth'

const REQUIRED = [
  'VITE_FIREBASE_API_KEY',
  'VITE_FIREBASE_AUTH_DOMAIN',
  'VITE_FIREBASE_PROJECT_ID',
  'VITE_FIREBASE_APP_ID',
] as const

export class FirebaseConfigError extends Error {}

export function readFirebaseConfig(env: Record<string, string | undefined> = import.meta.env) {
  const missing = REQUIRED.filter((key) => !env[key])
  if (missing.length > 0) {
    // Named explicitly. A misconfigured Firebase surfaces as every sign-in
    // failing with an opaque SDK error, which sends whoever is debugging it
    // looking at credentials rather than at a missing variable.
    throw new FirebaseConfigError(
      `Firebase is not configured. Missing: ${missing.join(', ')}. ` +
        'Copy frontend/.env.example to frontend/.env and fill in the values from ' +
        'Firebase console → Project settings → General → Your apps.',
    )
  }
  return {
    apiKey: env.VITE_FIREBASE_API_KEY as string,
    authDomain: env.VITE_FIREBASE_AUTH_DOMAIN as string,
    projectId: env.VITE_FIREBASE_PROJECT_ID as string,
    storageBucket: env.VITE_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: env.VITE_FIREBASE_MESSAGING_SENDER_ID,
    appId: env.VITE_FIREBASE_APP_ID as string,
  }
}

let app: FirebaseApp | undefined
let auth: Auth | undefined

/**
 * Lazily initialise, so importing this module never throws.
 *
 * A module-level `initializeApp` would take the whole application down with a
 * blank screen when configuration is missing, instead of letting the shell
 * render and show a readable message.
 */
export function getFirebaseAuth(): Auth {
  if (!auth) {
    app ??= initializeApp(readFirebaseConfig())
    auth = getAuth(app)
    // Survive a page reload. The alternative is signing a student out every time
    // they refresh, which teaches them the app is unreliable.
    void setPersistence(auth, browserLocalPersistence)
  }
  return auth
}

export function isFirebaseConfigured(
  env: Record<string, string | undefined> = import.meta.env,
): boolean {
  return REQUIRED.every((key) => Boolean(env[key]))
}
