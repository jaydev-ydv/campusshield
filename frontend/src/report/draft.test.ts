import { describe, expect, it } from 'vitest'

import {
  EMPTY_DRAFT,
  NARRATIVE_MIN,
  buildPayload,
  categoriesForKind,
  emergencyAllowed,
  isStepValid,
  toIsoInstant,
  toLocalInputValue,
  validateStep,
  type ReportDraft,
} from './draft'
import { CATEGORIES } from '../test/harness'

const EMERGENCY_CATEGORY = CATEGORIES[0] // incident, emergency_eligible
const CONCERN_CATEGORY = CATEGORIES[1] // concern, not emergency_eligible

function draft(overrides: Partial<ReportDraft> = {}): ReportDraft {
  return {
    ...EMPTY_DRAFT,
    kind: 'incident',
    locationId: 1,
    occurredAtLocal: toLocalInputValue(new Date(Date.now() - 3_600_000)),
    categoryId: EMERGENCY_CATEGORY.category_id,
    narrative: 'A description of what happened, long enough to be valid.',
    ...overrides,
  }
}

describe('step validation', () => {
  it('requires a report kind', () => {
    expect(validateStep(0, draft({ kind: null }), CATEGORIES).kind).toBeDefined()
    expect(validateStep(0, draft(), CATEGORIES)).toEqual({})
  })

  it('requires a campus location', () => {
    expect(validateStep(1, draft({ locationId: null }), CATEGORIES).locationId).toBeDefined()
  })

  it('caps the location hint', () => {
    const errors = validateStep(1, draft({ locationHint: 'x'.repeat(501) }), CATEGORIES)
    expect(errors.locationHint).toBeDefined()
  })

  it('requires a timestamp', () => {
    expect(
      validateStep(2, draft({ occurredAtLocal: '' }), CATEGORIES).occurredAtLocal,
    ).toBeDefined()
  })

  it('rejects a future timestamp', () => {
    const future = toLocalInputValue(new Date(Date.now() + 86_400_000))
    const errors = validateStep(2, draft({ occurredAtLocal: future }), CATEGORIES)
    expect(errors.occurredAtLocal).toMatch(/future/i)
  })

  it('tolerates small clock skew', () => {
    // A phone running a minute fast must still be able to file. The backend
    // allows 120 seconds; this mirrors it.
    const slightlyAhead = toLocalInputValue(new Date(Date.now() + 60_000))
    expect(validateStep(2, draft({ occurredAtLocal: slightlyAhead }), CATEGORIES)).toEqual({})
  })

  it('requires a category and a narrative', () => {
    expect(validateStep(3, draft({ categoryId: null }), CATEGORIES).categoryId).toBeDefined()
    expect(validateStep(3, draft({ narrative: 'short' }), CATEGORIES).narrative).toMatch(
      new RegExp(String(NARRATIVE_MIN)),
    )
  })

  it('rejects a category that no longer matches the chosen kind', () => {
    // Reachable by going back and changing the kind. Silently keeping the stale
    // category would submit something the student did not choose.
    const errors = validateStep(
      3,
      draft({ kind: 'concern', categoryId: EMERGENCY_CATEGORY.category_id }),
      CATEGORIES,
    )
    expect(errors.categoryId).toMatch(/no longer matches/i)
  })

  it('rejects a narrative of whitespace', () => {
    expect(
      validateStep(3, draft({ narrative: '          ' }), CATEGORIES).narrative,
    ).toBeDefined()
  })

  it('rejects emergency mode on a category that does not allow it', () => {
    const errors = validateStep(
      6,
      draft({ kind: 'concern', categoryId: CONCERN_CATEGORY.category_id, isEmergency: true }),
      CATEGORIES,
    )
    expect(errors.isEmergency).toBeDefined()
  })

  it('allows emergency mode on an eligible category', () => {
    expect(validateStep(6, draft({ isEmergency: true }), CATEGORIES)).toEqual({})
  })

  it('rejects ongoing without emergency', () => {
    const errors = validateStep(6, draft({ isEmergency: false, isOngoing: true }), CATEGORIES)
    expect(errors.isOngoing).toBeDefined()
  })

  it('allows ongoing with emergency', () => {
    expect(validateStep(6, draft({ isEmergency: true, isOngoing: true }), CATEGORIES)).toEqual(
      {},
    )
  })
})

