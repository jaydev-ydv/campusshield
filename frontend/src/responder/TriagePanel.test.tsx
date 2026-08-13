import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { TriagePanel } from './TriagePanel'
import type { ReportCategory, Triage } from '../lib/api'

const CATEGORIES: ReportCategory[] = [
  {
    category_id: 1,
    code: 'HARASS_VERBAL',
    label: 'Verbal harassment',
    kind: 'incident',
    emergency_eligible: true,
    requires_confidentiality: true,
  },
  {
    category_id: 2,
    code: 'STALKING',
    label: 'Following or stalking',
    kind: 'incident',
    emergency_eligible: true,
    requires_confidentiality: true,
  },
]

function triage(overrides: Partial<Triage> = {}): Triage {
  return {
    suggested_category: { category_id: 2, code: 'STALKING', label: 'Following or stalking' },
    confidence: 0.73,
    agrees_with_reporter: false,
    overridden_category: null,
    model: 'tfidf-logreg@1.0.0',
    model_trained_on_real_data: false,
    risk_score: 62,
    risk_band: 'high',
    risk_factors: {
      contributions: { category_severity: 24, emergency: 25, ongoing: 13 },
      weights: { category_severity: 40, emergency: 25, ongoing: 20 },
      excluded_by_design: ['reporter identity', 'reporter history'],
    },
    related: [],
    ...overrides,
  }
}

function renderPanel(value = triage(), props: Record<string, unknown> = {}) {
  const onOverride = vi.fn()
  const onReviewLink = vi.fn()
  render(
    <TriagePanel
      triage={value}
      declaredCategoryLabel="Verbal harassment"
      categories={CATEGORIES}
      onOverride={onOverride}
      onReviewLink={onReviewLink}
      {...props}
    />,
  )
  return { onOverride, onReviewLink }
}

const user = () => userEvent.setup({ delay: null })

describe('TriagePanel — framing', () => {
  it('is headed as suggestions, not analysis or assessment', () => {
    renderPanel()
    expect(screen.getByRole('heading', { name: /suggestions/i })).toBeInTheDocument()
    // Targets the panel *presenting itself* as a finding. The disclaimers
    // legitimately contain "assessment" — in "not an assessment of anyone" —
    // so the check is on headings and labels, not on every occurrence.
    const headings = Array.from(document.querySelectorAll('h3, dt, summary'))
      .map((node) => node.textContent ?? '')
      .join(' ')
    expect(headings).not.toMatch(/analysis|assessment|verdict|prediction/i)
  })

  it('says the model was not trained on real reports', () => {
    // The claim the whole ML layer has to keep making.
    renderPanel()
    expect(screen.getByText(/trained on example data/i)).toBeInTheDocument()
    expect(screen.getByText(/accuracy on real reports is unknown/i)).toBeInTheDocument()
    expect(screen.getByText(/a prompt to look, not as a finding/i)).toBeInTheDocument()
  })

  it('drops the caveat only when the model was trained on real data', () => {
    renderPanel(triage({ model_trained_on_real_data: true }))
    expect(screen.queryByText(/trained on example data/i)).not.toBeInTheDocument()
  })

  it('describes the risk band as ordering, not as predicted harm', () => {
    renderPanel()
    expect(screen.getByText(/rule-based ordering aid/i)).toBeInTheDocument()
    expect(screen.getByText(/not a prediction of harm/i)).toBeInTheDocument()
  })
})

describe('TriagePanel — the suggestion', () => {
  it('shows the suggestion with its confidence', () => {
    renderPanel()
    expect(screen.getByText(/following or stalking/i)).toBeInTheDocument()
    expect(screen.getByText(/73% confidence/i)).toBeInTheDocument()
  })

  it("shows the reporter's own category beside it", () => {
    // Disagreement should read as "the model differs", not "the student was
    // wrong".
    renderPanel()
    const text = document.body.textContent ?? ''
    expect(text).toMatch(/the reporter chose/i)
    expect(text).toMatch(/verbal harassment/i)
    expect(text).toMatch(/the suggestion differs/i)
  })

  it('does not show the full probability distribution', () => {
    renderPanel()
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/label_scores/i)
    // Only the one confidence figure, not sixteen.
    expect((text.match(/% confidence/g) ?? []).length).toBe(1)
  })

  it('says an override does not change what the reporter chose', () => {
    renderPanel()
    expect(
      screen.getByText(/does not change the category the reporter chose/i),
    ).toBeInTheDocument()
  })

  it('records a different category when a responder picks one', async () => {
    const u = user()
    const { onOverride } = renderPanel()

    await u.click(screen.getByRole('button', { name: /record a different category/i }))
    await u.selectOptions(screen.getByLabelText(/record a different category/i), '1')

    expect(onOverride).toHaveBeenCalledWith(1)
  })

  it('shows an existing override instead of offering another', () => {
    renderPanel(
      triage({
        overridden_category: {
          category_id: 1,
          code: 'HARASS_VERBAL',
          label: 'Verbal harassment',
        },
      }),
    )
    expect(screen.getByText(/a responder recorded this as/i)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /record a different category/i }),
    ).not.toBeInTheDocument()
  })
})

