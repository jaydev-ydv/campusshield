import { useState } from 'react'

import { Alert } from '../components/ui/Alert'
import { Button } from '../components/ui/Button'
import {
  LINK_TYPE_LABELS,
  RISK_BAND_LABELS,
  type ReportCategory,
  type Triage,
} from '../lib/api'

/**
 * What the machine suggested, framed as a suggestion.
 *
 * ## The framing is the feature
 *
 * A category suggestion and a risk band are easy to build and easy to make
 * dangerous. Someone triaging fourteen reports at 3am will take a confident
 * label at face value unless the interface actively resists that. So:
 *
 * - the section is headed "Suggestions", not "Analysis" or "Assessment";
 * - the model's training data is stated wherever a suggestion appears, because
 *   every model this project can produce is trained on generated text;
 * - the risk band is labelled as ordering, and its factors are shown in full —
 *   a number that sorts a queue must be answerable for on screen;
 * - the reporter's own category is shown next to the suggestion, so disagreement
 *   reads as "the model differs from the student", not "the student was wrong".
 *
 * ## What is not here
 *
 * The full probability distribution. Sixteen numbers invite reading tea leaves;
 * they stay in the database for evaluation.
 */
export function TriagePanel({
  triage,
  declaredCategoryLabel,
  categories,
  onOverride,
  onReviewLink,
  busy,
}: {
  triage: Triage
  declaredCategoryLabel: string | null
  categories: ReportCategory[] | null
  onOverride: (categoryId: number) => void
  onReviewLink: (linkId: number, confirmed: boolean) => void
  busy?: boolean
}) {
  const [choosing, setChoosing] = useState(false)

  const hasSuggestion = triage.suggested_category !== null
  const hasRisk = triage.risk_band !== null
  if (!hasSuggestion && !hasRisk && triage.related.length === 0) return null

  return (
    <section aria-labelledby="triage-heading" className="space-y-3">
      <h3 id="triage-heading" className="text-ink-900 text-sm font-medium">
        Suggestions
      </h3>

      {/* Stated once, above everything derived from a model. */}
      {!triage.model_trained_on_real_data && triage.model && (
        <Alert tone="info" title="These suggestions come from a model trained on example data">
          <p>
            No real campus reports were used to train it, so its accuracy on real reports is
            unknown. Treat everything in this section as a prompt to look, not as a finding.
          </p>
        </Alert>
      )}

      {hasSuggestion && (
        <div className="border-ink-200 rounded-lg border bg-white p-3">
          <p className="text-ink-600 text-xs font-medium tracking-wide uppercase">
            Suggested category
          </p>
          <p className="text-ink-900 mt-1 text-sm">
            {triage.suggested_category?.label}
            {triage.confidence !== null && (
              <span className="text-ink-500">
                {' '}
                · {Math.round(triage.confidence * 100)}% confidence
              </span>
            )}
          </p>

          <p className="text-ink-600 mt-2 text-sm">
            The reporter chose <strong>{declaredCategoryLabel ?? 'no category'}</strong>.
            {triage.agrees_with_reporter === false && ' The suggestion differs.'}
          </p>

          {triage.overridden_category ? (
            <p className="text-ink-700 mt-2 text-sm">
              A responder recorded this as <strong>{triage.overridden_category.label}</strong>.
            </p>
          ) : (
            <div className="mt-3">
              {choosing && categories ? (
                <div className="flex flex-wrap items-center gap-2">
                  <label htmlFor="override-category" className="sr-only">
                    Record a different category
                  </label>
                  <select
                    id="override-category"
                    className="border-ink-300 rounded-md border px-2 py-1.5 text-sm"
                    defaultValue=""
                    onChange={(event) => {
                      const value = Number(event.target.value)
                      if (value) {
                        onOverride(value)
                        setChoosing(false)
                      }
                    }}
                    disabled={busy}
                  >
                    <option value="" disabled>
                      Choose a category…
                    </option>
                    {categories.map((category) => (
                      <option key={category.category_id} value={category.category_id}>
                        {category.label}
                      </option>
                    ))}
                  </select>
                  <Button variant="ghost" size="sm" onClick={() => setChoosing(false)}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setChoosing(true)}
                  disabled={busy}
                >
                  Record a different category
                </Button>
              )}
              {/* Said explicitly, because it is the kind of thing a responder
                  would reasonably assume the opposite of. */}
              <p className="text-ink-500 mt-2 text-xs">
                This records your judgement alongside the report. It does not change the
                category the reporter chose.
              </p>
            </div>
          )}
        </div>
      )}

      {hasRisk && (
        <div className="border-ink-200 rounded-lg border bg-white p-3">
          <p className="text-ink-600 text-xs font-medium tracking-wide uppercase">
            Triage order
          </p>
          <p className="text-ink-900 mt-1 text-sm">
            {RISK_BAND_LABELS[triage.risk_band as keyof typeof RISK_BAND_LABELS]}
            {triage.risk_score !== null && (
              <span className="text-ink-500"> · {triage.risk_score}</span>
            )}
          </p>
          <p className="text-ink-600 mt-1 text-sm">
            A rule-based ordering aid, so overnight reports can be looked at in a sensible
            order. It is not a prediction of harm and not an assessment of anyone.
          </p>
          {triage.risk_factors && <RiskFactors factors={triage.risk_factors} />}
        </div>
      )}

      {triage.related.length > 0 && (
        <div className="border-ink-200 rounded-lg border bg-white p-3">
          <p className="text-ink-600 text-xs font-medium tracking-wide uppercase">
            Possibly related reports
          </p>
          <ul className="mt-2 space-y-3">
            {triage.related.map((link) => (
              <li key={link.link_id} className="text-sm">
                <p className="text-ink-900">
                  {link.public_ref}
                  <span className="text-ink-500"> · {LINK_TYPE_LABELS[link.link_type]}</span>
                </p>
                <p className="text-ink-600">
                  {link.category_label ?? 'Category not recorded'} · {link.location_name}
                </p>
                {link.review_state === 'unreviewed' ? (
                  <div className="mt-1.5 flex gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onReviewLink(link.link_id, true)}
                      disabled={busy}
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onReviewLink(link.link_id, false)}
                      disabled={busy}
                    >
                      Not related
                    </Button>
                  </div>
                ) : (
                  <p className="text-ink-500 mt-0.5 text-xs">Confirmed by a responder</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

/** The arithmetic, on screen rather than in a database. */
function RiskFactors({ factors }: { factors: Record<string, unknown> }) {
  const contributions = factors.contributions as Record<string, number> | undefined
  const excluded = factors.excluded_by_design as string[] | undefined
  if (!contributions) return null

  return (
    <details className="mt-2">
      <summary className="text-ink-700 cursor-pointer text-sm">Why this order?</summary>
      <dl className="text-ink-600 mt-2 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
        {Object.entries(contributions).map(([name, value]) => (
          <div key={name} className="contents">
            <dt>{name.replace(/_/g, ' ')}</dt>
            <dd className="text-right tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {excluded && (
        <p className="text-ink-500 mt-2 text-xs">
          Deliberately not considered: {excluded.join(', ')}.
        </p>
      )}
    </details>
  )
}
