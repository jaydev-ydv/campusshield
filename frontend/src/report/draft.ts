/**
 * The report draft, and the rules that govern it.
 *
 * Pure functions and plain data — no React, no network. Every rule here mirrors
 * one the backend already enforces, and the backend remains authoritative. This
 * exists so a student is told about a problem while they are still looking at
 * the field, rather than after they have written three paragraphs and pressed
 * submit.
 *
 * Where the two could drift, the backend wins. Nothing here is a security
 * control.
 */
import type {
  CampusLocation,
  ReportCategory,
  ReportKind,
  ReporterRelationship,
} from '../lib/api'

export const STEPS = [
  'What are you reporting?',
  'Where did this happen?',
  'When did this happen?',
  'Tell us what happened',
  // Placed after the narrative and before the privacy choice: the student now
  // knows what would help, and the warning about what a photo can reveal lands
  // next to the decision about staying anonymous.
  'Add a photo',
  'How were you involved?',
  'Privacy and urgency',
  'Review your report',
] as const

export type StepIndex = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7

export interface ReportDraft {
  kind: ReportKind | null
  locationId: number | null
  locationHint: string
  /** `datetime-local` value, i.e. campus-local wall clock with no zone. */
  occurredAtLocal: string
  categoryId: number | null
  narrative: string
  relationship: ReporterRelationship
  anonymous: boolean
  isEmergency: boolean
  isOngoing: boolean
  contactConsent: boolean
}

export const EMPTY_DRAFT: ReportDraft = {
  kind: null,
  locationId: null,
  locationHint: '',
  occurredAtLocal: '',
  categoryId: null,
  narrative: '',
  relationship: 'affected',
  anonymous: false,
  isEmergency: false,
  isOngoing: false,
  contactConsent: true,
}

export const NARRATIVE_MIN = 10
export const NARRATIVE_MAX = 8000
export const LOCATION_HINT_MAX = 500

/** Small tolerance for clock skew, matching the backend's 120 seconds. */
const FUTURE_TOLERANCE_MS = 120_000

export type StepErrors = Partial<Record<keyof ReportDraft, string>>

/** `datetime-local` wants `YYYY-MM-DDTHH:mm` in local time, not ISO/UTC. */
export function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

/**
 * Convert the local wall-clock value to an absolute instant.
 *
 * `new Date('2026-08-11T21:30')` is interpreted in the browser's timezone, which
 * is what we want: the student is telling us when it happened where they were.
 * The backend re-derives the campus-local hour and weekday from this instant.
 */