describe('helpers', () => {
  it('filters categories by kind', () => {
    expect(categoriesForKind(CATEGORIES, 'incident').every((c) => c.kind === 'incident')).toBe(
      true,
    )
    expect(categoriesForKind(CATEGORIES, 'concern').every((c) => c.kind === 'concern')).toBe(
      true,
    )
    expect(categoriesForKind(null, 'incident')).toEqual([])
    expect(categoriesForKind(CATEGORIES, null)).toEqual([])
  })

  it('reports emergency eligibility from the category', () => {
    expect(emergencyAllowed(EMERGENCY_CATEGORY)).toBe(true)
    expect(emergencyAllowed(CONCERN_CATEGORY)).toBe(false)
    expect(emergencyAllowed(null)).toBe(false)
  })

  it('converts a local wall-clock value to an absolute instant', () => {
    const iso = toIsoInstant('2026-08-10T21:30')
    expect(iso).toMatch(/Z$/)
    // Interpreted in the browser's zone: the student is saying when it happened
    // where they were.
    expect(new Date(iso!).getTime()).toBe(new Date('2026-08-10T21:30').getTime())
  })

  it('returns null for an unparseable value', () => {
    expect(toIsoInstant('')).toBeNull()
    expect(toIsoInstant('not-a-date')).toBeNull()
  })

  it('round-trips a date through the local input format', () => {
    const now = new Date()
    const parsed = new Date(toLocalInputValue(now))
    expect(Math.abs(parsed.getTime() - now.getTime())).toBeLessThan(60_000)
  })
})

describe('buildPayload', () => {
  it('sends only fields the backend schema accepts', () => {
    const payload = buildPayload(draft())
    expect(Object.keys(payload).sort()).toEqual(
      [
        'anonymous',
        'category_id',
        'contact_consent',
        'is_emergency',
        'is_ongoing',
        'location_id',
        'narrative',
        'occurred_at',
        'reporter_relationship',
      ].sort(),
    )
  })

  it('never includes identity fields', () => {
    // The reporter is taken from the verified token. `anonymous` is the only
    // lever a client has over identity, and the backend rejects the rest by name.
    const serialised = JSON.stringify(buildPayload(draft({ anonymous: false })))
    for (const forbidden of [
      'user_id',
      'email',
      'firebase',
      'uid',
      'role',
      'submission_mode',
      'reporter_contactable',
      'public_ref',
    ]) {
      expect(serialised).not.toContain(forbidden)
    }
  })

  it('omits contact_consent entirely on an anonymous report', () => {
    // There is no attribution row for consent to attach to, so sending it would
    // describe a contact channel that cannot exist.
    const payload = buildPayload(draft({ anonymous: true }))
    expect(payload).not.toHaveProperty('contact_consent')
    expect(payload.anonymous).toBe(true)
  })

  it('includes contact_consent on an identified report', () => {
    expect(
      buildPayload(draft({ contactConsent: false }).valueOf() as ReportDraft),
    ).toMatchObject({
      contact_consent: false,
    })
  })

  it('trims the narrative and drops an empty location hint', () => {
    const payload = buildPayload(
      draft({ narrative: '  padded narrative text  ', locationHint: '   ' }),
    )
    expect(payload.narrative).toBe('padded narrative text')
    expect(payload).not.toHaveProperty('location_hint')
  })

  it('includes a location hint when one was written', () => {
    expect(buildPayload(draft({ locationHint: ' near the stairwell ' })).location_hint).toBe(
      'near the stairwell',
    )
  })

  it('sends evidence tokens, never paths or filenames', () => {
    const payload = buildPayload(draft(), ['a'.repeat(32)])
    expect(payload.evidence_tokens).toEqual(['a'.repeat(32)])
    const serialised = JSON.stringify(payload)
    expect(serialised).not.toContain('storage_path')
    expect(serialised).not.toContain('.jpg')
    expect(serialised).not.toContain('filename')
  })

  it('omits evidence_tokens entirely when no photo was added', () => {
    expect(buildPayload(draft())).not.toHaveProperty('evidence_tokens')
  })

  it('refuses to build from an incomplete draft', () => {
    expect(() => buildPayload(draft({ categoryId: null }))).toThrow(/incomplete/)
    expect(() => buildPayload(draft({ locationId: null }))).toThrow(/incomplete/)
    expect(() => buildPayload(draft({ occurredAtLocal: '' }))).toThrow(/incomplete/)
  })

  it('sends occurred_at as an ISO instant', () => {
    expect(buildPayload(draft()).occurred_at).toMatch(/^\d{4}-\d{2}-\d{2}T.*Z$/)
  })
})

describe('isStepValid', () => {
  it('agrees with validateStep', () => {
    expect(isStepValid(0, draft(), CATEGORIES)).toBe(true)
    expect(isStepValid(0, draft({ kind: null }), CATEGORIES)).toBe(false)
  })
})
