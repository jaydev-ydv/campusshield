interface StepperProps {
  steps: readonly string[]
  /** Zero-based index of the step being shown. */
  current: number
}

/**
 * Progress through the reporting form.
 *
 * The visual bar is decorative and hidden from assistive technology; the
 * accessible version is one sentence — "Step 3 of 7: When did this happen?" —
 * because a screen reader user needs to know where they are, not to navigate a
 * row of coloured segments.
 */
export function Stepper({ steps, current }: StepperProps) {
  const total = steps.length
  const clamped = Math.min(Math.max(current, 0), total - 1)
  const percent = ((clamped + 1) / total) * 100

  return (
    <div className="mb-6">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-ink-800 text-sm font-medium">{steps[clamped]}</p>
        <p className="text-ink-500 shrink-0 text-xs tabular-nums">
          Step {clamped + 1} of {total}
        </p>
      </div>

      <div
        className="bg-ink-200 mt-2 h-1.5 overflow-hidden rounded-full"
        role="progressbar"
        aria-valuemin={1}
        aria-valuemax={total}
        aria-valuenow={clamped + 1}
        aria-label={`Step ${clamped + 1} of ${total}: ${steps[clamped]}`}
      >
        <div
          className="bg-brand-600 h-full rounded-full transition-[width] duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  )
}
