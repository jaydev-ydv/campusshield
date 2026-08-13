import { useId, type ReactNode, type TextareaHTMLAttributes } from 'react'

interface TextareaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'id'> {
  label: string
  hint?: ReactNode
  error?: string
  id?: string
  /** Shows a live remaining count when set. */
  maxLength?: number
  minLength?: number
}

export function Textarea({
  label,
  hint,
  error,
  id,
  maxLength,
  minLength,
  value,
  className = '',
  ...props
}: TextareaProps) {
  const generatedId = useId()
  const fieldId = id ?? generatedId
  const hintId = `${fieldId}-hint`
  const errorId = `${fieldId}-error`
  const countId = `${fieldId}-count`

  const length = typeof value === 'string' ? value.length : 0
  const describedBy = [
    hint ? hintId : null,
    maxLength ? countId : null,
    error ? errorId : null,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="space-y-1.5">
      <label htmlFor={fieldId} className="text-ink-700 block text-sm font-medium">
        {label}
      </label>

      {hint && (
        <p id={hintId} className="text-ink-500 text-xs leading-relaxed">
          {hint}
        </p>
      )}

      <textarea
        {...props}
        id={fieldId}
        value={value}
        maxLength={maxLength}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={[
          'w-full rounded-lg border px-3 py-2.5 text-base leading-relaxed',
          'placeholder:text-ink-400 bg-white transition-colors duration-150',
          error
            ? 'border-red-400 focus:border-red-500'
            : 'border-ink-300 focus:border-brand-500',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
      />

      {maxLength && (
        <p
          id={countId}
          // Polite, not assertive: a counter that interrupts on every keystroke
          // makes the field unusable with a screen reader.
          aria-live="polite"
          className="text-ink-500 text-right text-xs tabular-nums"
        >
          {minLength && length < minLength
            ? `${minLength - length} more character${minLength - length === 1 ? '' : 's'} needed`
            : `${length.toLocaleString()} / ${maxLength.toLocaleString()}`}
        </p>
      )}

      {error && (
        <p id={errorId} role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}
