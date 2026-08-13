/**
 * The seven steps of the reporting form.
 *
 * Each is a presentational component: it renders the draft and reports changes.
 * No validation logic, no network, no decisions — those live in `draft.ts` and
 * `ReportPage.tsx` respectively.
 *
 * The form is split into steps rather than one long page because a student may
 * be filling it in on a phone, possibly upset, possibly in a hurry. One question
 * at a time is easier to answer than twelve at once, and it means a mistake is
 * caught next to the field that caused it.
 */
import type { CampusLocation, ReportCategory } from '../lib/api'
import { RELATIONSHIP_LABELS } from '../lib/api'
import { Alert } from '../components/ui/Alert'
import { ChoiceCards } from '../components/ui/ChoiceCard'
import { Input } from '../components/ui/Input'
import { Select } from '../components/ui/Select'
import { Spinner } from '../components/ui/Spinner'
import { Textarea } from '../components/ui/Textarea'
import {
  LOCATION_HINT_MAX,
  NARRATIVE_MAX,
  NARRATIVE_MIN,
  categoriesForKind,
  emergencyAllowed,
  findCategory,
  findLocation,
  toLocalInputValue,
  type ReportDraft,
  type StepErrors,
} from './draft'
import { LocationMap } from './LocationMap'

interface StepProps {
  draft: ReportDraft
  errors: StepErrors
  update: (patch: Partial<ReportDraft>) => void
  locations: CampusLocation[] | null
  categories: ReportCategory[] | null
  /** Images successfully uploaded, for the review screen. */
  evidenceCount?: number
}

/* -------------------------------------------------------------------------- */
/* Step 1 — What are you reporting?                                            */
/* -------------------------------------------------------------------------- */

export function StepKind({ draft, errors, update }: StepProps) {
  return (
    <ChoiceCards
      legend="What would you like to report?"
      hint="Both matter. If you are not sure which fits, choose whichever is closer — it can be adjusted by the team who reviews it."
      name="report-kind"
      value={draft.kind}
      error={errors.kind}
      onChange={(kind) =>
        // Clearing the category is deliberate: the two lists do not overlap, and
        // carrying a stale selection forward would submit something the student
        // did not choose.
        update({ kind, categoryId: null, isEmergency: false, isOngoing: false })
      }
      choices={[
        {
          value: 'incident',
          label: 'Something happened',
          description:
            'An incident involving you or someone else — for example harassment, being followed, or unwanted contact.',
        },
        {
          value: 'concern',
          label: 'Something feels unsafe',
          description:
            'A condition on campus rather than an event — for example poor lighting, an isolated route, or a gate left unsecured.',
        },
      ]}
    />
  )
}

/* -------------------------------------------------------------------------- */
/* Step 2 — Where?                                                             */
/* -------------------------------------------------------------------------- */

