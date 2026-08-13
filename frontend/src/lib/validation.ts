/**
 * Form validation.
 *
 * Pure functions, kept out of the page components so they can be tested on their
 * own and so each page file exports only a component.
 *
 * This is a first line and never the guarantee. The backend validates every
 * field again, and the database constrains what the backend will accept — a
 * browser check exists to save someone a round trip and a confusing error, not
 * to protect anything.
 */

export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

/** Firebase rejects anything under 6; 8 is the floor this product sets. */
export const MIN_PASSWORD_LENGTH = 8

export interface LoginErrors {
  email?: string
  password?: string
}

export interface RegistrationErrors extends LoginErrors {
  confirmPassword?: string
}

export function validateLogin(email: string, password: string): LoginErrors {
  const errors: LoginErrors = {}

  if (!email.trim()) {
    errors.email = 'Enter your email address.'
  } else if (!EMAIL_PATTERN.test(email.trim())) {
    errors.email = 'Enter a valid email address.'
  }

  // Length is deliberately not checked on sign-in. An existing account may
  // predate any rule introduced later, and "your password is too short" on a
  // password that works is a dead end.
  if (!password) {
    errors.password = 'Enter your password.'
  }

  return errors
}

export function validateRegistration(
  email: string,
  password: string,
  confirmPassword: string,
): RegistrationErrors {
  const errors: RegistrationErrors = {}

  if (!email.trim()) {
    errors.email = 'Enter your email address.'
  } else if (!EMAIL_PATTERN.test(email.trim())) {
    errors.email = 'Enter a valid email address.'
  }

  if (!password) {
    errors.password = 'Choose a password.'
  } else if (password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`
  }

  if (!confirmPassword) {
    errors.confirmPassword = 'Re-enter your password.'
  } else if (password && password !== confirmPassword) {
    // Only reported when a password was actually entered: two errors for one
    // omission reads as two separate mistakes.
    errors.confirmPassword = 'Passwords do not match.'
  }

  return errors
}