export function toIsoInstant(localValue: string): string | null {
  if (!localValue) return null
  const parsed = new Date(localValue)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

export function categoriesForKind(
  categories: ReportCategory[] | null,
  kind: ReportKind | null,
): ReportCategory[] {
  if (!categories || !kind) return []
  return categories.filter((c) => c.kind === kind)
}

export function findCategory(
  categories: ReportCategory[] | null,
  id: number | null,
): ReportCategory | null {
  if (!categories || id == null) return null
  return categories.find((c) => c.category_id === id) ?? null
}

export function findLocation(
  locations: CampusLocation[] | null,
  id: number | null,
): CampusLocation | null {
  if (!locations || id == null) return null
  return locations.find((l) => l.location_id === id) ?? null
}

/** Emergency mode is only offered for categories the institution allows it for. */
export function emergencyAllowed(category: ReportCategory | null): boolean {
  return Boolean(category?.emergency_eligible)
}

export function validateStep(
  step: StepIndex,
  draft: ReportDraft,
  categories: ReportCategory[] | null,
  now: Date = new Date(),
): StepErrors {
  const errors: StepErrors = {}
  const category = findCategory(categories, draft.categoryId)

  switch (step) {
    case 0:
      if (!draft.kind) errors.kind = 'Choose what you would like to report.'
      break

    case 1:
      if (draft.locationId == null) errors.locationId = 'Choose where this happened.'
      if (draft.locationHint.length > LOCATION_HINT_MAX) {
        errors.locationHint = `Keep this under ${LOCATION_HINT_MAX} characters.`
      }
      break

    case 2: {
      if (!draft.occurredAtLocal) {
        errors.occurredAtLocal = 'Enter when this happened.'
        break
      }
      const instant = toIsoInstant(draft.occurredAtLocal)
      if (!instant) {
        errors.occurredAtLocal = 'Enter a valid date and time.'
        break
      }
      if (new Date(instant).getTime() - now.getTime() > FUTURE_TOLERANCE_MS) {
        errors.occurredAtLocal = 'This is in the future. Enter when it actually happened.'
      }
      break
    }

    case 3:
      if (draft.categoryId == null) {
        errors.categoryId = 'Choose the option that fits best.'
      } else if (category && draft.kind && category.kind !== draft.kind) {
        // Reachable by going back and changing the kind after picking a
        // category. Silently keeping the mismatched category would submit
        // something the student did not choose.
        errors.categoryId = 'This no longer matches what you are reporting. Choose again.'
      }
      if (draft.narrative.trim().length < NARRATIVE_MIN) {
        errors.narrative = `Please write at least ${NARRATIVE_MIN} characters.`
      } else if (draft.narrative.length > NARRATIVE_MAX) {
        errors.narrative = `Please keep this under ${NARRATIVE_MAX.toLocaleString()} characters.`
      }
      break

    case 4:
      // Evidence is optional and never blocks. An image that failed to upload is
      // shown in place with its own message; the student can retry or move on.
      break

    case 5:
      if (!draft.relationship) errors.relationship = 'Choose how you were involved.'
      break

    case 6:
      if (draft.isEmergency && !emergencyAllowed(category)) {
        errors.isEmergency = 'Emergency reporting is not available for this type of report.'
      }
      if (draft.isOngoing && !draft.isEmergency) {
        errors.isOngoing = 'Mark this as an emergency to say the situation is ongoing.'
      }
      break

    case 7:
      break
  }

  return errors
}

export function isStepValid(
  step: StepIndex,
  draft: ReportDraft,
  categories: ReportCategory[] | null,
  now?: Date,
): boolean {
  return Object.keys(validateStep(step, draft, categories, now)).length === 0
}

/**
 * The request body for `POST /api/v1/reports`.
 *
 * Every key here exists in `CreateReportSchema`; nothing is invented, and the
 * schema rejects unknown fields anyway. Note what is absent and stays absent:
 * no user id, no email, no Firebase UID, no role, no `submission_mode`. The
 * reporter is taken from the verified token, and `anonymous` is the only lever a
 * client has over identity.
 */
export interface ReportSubmissionPayload {
  category_id: number
  location_id: number
  occurred_at: string
  narrative: string
  anonymous: boolean
  reporter_relationship: ReporterRelationship
  location_hint?: string
  is_emergency: boolean
  is_ongoing: boolean
  contact_consent?: boolean
  evidence_tokens?: string[]
}

export function buildPayload(
  draft: ReportDraft,
  /**
   * Capability tokens from POST /evidence. Passed in rather than held on the
   * draft: upload state belongs to the upload component, and the draft should
   * only ever contain what the student answered.
   */
  evidenceTokens: string[] = [],
): ReportSubmissionPayload {
  const occurredAt = toIsoInstant(draft.occurredAtLocal)
  if (draft.categoryId == null || draft.locationId == null || !occurredAt) {
    throw new Error('draft is incomplete')
  }

  const payload: ReportSubmissionPayload = {
    category_id: draft.categoryId,
    location_id: draft.locationId,
    occurred_at: occurredAt,
    narrative: draft.narrative.trim(),
    anonymous: draft.anonymous,
    reporter_relationship: draft.relationship,
    is_emergency: draft.isEmergency,
    is_ongoing: draft.isOngoing,
  }

  const hint = draft.locationHint.trim()
  if (hint) payload.location_hint = hint

  // Only tokens travel. No filename, no storage path, no file metadata — the
  // server generated the path and knows what each token refers to.
  if (evidenceTokens.length > 0) payload.evidence_tokens = [...evidenceTokens]

  // Omitted entirely for an anonymous report. There is no attribution row for
  // consent to attach to, so sending it would be describing a contact channel
  // that cannot exist — and the point of the anonymous path is that the request
  // carries nothing about reaching the person.
  if (!draft.anonymous) payload.contact_consent = draft.contactConsent

  return payload
}
