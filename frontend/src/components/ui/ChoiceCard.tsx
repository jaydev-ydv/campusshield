import { useId, type ReactNode } from 'react'

interface Choice<T extends string> {
  value: T
  label: string
  description?: ReactNode
  /** Rendered small and muted beneath the description. */
  note?: ReactNode
  disabled?: boolean
  disabledReason?: string
}

interface ChoiceCardsProps<T extends string> {
  legend: string
  /** Guidance for the whole group, announced with it. */
  hint?: ReactNode
  name: string
  value: T | null
  onChange: (value: T) => void
  choices: Choice<T>[]
  error?: string
  columns?: 1 | 2
}

/**
 * A radio group rendered as tappable cards.
 *
 * Real `<input type="radio">` elements underneath, wrapped in a `<fieldset>`
 * with a `<legend>` — not divs with click handlers. Arrow-key navigation,
 * grouping announcements, and form semantics all come free, and every one of
 * them has to be rebuilt by hand otherwise.
 *
 * Cards rather than a bare list because these choices carry consequences a
 * one-word label cannot convey. "Anonymous" needs to say that the report will
 * not be linked to the account and cannot be recovered without the code.
 */
export function ChoiceCards<T extends string>({
  legend,
  hint,
  name,
  value,
  onChange,
  choices,
  error,
  columns = 1,
}: ChoiceCardsProps<T>) {
  const groupId = useId()
  const hintId = `${groupId}-hint`
  const errorId = `${groupId}-error`

  return (
    <fieldset
      aria-describedby={
        [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined
      }
    >
      <legend className="text-ink-800 text-base font-medium">{legend}</legend>

      {hint && (
        <p id={hintId} className="text-ink-600 mt-1.5 text-sm leading-relaxed">
          {hint}
        </p>
      )}

      <div
        className={['mt-4 grid gap-3', columns === 2 ? 'sm:grid-cols-2' : 'grid-cols-1'].join(
          ' ',
        )}
      >
        {choices.map((choice) => {
          const id = `${groupId}-${choice.value}`
          const selected = value === choice.value
          return (
            <div key={choice.value}>
              <input
                type="radio"
                id={id}
                name={name}
                value={choice.value}
                checked={selected}
                disabled={choice.disabled}
                onChange={() => onChange(choice.value)}
                className="peer sr-only"
              />
              <label
                htmlFor={id}
                className={[
                  'block cursor-pointer rounded-xl border p-4 transition-colors',
                  'peer-focus-visible:outline-brand-600 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2',
                  choice.disabled
                    ? 'border-ink-200 bg-ink-50 cursor-not-allowed opacity-70'
                    : selected
                      ? 'border-brand-600 bg-brand-50 ring-brand-600 ring-1'
                      : 'border-ink-300 bg-white hover:border-ink-400',
                ].join(' ')}
              >
                <span className="flex items-start gap-3">
                  <span
                    aria-hidden="true"
                    className={[
                      'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2',
                      selected ? 'border-brand-600' : 'border-ink-300',
                    ].join(' ')}
                  >
                    {selected && <span className="bg-brand-600 h-2.5 w-2.5 rounded-full" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="text-ink-900 block text-sm font-medium">
                      {choice.label}
                    </span>
                    {choice.description && (
                      <span className="text-ink-600 mt-1 block text-sm leading-relaxed">
                        {choice.description}
                      </span>
                    )}
                    {choice.disabled && choice.disabledReason && (
                      <span className="text-ink-500 mt-1.5 block text-xs">
                        {choice.disabledReason}
                      </span>
                    )}
                    {choice.note && !choice.disabled && (
                      <span className="text-ink-500 mt-1.5 block text-xs leading-relaxed">
                        {choice.note}
                      </span>
                    )}
                  </span>
                </span>
              </label>
            </div>
          )
        })}
      </div>

      {error && (
        <p id={errorId} role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </fieldset>
  )
}
