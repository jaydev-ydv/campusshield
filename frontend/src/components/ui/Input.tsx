import { useId, type InputHTMLAttributes, type ReactNode } from 'react'

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string
  /** Guidance shown before the user has done anything wrong. */
  hint?: ReactNode
  error?: string
  id?: string
}

export function Input({ label, hint, error, id, className = '', ...props }: InputProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hintId = `${inputId}-hint`
  const errorId = `${inputId}-error`

  // Both are linked, not just the error: a screen reader user needs the hint on
  // first focus, not only after the field has been rejected.
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ')

  return (
    <div className="space-y-1.5">
      {/* A real <label>, never a placeholder standing in for one. Placeholders
          vanish on focus, are invisible to some assistive technology, and leave
          nothing to tap for people using switch control. */}
      <label htmlFor={inputId} className="text-ink-700 block text-sm font-medium">
        {label}
      </label>

      {hint && (
        <p id={hintId} className="text-ink-500 text-xs">
          {hint}
        </p>
      )}

      <input
        {...props}
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={[
          'w-full rounded-lg border px-3 py-2.5 text-base',
          // 16px minimum: anything smaller makes iOS Safari zoom on focus, which
          // throws the layout of a form someone may be filling in urgently.
          'placeholder:text-ink-400 bg-white',
          'transition-colors duration-150',
          error
            ? 'border-red-400 focus:border-red-500'
            : 'border-ink-300 focus:border-brand-500',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
      />

      {error && (
        // role="alert" so the message is announced when it appears, rather than
        // silently changing the page for anyone not looking at that field.
        <p id={errorId} role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}