describe('TriagePanel — risk factors', () => {
  it('shows the arithmetic behind the band', async () => {
    const u = user()
    renderPanel()

    await u.click(screen.getByText(/why this order\?/i))

    expect(screen.getByText(/category severity/i)).toBeInTheDocument()
    expect(screen.getByText('25')).toBeInTheDocument()
  })

  it('states what the score deliberately ignored', async () => {
    const u = user()
    renderPanel()
    await u.click(screen.getByText(/why this order\?/i))
    expect(screen.getByText(/deliberately not considered/i)).toBeInTheDocument()
    expect(screen.getByText(/reporter identity/i)).toBeInTheDocument()
  })
})

describe('TriagePanel — related reports', () => {
  const related = {
    link_id: 7,
    public_ref: 'CS-2026-OTHER1',
    link_type: 'duplicate' as const,
    similarity: 0.91,
    review_state: 'unreviewed' as const,
    occurred_at: '2026-08-10T18:00:00+00:00',
    location_name: 'Library Block',
    category_label: 'Following or stalking',
  }

  it('proposes a link in language that does not assert it', () => {
    renderPanel(triage({ related: [related] }))
    expect(screen.getByText(/may be the same incident/i)).toBeInTheDocument()
  })

  it('lets a responder confirm or reject it', async () => {
    const u = user()
    const { onReviewLink } = renderPanel(triage({ related: [related] }))

    await u.click(screen.getByRole('button', { name: /^confirm$/i }))
    expect(onReviewLink).toHaveBeenCalledWith(7, true)

    await u.click(screen.getByRole('button', { name: /not related/i }))
    expect(onReviewLink).toHaveBeenCalledWith(7, false)
  })

  it('stops offering review once a link is confirmed', () => {
    renderPanel(triage({ related: [{ ...related, review_state: 'confirmed' }] }))
    expect(screen.queryByRole('button', { name: /^confirm$/i })).not.toBeInTheDocument()
    expect(screen.getByText(/confirmed by a responder/i)).toBeInTheDocument()
  })

  it('shows nothing when there is nothing to suggest', () => {
    const { container } = render(
      <TriagePanel
        triage={triage({
          suggested_category: null,
          confidence: null,
          risk_band: null,
          risk_score: null,
          risk_factors: null,
          related: [],
        })}
        declaredCategoryLabel="Verbal harassment"
        categories={CATEGORIES}
        onOverride={vi.fn()}
        onReviewLink={vi.fn()}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})

describe('TriagePanel — privacy', () => {
  it('renders no reporter identity or embedding', () => {
    renderPanel(
      triage({
        related: [
          {
            link_id: 7,
            public_ref: 'CS-2026-OTHER1',
            link_type: 'related',
            similarity: 0.8,
            review_state: 'unreviewed',
            occurred_at: '2026-08-10T18:00:00+00:00',
            location_name: 'Library Block',
            category_label: null,
          },
        ],
      }),
    )
    const markup = document.body.innerHTML
    expect(markup).not.toMatch(/user_id|embedding|reporter_relationship/i)
  })

  it('never presents a suggestion as a statement about the reporter', () => {
    renderPanel()
    const text = (document.body.textContent ?? '').toLowerCase()
    for (const phrase of [
      'credibility',
      'trust score',
      'reliability',
      'false report',
      'likely true',
    ]) {
      expect(text).not.toContain(phrase)
    }
  })
})

describe('TriagePanel — accessibility', () => {
  it('groups itself under a labelled region', () => {
    renderPanel()
    const region = screen.getByRole('region', { name: /suggestions/i })
    expect(within(region).getByText(/following or stalking/i)).toBeInTheDocument()
  })
})
