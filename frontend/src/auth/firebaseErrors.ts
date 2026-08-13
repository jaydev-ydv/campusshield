/**
 * Firebase error codes, translated for people.
 *
 * The SDK's own messages are written for developers ("The password is invalid or
 * the user does not have a password") and leak implementation detail. Worse,
 * some of them tell an attacker things the UI should not: distinguishing "no
 * such account" from "wrong password" turns the sign-in form into a way to
 * discover which email addresses are registered.
 *
 * So `auth/user-not-found` and `auth/wrong-password` deliberately return the
 * *same* message. Modern Firebase projects with email enumeration protection
 * enabled return `auth/invalid-credential` for both anyway; this keeps the
 * behaviour consistent whether or not that setting is on.
 */

const MESSAGES: Record<string, string> = {
  'auth/invalid-email': 'Enter a valid email address.',
  'auth/user-disabled': 'This account has been disabled. Contact your campus administrator.',

  // Deliberately identical — see above.
  'auth/user-not-found': 'That email address and password do not match.',
  'auth/wrong-password': 'That email address and password do not match.',
  'auth/invalid-credential': 'That email address and password do not match.',

  'auth/email-already-in-use': 'An account already exists for this email address.',
  'auth/weak-password': 'Choose a password of at least 8 characters.',
  'auth/missing-password': 'Enter your password.',

  'auth/too-many-requests':
    'Too many attempts. Wait a few minutes before trying again, or reset your password.',
  'auth/network-request-failed': 'Could not reach the sign-in service. Check your connection.',
  'auth/operation-not-allowed':
    'Email and password sign-in is not enabled for this project yet.',
  'auth/requires-recent-login': 'Please sign in again to continue.',
}

const FALLBACK = 'Something went wrong signing you in. Please try again.'

export function firebaseErrorMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'code' in error) {
    const code = String((error as { code: unknown }).code)
    if (code in MESSAGES) return MESSAGES[code]
  }
  return FALLBACK
}

export function firebaseErrorCode(error: unknown): string | undefined {
  if (typeof error === 'object' && error !== null && 'code' in error) {
    return String((error as { code: unknown }).code)
  }
  return undefined
}
