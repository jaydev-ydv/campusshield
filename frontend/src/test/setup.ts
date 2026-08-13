import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

import { firebaseAuthMock, resetAuthState } from './firebaseMock'

// Replaced globally: `onAuthStateChanged` and friends are module-level functions
// that read Firebase internals, so injecting an Auth object is not enough.
vi.mock('firebase/auth', () => firebaseAuthMock)
vi.mock('firebase/app', () => ({ initializeApp: vi.fn(() => ({ name: 'mock-app' })) }))

afterEach(() => {
  cleanup()
  resetAuthState()
  vi.clearAllMocks()
})

// jsdom implements neither of these, and anything checking reduced-motion or
// scrolling into view will reach for them.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})
window.scrollTo = vi.fn()

// jsdom has no Blob URL store, so `URL.createObjectURL` is simply absent.
// Anything rendering a local image preview reaches for it. A counter rather
// than a fixed string keeps the URLs distinct, which is what the code under
// test assumes when it revokes one.
let objectUrlSeq = 0
URL.createObjectURL = vi.fn(() => `blob:campusshield/${++objectUrlSeq}`)
URL.revokeObjectURL = vi.fn()