export function StepLocation({ draft, errors, update, locations }: StepProps) {
  // An honest blocking state, not an error. `GET /locations` returns only
  // places whose coordinates have been verified, and none have been yet for this
  // campus. Inventing options would put reports at places that do not exist.
  if (locations !== null && locations.length === 0) {
    return (
      <div className="space-y-4">
        <h2 className="text-ink-900 text-base font-medium">Where did this happen?</h2>
        <Alert tone="info" title="Campus locations are not available yet">
          <p>
            Reporting needs a confirmed campus location, and none have been published for this
            campus yet. Every report is anchored to a specific place so that repeated problems
            can be recognised and acted on — which means a location has to be verified before
            it can be offered here.
          </p>
          <p className="mt-2">
            If you need help now, contact campus security or emergency services directly.
          </p>
        </Alert>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-ink-900 text-base font-medium">Where did this happen?</h2>
        <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
          Choose the closest place from the list, or tap it on the map. Being consistent about
          locations is what lets the university notice when the same place comes up repeatedly.
        </p>
      </div>

      {locations === null ? (
        <div
          role="status"
          aria-live="polite"
          className="border-ink-200 bg-ink-50 flex h-64 items-center justify-center rounded-xl border sm:h-80"
        >
          <Spinner label="Loading the campus map…" />
        </div>
      ) : (
        <LocationMap
          locations={locations}
          selectedId={draft.locationId}
          onSelect={(locationId) => update({ locationId })}
        />
      )}

      <Select
        label="Campus location"
        placeholder={locations === null ? 'Loading locations…' : 'Select a location'}
        value={draft.locationId ?? ''}
        error={errors.locationId}
        disabled={locations === null}
        onChange={(e) =>
          update({ locationId: e.target.value ? Number(e.target.value) : null })
        }
      >
        {(locations ?? []).map((location) => (
          <option key={location.location_id} value={location.location_id}>
            {location.name}
          </option>
        ))}
      </Select>

      <Input
        label="More detail about the place (optional)"
        hint="For example “near the rear stairwell” or “by the bicycle racks”."
        value={draft.locationHint}
        maxLength={LOCATION_HINT_MAX}
        error={errors.locationHint}
        onChange={(e) => update({ locationHint: e.target.value })}
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Step 3 — When?                                                              */
/* -------------------------------------------------------------------------- */

export function StepWhen({ draft, errors, update }: StepProps) {
  // The browser blocks future values too, but only as a courtesy — the attribute
  // is trivially removable, so the real checks are in draft.ts and the backend.
  const maxLocal = toLocalInputValue(new Date())

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-ink-900 text-base font-medium">When did this happen?</h2>
        <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
          Your best estimate is fine. Time of day matters because patterns often show up around
          particular hours.
        </p>
      </div>

      <Input
        label="Date and time"
        type="datetime-local"
        value={draft.occurredAtLocal}
        max={maxLocal}
        error={errors.occurredAtLocal}
        onChange={(e) => update({ occurredAtLocal: e.target.value })}
      />

      <div className="flex flex-wrap gap-2">
        {[
          { label: 'Just now', minutesAgo: 0 },
          { label: '1 hour ago', minutesAgo: 60 },
          { label: 'Yesterday evening', minutesAgo: 60 * 24 },
        ].map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() =>
              update({
                occurredAtLocal: toLocalInputValue(
                  new Date(Date.now() - preset.minutesAgo * 60_000),
                ),
              })
            }
            className="border-ink-300 text-ink-700 hover:bg-ink-50 rounded-full border px-3 py-1.5 text-sm transition-colors"
          >
            {preset.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Step 4 — What happened?                                                     */
/* -------------------------------------------------------------------------- */

export function StepWhat({ draft, errors, update, categories }: StepProps) {
  const available = categoriesForKind(categories, draft.kind)

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-ink-900 text-base font-medium">Tell us what happened</h2>
        <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
          Write as much or as little as you want. There is no wrong way to describe it.
        </p>
      </div>

      <Select
        label={
          draft.kind === 'concern'
            ? 'What kind of concern is this?'
            : 'What kind of incident was this?'
        }
        placeholder={categories === null ? 'Loading…' : 'Select the closest option'}
        value={draft.categoryId ?? ''}
        error={errors.categoryId}
        disabled={categories === null}
        onChange={(e) =>
          update({
            categoryId: e.target.value ? Number(e.target.value) : null,
            // Emergency is a property of the category, so a change can make the
            // current selection invalid. Clearing it here avoids a confusing
            // rejection two steps later.
            isEmergency: false,
            isOngoing: false,
          })
        }
      >
        {available.map((category) => (
          <option key={category.category_id} value={category.category_id}>
            {category.label}
          </option>
        ))}
      </Select>

      <Textarea
        label="In your own words"
        hint="What happened, and anything that might help someone understand it. You do not need to name anyone."
        rows={7}
        value={draft.narrative}
        minLength={NARRATIVE_MIN}
        maxLength={NARRATIVE_MAX}
        error={errors.narrative}
        onChange={(e) => update({ narrative: e.target.value })}
        placeholder="Describe what happened…"
      />

      <p className="text-ink-500 text-xs leading-relaxed">
        What you write is read by the team responsible for this type of report. It is not used
        to decide whether your report is true — that is not something this system does.
      </p>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Step 6 — Your relationship                                                  */
/* -------------------------------------------------------------------------- */

export function StepRelationship({ draft, errors, update }: StepProps) {
  return (
    <ChoiceCards
      legend="How were you involved?"
      hint="This helps whoever responds understand the situation. It is not used to judge your report, and it does not affect how seriously it is taken."
      name="relationship"
      value={draft.relationship}
      error={errors.relationship}
      onChange={(relationship) => update({ relationship })}
      choices={[
        {
          value: 'affected',
          label: RELATIONSHIP_LABELS.affected,
          description: 'You were directly involved.',
        },
        {
          value: 'witness',
          label: RELATIONSHIP_LABELS.witness,
          description: 'You were there and saw it, but it was not directed at you.',
        },
        {
          value: 'third_party',
          label: RELATIONSHIP_LABELS.third_party,
          description: 'Someone told you about it, or you are reporting for them.',
        },
      ]}
    />
  )
}

/* -------------------------------------------------------------------------- */
/* Step 7 — Privacy and urgency                                                */
/* -------------------------------------------------------------------------- */

export function StepPrivacy({ draft, errors, update, categories }: StepProps) {
  const category = findCategory(categories, draft.categoryId)
  const canEscalate = emergencyAllowed(category)

  return (
    <div className="space-y-8">
      <ChoiceCards
        legend="Would you like to submit anonymously?"
        name="submission-mode"
        value={draft.anonymous ? 'anonymous' : 'identified'}
        onChange={(value) =>
          update({
            anonymous: value === 'anonymous',
            // Consent has nothing to attach to on an anonymous report, so it is
            // reset rather than carried into a state where it means nothing.
            contactConsent: value === 'anonymous' ? false : true,
          })
        }
        choices={[
          {
            value: 'identified',
            label: 'Submit with my account',
            description:
              'The report is linked to your account. You can see it in your reports, and the responding team can contact you if you allow it.',
          },
          {
            value: 'anonymous',
            label: 'Submit anonymously',
            description:
              'The report is not linked to your account in any way that can be reversed.',
            note: 'You will be given a code to check its status. It is shown once and cannot be recovered, and nobody can contact you about it.',
          },
        ]}
      />

      {!draft.anonymous && (
        <div className="border-ink-200 border-t pt-6">
          <ChoiceCards
            legend="May the responding team contact you?"
            name="contact-consent"
            value={draft.contactConsent ? 'yes' : 'no'}
            onChange={(value) => update({ contactConsent: value === 'yes' })}
            columns={2}
            choices={[
              { value: 'yes', label: 'Yes, they may contact me' },
              { value: 'no', label: 'No, please do not contact me' },
            ]}
          />
        </div>
      )}

      <div className="border-ink-200 border-t pt-6">
        <ChoiceCards
          legend="Does this need immediate attention?"
          hint="Emergency reporting is for situations requiring immediate institutional response. It is passed to campus security straight away rather than following the normal review queue."
          name="urgency"
          value={draft.isEmergency ? 'emergency' : 'normal'}
          error={errors.isEmergency}
          onChange={(value) =>
            update({
              isEmergency: value === 'emergency',
              isOngoing: value === 'emergency' ? draft.isOngoing : false,
            })
          }
          choices={[
            {
              value: 'normal',
              label: 'No — review in the usual way',
              description:
                'The report is reviewed by the responsible team in the normal queue.',
            },
            {
              value: 'emergency',
              label: 'Yes — this needs immediate attention',
              description: 'Campus security is alerted to the location as soon as you submit.',
              disabled: !canEscalate,
              disabledReason: category
                ? `Not available for “${category.label}”.`
                : 'Choose what happened first.',
              note: draft.anonymous
                ? 'Because you are submitting anonymously, security will be given the location but will have no way to reach you.'
                : undefined,
            },
          ]}
        />
      </div>

      {draft.isEmergency && (
        <div className="border-ink-200 border-t pt-6">
          <ChoiceCards
            legend="Is the situation still happening?"
            name="ongoing"
            value={draft.isOngoing ? 'ongoing' : 'not-ongoing'}
            error={errors.isOngoing}
            onChange={(value) => update({ isOngoing: value === 'ongoing' })}
            columns={2}
            choices={[
              { value: 'ongoing', label: 'Yes, right now' },
              { value: 'not-ongoing', label: 'No, it has ended' },
            ]}
          />
        </div>
      )}

      {draft.isEmergency && (
        <Alert tone="warning" title="If you are in immediate danger">
          Contact campus security or emergency services directly. This form alerts the
          institution, and it is not a substitute for an emergency call.
        </Alert>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Step 8 — Review                                                             */
/* -------------------------------------------------------------------------- */

function ReviewRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-ink-200 grid gap-1 border-b py-3 last:border-0 sm:grid-cols-3 sm:gap-4">
      <dt className="text-ink-500 text-sm">{label}</dt>
      <dd className="text-ink-900 text-sm sm:col-span-2">{children}</dd>
    </div>
  )
}

export function StepReview({ draft, locations, categories, evidenceCount = 0 }: StepProps) {
  const category = findCategory(categories, draft.categoryId)
  const location = findLocation(locations, draft.locationId)
  const occurredAt = draft.occurredAtLocal ? new Date(draft.occurredAtLocal) : null

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-ink-900 text-base font-medium">Review your report</h2>
        <p className="text-ink-600 mt-1.5 text-sm leading-relaxed">
          This is exactly what will be sent. Nothing else about you is included.
        </p>
      </div>

      <dl className="border-ink-200 rounded-xl border bg-white px-4 sm:px-5">
        <ReviewRow label="Reporting">
          {draft.kind === 'concern' ? 'A safety concern' : 'An incident'}
        </ReviewRow>
        <ReviewRow label="Type">{category?.label ?? '—'}</ReviewRow>
        <ReviewRow label="Where">
          {location?.name ?? '—'}
          {draft.locationHint.trim() && (
            <span className="text-ink-600 block">{draft.locationHint.trim()}</span>
          )}
        </ReviewRow>
        <ReviewRow label="When">
          {occurredAt
            ? occurredAt.toLocaleString(undefined, {
                dateStyle: 'full',
                timeStyle: 'short',
              })
            : '—'}
        </ReviewRow>
        <ReviewRow label="Your involvement">
          {RELATIONSHIP_LABELS[draft.relationship]}
        </ReviewRow>
        <ReviewRow label="What happened">
          <p className="whitespace-pre-wrap">{draft.narrative.trim()}</p>
        </ReviewRow>
        <ReviewRow label="Submitting as">
          {draft.anonymous ? (
            <>
              <span className="font-medium">Anonymously</span>
              <span className="text-ink-600 block">
                Not linked to your account. You will be given a code to check its status.
              </span>
            </>
          ) : (
            <>
              <span className="font-medium">With your account</span>
              <span className="text-ink-600 block">
                {draft.contactConsent
                  ? 'The responding team may contact you.'
                  : 'You have asked not to be contacted.'}
              </span>
            </>
          )}
        </ReviewRow>
        <ReviewRow label="Photos">
          {evidenceCount === 0
            ? 'None added'
            : `${evidenceCount} image${evidenceCount === 1 ? '' : 's'}`}
          {evidenceCount > 0 && (
            <span className="text-ink-600 block">
              Hidden location and device data removed. Anything visible in the picture is
              unchanged.
            </span>
          )}
        </ReviewRow>
        <ReviewRow label="Urgency">
          {draft.isEmergency ? (
            <>
              <span className="font-medium">Needs immediate attention</span>
              {draft.isOngoing && (
                <span className="text-ink-600 block">The situation is still happening.</span>
              )}
            </>
          ) : (
            'Reviewed in the usual way'
          )}
        </ReviewRow>
      </dl>

      <p className="text-ink-500 text-xs leading-relaxed">
        Your name is not stored by CampusShield, and it is not part of this report.
        {draft.anonymous
          ? ' Because you chose to submit anonymously, your account is not recorded against it either.'
          : ' Your account is recorded so that you can follow this report.'}
      </p>
    </div>
  )
}
